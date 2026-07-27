#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


LAYOUT_SIGNATURE_FIELDS = (
    "package",
    "group_label",
    "embedded_index",
    "width",
    "height",
    "storage_format",
)
CATALOG_SIGNATURE_FIELDS = (
    *LAYOUT_SIGNATURE_FIELDS,
    "pixel_sha256",
)
DATABASE_MANIFEST = "database.json"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_signature(path, fields, prefix):
    digest = hashlib.sha256()
    digest.update(prefix)
    count = 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            count += 1
            for field in fields:
                digest.update(row.get(field, "").encode("utf-8"))
                digest.update(b"\0")
            digest.update(b"\n")
    return digest.hexdigest(), count


def stable_sorted_signature(path, fields, prefix):
    records = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            records.append(
                b"\0".join(
                    row.get(field, "").encode("utf-8")
                    for field in fields
                )
            )
    digest = hashlib.sha256()
    digest.update(prefix)
    for record in sorted(records):
        digest.update(record)
        digest.update(b"\n")
    return digest.hexdigest(), len(records)


def catalog_signatures(path):
    catalog, catalog_count = stable_signature(
        path,
        CATALOG_SIGNATURE_FIELDS,
        b"EXVSIB_TEXTURE_CATALOG_SIGNATURE_V1\n",
    )
    layout, layout_count = stable_signature(
        path,
        LAYOUT_SIGNATURE_FIELDS,
        b"EXVSIB_TEXTURE_LAYOUT_SIGNATURE_V1\n",
    )
    layout_sorted, layout_sorted_count = stable_sorted_signature(
        path,
        LAYOUT_SIGNATURE_FIELDS,
        b"EXVSIB_TEXTURE_LAYOUT_SORTED_SIGNATURE_V1\n",
    )
    if not (
        catalog_count == layout_count == layout_sorted_count
    ):
        raise AssertionError("catalog signature row counts differ")
    return {
        "catalog": catalog,
        "layout": layout,
        "layout_sorted": layout_sorted,
        "texture_count": catalog_count,
    }


def catalog_signature(path):
    signatures = catalog_signatures(path)
    return signatures["catalog"], signatures["texture_count"]


def safe_clear(path):
    if not path.exists():
        return
    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    if resolved == cwd or cwd not in resolved.parents:
        raise ValueError(f"refusing to clear unsafe path: {path}")
    shutil.rmtree(path)


