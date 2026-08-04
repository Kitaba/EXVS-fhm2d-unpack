#!/usr/bin/env python3
"""Print structural index-buffer candidates from an HBSS/HSEM mesh."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path


def probe(path: Path) -> dict:
    data = path.read_bytes()
    u32 = lambda offset: struct.unpack_from("<I", data, offset)[0]
    u64 = lambda offset: struct.unpack_from("<Q", data, offset)[0]
    section_count = u32(0x90)
    sections = []
    for index in range(section_count):
        base = 0x110 + index * 0xD0
        sections.append({
            "vertex_count": u32(base),
            "index_count": u32(base + 4),
            "stream_count": u32(base + 8),
            "stream_base_offsets": [u32(base + 0x0C + item * 4) for item in range(u32(base + 8))],
            "stream_strides": [u32(base + 0x1C + item * 4) for item in range(u32(base + 8))],
            "index_base_offset": u32(base + 0x2C),
        })
    span = max(item["index_base_offset"] + item["index_count"] * 2 for item in sections)
    candidates = {
        "c0": u64(0xC0),
        "c0_plus_section_table": u64(0xC0) + section_count * 0x40,
        "d0_minus_0x30": u64(0xD0) - 0x30,
        "d0_minus_span": u64(0xD0) - span,
        "tail_minus_span": len(data) - span,
    }
    checked = []
    for label, offset in candidates.items():
        item = {"label": label, "offset": offset, "in_bounds": 0 <= offset <= len(data) - span}
        if item["in_bounds"]:
            item["sections"] = []
            for section in sections:
                values = struct.unpack_from(
                    "<{}H".format(section["index_count"]),
                    data,
                    offset + section["index_base_offset"],
                )
                item["sections"].append({
                    "minimum": min(values),
                    "maximum": max(values),
                    "vertex_count": section["vertex_count"],
                    "valid": max(values) < section["vertex_count"],
                })
        checked.append(item)
    nearby_section_offsets = []
    index_origin = u64(0xC0)
    for section in sections:
        matches = []
        declared = section["index_base_offset"]
        for delta in range(-0x100, 0x102, 2):
            offset = index_origin + declared + delta
            size = section["index_count"] * 2
            if offset < 0 or offset + size > len(data):
                continue
            values = struct.unpack_from("<{}H".format(section["index_count"]), data, offset)
            if max(values) < section["vertex_count"]:
                matches.append({"delta": delta, "minimum": min(values), "maximum": max(values)})
        nearby_section_offsets.append(matches[:16])
    return {
        "path": str(path.resolve()),
        "size": len(data),
        "section_count": section_count,
        "index_span": span,
        "c0": u64(0xC0),
        "d0": u64(0xD0),
        "sections": sections,
        "candidates": checked,
        "nearby_section_offsets_from_c0": nearby_section_offsets,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mesh", type=Path)
    args = parser.parse_args()
    print(json.dumps(probe(args.mesh), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
