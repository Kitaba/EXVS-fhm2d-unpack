#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

from fhm2d_unpack import iter_deflate_blocks, read_header


TEXTURE_NAME_PREFIX = b"46XTimg-"
TEXTURE_METADATA_SIZE = 0xB0
TEXTURE_NAME_OFFSET = 0x40
TEXTURE_WIDTH_OFFSET = 0x84
TEXTURE_HEIGHT_OFFSET = 0x88
TEXTURE_MIP_COUNT_OFFSET = 0x8C
TEXTURE_FORMAT_OFFSET = 0x90
TEXTURE_BLOCK_DIMENSION_OFFSET = 0x94
TEXTURE_ARRAY_SIZE_OFFSET = 0x98
TEXTURE_DEPTH_OFFSET = 0xA0
TEXTURE_DATA_SIZE_COPY_OFFSET = 0xA4
TEXTURE_MARKER_OFFSET = 0xA8

FHM2D_BC7_FORMAT = 0x4E0
FHM2D_RGBA8_FORMAT = 0x400
TEXTURE_MARKER = 0x54455820
DXGI_FORMAT_R8G8B8A8_UNORM = 28
DXGI_FORMAT_BC7_UNORM = 98
BC7_BLOCK_SIZE = 16
BC7_BLOCK_DIMENSION = 4


def u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def bc7_size(width, height):
    return (
        ((width + BC7_BLOCK_DIMENSION - 1) // BC7_BLOCK_DIMENSION)
        * ((height + BC7_BLOCK_DIMENSION - 1) // BC7_BLOCK_DIMENSION)
        * BC7_BLOCK_SIZE
    )


def rgba8_size(width, height):
    return width * height * 4


def make_bc7_dds(width, height, pixel_data):
    expected_size = bc7_size(width, height)
    if len(pixel_data) != expected_size:
        raise ValueError(
            f"BC7 data size mismatch: got {len(pixel_data)}, expected {expected_size}"
        )

    # Matches the DX10 BC7_UNORM headers emitted by RenderDoc for these resources.
    header = bytearray(148)
    header[0:4] = b"DDS "
    struct.pack_into("<I", header, 0x04, 124)
    struct.pack_into("<I", header, 0x08, 0x00081007)
    struct.pack_into("<I", header, 0x0C, height)
    struct.pack_into("<I", header, 0x10, width)
    struct.pack_into("<I", header, 0x14, expected_size)
    struct.pack_into("<I", header, 0x18, 1)
    struct.pack_into("<I", header, 0x1C, 1)
    struct.pack_into("<I", header, 0x4C, 32)
    struct.pack_into("<I", header, 0x50, 0x4)
    header[0x54:0x58] = b"DX10"
    struct.pack_into("<I", header, 0x6C, 0x1000)
    struct.pack_into("<I", header, 0x80, DXGI_FORMAT_BC7_UNORM)
    struct.pack_into("<I", header, 0x84, 3)
    struct.pack_into("<I", header, 0x8C, 1)
    return bytes(header) + pixel_data


def make_rgba8_dds(width, height, pixel_data):
    expected_size = rgba8_size(width, height)
    if len(pixel_data) != expected_size:
        raise ValueError(
            f"RGBA8 data size mismatch: got {len(pixel_data)}, "
            f"expected {expected_size}"
        )

    header = bytearray(148)
    header[0:4] = b"DDS "
    struct.pack_into("<I", header, 0x04, 124)
    struct.pack_into("<I", header, 0x08, 0x0000100F)
    struct.pack_into("<I", header, 0x0C, height)
    struct.pack_into("<I", header, 0x10, width)
    struct.pack_into("<I", header, 0x14, width * 4)
    struct.pack_into("<I", header, 0x18, 1)
    struct.pack_into("<I", header, 0x1C, 1)
    struct.pack_into("<I", header, 0x4C, 32)
    struct.pack_into("<I", header, 0x50, 0x4)
    header[0x54:0x58] = b"DX10"
    struct.pack_into("<I", header, 0x6C, 0x1000)
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
        if payload_size + len(data) > declared_size:
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
        supported_formats = {FHM2D_BC7_FORMAT}
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

        name_bytes = payload[name_offset : name_offset + 0x20].split(b"\0", 1)[0]
        try:
            embedded_name = name_bytes.decode("ascii")
        except UnicodeDecodeError:
            continue
        name_match = re.fullmatch(r"46XTimg-(\d+)", embedded_name)
        if not name_match:
            continue

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
        data_size_copy = u32(
            payload, metadata_offset + TEXTURE_DATA_SIZE_COPY_OFFSET
        )
        marker = u32(payload, metadata_offset + TEXTURE_MARKER_OFFSET)
        data_offset = metadata_offset - data_size

        problems = []
        if data_offset < 0:
            problems.append("negative data offset")
        if data_size != data_size_copy:
            problems.append("data size fields differ")
        if width == 0 or height == 0:
            problems.append("zero dimensions")
        if mip_count != 1:
            problems.append(f"unsupported mip count {mip_count}")
        if array_size != 1 or depth != 1:
            problems.append(f"unsupported array/depth {array_size}/{depth}")
        format_info = TEXTURE_FORMATS.get(format_code)
        if format_code not in supported_formats or format_info is None:
            problems.append(f"unsupported format 0x{format_code:X}")
        if block_dimension != BC7_BLOCK_DIMENSION:
            problems.append(f"unexpected block dimension {block_dimension}")
        if marker != TEXTURE_MARKER:
            problems.append(f"unexpected marker 0x{marker:X}")
        expected_size = (
            format_info["size"](width, height) if format_info is not None else None
        )
        if expected_size is not None and data_size != expected_size:
            problems.append(
                f"size {data_size} != {format_info['name'].upper()} "
                f"size {expected_size}"
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
                "embedded_index": int(name_match.group(1)),
                "payload_data_offset": data_offset,
                "payload_metadata_offset": metadata_offset,
                "data_size": data_size,
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
        raise ValueError("no supported 46XTimg textures found")
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
        end = start + texture["data_size"]
        pixel_data = payload[start:end]
        format_info = TEXTURE_FORMATS[texture["fhm2d_format"]]
        file_name = (
            f"{texture['group_label']}_{texture['embedded_index']:05d}_"
            f"{texture['width']}x{texture['height']}_"
            f"{texture['storage_format']}.dds"
        )
        metadata_name = file_name[:-4] + ".bin"
        dds_data = format_info["make_dds"](
            texture["width"], texture["height"], pixel_data
        )
        (dds_dir / file_name).write_bytes(dds_data)
        metadata_offset = texture["payload_metadata_offset"]
        metadata = payload[
            metadata_offset : metadata_offset + TEXTURE_METADATA_SIZE
        ]
        (metadata_dir / metadata_name).write_bytes(metadata)
        texture["dds_sha256"] = hashlib.sha256(dds_data).hexdigest()
        texture["pixel_sha256"] = hashlib.sha256(pixel_data).hexdigest()
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
        "metadata_size",
        "pixel_sha256",
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
            "Extract all supported 46XTimg textures from EXVSIB fhm2d files "
            "as reproducible DX10 BC7 DDS files."
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
