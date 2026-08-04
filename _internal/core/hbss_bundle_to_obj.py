#!/usr/bin/env python3
"""Extract and decode every SKEL/MESH/MODL model in an FHM2D HBSS bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .fhm2d_unpack import iter_deflate_blocks
    from .hbss_first_model_extract import model_materials, model_name, printable_strings, resource_groups
    from .hbss_mesh_to_obj import decode_mesh, write_obj
except ImportError:
    from fhm2d_unpack import iter_deflate_blocks
    from hbss_first_model_extract import model_materials, model_name, printable_strings, resource_groups
    from hbss_mesh_to_obj import decode_mesh, write_obj


def safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    package = args.package.resolve()
    blocks = []
    for block_index, _, _, data in iter_deflate_blocks(package.read_bytes()):
        if block_index is None:
            break
        blocks.append((block_index, data))
    groups = resource_groups(blocks)
    counts = {kind: len(resources) for kind, resources in groups.items()}
    if len(set(counts.values())) != 1:
        raise ValueError("unbalanced HBSS model resources: {}".format(counts))

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    models = []
    for ordinal in range(counts["model"]):
        raw_name = model_name(bytes(groups["model"][ordinal]["data"]), ordinal)
        name = safe_name(raw_name)
        model_dir = output / "{:03d}_{}".format(ordinal, name)
        model_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "ordinal": ordinal,
            "name": raw_name,
            "directory": str(model_dir),
            "status": "ok",
            "resources": {},
        }
        for kind in ("skeleton", "mesh", "model"):
            resource = groups[kind][ordinal]
            data = bytes(resource["data"])
            filename = "{}.hbss".format(kind)
            (model_dir / filename).write_bytes(data)
            record["resources"][kind] = {
                "file": filename,
                "size": len(data),
                "start_block": resource["start_block"],
                "block_indices": resource["block_indices"],
            }
        try:
            materials = model_materials(bytes(groups["model"][ordinal]["data"]))
            mesh = decode_mesh(bytes(groups["mesh"][ordinal]["data"]))
            source_material_count = len(materials)
            if len(materials) < mesh["section_count"]:
                materials.extend(
                    "section_{:02d}".format(index)
                    for index in range(len(materials), mesh["section_count"])
                )
            elif len(materials) > mesh["section_count"]:
                materials = materials[:mesh["section_count"]]
            obj_path = model_dir / "{}.obj".format(name)
            write_obj(obj_path, mesh, name, materials)
            record["obj"] = str(obj_path)
            record["materials"] = materials
            record["source_material_count"] = source_material_count
            record["material_binding_complete"] = source_material_count == mesh["section_count"]
            record["mesh"] = {
                key: value for key, value in mesh.items()
                if key not in {"vertices", "normals", "uvs", "indices"}
            }
        except Exception as exc:
            record["status"] = "decode_error"
            record["error"] = str(exc)
            record["model_strings"] = printable_strings(
                bytes(groups["model"][ordinal]["data"])
            )[:32]
        models.append(record)
        print("[{}/{}] {} {}".format(ordinal + 1, counts["model"], record["status"], raw_name))

    report = {
        "schema": "exvs-hbss-bundle-models/v1",
        "source": str(package),
        "resource_counts": counts,
        "model_count": len(models),
        "decoded_count": sum(item["status"] == "ok" for item in models),
        "failed_count": sum(item["status"] != "ok" for item in models),
        "total_vertices": sum(item.get("mesh", {}).get("vertex_count", 0) for item in models),
        "total_triangles": sum(item.get("mesh", {}).get("triangle_count", 0) for item in models),
        "models": models,
    }
    report_path = output / "bundle_models.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "models={} decoded={} failed={} vertices={} triangles={} report={}".format(
            report["model_count"], report["decoded_count"], report["failed_count"],
            report["total_vertices"], report["total_triangles"], report_path,
        )
    )
    # A bundle may mix several HSEM layout generations. Preserve and continue
    # with every successfully decoded model; the report retains partial failures.
    return 0 if report["decoded_count"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
