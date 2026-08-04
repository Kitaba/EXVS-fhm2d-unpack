#!/usr/bin/env python3
"""Cluster EXVS PBR draw calls into model instances.

The classifier combines world-transform proximity with runtime texture
ResourceId proximity.  This separates neighbouring characters whose draw calls
are interleaved while retaining detached parts of one model.
"""

import argparse
import json
import math
import re
import struct
from collections import defaultdict
from pathlib import Path


PBR_NAMES = {
    "BaseColorMap",
    "NormalMap",
    "MetallicMap",
    "RoughnessMap",
    "AmbientOcclusionMap",
    "EmissiveMap",
}


def resource_number(value):
    match = re.search(r"(\d+)$", str(value))
    return int(match.group(1)) if match else None


def draw_resources(draw):
    result = {}
    for item in draw.get("ps_resources", []):
        if item.get("name") not in PBR_NAMES or not item.get("resources"):
            continue
        result[str(item["name"])] = str(item["resources"][0])
    return result


def load_world_from_export(event_path):
    if not event_path.is_file():
        return None
    event = json.loads(event_path.read_text(encoding="utf-8"))
    block = next(
        (item for item in event.get("constant_buffers", []) if item.get("name") == "nuPerWorldCBuffer"),
        None,
    )
    if block is None:
        return None
    raw_path = Path(block["export"]["path"])
    data = raw_path.read_bytes()
    if len(data) < 64:
        return None
    values = struct.unpack_from("<16f", data, 0)
    matrix = [list(values[row * 4 : row * 4 + 4]) for row in range(4)]
    return {"matrix": matrix, "translation": matrix[3][:3]}


def normalized_draws(manifest_path, batch_root=None):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = batch_root or manifest_path.parent
    rows = []
    for draw in manifest.get("draws", []):
        if not draw.get("matches_exvs_pbr6"):
            continue
        event_id = int(draw["event_id"])
        lightweight = draw.get("lightweight_state") or {}
        world = lightweight.get("world_transform")
        if world is None:
            world = load_world_from_export(root / "E{}".format(event_id) / "event.json")
        if world is None or len(world.get("translation", [])) != 3:
            continue
        resources = draw_resources(draw)
        resource_numbers = sorted(
            number for number in (resource_number(value) for value in resources.values()) if number is not None
        )
        rows.append(
            {
                "event_id": event_id,
                "num_indices": int(draw.get("num_indices", 0)),
                "num_instances": int(draw.get("num_instances", 0)),
                "translation": [float(value) for value in world["translation"]],
                "world_matrix": world.get("matrix"),
                "resources": resources,
                "resource_numbers": resource_numbers,
                "marker_path": list(draw.get("marker_path", [])),
                "vertex_buffers": lightweight.get("vertex_buffers", []),
                "index_buffer": lightweight.get("index_buffer"),
                "shaders": lightweight.get("shaders", {}),
            }
        )
    return sorted(rows, key=lambda row: row["event_id"])


def distance(left, right):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def resource_distance(left, right):
    if not left or not right:
        return 2**31 - 1
    return min(abs(a - b) for a in left for b in right)


def material_signature(resources):
    return "|".join("{}={}".format(name, resources.get(name, "")) for name in sorted(PBR_NAMES))


def cluster_draws(draws, spatial_threshold=15.0, resource_gap=512, min_total_indices=300):
    parents = list(range(len(draws)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for left_index, left in enumerate(draws):
        for right_index in range(left_index + 1, len(draws)):
            right = draws[right_index]
            if distance(left["translation"], right["translation"]) > spatial_threshold:
                continue
            shared = set(left["resources"].values()) & set(right["resources"].values())
            if not shared and resource_distance(left["resource_numbers"], right["resource_numbers"]) > resource_gap:
                continue
            union(left_index, right_index)

    components = defaultdict(list)
    for index, draw in enumerate(draws):
        components[find(index)].append(draw)

    groups = []
    ordered = sorted(components.values(), key=lambda rows: min(row["event_id"] for row in rows))
    for rows in ordered:
        total_indices = sum(row["num_indices"] for row in rows)
        if total_indices < min_total_indices:
            continue
        anchor = max(rows, key=lambda row: (row["num_indices"], -row["event_id"]))
        signatures = defaultdict(list)
        for row in rows:
            signatures[material_signature(row["resources"])].append(row["event_id"])
        minimum = [min(row["translation"][axis] for row in rows) for axis in range(3)]
        maximum = [max(row["translation"][axis] for row in rows) for axis in range(3)]
        groups.append(
            {
                "group_id": len(groups) + 1,
                "name": "model_{:03d}_E{}".format(len(groups) + 1, anchor["event_id"]),
                "anchor_event": anchor["event_id"],
                "events": sorted(row["event_id"] for row in rows),
                "draw_count": len(rows),
                "total_indices": total_indices,
                "material_count": len(signatures),
                "material_groups": [
                    {"signature": signature, "events": sorted(event_ids)}
                    for signature, event_ids in sorted(signatures.items(), key=lambda item: min(item[1]))
                ],
                "translation_bbox": {"min": minimum, "max": maximum},
                "draws": rows,
            }
        )
    return groups


def build_report(manifest_path, batch_root, spatial_threshold, resource_gap, min_total_indices):
    draws = normalized_draws(manifest_path, batch_root)
    groups = cluster_draws(draws, spatial_threshold, resource_gap, min_total_indices)
    return {
        "schema": "exvs-renderdoc-model-groups/v1",
        "manifest": str(manifest_path.resolve()),
        "batch_root": str((batch_root or manifest_path.parent).resolve()),
        "settings": {
            "spatial_threshold": spatial_threshold,
            "resource_gap": resource_gap,
            "min_total_indices": min_total_indices,
        },
        "eligible_draw_count": len(draws),
        "group_count": len(groups),
        "unassigned_event_ids": sorted(
            set(row["event_id"] for row in draws)
            - {event_id for group in groups for event_id in group["events"]}
        ),
        "groups": groups,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--batch-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--spatial-threshold", type=float, default=15.0)
    parser.add_argument("--resource-gap", type=int, default=512)
    parser.add_argument("--min-total-indices", type=int, default=300)
    args = parser.parse_args(argv)
    report = build_report(
        args.manifest.resolve(),
        args.batch_root.resolve() if args.batch_root else None,
        args.spatial_threshold,
        args.resource_gap,
        args.min_total_indices,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("draws={} groups={} output={}".format(
        report["eligible_draw_count"], report["group_count"], args.output.resolve()
    ))
    for group in report["groups"]:
        print("{} anchor=E{} draws={} indices={} materials={} events={}".format(
            group["name"], group["anchor_event"], group["draw_count"], group["total_indices"],
            group["material_count"], ",".join(str(item) for item in group["events"]),
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
