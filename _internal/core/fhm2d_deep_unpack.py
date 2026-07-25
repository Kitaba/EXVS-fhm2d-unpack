#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

from fhm2d_unpack import iter_deflate_blocks, read_header


CATALOG_RECORD_SIZE = 0x35
CATALOG_WIDTH = 0x100
CATALOG_HEIGHT = 0x100


def u24(data, offset):
    return int.from_bytes(data[offset : offset + 3], "little")


def parse_allocation_table(index_data, payload_size):
    if len(index_data) < 0x68:
        raise ValueError("block0 is too short for the allocation header")

    declared_item_count = int.from_bytes(index_data[0x00:0x04], "little")
    allocation_count = int.from_bytes(index_data[0x1C:0x20], "little")
    kind_counts = {
        0: int.from_bytes(index_data[0x40:0x44], "little"),
        1: int.from_bytes(index_data[0x60:0x64], "little"),
    }
    table_start = 0x68
    table_end = table_start + allocation_count * 12
    if table_end > len(index_data):
        raise ValueError("allocation table extends beyond block0")

    allocations = []
    observed_kind_counts = {0: 0, 1: 0}
    for table_index in range(allocation_count):
        offset = table_start + table_index * 12
        payload_offset = int.from_bytes(index_data[offset : offset + 8], "little")
        kind = int.from_bytes(index_data[offset + 8 : offset + 12], "little")
        if payload_offset >= payload_size:
            raise ValueError(
                f"allocation {table_index} points outside payload: 0x{payload_offset:x}"
            )
        if kind not in observed_kind_counts:
            raise ValueError(f"allocation {table_index} has unknown kind {kind}")
        observed_kind_counts[kind] += 1
        allocations.append(
            {
                "table_index": table_index,
                "index_offset": offset,
                "payload_offset": payload_offset,
                "kind": kind,
            }
        )

    if observed_kind_counts != kind_counts:
        raise ValueError(
            f"allocation kind counts mismatch: header={kind_counts}, "
            f"table={observed_kind_counts}"
        )

    return {
        "declared_item_count": declared_item_count,
        "allocation_count": allocation_count,
        "kind_counts": kind_counts,
        "table_start": table_start,
        "table_end": table_end,
        "entries": allocations,
    }


def find_catalog_record(index_data, block, data_base):
    relative_offset = block["file_offset"] - data_base
    signature = relative_offset.to_bytes(4, "little")
    position = 0
    matches = []

    while True:
        position = index_data.find(signature, position)
        if position < 0:
            break
        start = position - 9
        if (
            start >= 0
            and start + CATALOG_RECORD_SIZE <= len(index_data)
            and int.from_bytes(index_data[start : start + 4], "little") == CATALOG_WIDTH
            and int.from_bytes(index_data[start + 4 : start + 8], "little") == CATALOG_HEIGHT
            and u24(index_data, start + 0x16) == block["compressed_size"]
        ):
            matches.append(start)
        position += 1

    return matches


def consecutive_runs(values):
    if not values:
        return []
    runs = []
    start = previous = values[0]
    for value in values[1:]:
        if value != previous + 1:
            runs.append((start, previous))
            start = value
        previous = value
    runs.append((start, previous))
    return runs


def find_index_references(index_data, value):
    if value == 0:
        return []
    signature = value.to_bytes(8, "little")
    positions = []
    position = 0
    while True:
        position = index_data.find(signature, position)
        if position < 0:
            break
        positions.append(position)
        position += 1
    return positions


def parse_group_labels(trailing_data, expected_count):
    if not trailing_data:
        return []
    prefix = trailing_data.split(b"\0", 1)[0]
    if not re.fullmatch(rb"(?:[a-z][0-9])+", prefix):
        return []
    labels = [
        prefix[offset : offset + 2].decode("ascii")
        for offset in range(0, len(prefix), 2)
    ]
    return labels if len(labels) == expected_count else []


