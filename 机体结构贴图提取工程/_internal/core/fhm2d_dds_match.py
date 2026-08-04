#!/usr/bin/env python3
import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


DDS_MAGIC = b"DDS "
DX10_FOURCC = b"DX10"
BC7_FORMATS = {97, 98, 99}
BC3_FORMATS = {76, 77, 78}
BC4_FORMATS = {79, 80, 81}
RGBA8_FORMATS = {27, 28, 29}
BC7_BLOCK_SIZE = 16
ZERO_BLOCK = b"\0" * BC7_BLOCK_SIZE


def parse_dds(path, allowed_formats=BC7_FORMATS):
    data = path.read_bytes()
    if len(data) < 128 or data[:4] != DDS_MAGIC:
        raise ValueError(f"{path} is not a DDS file")
    if int.from_bytes(data[4:8], "little") != 124:
        raise ValueError(f"{path} has an unsupported DDS header")

    height = int.from_bytes(data[12:16], "little")
    width = int.from_bytes(data[16:20], "little")
    mip_count = max(1, int.from_bytes(data[28:32], "little"))
    fourcc = data[84:88]
    if fourcc != DX10_FOURCC:
        raise ValueError(f"{path} is not a DX10 DDS")
    if len(data) < 148:
        raise ValueError(f"{path} has a truncated DX10 header")

    dxgi_format = int.from_bytes(data[128:132], "little")
    array_size = int.from_bytes(data[140:144], "little")
    if allowed_formats is not None and dxgi_format not in allowed_formats:
        expected = "/".join(str(item) for item in sorted(allowed_formats))
        raise ValueError(
            f"{path} uses DXGI format {dxgi_format}, expected {expected}"
        )

    if dxgi_format in BC7_FORMATS or dxgi_format in BC3_FORMATS:
        top_mip_size = (
            ((width + 3) // 4)
            * ((height + 3) // 4)
            * BC7_BLOCK_SIZE
        )
    elif dxgi_format in BC4_FORMATS:
        top_mip_size = (
            ((width + 3) // 4)
            * ((height + 3) // 4)
            * 8
        )
    elif dxgi_format in RGBA8_FORMATS:
        top_mip_size = width * height * 4
    else:
        raise ValueError(
            f"{path} uses unsupported DXGI format {dxgi_format}"
        )
    pixel_offset = 148
    if pixel_offset + top_mip_size > len(data):
        raise ValueError(f"{path} does not contain a complete top mip")
    return {
        "path": str(path),
        "width": width,
        "height": height,
        "mip_count": mip_count,
        "array_size": array_size,
        "dxgi_format": dxgi_format,
        "top_mip": data[pixel_offset : pixel_offset + top_mip_size],
        "pixel_data": data[pixel_offset:],
    }


def find_deep_dir(item, deep_root):
    path = Path(item)
    if path.is_dir():
        return path
    return Path(deep_root) / path.stem


def blocks(data):
    usable = len(data) - (len(data) % BC7_BLOCK_SIZE)
    return [
        data[offset : offset + BC7_BLOCK_SIZE]
        for offset in range(0, usable, BC7_BLOCK_SIZE)
    ]


def nonzero_counter(items):
    result = Counter(items)
    result.pop(ZERO_BLOCK, None)
    return result


def match_candidate(dds_blocks, candidate_data):
    source_blocks = blocks(candidate_data)
    dds_counts = nonzero_counter(dds_blocks)
    source_counts = nonzero_counter(source_blocks)
    matched = sum(
        min(count, source_counts.get(block, 0))
        for block, count in dds_counts.items()
    )
    dds_total = sum(dds_counts.values())
    source_total = sum(source_counts.values())
    return {
        "matched_nonzero_blocks": matched,
        "dds_nonzero_blocks": dds_total,
        "source_nonzero_blocks": source_total,
        "dds_match_ratio": matched / dds_total if dds_total else 0.0,
        "source_match_ratio": matched / source_total if source_total else 0.0,
        "source_blocks": source_blocks,
        "dds_counts": dds_counts,
        "source_counts": source_counts,
    }


def unique_mapping(source_blocks, dds_blocks):
    source_positions = defaultdict(list)
    dds_positions = defaultdict(list)
    for index, block in enumerate(source_blocks):
        if block != ZERO_BLOCK:
            source_positions[block].append(index)
    for index, block in enumerate(dds_blocks):
        if block != ZERO_BLOCK:
            dds_positions[block].append(index)

    mapping = []
    for block, source_indices in source_positions.items():
        dds_indices = dds_positions.get(block, [])
        if len(source_indices) == 1 and len(dds_indices) == 1:
            mapping.append(
                {
                    "source_block_index": source_indices[0],
                    "dds_block_index": dds_indices[0],
                    "block_hex": block.hex(),
                }
            )
    mapping.sort(key=lambda row: row["source_block_index"])
    return mapping


def load_candidates(deep_dir, source_bytes, minimum_size):
    manifest_path = deep_dir / "allocations.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"missing {manifest_path}; run fhm2d_deep_unpack.py first"
        )
    rows = list(csv.DictReader(manifest_path.open(encoding="utf-8-sig")))
    candidates = []
    for row in rows:
        size = int(row["size"])
        if size < minimum_size:
            continue
        data = (deep_dir / row["output"]).read_bytes()
        if source_bytes:
            data = data[:source_bytes]
        candidates.append((row, data))
    return candidates


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Match a runtime-exported BC7 DDS against fhm2d allocation slices "
            "using exact 16-byte BC7 block identities."
        )
    )
    parser.add_argument("dds", help="BC7 DDS exported from a GPU capture")
    parser.add_argument("inputs", nargs="+", help=".fhm2d files or deep-unpack dirs")
    parser.add_argument("--deep-root", default="patch/fhm2d_deep_unpacked")
    parser.add_argument("-o", "--output", default="patch/fhm2d_dds_matches")
    parser.add_argument(
        "--source-bytes",
        type=lambda value: int(value, 0),
        default=0xC0000,
        help="Bytes tested from each allocation. Default: 0xC0000",
    )
    parser.add_argument(
        "--minimum-size",
        type=lambda value: int(value, 0),
        default=0xC0000,
    )
    args = parser.parse_args(argv)

    try:
        dds = parse_dds(Path(args.dds))
        dds_blocks = blocks(dds["top_mip"])
        matches = []
        candidate_data = {}
        for item in args.inputs:
            deep_dir = find_deep_dir(item, args.deep_root)
            for row, data in load_candidates(
                deep_dir, args.source_bytes, args.minimum_size
            ):
                result = match_candidate(dds_blocks, data)
                key = f"{deep_dir.name}:{row['slice_index']}"
                candidate_data[key] = (data, result)
                matches.append(
                    {
                        "candidate": key,
                        "package": deep_dir.name,
                        "slice_index": int(row["slice_index"]),
                        "payload_offset": int(row["payload_offset"]),
                        "allocation_size": int(row["size"]),
                        "tested_size": len(data),
                        "matched_nonzero_blocks": result["matched_nonzero_blocks"],
                        "dds_nonzero_blocks": result["dds_nonzero_blocks"],
                        "source_nonzero_blocks": result["source_nonzero_blocks"],
                        "dds_match_ratio": result["dds_match_ratio"],
                        "source_match_ratio": result["source_match_ratio"],
                        "allocation_output": row["output"],
                    }
                )
    except (FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2

    if not matches:
        print(
            "no allocation slices satisfy --minimum-size/--source-bytes",
            file=sys.stderr,
        )
        return 2

    matches.sort(
        key=lambda row: (row["matched_nonzero_blocks"], row["dds_match_ratio"]),
        reverse=True,
    )
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(args.dds).stem

    with (output_dir / f"{stem}_matches.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=matches[0].keys())
        writer.writeheader()
        writer.writerows(matches)

    best = matches[0]
    best_data, best_result = candidate_data[best["candidate"]]
    mapping = unique_mapping(blocks(best_data), dds_blocks)
    mapping_path = output_dir / f"{stem}_best_unique_block_mapping.csv"
    with mapping_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["source_block_index", "dds_block_index", "block_hex"],
        )
        writer.writeheader()
        writer.writerows(mapping)

    report = {
        "dds": {key: value for key, value in dds.items() if key != "top_mip"},
        "best_match": best,
        "unique_block_mapping_count": len(mapping),
        "matches_output": f"{stem}_matches.csv",
        "mapping_output": mapping_path.name,
    }
    (output_dir / f"{stem}_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"best={best['candidate']} matched={best['matched_nonzero_blocks']}/"
        f"{best['dds_nonzero_blocks']} ({best['dds_match_ratio']:.2%}) "
        f"unique_mapping={len(mapping)} -> {output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
