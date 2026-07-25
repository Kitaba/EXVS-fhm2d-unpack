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


SIGNATURE_FIELDS = (
    "package",
    "group_label",
    "embedded_index",
    "width",
    "height",
    "storage_format",
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


def catalog_signature(path):
    digest = hashlib.sha256()
    digest.update(b"EXVSIB_TEXTURE_CATALOG_SIGNATURE_V1\n")
    count = 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            count += 1
            for field in SIGNATURE_FIELDS:
                digest.update(row.get(field, "").encode("utf-8"))
                digest.update(b"\0")
            digest.update(b"\n")
    return digest.hexdigest(), count


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


def create_command(args):
    catalog = Path(args.catalog).resolve()
    mapping_root = Path(args.mapping).resolve()
    output = Path(args.output)
    signature, texture_count = catalog_signature(catalog)
    if output.exists():
        safe_clear(output)
    output.mkdir(parents=True)
    copy_mapping_database(mapping_root, output)
    mapping = json.loads(
        (mapping_root / "mapping.json").read_text(encoding="utf-8")
    )
    manifest = {
        "database_version": 1,
        "game_version": args.game_version,
        "created_utc": utc_now(),
        "catalog_signature_algorithm": "stable_texture_catalog_v1",
        "catalog_signature": signature,
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


def find_database(database_root, signature, texture_count):
    for manifest_path in sorted(
        Path(database_root).glob(f"*/{DATABASE_MANIFEST}")
    ):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("catalog_signature") == signature
            and int(manifest.get("texture_count", -1)) == texture_count
        ):
            return manifest_path.parent, manifest
    return None, None


def install_database(database, manifest, catalog, texture_root, output):
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
            "source_catalog_signature": manifest["catalog_signature"],
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
    signature, texture_count = catalog_signature(catalog)
    database, manifest = find_database(
        Path(args.database_root), signature, texture_count
    )
    if database:
        install_database(
            database, manifest, catalog, texture_root, output
        )
        return 0

    if args.require_database:
        raise ValueError(
            "No matching prebuilt mapping database. For VSAC29, finish the "
            "scan and PNG extraction first; an incomplete or different "
            "texture catalog cannot use the built-in mapping."
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
