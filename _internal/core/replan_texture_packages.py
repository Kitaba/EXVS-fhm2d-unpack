#!/usr/bin/env python3
"""Categorize extracted package folders and rewrite all manifest references."""

import argparse
import csv
import json
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from texture_layout import (
    DEFAULT_PACKAGE_CATEGORY,
    PACKAGE_CATEGORIES,
    PACKAGE_ROOT_NAME,
)


MAPPED_CATEGORIES = set(PACKAGE_CATEGORIES) - {DEFAULT_PACKAGE_CATEGORY}


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    if not rows:
        return
    fields = list(rows[0])
    temporary = path.with_suffix(path.suffix + ".replan.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def package_categories(mapping_root):
    groups_path = mapping_root / "groups.csv"
    if not groups_path.is_file():
        raise FileNotFoundError(f"missing mapping groups: {groups_path}")
    categories = defaultdict(set)
    for row in read_csv(groups_path):
        category = row.get("category", "")
        if category in MAPPED_CATEGORIES:
            categories[row["package"]].add(category)
    return categories


def classify(package, categories):
    found = categories.get(package, set())
    if len(found) == 1:
        return next(iter(found))
    return DEFAULT_PACKAGE_CATEGORY


def discover_packages(package_root, categories):
    package_to_category = {}
    package_locations = {}
    for category in PACKAGE_CATEGORIES:
        category_root = package_root / category
        if not category_root.is_dir():
            continue
        for path in sorted(item for item in category_root.iterdir() if item.is_dir()):
            if path.name in package_locations:
                raise ValueError(f"duplicate categorized package: {path.name}")
            package_locations[path.name] = (path, category)
    for path in sorted(item for item in package_root.iterdir() if item.is_dir()):
        if path.name in PACKAGE_CATEGORIES:
            continue
        if path.name in package_locations:
            raise ValueError(
                f"package exists both flat and categorized: {path.name}"
            )
        package_locations[path.name] = (path, None)

    moves = []
    for package, (path, current_category) in package_locations.items():
        desired_category = classify(package, categories)
        package_to_category[package] = desired_category
        if current_category != desired_category:
            moves.append((path, desired_category))
    return package_to_category, moves


def rewrite_package_path(value, package, category):
    if not value:
        return value
    value = str(value)
    for separator in ("\\", "/"):
        desired = (
            f"{PACKAGE_ROOT_NAME}{separator}{category}"
            f"{separator}{package}"
        )
        sources = [f"{PACKAGE_ROOT_NAME}{separator}{package}"]
        sources.extend(
            f"{PACKAGE_ROOT_NAME}{separator}{old_category}"
            f"{separator}{package}"
            for old_category in PACKAGE_CATEGORIES
        )
        for source in sources:
            if source in value:
                return value.replace(source, desired, 1)
    return value


def update_csv_paths(path, package_to_category, fields):
    if not path.is_file():
        return 0
    rows = read_csv(path)
    changed = 0
    for row in rows:
        package = row.get("package", "")
        category = package_to_category.get(package)
        if not category:
            continue
        for field in fields:
            old = row.get(field, "")
            new = rewrite_package_path(old, package, category)
            if new != old:
                row[field] = new
                changed += 1
    if changed:
        write_csv(path, rows)
    return changed


def rewrite_json_paths(value, package_to_category):
    changed = 0
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if isinstance(item, str) and key == "source_png":
                for package, category in package_to_category.items():
                    new = rewrite_package_path(item, package, category)
                    if new != item:
                        value[key] = new
                        changed += 1
                        break
            else:
                changed += rewrite_json_paths(item, package_to_category)
    elif isinstance(value, list):
        for item in value:
            changed += rewrite_json_paths(item, package_to_category)
    return changed


def update_compositions(mapping_root, package_to_category):
    changed = 0
    for path in (mapping_root / "projects").rglob("composition.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        count = rewrite_json_paths(data, package_to_category)
        if count:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            changed += count
    return changed


def backup_manifests(all_textures, mapping_root, backup_root):
    backup_root.mkdir(parents=True, exist_ok=True)
    for path in (
        all_textures / "inventory" / "textures.csv",
        mapping_root / "groups.csv",
        mapping_root / "layers.csv",
        mapping_root / "mapping.json",
    ):
        if path.is_file():
            target = backup_root / path.name
            shutil.copy2(path, target)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Replan all-textures/packages into categorized folders."
    )
    parser.add_argument("--all-textures", required=True)
    parser.add_argument("--mapping", required=True, help="asset-mapping directory")
    parser.add_argument("--apply", action="store_true", help="move and rewrite files")
    args = parser.parse_args(argv)

    try:
        all_textures = Path(args.all_textures).resolve()
        mapping_root = Path(args.mapping).resolve()
        package_root = all_textures / PACKAGE_ROOT_NAME
        categories = package_categories(mapping_root)
        package_to_category, moves = discover_packages(
            package_root, categories
        )
        counts = Counter(package_to_category.values())
        print(
            json.dumps(
                {
                    "packages": len(package_to_category),
                    "packages_to_move": len(moves),
                    "categories": counts,
                },
                default=dict,
            )
        )
        if not args.apply:
            for category in PACKAGE_CATEGORIES:
                print(f"{category}: {counts.get(category, 0)}")
            print("dry-run: no files changed; rerun with --apply")
            return 0

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_root = mapping_root / "replan-backups" / stamp
        backup_manifests(all_textures, mapping_root, backup_root)
        for category in PACKAGE_CATEGORIES:
            (package_root / category).mkdir(parents=True, exist_ok=True)
        for source, category in moves:
            target = package_root / category / source.name
            if target.exists():
                raise FileExistsError(f"target already exists: {target}")
            shutil.move(str(source), str(target))
        manifest = [
            {"package": package, "category": category}
            for package, category in sorted(package_to_category.items())
        ]

        changed = {
            "textures_csv": update_csv_paths(
                all_textures / "inventory" / "textures.csv",
                package_to_category,
                ("package_directory", "png_output"),
            ),
            "groups_csv": update_csv_paths(
                mapping_root / "groups.csv",
                package_to_category,
                ("body_png",),
            ),
            "layers_csv": update_csv_paths(
                mapping_root / "layers.csv",
                package_to_category,
                ("source_png",),
            ),
            "compositions": update_compositions(mapping_root, package_to_category),
        }
        mapping_path = mapping_root / "mapping.json"
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        mapping["package_layout_version"] = 1
        mapping["package_directory_template"] = "packages/{category}/{package}"
        mapping["package_category_directories"] = {
            category: category for category in PACKAGE_CATEGORIES
        }
        mapping["package_category_counts"] = dict(
            sorted(counts.items())
        )
        mapping["package_replan_manifest"] = str(
            Path("replan-manifest.json")
        )
        mapping_path.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (mapping_root / "replan-manifest.json").write_text(
            json.dumps(
                {
                    "layout_version": 1,
                    "backup": str(backup_root),
                    "packages": manifest,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "applied": True,
                    "moved_packages": len(moves),
                    "total_packages": len(package_to_category),
                    "categories": dict(sorted(counts.items())),
                    "changed": changed,
                    "backup": str(backup_root),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
