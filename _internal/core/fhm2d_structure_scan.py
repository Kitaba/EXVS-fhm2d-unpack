#!/usr/bin/env python3
"""Read-only batch scanner for structural variants found in FHM2D files."""

import argparse
import csv
import hashlib
import json
import struct
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from fhm2d_extract_textures import (
    PAYLOAD_PADDING_TOLERANCE,
    TEXTURE_FORMATS,
    assign_groups,
    parse_group_labels,
    scan_textures,
)
from fhm2d_unpack import iter_deflate_blocks, read_header


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def printable_magic(data):
    return "".join(
        chr(value) if 32 <= value < 127 else "." for value in data[:4]
    )


def scan_one(path):
    path = Path(path)
    result = {
        "file": str(path),
        "name": path.name,
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "status": "ok",
        "errors": [],
        "warnings": [],
    }
    try:
        blob = path.read_bytes()
        header = read_header(blob)
        result["header"] = header
        result["header_file_size_match"] = (
            header["file_size_header"] == len(blob)
        )
        if not result["header_file_size_match"]:
            result["warnings"].append("header_file_size_mismatch")

        blocks = []
        decode_error = None
        for index, offset, compressed_size, data in iter_deflate_blocks(blob):
            if index is None:
                decode_error = "invalid_deflate_after_block"
                break
            blocks.append(
                {
                    "index": index,
                    "offset": offset,
                    "compressed_size": compressed_size,
                    "uncompressed_size": len(data),
                    "magic": data[:4].hex(),
                    "magic_text": printable_magic(data),
                    "data": data,
                }
            )
        if decode_error:
            result["warnings"].append(decode_error)
        if not blocks:
            raise ValueError("no deflate blocks")

        index_data = blocks[0]["data"]
        declared_payload_size = None
        if len(index_data) >= 0x3C:
            declared_payload_size = struct.unpack_from(
                "<Q", index_data, 0x34
            )[0]
        result["index"] = {
            "uncompressed_size": len(index_data),
            "compressed_size": blocks[0]["compressed_size"],
            "magic": blocks[0]["magic_text"],
            "declared_payload_size": declared_payload_size,
        }

        payload_blocks = []
        extra_blocks = []
        payload_size = 0
        if declared_payload_size is None:
            extra_blocks = blocks[1:]
        else:
            for block in blocks[1:]:
                if payload_size + block["uncompressed_size"] <= declared_payload_size:
                    payload_blocks.append(block)
                    payload_size += block["uncompressed_size"]
                    if payload_size == declared_payload_size:
                        continue
                else:
                    extra_blocks.append(block)
            if payload_size != declared_payload_size:
                padding_delta = declared_payload_size - payload_size
                if (
                    not extra_blocks
                    and 0 < padding_delta <= PAYLOAD_PADDING_TOLERANCE
                ):
                    result["warnings"].append(
                        f"tolerated_payload_padding_{padding_delta}"
                    )
                    result["structure_flags"] = [
                        "declared_payload_has_small_padding"
                    ]
                else:
                    result["errors"].append("payload_size_mismatch")

        payload = b"".join(block["data"] for block in payload_blocks)
        trailing = b"".join(block["data"] for block in extra_blocks)
        result["payload"] = {
            "declared_size": declared_payload_size,
            "decoded_size": payload_size,
            "block_count": len(payload_blocks),
            "compressed_size": sum(
                block["compressed_size"] for block in payload_blocks
            ),
        }
        result["trailing"] = {
            "block_count": len(extra_blocks),
            "decoded_size": len(trailing),
            "raw_size": len(blob)
            - (extra_blocks[0]["offset"] if extra_blocks else len(blob)),
            "blocks": [
                {
                    "index": block["index"],
                    "magic": block["magic_text"],
                    "compressed_size": block["compressed_size"],
                    "uncompressed_size": block["uncompressed_size"],
                }
                for block in extra_blocks
            ],
        }

        if extra_blocks:
            result.setdefault("structure_flags", [])
            result["structure_flags"].extend([
                "has_trailing_blocks",
                "trailing_magic_" + extra_blocks[0]["magic_text"].replace(".", "_")[:4],
            ])
        else:
            result.setdefault("structure_flags", [])
        if any(block["uncompressed_size"] != 65536 for block in payload_blocks[:-1]):
            result["structure_flags"].append("nonstandard_payload_block_size")

        texture_report = {}
        if payload:
            try:
                textures = scan_textures(
                    payload, supported_formats=set(TEXTURE_FORMATS)
                )
                labels = parse_group_labels(trailing)
                try:
                    assign_groups(textures, labels)
                    group_error = None
                except ValueError as exc:
                    group_error = str(exc)
                texture_report = {
                    "count": len(textures),
                    "formats": sorted(
                        Counter(item["storage_format"] for item in textures)
                    ),
                    "dimensions": sorted(
                        Counter(
                            f"{item['width']}x{item['height']}"
                            for item in textures
                        )
                    ),
                    "group_count": len(
                        {item.get("group_index") for item in textures}
                    )
                    if group_error is None
                    else None,
                    "trailer_labels": labels,
                    "group_error": group_error,
                }
                if group_error:
                    result["warnings"].append("texture_group_label_mismatch")
            except ValueError as exc:
                texture_report = {"error": str(exc)}
                result["warnings"].append("texture_scan_failed")
        result["textures"] = texture_report
        result["block_sizes"] = [
            {
                "index": block["index"],
                "compressed": block["compressed_size"],
                "uncompressed": block["uncompressed_size"],
                "magic": block["magic_text"],
            }
            for block in blocks
        ]
        if result["errors"]:
            result["status"] = "error"
        elif result["warnings"]:
            result["status"] = "warning"
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    for block in result.get("block_sizes", []):
        block.pop("data", None)
    return result


