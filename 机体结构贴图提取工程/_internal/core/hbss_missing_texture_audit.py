#!/usr/bin/env python3
"""Classify model packages where the supported 46XT scanner found no textures."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from .fhm2d_audit_texture_names import inspect_file
except ImportError:
    from fhm2d_audit_texture_names import inspect_file


def normalize(value: str) -> str:
    value = Path(value.replace("\\", "/")).name.lower()
    if value.startswith("46xt"):
        value = value[4:]
    return value


def inventory_names(packages_root: Path) -> set[str]:
    names = set()
    for path in packages_root.glob("*/textures_extracted/*/textures.csv"):
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            names.update(normalize(row["embedded_name"]) for row in csv.DictReader(stream))
    return names


def logical_texture_names(package_dir: Path) -> set[str]:
    path = package_dir / "material_relations.json"
    if not path.exists():
        return set()
    report = json.loads(path.read_text(encoding="utf-8"))
    names = set()
    for material in report.get("preferred_materials", {}).values():
        for texture in material.get("textures", []):
            names.add(normalize(texture.get("path", "")))
    return {name for name in names if name}


def audit_one(item: dict, global_names: set[str], packages_root: Path) -> dict:
    source = Path(item["package"])
    package_dir = packages_root / source.stem
    naming = inspect_file(source)
    logical = logical_texture_names(package_dir)
    matched = sorted(logical & global_names)
    families = naming.get("families", {})
    valid = naming.get("valid_families", {})
    unknown_valid = naming.get("unrecognized_valid", {})
    if unknown_valid:
        classification = "valid_46xt_unrecognized_name_or_layout"
    elif families and not valid:
        classification = "46xt_strings_without_valid_texture_metadata"
    elif matched:
        classification = "model_only_with_cross_package_texture_references"
    elif logical:
        classification = "model_only_with_unresolved_external_references"
    else:
        classification = "model_only_no_texture_references"
    return {
        "package": source.stem,
        "source": str(source),
        "classification": classification,
        "46xt_families": families,
        "valid_46xt_families": valid,
        "valid_unrecognized": unknown_valid,
        "logical_texture_count": len(logical),
        "logical_textures": sorted(logical),
        "cross_package_match_count": len(matched),
        "cross_package_matches": matched,
        "decode_error": naming.get("error"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_root", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    root = args.batch_root.resolve()
    manifest = json.loads((root / "assembly_manifest.json").read_text(encoding="utf-8"))
    targets = [item for item in manifest["results"] if item.get("texture_warning")]
    global_names = inventory_names(root / "packages")
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = list(executor.map(
            lambda item: audit_one(item, global_names, root / "packages"), targets
        ))
    results.sort(key=lambda item: item["package"])
    counts = Counter(item["classification"] for item in results)
    report = {
        "schema": "exvs-hbss-missing-texture-audit/v1",
        "target_count": len(targets),
        "global_texture_name_count": len(global_names),
        "classification_counts": dict(counts),
        "results": results,
    }
    output = root / "missing_texture_audit.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = root / "missing_texture_audit.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        fields = [
            "package", "classification", "logical_texture_count",
            "cross_package_match_count", "source",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: item[key] for key in fields} for item in results)
    print(json.dumps(report["classification_counts"], ensure_ascii=False, indent=2))
    print("audit: targets={} output={}".format(len(results), output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
