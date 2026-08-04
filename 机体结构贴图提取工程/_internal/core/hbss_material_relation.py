#!/usr/bin/env python3
"""Resolve MODL section materials against LTAM texture references in one FHM2D."""

from __future__ import annotations

import argparse
import json
import struct
from collections import Counter, defaultdict
from pathlib import Path

try:
    from .fhm2d_unpack import iter_deflate_blocks
    from .hbss_first_model_extract import model_materials, model_name, printable_strings
    from .hbss_material_table import decode_material_table
except ImportError:
    from fhm2d_unpack import iter_deflate_blocks
    from hbss_first_model_extract import model_materials, model_name, printable_strings
    from hbss_material_table import decode_material_table


def multiset(values: list[str]) -> Counter:
    return Counter(value.lower() for value in values)


def resolve(package: Path) -> dict:
    material_tables = []
    models = []
    helpers = []
    for block_index, _, _, data in iter_deflate_blocks(package.read_bytes()):
        if block_index is None:
            break
        if len(data) < 20 or data[:4] != b"HBSS":
            continue
        inner = data[16:20]
        if inner == b"LTAM":
            table = decode_material_table(data)
            material_tables.append({"block_index": block_index, "size": len(data), **table})
        elif inner == b"LDOM":
            models.append({
                "block_index": block_index,
                "name": model_name(data, len(models)),
                "section_materials": model_materials(data),
            })
        elif inner == b"BPLH":
            helpers.append({
                "block_index": block_index,
                "size": len(data),
                "header_u32": list(struct.unpack_from("<{}I".format(min(24, len(data) // 4)), data, 0)),
                "strings": printable_strings(data)[:64],
            })

    material_variants = defaultdict(list)
    for table in material_tables:
        for material in table["materials"]:
            variant = {
                "table_block": table["block_index"],
                "textures": material["textures"],
                "shaders": material["shaders"],
            }
            if variant not in material_variants[material["name"]]:
                material_variants[material["name"]].append(variant)

    preferred_materials = {}
    for name, variants in material_variants.items():
        preferred_materials[name] = max(
            variants,
            key=lambda item: (
                len({texture["semantic"] for texture in item["textures"]}),
                bool(item["shaders"]),
                len(item["textures"]),
            ),
        )

    for model in models:
        required = multiset(model["section_materials"])
        exact = []
        supersets = []
        for table in material_tables:
            available = multiset([item["name"] for item in table["materials"]])
            if available == required:
                exact.append(table["block_index"])
            elif not (required - available):
                supersets.append(table["block_index"])
        model["exact_ltam_candidates"] = exact
        model["superset_ltam_candidates"] = supersets
        model["resolved_materials"] = {
            name: material_variants.get(name, []) for name in dict.fromkeys(model["section_materials"])
        }
        model["sections"] = [
            {
                "section_index": section_index,
                "material": name,
                "preferred_ltam": preferred_materials.get(name),
            }
            for section_index, name in enumerate(model["section_materials"])
        ]

    return {
        "schema": "exvs-hbss-material-relations/v1",
        "source": str(package),
        "model_count": len(models),
        "material_table_count": len(material_tables),
        "helper_count": len(helpers),
        "models": models,
        "material_tables": material_tables,
        "material_variants": dict(material_variants),
        "preferred_materials": preferred_materials,
        "helpers": helpers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = resolve(args.package.resolve())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("models={} LTAM={} BPLH={} output={}".format(
        report["model_count"], report["material_table_count"], report["helper_count"], output
    ))
    for model in report["models"]:
        print("  {} sections={} exact={} superset={}".format(
            model["name"], len(model["section_materials"]),
            model["exact_ltam_candidates"], model["superset_ltam_candidates"],
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