def decode_file(input_path, output_root):
    blob = input_path.read_bytes()
    header = read_header(blob)
    decoded = []
    trailing_offset = None

    for index, file_offset, compressed_size, data in iter_deflate_blocks(blob):
        if index is None:
            trailing_offset = file_offset
            break
        decoded.append(
            {
                "index": index,
                "file_offset": file_offset,
                "compressed_size": compressed_size,
                "uncompressed_size": len(data),
                "data": data,
            }
        )

    if len(decoded) < 2:
        raise ValueError("fhm2d contains no payload blocks")

    index_block = decoded[0]
    index_data = index_block["data"]
    payload_blocks = decoded[1:]
    data_base = payload_blocks[0]["file_offset"]

    payload_offset = 0
    for block in payload_blocks:
        block["payload_offset"] = payload_offset
        block["catalog_offsets"] = find_catalog_record(index_data, block, data_base)
        block["index_references"] = find_index_references(index_data, payload_offset)
        payload_offset += block["uncompressed_size"]

    payload = b"".join(block["data"] for block in payload_blocks)
    declared_payload_size = (
        int.from_bytes(index_data[0x34:0x3C], "little") if len(index_data) >= 0x3C else None
    )
    if declared_payload_size != len(payload):
        raise ValueError(
            f"payload size mismatch: block0={declared_payload_size}, decoded={len(payload)}"
        )
    allocation_table = parse_allocation_table(index_data, len(payload))

    explicit_indices = [
        block["index"] for block in payload_blocks if block["catalog_offsets"]
    ]
    explicit_runs = consecutive_runs(explicit_indices)
    groups = []
    first_block = 1
    by_index = {block["index"]: block for block in payload_blocks}
    for group_index, (catalog_first, catalog_last) in enumerate(explicit_runs):
        group_blocks = [
            by_index[index] for index in range(first_block, catalog_last + 1)
        ]
        group_payload_offset = group_blocks[0]["payload_offset"]
        group_data = b"".join(block["data"] for block in group_blocks)
        groups.append(
            {
                "group": group_index,
                "first_block": first_block,
                "last_block": catalog_last,
                "bulk_first_block": first_block,
                "bulk_last_block": catalog_first - 1,
                "catalog_first_block": catalog_first,
                "catalog_last_block": catalog_last,
                "block_count": len(group_blocks),
                "payload_offset": group_payload_offset,
                "uncompressed_size": len(group_data),
                "sha256": hashlib.sha256(group_data).hexdigest(),
                "data": group_data,
            }
        )
        first_block = catalog_last + 1

    if not groups or first_block <= payload_blocks[-1]["index"]:
        raise ValueError("catalog records do not cover all payload blocks")
    trailing_data = blob[trailing_offset:] if trailing_offset is not None else b""
    group_labels = parse_group_labels(trailing_data, len(groups))
    for group_index, group in enumerate(groups):
        group["label"] = group_labels[group_index] if group_labels else ""

    output_dir = output_root / input_path.stem
    groups_dir = output_dir / "groups"
    allocations_dir = output_dir / "allocation_slices"
    groups_dir.mkdir(parents=True, exist_ok=True)
    allocations_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "payload.bin").write_bytes(payload)
    (output_dir / "block0_index.bin").write_bytes(index_data)
    if trailing_offset is not None:
        (output_dir / "trailing.bin").write_bytes(trailing_data)

    for group in groups:
        name = (
            f"group_{group['group']:02d}_blocks_"
            f"{group['first_block']:04d}-{group['last_block']:04d}_"
            f"off_{group['payload_offset']:08X}_size_{group['uncompressed_size']:08X}.bin"
        )
        (groups_dir / name).write_bytes(group.pop("data"))
        group["output"] = str(Path("groups") / name)

    allocations_by_offset = {}
    for allocation in allocation_table["entries"]:
        item = allocations_by_offset.setdefault(
            allocation["payload_offset"],
            {"table_indices": [], "kinds": []},
        )
        item["table_indices"].append(allocation["table_index"])
        item["kinds"].append(allocation["kind"])

    allocation_rows = []
    sorted_offsets = sorted(allocations_by_offset)
    for slice_index, payload_start in enumerate(sorted_offsets):
        payload_end = (
            sorted_offsets[slice_index + 1]
            if slice_index + 1 < len(sorted_offsets)
            else len(payload)
        )
        data = payload[payload_start:payload_end]
        metadata = allocations_by_offset[payload_start]
        kinds = sorted(set(metadata["kinds"]))
        name = (
            f"allocation_{slice_index:03d}_off_{payload_start:08X}_"
            f"size_{len(data):08X}_kind_{'-'.join(str(kind) for kind in kinds)}.bin"
        )
        (allocations_dir / name).write_bytes(data)
        allocation_rows.append(
            {
                "slice_index": slice_index,
                "payload_offset": payload_start,
                "payload_end": payload_end,
                "size": len(data),
                "kinds": " ".join(str(kind) for kind in kinds),
                "table_indices": " ".join(
                    str(index) for index in metadata["table_indices"]
                ),
                "head_16": data[:16].hex(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "output": str(Path("allocation_slices") / name),
            }
        )

    with (output_dir / "allocations.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=allocation_rows[0].keys())
        writer.writeheader()
        writer.writerows(allocation_rows)

    block_rows = []
    for block in payload_blocks:
        block_rows.append(
            {
                "block_index": block["index"],
                "file_offset": block["file_offset"],
                "relative_compressed_offset": block["file_offset"] - data_base,
                "compressed_size": block["compressed_size"],
                "payload_offset": block["payload_offset"],
                "uncompressed_size": block["uncompressed_size"],
                "catalog_record_offsets": " ".join(
                    f"0x{offset:X}" for offset in block["catalog_offsets"]
                ),
                "index_reference_offsets": " ".join(
                    f"0x{offset:X}" for offset in block["index_references"]
                ),
            }
        )

    with (output_dir / "block_map.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=block_rows[0].keys())
        writer.writeheader()
        writer.writerows(block_rows)

    group_rows = [
        {
            key: value
            for key, value in group.items()
            if key not in {"sha256"}
        }
        for group in groups
    ]
    with (output_dir / "stream_groups.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=group_rows[0].keys())
        writer.writeheader()
        writer.writerows(group_rows)

    report = {
        "source": str(input_path),
        "source_size": len(blob),
        "header": header,
        "index": {
            "uncompressed_size": len(index_data),
            "declared_item_count": int.from_bytes(index_data[0:4], "little"),
            "declared_payload_size": declared_payload_size,
            "catalog_record_size": CATALOG_RECORD_SIZE,
            "catalog_record_count": len(explicit_indices),
            "catalog_block_runs": explicit_runs,
        },
        "allocation_table": {
            key: value
            for key, value in allocation_table.items()
            if key != "entries"
        },
        "allocation_slices": {
            "count": len(allocation_rows),
            "output": "allocations.csv",
        },
        "payload": {
            "size": len(payload),
            "block_count": len(payload_blocks),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "output": "payload.bin",
        },
        "trailing": (
            {
                "file_offset": trailing_offset,
                "size": len(blob) - trailing_offset,
                "sha256": hashlib.sha256(trailing_data).hexdigest(),
                "group_labels": group_labels,
                "output": "trailing.bin",
            }
            if trailing_offset is not None
            else None
        ),
        "groups": groups,
    }
    (output_dir / "deep_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report, output_dir


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild EXVSIB fhm2d runtime payloads and catalog-derived stream groups."
        )
    )
    parser.add_argument("inputs", nargs="+", help="Input .fhm2d files")
    parser.add_argument(
        "-o",
        "--output",
        default="patch/fhm2d_deep_unpacked",
        help="Output root. Default: patch/fhm2d_deep_unpacked",
    )
    args = parser.parse_args(argv)

    output_root = Path(args.output)
    for item in args.inputs:
        input_path = Path(item)
        if not input_path.is_file():
            print(f"missing input: {input_path}", file=sys.stderr)
            return 2
        report, output_dir = decode_file(input_path, output_root)
        print(
            f"{input_path.name}: payload={report['payload']['size']} "
            f"blocks={report['payload']['block_count']} "
            f"groups={len(report['groups'])} -> {output_dir}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
