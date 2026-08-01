#!/usr/bin/env python3
"""Categorize extracted package folders and rewrite all manifest references."""

import argparse
import csv
import errno
import json
import os
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from texture_layout import (
    DEFAULT_PACKAGE_CATEGORY,
    PACKAGE_CATEGORIES,
    PACKAGE_ROOT_NAME,
)


MAPPED_CATEGORIES = set(PACKAGE_CATEGORIES) - {DEFAULT_PACKAGE_CATEGORY}
SOURCE_PNG_PATTERN = re.compile(
    r'("source_png"\s*:\s*)("(?:\\.|[^"\\])*")'
)


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


def rewrite_known_package_path(value, package_to_category):
    """Rewrite one package path with a direct package lookup."""
    if not value:
        return value
    value = str(value)
    for separator in ("\\", "/"):
        marker = f"{PACKAGE_ROOT_NAME}{separator}"
        start = value.find(marker)
        if start < 0:
            continue
        tail = value[start + len(marker):]
        parts = tail.split(separator, 2)
        if not parts:
            continue
        if parts[0] in PACKAGE_CATEGORIES:
            if len(parts) < 2:
                continue
            package = parts[1]
            remainder = parts[2] if len(parts) > 2 else ""
        else:
            package = parts[0]
            remainder = separator.join(parts[1:])
        category = package_to_category.get(package)
        if category:
            rewritten = (
                value[:start]
                + marker
                + category
                + separator
                + package
            )
            if remainder:
                rewritten += separator + remainder
            return rewritten
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
                new = rewrite_known_package_path(
                    item, package_to_category
                )
                if new != item:
                    value[key] = new
                    changed += 1
            else:
                changed += rewrite_json_paths(item, package_to_category)
    elif isinstance(value, list):
        for item in value:
            changed += rewrite_json_paths(item, package_to_category)
    return changed


def rewrite_composition_text(text, package_to_category):
    """Rewrite only JSON source_png string values without rebuilding JSON."""
    changed = 0

    def replace(match):
        nonlocal changed
        encoded = match.group(2)
        source = json.loads(encoded)
        rewritten = rewrite_known_package_path(
            source, package_to_category
        )
        if rewritten == source:
            return match.group(0)
        changed += 1
        return match.group(1) + json.dumps(
            rewritten, ensure_ascii=False
        )

    return SOURCE_PNG_PATTERN.sub(replace, text), changed


def update_compositions(mapping_root, package_to_category, workers=8):
    projects_root = mapping_root / "projects"
    if len(package_to_category) <= 5000:
        paths = []
        for package, category in package_to_category.items():
            package_root = projects_root / category / package
            if package_root.is_dir():
                paths.extend(package_root.rglob("composition.json"))
    else:
        paths = list(projects_root.rglob("composition.json"))

    def update_one(path):
        text = path.read_text(encoding="utf-8")
        rewritten, count = rewrite_composition_text(
            text, package_to_category
        )
        if count:
            temporary = path.with_suffix(".json.replan.tmp")
            temporary.write_text(rewritten, encoding="utf-8")
            temporary.replace(path)
        return count

    changed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = executor.map(update_one, paths)
        for index, count in enumerate(results, 1):
            changed += count
            if index % 500 == 0:
                print(
                    f"replan_compositions={index}/{len(paths)}",
                    flush=True,
                )
    return changed


def move_package(source, target):
    """Use a metadata-only rename, falling back across filesystems."""
    try:
        os.replace(source, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.move(str(source), str(target))


def timed_stage(label, function):
    started = time.perf_counter()
    result = function()
    elapsed = time.perf_counter() - started
    print(f"replan_{label}_seconds={elapsed:.2f}", flush=True)
    return result


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
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="parallel filesystem workers (default: 8)",
    )
    args = parser.parse_args(argv)

    try:
        all_textures = Path(args.all_textures).resolve()
        mapping_root = Path(args.mapping).resolve()
        workers = max(1, min(int(args.workers), 32))
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
        marker_path = mapping_root / "replan-in-progress.json"
        recovering = marker_path.is_file()
        marker_path.write_text(
            json.dumps(
                {
                    "started": stamp,
                    "packages_to_move": len(moves),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if recovering:
            print("replan_recovery=true", flush=True)
        timed_stage(
            "backup",
            lambda: backup_manifests(
                all_textures, mapping_root, backup_root
            ),
        )
        for category in PACKAGE_CATEGORIES:
            (package_root / category).mkdir(parents=True, exist_ok=True)

        def move_packages():
            plan = []
            for source, category in moves:
                target = package_root / category / source.name
                if target.exists():
                    raise FileExistsError(
                        f"target already exists: {target}"
                    )
                plan.append((source, target))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                results = executor.map(
                    lambda item: move_package(*item), plan
                )
                for index, _ in enumerate(results, 1):
                    if index % 250 == 0:
                        print(
                            f"replan_moves={index}/{len(plan)}",
                            flush=True,
                        )

        timed_stage("move", move_packages)
        rewrite_categories = (
            package_to_category
            if recovering
            else {source.name: category for source, category in moves}
        )
        manifest = [
            {"package": package, "category": category}
            for package, category in sorted(package_to_category.items())
        ]

        def rewrite_manifests():
            if not rewrite_categories:
                return {
                    "textures_csv": 0,
                    "groups_csv": 0,
                    "layers_csv": 0,
                    "compositions": 0,
                }
            return {
                "textures_csv": update_csv_paths(
                    all_textures / "inventory" / "textures.csv",
                    rewrite_categories,
                    ("package_directory", "png_output"),
                ),
                "groups_csv": update_csv_paths(
                    mapping_root / "groups.csv",
                    rewrite_categories,
                    ("body_png",),
                ),
                "layers_csv": update_csv_paths(
                    mapping_root / "layers.csv",
                    rewrite_categories,
                    ("source_png",),
                ),
                "compositions": update_compositions(
                    mapping_root,
                    rewrite_categories,
                    workers=workers,
                ),
            }

        changed = timed_stage("rewrite", rewrite_manifests)
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
        marker_path.unlink(missing_ok=True)
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
