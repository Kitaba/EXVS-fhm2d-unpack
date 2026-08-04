#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

try:
    from .fhm2d_unpack import iter_deflate_blocks, read_header
except ImportError:
    from fhm2d_unpack import iter_deflate_blocks, read_header


TEXTURE_NAME_PREFIX = b"46XT"
TEXTURE_NAME_PATTERN = re.compile(
    r"(?:46XTimg-(?P<img_index>\d+)|46XT[!-~]+)"
)
TEXTURE_SCANNER_VERSION = 3
TEXTURE_METADATA_SIZE = 0xB0
TEXTURE_NAME_OFFSET = 0x40
TEXTURE_WIDTH_OFFSET = 0x84
TEXTURE_HEIGHT_OFFSET = 0x88
TEXTURE_MIP_COUNT_OFFSET = 0x98
TEXTURE_FORMAT_OFFSET = 0x90
TEXTURE_BLOCK_DIMENSION_OFFSET = 0x94
TEXTURE_ARRAY_SIZE_OFFSET = 0x8C
TEXTURE_DEPTH_OFFSET = 0xA0
TEXTURE_DATA_SIZE_COPY_OFFSET = 0xA4
TEXTURE_MARKER_OFFSET = 0xA8

FHM2D_BC7_FORMAT = 0x4E0
FHM2D_BC7_SRGB_FORMAT = 0x4E5
FHM2D_BC3_FORMAT = 0x4A0
FHM2D_BC3_SRGB_FORMAT = 0x4A5
FHM2D_BC4_FORMAT = 0x180
FHM2D_RGBA8_FORMAT = 0x400
TEXTURE_MARKER = 0x54455820
DXGI_FORMAT_R8G8B8A8_UNORM = 28
DXGI_FORMAT_BC7_UNORM = 98
DXGI_FORMAT_BC7_UNORM_SRGB = 99
DXGI_FORMAT_BC3_UNORM = 77
DXGI_FORMAT_BC3_UNORM_SRGB = 78
DXGI_FORMAT_BC4_UNORM = 80
BC7_BLOCK_SIZE = 16
BC7_BLOCK_DIMENSION = 4


class NoSupportedTexturesError(ValueError):
    """The payload contains no structurally valid supported 46XT textures."""


