#!/usr/bin/env python3
"""Inventory model/material/texture relationship evidence inside one FHM2D."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

try:
    from .fhm2d_unpack import iter_deflate_blocks
except ImportError:
    from fhm2d_unpack import iter_deflate_blocks


KEYWORDS = (
    "mtl", "numat", "basecolor", "normal", "metallic", "roughness",
    "ambientocclusion", "emissive", "texture", "cubemap",
)


def probe(package: Path) -> dict:
    types = Counter()
    evidence = []
    hbss_resources = []
    blocks = []
    for block_index, _, _, data in iter_deflate_blocks(package.read_bytes()):
        if block_index is None:
            break
        outer = data[:4].decode("ascii", "replace") if len(data) >= 4 else ""
        inner = data[16:20].decode("ascii", "replace") if len(data) >= 20 and data[:4] == b"HBSS" else ""
        types[(outer, inner)] += 1
        strings = [value.decode("ascii") for value in re.findall(rb"[ -~]{4,}", data)]
        hits = [value for value in strings if any(key in value.lower() for key in KEYWORDS)]
        record = {
            "block_index": block_index,
            "size": len(data),
            "outer_magic": outer,
            "inner_magic": inner,
            "strings": strings[:64],
            "relationship_hits": hits,
        }
        blocks.append(record)
        if outer == "HBSS":
            hbss_resources.append(record)
        if hits:
            evidence.append(record)
    return {
        "schema": "exvs-fhm2d-relationship-probe/v1",
        "source": str(package),
        "block_count": len(blocks),
        "type_counts": [
            {"outer_magic": outer, "inner_magic": inner, "count": count}
            for (outer, inner), count in types.most_common()
        ],
        "evidence": evidence,
        "hbss_resources": hbss_resources,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = probe(args.package.resolve())
    output = args.output or args.package.with_suffix(".relationships.json")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("blocks={} types={} evidence={} output={}".format(
        report["block_count"], len(report["type_counts"]), len(report["evidence"]), output
    ))
    for item in report["type_counts"]:
        print("  {!r}/{!r}: {}".format(item["outer_magic"], item["inner_magic"], item["count"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
