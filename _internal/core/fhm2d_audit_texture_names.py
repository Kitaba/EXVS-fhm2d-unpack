#!/usr/bin/env python3
import argparse
import collections
import json
import multiprocessing as mp
import os
import re
import struct
from pathlib import Path

from fhm2d_extract_textures import (
    TEXTURE_DATA_SIZE_COPY_OFFSET,
    TEXTURE_FORMATS,
    TEXTURE_FORMAT_OFFSET,
    TEXTURE_HEIGHT_OFFSET,
    TEXTURE_MARKER,
    TEXTURE_MARKER_OFFSET,
    TEXTURE_METADATA_SIZE,
    TEXTURE_NAME_OFFSET,
    TEXTURE_NAME_PATTERN,
    TEXTURE_WIDTH_OFFSET,
    decode_payload,
)


PRINTABLE_NAME = re.compile(rb"46XT[\x21-\x7e]{0,63}")


def u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def inspect_candidate(payload, name_offset, name):
    metadata_offset = name_offset - TEXTURE_NAME_OFFSET
    result = {
        "name": name,
        "recognized": bool(TEXTURE_NAME_PATTERN.fullmatch(name)),
        "metadata_valid": False,
    }
    if metadata_offset < 0 or metadata_offset + TEXTURE_METADATA_SIZE > len(payload):
        return result

    data_size = u32(payload, metadata_offset)
    width = u32(payload, metadata_offset + TEXTURE_WIDTH_OFFSET)
    height = u32(payload, metadata_offset + TEXTURE_HEIGHT_OFFSET)
    format_code = u32(payload, metadata_offset + TEXTURE_FORMAT_OFFSET)
    data_size_copy = u32(
        payload, metadata_offset + TEXTURE_DATA_SIZE_COPY_OFFSET
    )
    marker = u32(payload, metadata_offset + TEXTURE_MARKER_OFFSET)
    format_info = TEXTURE_FORMATS.get(format_code)
    expected_size = (
        format_info["size"](width, height)
        if format_info and width and height
        else None
    )
    result.update(
        {
            "data_size": data_size,
            "width": width,
            "height": height,
            "format": f"0x{format_code:03X}",
            "metadata_valid": (
                marker == TEXTURE_MARKER
                and data_size == data_size_copy
                and expected_size == data_size
                and data_size <= metadata_offset
            ),
        }
    )
    return result


def family(name):
    if name.startswith("46XTimg-"):
        return "46XTimg-"
    match = re.match(r"(46XT[A-Za-z]+(?:[_-])?)", name)
    return match.group(1) if match else name


def inspect_file(path):
    families = collections.Counter()
    valid_families = collections.Counter()
    unrecognized_valid = collections.defaultdict(list)
    invalid_examples = collections.defaultdict(list)
    error = None
    try:
        _, _, payload, _ = decode_payload(path, strict=False)
        seen_offsets = set()
        for match in PRINTABLE_NAME.finditer(payload):
            if match.start() in seen_offsets:
                continue
            seen_offsets.add(match.start())
            raw_name = match.group(0).split(b"\0", 1)[0]
            try:
                name = raw_name.decode("ascii")
            except UnicodeDecodeError:
                continue
            item = inspect_candidate(payload, match.start(), name)
            name_family = family(name)
            families[name_family] += 1
            if item["metadata_valid"]:
                valid_families[name_family] += 1
                if not item["recognized"]:
                    unrecognized_valid[name_family].append(
                        {"package": path.name, **item}
                    )
            else:
                invalid_examples[name_family].append(
                    {"package": path.name, **item}
                )
    except Exception as exc:
        error = {
            "package": path.name,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "families": dict(families),
        "valid_families": dict(valid_families),
        "unrecognized_valid": dict(unrecognized_valid),
        "invalid_examples": dict(invalid_examples),
        "error": error,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Audit 46XT texture record naming across FHM2D packages."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--pattern", default="*.fhm2d")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--checkpoint", type=int, default=100)
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=max(1, (os.cpu_count() or 4) // 2),
    )
    args = parser.parse_args()

    files = sorted(args.source.glob(args.pattern))
    families = collections.Counter()
    valid_families = collections.Counter()
    unrecognized_valid = collections.defaultdict(list)
    invalid_examples = collections.defaultdict(list)
    errors = []

    context = mp.get_context("spawn")
    with context.Pool(args.jobs) as pool:
        for file_index, result in enumerate(
            pool.imap_unordered(inspect_file, files, chunksize=1), 1
        ):
            families.update(result["families"])
            valid_families.update(result["valid_families"])
            for name_family, rows in result["unrecognized_valid"].items():
                remaining = 20 - len(unrecognized_valid[name_family])
                if remaining > 0:
                    unrecognized_valid[name_family].extend(rows[:remaining])
            for name_family, rows in result["invalid_examples"].items():
                remaining = 3 - len(invalid_examples[name_family])
                if remaining > 0:
                    invalid_examples[name_family].extend(rows[:remaining])
            if result["error"] and len(errors) < 100:
                errors.append(result["error"])
            if file_index % args.checkpoint == 0 or file_index == len(files):
                print(f"progress={file_index}/{len(files)}", flush=True)

    report = {
        "files": len(files),
        "families": dict(families.most_common()),
        "metadata_valid_families": dict(valid_families.most_common()),
        "metadata_valid_but_unrecognized": dict(unrecognized_valid),
        "invalid_examples": dict(invalid_examples),
        "decode_errors": errors,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"report={args.output}")
    else:
        print(rendered)


if __name__ == "__main__":
    mp.freeze_support()
    main()