def u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def bc7_size(width, height):
    return (
        ((width + BC7_BLOCK_DIMENSION - 1) // BC7_BLOCK_DIMENSION)
        * ((height + BC7_BLOCK_DIMENSION - 1) // BC7_BLOCK_DIMENSION)
        * BC7_BLOCK_SIZE
    )


def bc3_size(width, height):
    return ((width + 3) // 4) * ((height + 3) // 4) * 16


def bc4_size(width, height):
    return ((width + 3) // 4) * ((height + 3) // 4) * 8


def rgba8_size(width, height):
    return width * height * 4


def mip_chain_size(width, height, mip_count, size_function):
    total = 0
    for _ in range(mip_count):
        total += size_function(width, height)
        width = max(1, width // 2)
        height = max(1, height // 2)
    return total


def make_block_dds(
    width, height, pixel_data, dxgi_format, size_function, mip_count=1
):
    top_size = size_function(width, height)
    expected_size = mip_chain_size(width, height, mip_count, size_function)
    if len(pixel_data) != expected_size:
        raise ValueError(
            f"block data size mismatch: got {len(pixel_data)}, expected {expected_size}"
        )

    # Matches the DX10 BC7_UNORM headers emitted by RenderDoc for these resources.
    header = bytearray(148)
    header[0:4] = b"DDS "
    struct.pack_into("<I", header, 0x04, 124)
    flags = 0x00081007 | (0x00020000 if mip_count > 1 else 0)
    struct.pack_into("<I", header, 0x08, flags)
    struct.pack_into("<I", header, 0x0C, height)
    struct.pack_into("<I", header, 0x10, width)
    struct.pack_into("<I", header, 0x14, top_size)
    struct.pack_into("<I", header, 0x18, 1)
    struct.pack_into("<I", header, 0x1C, mip_count)
    struct.pack_into("<I", header, 0x4C, 32)
    struct.pack_into("<I", header, 0x50, 0x4)
    header[0x54:0x58] = b"DX10"
    caps = 0x1000 | (0x00400008 if mip_count > 1 else 0)
    struct.pack_into("<I", header, 0x6C, caps)
    struct.pack_into("<I", header, 0x80, dxgi_format)
    struct.pack_into("<I", header, 0x84, 3)
    struct.pack_into("<I", header, 0x8C, 1)
    return bytes(header) + pixel_data


def make_bc7_dds(width, height, pixel_data, mip_count=1):
    return make_block_dds(
        width, height, pixel_data, DXGI_FORMAT_BC7_UNORM, bc7_size, mip_count
    )


def make_bc7_srgb_dds(width, height, pixel_data, mip_count=1):
    return make_block_dds(
        width, height, pixel_data, DXGI_FORMAT_BC7_UNORM_SRGB, bc7_size, mip_count
    )


def make_bc3_dds(width, height, pixel_data, mip_count=1):
    return make_block_dds(
        width, height, pixel_data, DXGI_FORMAT_BC3_UNORM, bc3_size, mip_count
    )


def make_bc3_srgb_dds(width, height, pixel_data, mip_count=1):
    return make_block_dds(
        width, height, pixel_data, DXGI_FORMAT_BC3_UNORM_SRGB, bc3_size, mip_count
    )


def make_bc4_dds(width, height, pixel_data, mip_count=1):
    return make_block_dds(
        width, height, pixel_data, DXGI_FORMAT_BC4_UNORM, bc4_size, mip_count
    )


def make_rgba8_dds(width, height, pixel_data, mip_count=1):
    expected_size = mip_chain_size(width, height, mip_count, rgba8_size)
    if len(pixel_data) != expected_size:
        raise ValueError(
            f"RGBA8 data size mismatch: got {len(pixel_data)}, "
            f"expected {expected_size}"
        )

    header = bytearray(148)
    header[0:4] = b"DDS "
    struct.pack_into("<I", header, 0x04, 124)
    flags = 0x0000100F | (0x00020000 if mip_count > 1 else 0)
    struct.pack_into("<I", header, 0x08, flags)
    struct.pack_into("<I", header, 0x0C, height)
    struct.pack_into("<I", header, 0x10, width)
    struct.pack_into("<I", header, 0x14, width * 4)
    struct.pack_into("<I", header, 0x18, 1)
    struct.pack_into("<I", header, 0x1C, mip_count)
    struct.pack_into("<I", header, 0x4C, 32)
    struct.pack_into("<I", header, 0x50, 0x4)
    header[0x54:0x58] = b"DX10"
    caps = 0x1000 | (0x00400008 if mip_count > 1 else 0)
    struct.pack_into("<I", header, 0x6C, caps)
    struct.pack_into("<I", header, 0x80, DXGI_FORMAT_R8G8B8A8_UNORM)
    struct.pack_into("<I", header, 0x84, 3)
    struct.pack_into("<I", header, 0x8C, 1)
    return bytes(header) + pixel_data


TEXTURE_FORMATS = {
    FHM2D_RGBA8_FORMAT: {
        "name": "rgba8",
        "description": "R8G8B8A8_UNORM",
        "dxgi_format": DXGI_FORMAT_R8G8B8A8_UNORM,
        "size": rgba8_size,
        "make_dds": make_rgba8_dds,
    },
    FHM2D_BC7_FORMAT: {
        "name": "bc7",
        "description": "BC7_UNORM",
        "dxgi_format": DXGI_FORMAT_BC7_UNORM,
        "size": bc7_size,
        "make_dds": make_bc7_dds,
    },
    FHM2D_BC7_SRGB_FORMAT: {
        "name": "bc7_srgb",
        "description": "BC7_UNORM_SRGB",
        "dxgi_format": DXGI_FORMAT_BC7_UNORM_SRGB,
        "size": bc7_size,
        "make_dds": make_bc7_srgb_dds,
    },
    FHM2D_BC3_FORMAT: {
        "name": "bc3",
        "description": "BC3_UNORM",
        "dxgi_format": DXGI_FORMAT_BC3_UNORM,
        "size": bc3_size,
        "make_dds": make_bc3_dds,
    },
    FHM2D_BC3_SRGB_FORMAT: {
        "name": "bc3_srgb",
        "description": "BC3_UNORM_SRGB",
        "dxgi_format": DXGI_FORMAT_BC3_UNORM_SRGB,
        "size": bc3_size,
        "make_dds": make_bc3_srgb_dds,
    },
    FHM2D_BC4_FORMAT: {
        "name": "bc4",
        "description": "BC4_UNORM",
        "dxgi_format": DXGI_FORMAT_BC4_UNORM,
        "size": bc4_size,
        "make_dds": make_bc4_dds,
    },
}

PAYLOAD_PADDING_TOLERANCE = 0x100


def decode_payload(input_path, strict=True):
    blob = input_path.read_bytes()
    header = read_header(blob)
    decoded = []
    trailing_offset = None

    for index, file_offset, compressed_size, data in iter_deflate_blocks(blob):
        if index is None:
            trailing_offset = file_offset
            break
        decoded.append((index, file_offset, compressed_size, data))

    if len(decoded) < 2:
        raise ValueError("fhm2d contains no payload blocks")

    index_data = decoded[0][3]
    declared_size = u32(index_data, 0x34) | (u32(index_data, 0x38) << 32)
    payload_parts = []
    payload_size = 0
    payload_end = None
    for position, (_, file_offset, _, data) in enumerate(decoded[1:], 1):
        remaining = declared_size - payload_size
        if remaining <= 0:
            break
        if len(data) > remaining:
            payload_parts.append(data[:remaining])
            payload_size += remaining
            next_block = (
                decoded[position + 1]
                if position + 1 < len(decoded)
                else None
            )
            payload_end = next_block[1] if next_block else len(blob)
            break
        payload_parts.append(data)
        payload_size += len(data)
        if payload_size == declared_size:
            next_block = decoded[position + 1] if position + 1 < len(decoded) else None
            payload_end = next_block[1] if next_block else len(blob)
            break
    payload = b"".join(payload_parts)
    tolerated_padding = (
        declared_size > payload_size
        and declared_size - payload_size <= PAYLOAD_PADDING_TOLERANCE
        and payload_end is None
    )
    if strict and declared_size != payload_size and not tolerated_padding:
        raise ValueError(
            f"payload size mismatch: index={declared_size}, decoded={payload_size}"
        )

    if payload_end is not None:
        trailing_offset = payload_end
    trailing = blob[trailing_offset:] if trailing_offset is not None else b""
    return blob, header, payload, trailing


def parse_group_labels(trailing):
    prefix = trailing.split(b"\0", 1)[0]
    if not re.fullmatch(rb"(?:[a-z][0-9])+", prefix):
        return []
    return [
        prefix[offset : offset + 2].decode("ascii")
        for offset in range(0, len(prefix), 2)
    ]


def scan_textures(payload, supported_formats=None):
    if supported_formats is None:
        supported_formats = set(TEXTURE_FORMATS)
    else:
        supported_formats = set(supported_formats)
    textures = []
    search_offset = 0

    while True:
        name_offset = payload.find(TEXTURE_NAME_PREFIX, search_offset)
        if name_offset < 0:
            break
        metadata_offset = name_offset - TEXTURE_NAME_OFFSET
        search_offset = name_offset + 1
        if metadata_offset < 0 or metadata_offset + TEXTURE_METADATA_SIZE > len(
            payload
        ):
            continue

        name_bytes = payload[name_offset : name_offset + 0x40].split(b"\0", 1)[0]
        try:
            embedded_name = name_bytes.decode("ascii")
        except UnicodeDecodeError:
            continue
        name_match = TEXTURE_NAME_PATTERN.fullmatch(embedded_name)
        if not name_match:
            continue
        embedded_index = (
            int(name_match.group("img_index"))
            if name_match.group("img_index") is not None
            else len(textures)
        )

        data_size = u32(payload, metadata_offset)
        width = u32(payload, metadata_offset + TEXTURE_WIDTH_OFFSET)
        height = u32(payload, metadata_offset + TEXTURE_HEIGHT_OFFSET)
        mip_count = u32(payload, metadata_offset + TEXTURE_MIP_COUNT_OFFSET)
        format_code = u32(payload, metadata_offset + TEXTURE_FORMAT_OFFSET)
        block_dimension = u32(
            payload, metadata_offset + TEXTURE_BLOCK_DIMENSION_OFFSET
        )
        array_size = u32(payload, metadata_offset + TEXTURE_ARRAY_SIZE_OFFSET)
        depth = u32(payload, metadata_offset + TEXTURE_DEPTH_OFFSET)
        allocation_size = u32(
            payload, metadata_offset + TEXTURE_DATA_SIZE_COPY_OFFSET
        )
        marker = u32(payload, metadata_offset + TEXTURE_MARKER_OFFSET)
        data_offset = metadata_offset - allocation_size

        # Other resource types also use 46XT names. Confirm the texture
        # record signature before applying strict texture validation.
        if (
            marker != TEXTURE_MARKER
            or data_offset < 0
            or allocation_size < data_size
            or width == 0
            or height == 0
        ):
            continue

        problems = []
        format_info = TEXTURE_FORMATS.get(format_code)
        # Mixed resource packages can contain valid texture formats that this
        # extractor does not yet understand (for example cubemaps).  Leave
        # those records untouched while still extracting every known format.
        if format_code not in supported_formats or format_info is None:
            continue
        if mip_count < 1:
            problems.append(f"invalid mip count {mip_count}")
        if array_size != 1 or depth != 1:
            problems.append(f"unsupported array/depth {array_size}/{depth}")
        if block_dimension not in {2, BC7_BLOCK_DIMENSION}:
            problems.append(f"unexpected texture dimension {block_dimension}")
        expected_size = (
            format_info["size"](width, height) if format_info is not None else None
        )
        if expected_size is not None and data_size != expected_size:
            problems.append(
                f"size {data_size} != {format_info['name'].upper()} "
                f"size {expected_size}"
            )
        expected_allocation_size = (
            mip_chain_size(width, height, mip_count, format_info["size"])
            if format_info is not None and mip_count >= 1
            else None
        )
        if (
            expected_allocation_size is not None
            and allocation_size != expected_allocation_size
        ):
            problems.append(
                f"allocation {allocation_size} != full mip chain "
                f"size {expected_allocation_size}"
            )
        if problems:
            raise ValueError(
                f"invalid texture metadata at payload 0x{metadata_offset:X}: "
                + "; ".join(problems)
            )

        textures.append(
            {
                "texture_index": len(textures),
                "embedded_name": embedded_name,
                "embedded_index": embedded_index,
                "payload_data_offset": data_offset,
                "payload_metadata_offset": metadata_offset,
                "data_size": data_size,
                "allocation_size": allocation_size,
                "metadata_size": TEXTURE_METADATA_SIZE,
                "width": width,
                "height": height,
                "mip_count": mip_count,
                "array_size": array_size,
                "depth": depth,
                "fhm2d_format": format_code,
                "storage_format": format_info["name"],
                "dxgi_format": format_info["dxgi_format"],
            }
        )

    if not textures:
        raise NoSupportedTexturesError(
            "no supported 46XT texture records found"
        )
    return textures


def assign_groups(textures, labels):
    group_index = 0
    previous_embedded_index = None
    for texture in textures:
        embedded_index = texture["embedded_index"]
        if (
            previous_embedded_index is not None
            and embedded_index <= previous_embedded_index
        ):
            group_index += 1
        texture["group_index"] = group_index
        texture["group_label"] = (
            labels[group_index] if group_index < len(labels) else f"g{group_index:02d}"
        )
        previous_embedded_index = embedded_index

    observed_count = group_index + 1
    if labels and observed_count != len(labels):
        raise ValueError(
            f"texture groups ({observed_count}) do not match trailer labels "
            f"({len(labels)})"
        )


def extract_file(
    input_path, output_root, strict_payload=True, supported_formats=None
):
    blob, header, payload, trailing = decode_payload(
        input_path, strict=strict_payload
    )
    labels = parse_group_labels(trailing)
    textures = scan_textures(payload, supported_formats=supported_formats)
    assign_groups(textures, labels)

    output_dir = output_root / input_path.stem
    dds_dir = output_dir / "dds"
    metadata_dir = output_dir / "metadata"
    dds_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    for texture in textures:
        start = texture["payload_data_offset"]
        end = start + texture["allocation_size"]
        pixel_data = payload[start:end]
        format_info = TEXTURE_FORMATS[texture["fhm2d_format"]]
        file_name = (
            f"{texture['group_label']}_{texture['embedded_index']:05d}_"
            f"{texture['width']}x{texture['height']}_"
            f"{texture['storage_format']}.dds"
        )
        metadata_name = file_name[:-4] + ".bin"
        dds_data = format_info["make_dds"](
            texture["width"], texture["height"], pixel_data, texture["mip_count"]
        )
        (dds_dir / file_name).write_bytes(dds_data)
        metadata_offset = texture["payload_metadata_offset"]
        metadata = payload[
            metadata_offset : metadata_offset + TEXTURE_METADATA_SIZE
        ]
        (metadata_dir / metadata_name).write_bytes(metadata)
        texture["dds_sha256"] = hashlib.sha256(dds_data).hexdigest()
        texture["pixel_sha256"] = hashlib.sha256(
            pixel_data[: texture["data_size"]]
        ).hexdigest()
        texture["full_pixel_sha256"] = hashlib.sha256(pixel_data).hexdigest()
        texture["dds_output"] = str(Path("dds") / file_name)
        texture["metadata_output"] = str(Path("metadata") / metadata_name)

    csv_fields = [
        "texture_index",
        "group_index",
        "group_label",
        "embedded_name",
        "embedded_index",
        "width",
        "height",
        "mip_count",
        "array_size",
        "depth",
        "fhm2d_format",
        "storage_format",
        "dxgi_format",
        "payload_data_offset",
        "payload_metadata_offset",
        "data_size",
        "allocation_size",
        "metadata_size",
        "pixel_sha256",
        "full_pixel_sha256",
        "dds_sha256",
        "dds_output",
        "metadata_output",
    ]
    with (output_dir / "textures.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=csv_fields)
        writer.writeheader()
        writer.writerows(
            {key: texture[key] for key in csv_fields} for texture in textures
        )

    report = {
        "texture_scanner_version": TEXTURE_SCANNER_VERSION,
        "source": str(input_path),
        "source_size": len(blob),
        "source_sha256": hashlib.sha256(blob).hexdigest(),
        "header": header,
        "payload_size": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "texture_count": len(textures),
        "group_labels": labels,
        "formats": sorted(
            {
                (
                    texture["fhm2d_format"],
                    texture["dxgi_format"],
                    TEXTURE_FORMATS[texture["fhm2d_format"]]["description"],
                )
                for texture in textures
            }
        ),
        "textures_manifest": "textures.csv",
        "dds_directory": "dds",
        "metadata_directory": "metadata",
    }
    (output_dir / "extract_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report, output_dir


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Extract all supported 46XT textures from EXVSIB fhm2d files "
            "as reproducible DX10 DDS files."
        )
    )
    parser.add_argument("inputs", nargs="+", help="Input .fhm2d files")
    parser.add_argument(
        "-o",
        "--output",
        default="patch/fhm2d_textures",
        help="Output root. Default: patch/fhm2d_textures",
    )
    args = parser.parse_args(argv)

    output_root = Path(args.output)
    try:
        for item in args.inputs:
            input_path = Path(item)
            if not input_path.is_file():
                raise FileNotFoundError(f"missing input: {input_path}")
            report, output_dir = extract_file(input_path, output_root)
            print(
                f"{input_path.name}: textures={report['texture_count']} "
                f"groups={len(report['group_labels'])} -> {output_dir}"
            )
    except (FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
