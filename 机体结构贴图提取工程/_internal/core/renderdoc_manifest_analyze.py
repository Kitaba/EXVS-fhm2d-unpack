#!/usr/bin/env python3
"""Summarize EXVS RenderDoc batch manifests and material resource groups."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


PBR_NAMES = (
    "BaseColorMap",
    "NormalMap",
    "MetallicMap",
    "RoughnessMap",
    "AmbientOcclusionMap",
    "EmissiveMap",
)


def resource_signature(draw):
    resources = {}
    for item in draw.get("ps_resources", []):
        name = item.get("name")
        if name in PBR_NAMES:
            resources[name] = ",".join(item.get("resources", []))
    return "|".join("{}={}".format(name, resources.get(name, "")) for name in sorted(PBR_NAMES))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--anchor-event", type=int, default=5694)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    rows = []
    groups = defaultdict(list)
    for draw in manifest.get("draws", []):
        if not draw.get("matches_exvs_pbr6"):
            continue
        signature = resource_signature(draw)
        row = {
            "event_id": int(draw["event_id"]),
            "num_indices": int(draw.get("num_indices", 0)),
            "num_instances": int(draw.get("num_instances", 0)),
            "index_offset": int(draw.get("index_offset", 0)),
            "base_vertex": int(draw.get("base_vertex", 0)),
            "marker_path": " > ".join(draw.get("marker_path", [])),
            "material_signature": signature,
        }
        rows.append(row)
        groups[signature].append(row)

    rows.sort(key=lambda row: row["event_id"])
    anchor = next((row for row in rows if row["event_id"] == args.anchor_event), None)
    group_rows = []
    for group_id, (signature, members) in enumerate(
        sorted(groups.items(), key=lambda item: min(row["event_id"] for row in item[1])), 1
    ):
        group_rows.append(
            {
                "group_id": group_id,
                "draw_count": len(members),
                "total_indices": sum(row["num_indices"] for row in members),
                "first_event": min(row["event_id"] for row in members),
                "last_event": max(row["event_id"] for row in members),
                "includes_anchor": bool(anchor and signature == anchor["material_signature"]),
                "material_signature": signature,
            }
        )

    with (output / "pbr_draws.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ["event_id"])
        writer.writeheader()
        writer.writerows(rows)
    with (output / "material_groups.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        fields = list(group_rows[0]) if group_rows else ["group_id"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(group_rows)

    report = {
        "schema": "exvs-renderdoc-manifest-analysis/v1",
        "manifest": str(manifest_path),
        "scan_only": manifest.get("scan_only"),
        "min_index_count": manifest.get("min_index_count"),
        "candidate_count": manifest.get("candidate_count"),
        "matching_count": len(rows),
        "error_count": sum(bool(row.get("error")) for row in manifest.get("draws", [])),
        "material_group_count": len(group_rows),
        "anchor_event": args.anchor_event,
        "anchor_material_events": (
            [row["event_id"] for row in groups[anchor["material_signature"]]] if anchor else []
        ),
    }
    (output / "manifest_analysis.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
