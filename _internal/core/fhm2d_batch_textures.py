#!/usr/bin/env python3
import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fhm2d_extract_textures import (
    FHM2D_BC7_FORMAT,
    FHM2D_RGBA8_FORMAT,
    assign_groups,
    decode_payload,
    extract_file,
    parse_group_labels,
    scan_textures,
)
from png_color_profile import png_color_metadata, retag_png_srgb

SUPPORTED_BATCH_FORMATS = {FHM2D_BC7_FORMAT, FHM2D_RGBA8_FORMAT}


PACKAGE_FIELDS = [
    "source",
    "name",
    "size",
    "mtime_ns",
    "status",
    "texture_count",
    "group_count",
    "group_labels",
    "texture_data_bytes",
    "max_width",
    "max_height",
    "scan_seconds",
    "error",
]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_csv_atomic(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(
            {field: row.get(field, "") for field in fieldnames} for row in rows
        )
    for attempt in range(20):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


def read_inventory(path):
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def scan_one(path, detail_dir):
    started = time.perf_counter()
    stat = path.stat()
    row = {
        "source": str(path.resolve()),
        "name": path.name,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "status": "",
        "texture_count": 0,
        "group_count": 0,
        "group_labels": "",
        "texture_data_bytes": 0,
        "max_width": 0,
        "max_height": 0,
        "scan_seconds": 0,
        "error": "",
    }
    try:
        _, _, payload, trailing = decode_payload(path, strict=False)
        if b"46XTimg-" not in payload:
            row["status"] = "no_supported_textures"
        else:
            labels = parse_group_labels(trailing)
            textures = scan_textures(
                payload, supported_formats=SUPPORTED_BATCH_FORMATS
            )
            assign_groups(textures, labels)
            row.update(
                {
                    "status": "supported_textures",
                    "texture_count": len(textures),
                    "group_count": max(
                        texture["group_index"] for texture in textures
                    )
                    + 1,
                    "group_labels": " ".join(labels),
                    "texture_data_bytes": sum(
                        texture["data_size"] for texture in textures
                    ),
                    "max_width": max(texture["width"] for texture in textures),
                    "max_height": max(texture["height"] for texture in textures),
                }
            )
            detail_dir.mkdir(parents=True, exist_ok=True)
            detail = {
                "source": str(path.resolve()),
                "scanned_utc": utc_now(),
                "group_labels": labels,
                "textures": textures,
            }
            (detail_dir / f"{path.stem}.json").write_text(
                json.dumps(detail, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    except Exception as exc:
        row["status"] = "error"
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["scan_seconds"] = f"{time.perf_counter() - started:.6f}"
    return row


def scan_command(args):
    source_dir = Path(args.source)
    output_root = Path(args.output)
    inventory_path = output_root / "inventory" / "packages.csv"
    detail_dir = output_root / "inventory" / "details"
    files = sorted(source_dir.glob(args.pattern))
    existing_rows = read_inventory(inventory_path)
    existing = {row["source"].lower(): row for row in existing_rows}
    rows_by_source = dict(existing)
    scanned = skipped = 0

    for index, path in enumerate(files, 1):
        resolved = str(path.resolve())
        stat = path.stat()
        old = existing.get(resolved.lower())
        if (
            not args.force
            and old
            and int(old["size"]) == stat.st_size
            and int(old["mtime_ns"]) == stat.st_mtime_ns
        ):
            skipped += 1
        else:
            rows_by_source[resolved.lower()] = scan_one(path, detail_dir)
            scanned += 1
        if index % args.checkpoint == 0 or index == len(files):
            ordered = sorted(rows_by_source.values(), key=lambda row: row["name"])
            write_csv_atomic(inventory_path, PACKAGE_FIELDS, ordered)
            supported = sum(
                row["status"] == "supported_textures" for row in ordered
            )
            errors = sum(row["status"] == "error" for row in ordered)
            print(
                f"progress={index}/{len(files)} scanned={scanned} "
                f"skipped={skipped} supported={supported} errors={errors}",
                flush=True,
            )

    write_summary(output_root, rows_by_source.values())
    return 0


def write_summary(output_root, rows):
    rows = list(rows)
    supported = [row for row in rows if row["status"] == "supported_textures"]
    summary = {
        "updated_utc": utc_now(),
        "package_count": len(rows),
        "supported_package_count": len(supported),
        "no_supported_texture_package_count": sum(
            row["status"] == "no_supported_textures" for row in rows
        ),
        "error_package_count": sum(row["status"] == "error" for row in rows),
        "texture_count": sum(int(row["texture_count"]) for row in supported),
        "texture_data_bytes": sum(
            int(row["texture_data_bytes"]) for row in supported
        ),
    }
    summary_path = output_root / "inventory" / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def available_space(path):
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free


def compact_package(
    output_root, package_dir, manifest, row, texconv, reserve, ignore_space_check
):
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["batch_storage"] = "compact_pending"
    manifest_data["dds_retained"] = True
    manifest.write_text(
        json.dumps(manifest_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    dds_files = sorted((package_dir / "dds").glob("*.dds"))
    png_dir = package_dir / "png"
    if dds_files:
        required = int(row["texture_data_bytes"]) * 2 + reserve
        free = available_space(output_root)
        if required > free and not ignore_space_check:
            raise ValueError(
                f"compact conversion needs up to "
                f"{required / 1024**3:.2f} GiB including reserve, "
                f"but only {free / 1024**3:.2f} GiB is free"
            )
        convert_dds_batch(texconv, dds_files, png_dir)
        for dds_path in dds_files:
            dds_path.unlink()
    expected_pngs = int(row["texture_count"])
    actual_pngs = len(list(png_dir.glob("*.png")))
    if actual_pngs != expected_pngs:
        raise ValueError(
            f"compact package has {actual_pngs} PNG files, "
            f"expected {expected_pngs}"
        )
    dds_dir = package_dir / "dds"
    if dds_dir.is_dir() and not any(dds_dir.iterdir()):
        dds_dir.rmdir()
    manifest_data["batch_storage"] = "png_only"
    manifest_data["dds_retained"] = False
    manifest.write_text(
        json.dumps(manifest_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def extract_command(args):
    output_root = Path(args.output)
    inventory_path = output_root / "inventory" / "packages.csv"
    rows = read_inventory(inventory_path)
    supported = [row for row in rows if row["status"] == "supported_textures"]
    if not supported:
        raise ValueError("inventory contains no supported texture packages")

    package_root = output_root / "packages"
    free = available_space(output_root)
    reserve = int(args.reserve_gib * 1024**3)
    if args.compact:
        largest_package = max(
            int(row["texture_data_bytes"]) for row in supported
        )
        estimated = largest_package * 2
        estimate_label = "largest temporary DDS+PNG package"
    else:
        estimated = sum(
            int(row["texture_data_bytes"])
            + int(row["texture_count"]) * (148 + 176)
            for row in supported
        )
        estimate_label = "estimated DDS output"
    if estimated + reserve > free and not args.ignore_space_check:
        raise ValueError(
            f"{estimate_label} {estimated / 1024**3:.2f} GiB plus "
            f"reserve {args.reserve_gib:.2f} GiB exceeds free space "
            f"{free / 1024**3:.2f} GiB"
        )

    texconv = find_texconv(args.texconv) if args.compact else None
    result_rows = []
    for index, row in enumerate(supported, 1):
        source = Path(row["source"])
        manifest = package_root / source.stem / "extract_manifest.json"
        resume_compact = False
        if manifest.is_file() and args.compact and not args.force:
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            resume_compact = (
                manifest_data.get("batch_storage") == "compact_pending"
            )
        if manifest.is_file() and not args.force and not resume_compact:
            status = "skipped_existing"
            error = ""
        else:
            try:
                if not resume_compact:
                    report, _ = extract_file(
                        source,
                        package_root,
                        strict_payload=False,
                        supported_formats=SUPPORTED_BATCH_FORMATS,
                    )
                    if report["texture_count"] != int(row["texture_count"]):
                        raise ValueError(
                            f"texture count changed for {source.name}: "
                            f"{report['texture_count']} != "
                            f"{row['texture_count']}"
                        )
                status = "extracted"
                error = ""
                if args.compact:
                    package_dir = package_root / source.stem
                    compact_package(
                        output_root,
                        package_dir,
                        manifest,
                        row,
                        texconv,
                        reserve,
                        args.ignore_space_check,
                    )
                    status = "extracted_compact"
            except Exception as exc:
                status = "error"
                error = f"{type(exc).__name__}: {exc}"
        result_rows.append(
            {
                "source": str(source),
                "name": source.name,
                "status": status,
                "texture_count": row["texture_count"],
                "error": error,
            }
        )
        write_csv_atomic(
            output_root / "inventory" / "extraction.csv",
            ["source", "name", "status", "texture_count", "error"],
            result_rows,
        )
        print(
            f"extract={index}/{len(supported)} {source.name} {status}",
            flush=True,
        )
    catalog_command(args)
    return 0


def catalog_command(args):
    output_root = Path(args.output)
    package_root = output_root / "packages"
    rows = []
    for csv_path in sorted(package_root.glob("*/textures.csv")):
        package = csv_path.parent.name
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                row = dict(row)
                row["package"] = package
                row["package_directory"] = str(csv_path.parent)
                if not row.get("storage_format"):
                    row["storage_format"] = {
                        str(FHM2D_BC7_FORMAT): "bc7",
                        str(FHM2D_RGBA8_FORMAT): "rgba8",
                    }.get(row.get("fhm2d_format"), "unknown")
                png_name = Path(row["dds_output"]).with_suffix(".png").name
                row["png_output"] = str(
                    Path("packages") / package / "png" / png_name
                )
                row["png_available"] = (
                    output_root / row["png_output"]
                ).is_file()
                row["dds_available"] = (
                    csv_path.parent / row["dds_output"]
                ).is_file()
                rows.append(row)
    if rows:
        fields = [
            "package",
            "package_directory",
            "png_output",
            "png_available",
            "dds_available",
            *[
                field
                for field in rows[0]
                if field
                not in (
                    "package",
                    "package_directory",
                    "png_output",
                    "png_available",
                    "dds_available",
                )
            ],
        ]
        write_csv_atomic(
            output_root / "inventory" / "textures.csv", fields, rows
        )
        dimensions = {}
        for row in rows:
            key = (row["width"], row["height"])
            dimensions[key] = dimensions.get(key, 0) + 1
        dimension_rows = [
            {"width": width, "height": height, "texture_count": count}
            for (width, height), count in sorted(
                dimensions.items(),
                key=lambda item: (-item[1], int(item[0][0]), int(item[0][1])),
            )
        ]
        write_csv_atomic(
            output_root / "inventory" / "dimensions.csv",
            ["width", "height", "texture_count"],
            dimension_rows,
        )
        format_rows = []
        for storage_format in sorted({row["storage_format"] for row in rows}):
            selected = [
                row for row in rows if row["storage_format"] == storage_format
            ]
            format_rows.append(
                {
                    "storage_format": storage_format,
                    "texture_count": len(selected),
                    "texture_data_bytes": sum(
                        int(row["data_size"]) for row in selected
                    ),
                    "dds_retained": sum(
                        str(row["dds_available"]).lower() == "true"
                        for row in selected
                    ),
                    "png_available": sum(
                        str(row["png_available"]).lower() == "true"
                        for row in selected
                    ),
                }
            )
        write_csv_atomic(
            output_root / "inventory" / "formats.csv",
            [
                "storage_format",
                "texture_count",
                "texture_data_bytes",
                "dds_retained",
                "png_available",
            ],
            format_rows,
        )
    print(f"catalog_textures={len(rows)}", flush=True)
    return 0


def find_texconv(explicit):
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path(__file__).resolve().parent / "tools" / "texconv.exe")
    discovered = shutil.which("texconv.exe") or shutil.which("texconv")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("texconv.exe was not found")


def convert_dds_batch(texconv, dds_files, png_dir):
    png_dir.mkdir(parents=True, exist_ok=True)
    for start in range(0, len(dds_files), 24):
        batch = dds_files[start : start + 24]
        command = [
            str(texconv),
            "-nologo",
            "-y",
            "-ft",
            "png",
            "--ignore-srgb",
            "-o",
            str(png_dir),
            *[str(path) for path in batch],
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode:
            raise ValueError(
                f"texconv failed: {result.stdout}\n{result.stderr}"
            )
        for dds_path in batch:
            retag_png_srgb(png_dir / dds_path.with_suffix(".png").name)
    missing = [
        path.name
        for path in dds_files
        if not (png_dir / path.with_suffix(".png").name).is_file()
    ]
    if missing:
        raise ValueError(f"texconv did not create {len(missing)} PNG files")


def png_command(args):
    output_root = Path(args.output)
    package_dirs = sorted((output_root / "packages").glob("*"))
    texconv = find_texconv(args.texconv)
    catalog_path = output_root / "inventory" / "textures.csv"
    catalog = read_inventory(catalog_path)
    worst_case = sum(
        int(row["width"]) * int(row["height"]) * 4 for row in catalog
    )
    free = available_space(output_root)
    reserve = int(args.reserve_gib * 1024**3)
    if worst_case + reserve > free and not args.ignore_space_check:
        raise ValueError(
            f"worst-case RGBA output {worst_case / 1024**3:.2f} GiB plus "
            f"reserve {args.reserve_gib:.2f} GiB exceeds free space "
            f"{free / 1024**3:.2f} GiB"
        )
    converted = skipped = 0
    for package_index, package_dir in enumerate(package_dirs, 1):
        dds_files = sorted((package_dir / "dds").glob("*.dds"))
        if not dds_files:
            continue
        png_dir = package_dir / "png"
        png_dir.mkdir(parents=True, exist_ok=True)
        pending = [
            path
            for path in dds_files
            if args.force or not (png_dir / path.with_suffix(".png").name).is_file()
        ]
        skipped += len(dds_files) - len(pending)
        convert_dds_batch(texconv, pending, png_dir)
        converted += len(pending)
        print(
            f"png_package={package_index}/{len(package_dirs)} "
            f"{package_dir.name} converted={len(pending)}",
            flush=True,
        )
    print(f"png_converted={converted} skipped={skipped}", flush=True)
    return 0


def retag_srgb_command(args):
    output_root = Path(args.output)
    catalog = read_inventory(output_root / "inventory" / "textures.csv")
    if not catalog:
        raise FileNotFoundError(
            f"texture catalog was not found or is empty: {output_root}"
        )
    counts = {
        "gamma_updated": 0,
        "gamma_inserted": 0,
        "already_srgb": 0,
        "missing": 0,
    }
    for index, row in enumerate(catalog, 1):
        png_path = output_root / row["png_output"]
        if not png_path.is_file():
            counts["missing"] += 1
            continue
        result = retag_png_srgb(png_path)
        counts[result] += 1
        if index % 1000 == 0:
            print(
                f"retag_srgb={index}/{len(catalog)} "
                f"updated={counts['gamma_updated'] + counts['gamma_inserted']} "
                f"missing={counts['missing']}",
                flush=True,
            )
    print(json.dumps(counts, ensure_ascii=False), flush=True)
    return 0


def validate_command(args):
    from PIL import Image

    output_root = Path(args.output)
    catalog = read_inventory(output_root / "inventory" / "textures.csv")
    summary = json.loads(
        (output_root / "inventory" / "summary.json").read_text(
            encoding="utf-8"
        )
    )
    problems = []
    format_counts = {}
    dds_count = png_count = metadata_count = 0
    color_profile_problem_count = 0
    for index, row in enumerate(catalog, 1):
        package_dir = Path(row["package_directory"])
        png_path = output_root / row["png_output"]
        metadata_path = package_dir / row["metadata_output"]
        dds_path = package_dir / row["dds_output"]
        storage_format = row.get("storage_format", "unknown")
        format_counts[storage_format] = format_counts.get(storage_format, 0) + 1
        if not png_path.is_file():
            problems.append(f"missing PNG: {png_path}")
        else:
            png_count += 1
            try:
                with Image.open(png_path) as image:
                    if image.mode != "RGBA":
                        problems.append(
                            f"PNG mode {image.mode} is not RGBA: {png_path}"
                        )
                    expected = (int(row["width"]), int(row["height"]))
                    if image.size != expected:
                        problems.append(
                            f"PNG size {image.size} != {expected}: {png_path}"
                        )
                color_metadata = png_color_metadata(png_path)
                if not color_metadata["has_srgb"] and (
                    color_metadata["gamma"] != 45455
                ):
                    color_profile_problem_count += 1
            except Exception as exc:
                problems.append(f"invalid PNG {png_path}: {exc}")
        if metadata_path.is_file():
            metadata_count += 1
        else:
            problems.append(f"missing metadata: {metadata_path}")
        if dds_path.is_file():
            dds_count += 1
        if len(problems) >= args.max_problems:
            break
        if index % 5000 == 0:
            print(f"validate={index}/{len(catalog)}", flush=True)

    if len(catalog) != int(summary["texture_count"]):
        problems.append(
            f"catalog count {len(catalog)} != inventory count "
            f"{summary['texture_count']}"
        )
    report = {
        "validated_utc": utc_now(),
        "catalog_texture_count": len(catalog),
        "inventory_texture_count": int(summary["texture_count"]),
        "format_counts": format_counts,
        "png_count": png_count,
        "dds_count": dds_count,
        "metadata_count": metadata_count,
        "color_profile_problem_count": color_profile_problem_count,
        "problem_count": len(problems),
        "problems": problems,
    }
    report_path = output_root / "inventory" / "validation.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if problems else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Resumable inventory, extraction, cataloging, and PNG conversion "
            "for supported EXVSIB 46XTimg BC7 and RGBA8 resources."
        )
    )
    parser.add_argument(
        "--output", default="patch-edit/all-textures", help="Batch output root"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument(
        "--source", default="data/x64/dplcache_release"
    )
    scan_parser.add_argument("--pattern", default="*.fhm2d")
    scan_parser.add_argument("--checkpoint", type=int, default=100)
    scan_parser.add_argument("--force", action="store_true")
    scan_parser.set_defaults(func=scan_command)

    extract_parser = subparsers.add_parser("extract")
    extract_parser.add_argument("--reserve-gib", type=float, default=2.0)
    extract_parser.add_argument("--ignore-space-check", action="store_true")
    extract_parser.add_argument("--force", action="store_true")
    extract_parser.add_argument(
        "--compact",
        action="store_true",
        help="Convert each new package to PNG and discard its temporary DDS",
    )
    extract_parser.add_argument("--texconv")
    extract_parser.set_defaults(func=extract_command)

    catalog_parser = subparsers.add_parser("catalog")
    catalog_parser.set_defaults(func=catalog_command)

    png_parser = subparsers.add_parser("png")
    png_parser.add_argument("--texconv")
    png_parser.add_argument("--reserve-gib", type=float, default=2.0)
    png_parser.add_argument("--ignore-space-check", action="store_true")
    png_parser.add_argument("--force", action="store_true")
    png_parser.set_defaults(func=png_command)

    retag_parser = subparsers.add_parser(
        "retag-srgb",
        help="Fix PNG display metadata without changing RGBA pixel values",
    )
    retag_parser.set_defaults(func=retag_srgb_command)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--max-problems", type=int, default=100)
    validate_parser.set_defaults(func=validate_command)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