def copy_mapping_database(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name in (
        "mapping.json",
        "groups.csv",
        "layers.csv",
        "exceptions.csv",
        "projects",
    ):
        source_path = source / name
        destination_path = destination / name
        if source_path.is_dir():
            shutil.copytree(source_path, destination_path)
        elif source_path.is_file():
            shutil.copy2(source_path, destination_path)
        else:
            raise FileNotFoundError(f"missing mapping component: {source_path}")


def catalog_source_paths(catalog):
    paths = {}
    with catalog.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            texture_id = (
                f"{row['package']}/{row['group_label']}/"
                f"{int(row['embedded_index']):05d}"
            )
            if texture_id in paths:
                raise ValueError(f"duplicate texture ID in catalog: {texture_id}")
            paths[texture_id] = row["png_output"]
    return paths


def refresh_mapping_source_paths(mapping_root, catalog):
    source_paths = catalog_source_paths(catalog)
    layers_path = mapping_root / "layers.csv"
    with layers_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames
        layers = list(reader)
    if not layers or not fieldnames:
        raise ValueError("mapping layers.csv is empty")
    for layer in layers:
        texture_id = layer["texture_id"]
        if texture_id not in source_paths:
            raise ValueError(
                f"mapping texture is missing from catalog: {texture_id}"
            )
        layer["source_png"] = source_paths[texture_id]
    with layers_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(layers)

    mapping = json.loads(
        (mapping_root / "mapping.json").read_text(encoding="utf-8")
    )
    updated_layers = 0
    for item in mapping["compositions"]:
        composition_path = mapping_root / item["composition"]
        composition = json.loads(composition_path.read_text(encoding="utf-8"))
        composition_layers = [composition["body"]]
        for family in composition["families"]:
            composition_layers.extend(family["states"])
        for layer in composition_layers:
            texture_id = layer["texture_id"]
            if texture_id not in source_paths:
                raise ValueError(
                    f"mapping texture is missing from catalog: {texture_id}"
                )
            layer["source_png"] = source_paths[texture_id]
            updated_layers += 1
        composition_path.write_text(
            json.dumps(composition, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if updated_layers != len(layers):
        raise ValueError(
            f"mapping layer count differs: compositions={updated_layers}, "
            f"layers.csv={len(layers)}"
        )


def create_command(args):
    catalog = Path(args.catalog).resolve()
    mapping_root = Path(args.mapping).resolve()
    output = Path(args.output)
    signatures = catalog_signatures(catalog)
    signature = signatures["catalog"]
    texture_count = signatures["texture_count"]
    if output.exists():
        safe_clear(output)
    output.mkdir(parents=True)
    copy_mapping_database(mapping_root, output)
    refresh_mapping_source_paths(output, catalog)
    mapping = json.loads(
        (mapping_root / "mapping.json").read_text(encoding="utf-8")
    )
    manifest = {
        "database_version": 2,
        "game_version": args.game_version,
        "created_utc": utc_now(),
        "catalog_signature_algorithm": "stable_texture_catalog_v1",
        "catalog_signature": signature,
        "catalog_layout_signature_algorithm": "stable_texture_layout_v1",
        "catalog_layout_signature": signatures["layout"],
        "catalog_layout_sorted_signature_algorithm": (
            "stable_texture_layout_sorted_v1"
        ),
        "catalog_layout_sorted_signature": signatures["layout_sorted"],
        "texture_count": texture_count,
        "group_count": mapping["group_count"],
        "layer_count": mapping["layer_count"],
        "category_counts": mapping["category_counts"],
    }
    (output / DATABASE_MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def find_database(
    database_root,
    signature,
    layout_signature,
    layout_sorted_signature,
    texture_count,
):
    layout_match = None
    for manifest_path in sorted(
        Path(database_root).glob(f"*/{DATABASE_MANIFEST}")
    ):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if int(manifest.get("texture_count", -1)) != texture_count:
            continue
        if manifest.get("catalog_signature") == signature:
            return manifest_path.parent, manifest, "exact"
        if (
            layout_match is None
            and manifest.get("catalog_layout_signature") == layout_signature
        ):
            layout_match = (manifest_path.parent, manifest, "layout")
        if (
            layout_match is None
            and manifest.get("catalog_layout_sorted_signature")
            == layout_sorted_signature
        ):
            layout_match = (
                manifest_path.parent,
                manifest,
                "layout_sorted",
            )
    return layout_match or (None, None, None)


def install_database(
    database,
    manifest,
    catalog,
    texture_root,
    output,
    signatures,
    match_mode,
):
    replacement_backup = output.parent / f".{output.name}.replacements-backup"
    if replacement_backup.exists():
        raise ValueError(
            f"stale replacement backup exists: {replacement_backup}"
        )
    replacements = output / "replacements"
    if replacements.is_dir():
        shutil.move(replacements, replacement_backup)
    if output.exists():
        safe_clear(output)
    output.mkdir(parents=True)
    copy_mapping_database(database, output)
    refresh_mapping_source_paths(output, catalog)
    if replacement_backup.is_dir():
        shutil.move(replacement_backup, output / "replacements")

    mapping_path = output / "mapping.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    mapping.update(
        {
            "installed_utc": utc_now(),
            "mapping_source": "prebuilt_database",
            "mapping_database_version": manifest["database_version"],
            "game_version": manifest["game_version"],
            "texture_root": str(texture_root.resolve()),
            "texture_root_relative": "../all-textures",
            "source_catalog": str(catalog.resolve()),
            "source_catalog_relative": "../all-textures/inventory/textures.csv",
            "source_catalog_sha256": sha256_file(catalog),
            "source_catalog_signature": signatures["catalog"],
            "source_catalog_layout_signature": signatures["layout"],
            "source_catalog_layout_sorted_signature": signatures[
                "layout_sorted"
            ],
            "mapping_database_catalog_signature": manifest["catalog_signature"],
            "mapping_database_match": match_mode,
            "previews_mode": "lazy",
        }
    )
    mapping_path.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "installed": True,
                "game_version": manifest["game_version"],
                "database_match": match_mode,
                "group_count": mapping["group_count"],
                "layer_count": mapping["layer_count"],
                "output": str(output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def apply_command(args):
    catalog = Path(args.catalog).resolve()
    texture_root = Path(args.texture_root).resolve()
    output = Path(args.output)
    signatures = catalog_signatures(catalog)
    database, manifest, match_mode = find_database(
        Path(args.database_root),
        signatures["catalog"],
        signatures["layout"],
        signatures["layout_sorted"],
        signatures["texture_count"],
    )
    if database:
        install_database(
            database,
            manifest,
            catalog,
            texture_root,
            output,
            signatures,
            match_mode,
        )
        return 0

    if args.require_database:
        candidates = []
        for manifest_path in sorted(
            Path(args.database_root).glob(f"*/{DATABASE_MANIFEST}")
        ):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            candidates.append(
                {
                    "game_version": manifest.get("game_version"),
                    "texture_count": manifest.get("texture_count"),
                    "layout_signature": manifest.get(
                        "catalog_layout_signature"
                    ),
                    "layout_sorted_signature": manifest.get(
                        "catalog_layout_sorted_signature"
                    ),
                }
            )
        raise ValueError(
            "No matching prebuilt mapping database. Pixel-only texture "
            "changes are allowed, but the package/group/index/dimension/"
            "format layout must match a supported VSAC29 catalog. Finish "
            "the scan and PNG extraction first "
            f"(textures={signatures['texture_count']}, "
            f"layout={signatures['layout']}, "
            f"layout_sorted={signatures['layout_sorted']}, "
            f"available={json.dumps(candidates, ensure_ascii=False)})."
        )

    print(
        "No matching prebuilt mapping database; running mapper analysis.",
        flush=True,
    )
    command = [
        sys.executable,
        str(Path(args.mapper).resolve()),
        "build",
        "--texture-root",
        str(texture_root),
        "--output",
        str(output),
        "--force",
    ]
    return subprocess.run(command).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create or install versioned EXVSIB mapping databases."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--catalog", required=True)
    create_parser.add_argument("--mapping", required=True)
    create_parser.add_argument("--output", required=True)
    create_parser.add_argument("--game-version", required=True)
    create_parser.set_defaults(func=create_command)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--catalog", required=True)
    apply_parser.add_argument("--texture-root", required=True)
    apply_parser.add_argument("--database-root", required=True)
    apply_parser.add_argument("--output", required=True)
    apply_parser.add_argument("--mapper", required=True)
    apply_parser.add_argument(
        "--require-database",
        action="store_true",
        help="Fail instead of running mapper analysis when no database matches.",
    )
    apply_parser.set_defaults(func=apply_command)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