def flatten(result):
    index = result.get("index", {})
    payload = result.get("payload", {})
    trailing = result.get("trailing", {})
    textures = result.get("textures", {})
    return {
        "file": result.get("file", ""),
        "name": result.get("name", ""),
        "status": result.get("status", "error"),
        "size": result.get("size", 0),
        "sha256": result.get("sha256", ""),
        "index_size": index.get("uncompressed_size", ""),
        "declared_payload_size": payload.get("declared_size", ""),
        "decoded_payload_size": payload.get("decoded_size", ""),
        "payload_blocks": payload.get("block_count", ""),
        "trailing_blocks": trailing.get("block_count", ""),
        "trailing_magic": (
            trailing.get("blocks", [{}])[0].get("magic", "")
            if trailing.get("blocks")
            else ""
        ),
        "texture_count": textures.get("count", ""),
        "texture_formats": ";".join(textures.get("formats", [])),
        "dimensions": ";".join(textures.get("dimensions", [])),
        "flags": ";".join(result.get("structure_flags", [])),
        "errors": ";".join(result.get("errors", [])),
        "warnings": ";".join(result.get("warnings", [])),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Scan all FHM2D files and classify structural variants."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default="data/x64/dplcache_release",
        help="Directory containing FHM2D files",
    )
    parser.add_argument(
        "-o", "--output", default="patch/fhm2d_structure_scan"
    )
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    output = Path(args.output).resolve()
    files = [root] if root.is_file() else sorted(root.rglob("*.fhm2d"))
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(scan_one, path): path for path in files}
        for number, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            if number % 100 == 0 or number == len(files):
                print(f"scanned {number}/{len(files)}")
    results.sort(key=lambda item: item["file"])

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "file_count": len(results),
        "status_counts": dict(Counter(item["status"] for item in results)),
        "flag_counts": dict(
            Counter(
                flag
                for item in results
                for flag in item.get("structure_flags", [])
            )
        ),
        "trailing_magic_counts": dict(
            Counter(
                flatten(item)["trailing_magic"]
                for item in results
                if flatten(item)["trailing_magic"]
            )
        ),
    }
    (output / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "details.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    fields = list(flatten(results[0]).keys()) if results else []
    with (output / "summary.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(flatten(item) for item in results)
    print(f"done: {len(results)} files -> {output}")
    print(json.dumps(report["status_counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
