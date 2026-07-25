#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import shutil
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from png_color_profile import retag_png_srgb

from fhm2d_dds_match import parse_dds
from fhm2d_extract_textures import extract_file
from fhm2d_repack import decode_container, repack_file


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_texconv(explicit=None):
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path(__file__).resolve().parent / "tools" / "texconv.exe")
    discovered = shutil.which("texconv.exe") or shutil.which("texconv")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "texconv.exe was not found; expected patch/tools/texconv.exe "
        "or use --texconv"
    )


def run_texconv(texconv, arguments):
    command = [str(texconv), "-nologo", *[str(item) for item in arguments]]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        details = (result.stdout + "\n" + result.stderr).strip()
        raise ValueError(f"texconv failed ({result.returncode}):\n{details}")
    return result.stdout.strip()


def texconv_version(texconv):
    result = subprocess.run(
        [str(texconv), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return (result.stdout or result.stderr).strip()


def parse_png(path):
    data = path.read_bytes()
    if len(data) < 33 or data[:8] != PNG_SIGNATURE:
        raise ValueError(f"{path} is not a PNG file")
    ihdr_size = struct.unpack_from(">I", data, 8)[0]
    if data[12:16] != b"IHDR" or ihdr_size != 13:
        raise ValueError(f"{path} has an invalid PNG IHDR")
    width, height = struct.unpack_from(">II", data, 16)
    return {
        "width": width,
        "height": height,
        "bit_depth": data[24],
        "color_type": data[25],
        "compression": data[26],
        "filter": data[27],
        "interlace": data[28],
    }


def validate_png(path, expected_width, expected_height):
    png = parse_png(path)
    problems = []
    if png["width"] != expected_width or png["height"] != expected_height:
        problems.append(
            f"dimensions {png['width']}x{png['height']}, expected "
            f"{expected_width}x{expected_height}"
        )
    if png["bit_depth"] != 8:
        problems.append(f"bit depth {png['bit_depth']}, expected 8")
    if png["color_type"] != 6:
        problems.append(
            f"color type {png['color_type']}, expected 6 (RGBA)"
        )
    if png["compression"] != 0 or png["filter"] != 0:
        problems.append("unsupported PNG compression/filter method")
    if png["interlace"] != 0:
        problems.append("interlaced PNG is not accepted")
    if problems:
        raise ValueError(f"invalid editable PNG {path}: " + "; ".join(problems))
    return png


def read_csv(path):
    return list(csv.DictReader(path.open(encoding="utf-8-sig")))


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def clear_directory(target, root):
    if not target.exists():
        return
    resolved = target.resolve()
    resolved_root = root.resolve()
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise ValueError(f"refusing to clear unsafe directory: {resolved}")
    shutil.rmtree(resolved)


def convert_dds_to_png(texconv, dds_paths, png_dir):
    png_dir.mkdir(parents=True, exist_ok=True)
    batch_size = 24
    for start in range(0, len(dds_paths), batch_size):
        batch = dds_paths[start : start + batch_size]
        run_texconv(
            texconv,
            ["-y", "-ft", "png", "--ignore-srgb", "-o", png_dir, *batch],
        )
        for dds_path in batch:
            retag_png_srgb(png_dir / dds_path.with_suffix(".png").name)


def export_project(input_path, output_root, texconv, force=False):
    project_dir = output_root / input_path.stem
    project_path = project_dir / "project.json"
    if project_path.exists() and not force:
        raise ValueError(
            f"project already exists: {project_dir}; use --force only if edited "
            "PNG files may be overwritten"
        )
    if force:
        clear_directory(project_dir, output_root)

    extract_report, project_dir = extract_file(input_path, output_root)
    texture_rows = read_csv(project_dir / "textures.csv")
    dds_paths = [project_dir / row["dds_output"] for row in texture_rows]
    png_dir = project_dir / "png_edit"
    convert_dds_to_png(texconv, dds_paths, png_dir)

    png_rows = []
    for texture in texture_rows:
        dds_path = Path(texture["dds_output"])
        png_name = dds_path.with_suffix(".png").name
        png_path = png_dir / png_name
        if not png_path.is_file():
            raise ValueError(f"texconv did not create {png_path}")
        validate_png(
            png_path,
            int(texture["width"]),
            int(texture["height"]),
        )
        png_rows.append(
            {
                "texture_index": texture["texture_index"],
                "png_file": png_name,
                "width": texture["width"],
                "height": texture["height"],
                "sha256": sha256_file(png_path),
            }
        )

    write_csv(
        project_dir / "png_manifest.csv",
        png_rows,
        ["texture_index", "png_file", "width", "height", "sha256"],
    )
    project = {
        "workflow_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(input_path.resolve()),
        "source_name": input_path.name,
        "source_size": input_path.stat().st_size,
        "source_sha256": sha256_file(input_path),
        "texture_count": extract_report["texture_count"],
        "group_labels": extract_report["group_labels"],
        "textures_manifest": "textures.csv",
        "png_manifest": "png_manifest.csv",
        "editable_png_directory": "png_edit",
        "original_dds_directory": "dds",
        "metadata_directory": "metadata",
        "texconv": {
            "path": str(texconv),
            "version": texconv_version(texconv),
            "sha256": sha256_file(texconv),
        },
        "png_requirements": {
            "bit_depth": 8,
            "color_type": 6,
            "color_type_name": "RGBA",
            "interlace": 0,
            "dimensions": "must match textures.csv",
            "filename": "must not change",
        },
    }
    project_path.write_text(
        json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return project, project_dir


def load_project(project_dir):
    project_path = project_dir / "project.json"
    if not project_path.is_file():
        raise FileNotFoundError(f"missing project file: {project_path}")
    project = json.loads(project_path.read_text(encoding="utf-8"))
    if project.get("workflow_version") != 1:
        raise ValueError(
            f"unsupported workflow version: {project.get('workflow_version')}"
        )
    source_path = Path(project["source"])
    if not source_path.is_file():
        raise FileNotFoundError(f"source fhm2d is missing: {source_path}")
    source_hash = sha256_file(source_path)
    if source_hash != project["source_sha256"]:
        raise ValueError(
            f"source fhm2d hash changed: {source_path}; expected "
            f"{project['source_sha256']}, got {source_hash}"
        )
    textures = read_csv(project_dir / project["textures_manifest"])
    png_manifest = {
        row["texture_index"]: row
        for row in read_csv(project_dir / project["png_manifest"])
    }
    if len(textures) != project["texture_count"]:
        raise ValueError("textures.csv count differs from project.json")
    return project, source_path, textures, png_manifest


def project_status(project_dir):
    project, source_path, textures, png_manifest = load_project(project_dir)
    png_dir = project_dir / project["editable_png_directory"]
    rows = []
    for texture in textures:
        manifest = png_manifest.get(texture["texture_index"])
        if manifest is None:
            raise ValueError(
                f"PNG manifest lacks texture {texture['texture_index']}"
            )
        png_path = png_dir / manifest["png_file"]
        if not png_path.is_file():
            raise FileNotFoundError(f"missing editable PNG: {png_path}")
        validate_png(
            png_path,
            int(texture["width"]),
            int(texture["height"]),
        )
        current_hash = sha256_file(png_path)
        rows.append(
            {
                "texture_index": int(texture["texture_index"]),
                "group_label": texture["group_label"],
                "embedded_index": int(texture["embedded_index"]),
                "png_file": manifest["png_file"],
                "width": int(texture["width"]),
                "height": int(texture["height"]),
                "original_sha256": manifest["sha256"],
                "current_sha256": current_hash,
                "modified": current_hash != manifest["sha256"],
            }
        )
    return project, source_path, textures, rows


def convert_changed_pngs(texconv, project_dir, changed_rows, dds_dir):
    dds_dir.mkdir(parents=True, exist_ok=True)
    png_dir = project_dir / "png_edit"

    def encode(row):
        png_path = png_dir / row["png_file"]
        run_texconv(
            texconv,
            [
                "-y",
                "-f",
                "BC7_UNORM",
                "-m",
                "1",
                "-dx10",
                "-nogpu",
                "--single-proc",
                "-bc",
                "x",
                "--ignore-srgb",
                "-o",
                dds_dir,
                png_path,
            ],
        )
        dds_path = dds_dir / png_path.with_suffix(".dds").name
        if not dds_path.is_file():
            raise ValueError(f"texconv did not create {dds_path}")
        dds = parse_dds(dds_path)
        if dds["width"] != row["width"] or dds["height"] != row["height"]:
            raise ValueError(
                f"encoded DDS dimensions changed for {row['png_file']}"
            )
        if dds["mip_count"] != 1 or dds["array_size"] != 1:
            raise ValueError(
                f"encoded DDS subresources are invalid for {row['png_file']}"
            )
        if dds["dxgi_format"] != 98:
            raise ValueError(
                f"encoded DDS format {dds['dxgi_format']} is not BC7_UNORM"
            )
        return str(row["texture_index"]), {"path": dds_path, "dds": dds}

    encoded = {}
    worker_count = min(4, len(changed_rows))
    if worker_count == 0:
        return encoded
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(encode, row) for row in changed_rows]
        for future in as_completed(futures):
            key, item = future.result()
            encoded[key] = item
    return encoded


def build_project(project_dir, output_path, texconv, force=False):
    project, source_path, textures, status_rows = project_status(project_dir)
    changed_rows = [row for row in status_rows if row["modified"]]

    if output_path.resolve() == source_path.resolve():
        raise ValueError("output path must not overwrite the original fhm2d")
    if output_path.exists() and not force:
        raise ValueError(f"output already exists: {output_path}; use --force")

    build_dir = project_dir / "build"
    dds_dir = build_dir / "dds_modified"
    clear_directory(dds_dir, project_dir)

    encoded = convert_changed_pngs(
        texconv, project_dir, changed_rows, dds_dir
    )
    container = decode_container(source_path)
    payload = bytearray(container["payload"])
    textures_by_index = {row["texture_index"]: row for row in textures}
    modified_report = []

    for status in changed_rows:
        key = str(status["texture_index"])
        texture = textures_by_index[key]
        item = encoded[key]
        top_mip = item["dds"]["top_mip"]
        expected_size = int(texture["data_size"])
        if len(top_mip) != expected_size:
            raise ValueError(
                f"encoded data size {len(top_mip)} differs from expected "
                f"{expected_size} for {status['png_file']}"
            )
        start = int(texture["payload_data_offset"])
        end = start + expected_size
        original_pixel_hash = hashlib.sha256(payload[start:end]).hexdigest()
        payload[start:end] = top_mip
        modified_report.append(
            {
                "texture_index": status["texture_index"],
                "group_label": status["group_label"],
                "embedded_index": status["embedded_index"],
                "png_file": status["png_file"],
                "width": status["width"],
                "height": status["height"],
                "payload_data_offset": start,
                "data_size": expected_size,
                "original_png_sha256": status["original_sha256"],
                "modified_png_sha256": status["current_sha256"],
                "original_bc7_sha256": original_pixel_hash,
                "modified_bc7_sha256": hashlib.sha256(top_mip).hexdigest(),
                "encoded_dds": str(item["path"].relative_to(project_dir)),
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    repack_report = repack_file(source_path, output_path, bytes(payload))
    report = {
        "workflow_version": 1,
        "built_utc": datetime.now(timezone.utc).isoformat(),
        "project": str(project_dir.resolve()),
        "source": str(source_path.resolve()),
        "source_sha256": project["source_sha256"],
        "output": str(output_path.resolve()),
        "output_size": repack_report["output_size"],
        "output_sha256": repack_report["output_sha256"],
        "byte_identical_to_source": repack_report["byte_identical"],
        "modified_texture_count": len(modified_report),
        "modified_textures": modified_report,
        "texconv": {
            "path": str(texconv),
            "version": texconv_version(texconv),
            "sha256": sha256_file(texconv),
            "encoding": (
                "BC7_UNORM, one mip, DX10 header, CPU, single process, "
                "maximum BC7 compression"
            ),
        },
    }
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="End-to-end fhm2d -> DDS/PNG -> edited fhm2d workflow."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export", help="Extract DDS files and create editable RGBA PNG files"
    )
    export_parser.add_argument("input", help="Original .fhm2d")
    export_parser.add_argument(
        "-o",
        "--output",
        default="patch/fhm2d_projects",
        help="Project root. Default: patch/fhm2d_projects",
    )
    export_parser.add_argument("--texconv", help="Path to texconv.exe")
    export_parser.add_argument("--force", action="store_true")

    status_parser = subparsers.add_parser(
        "status", help="Validate editable PNG files and list modifications"
    )
    status_parser.add_argument("project", help="Package project directory")

    build_parser = subparsers.add_parser(
        "build", help="Encode modified PNG files and rebuild the fhm2d"
    )
    build_parser.add_argument("project", help="Package project directory")
    build_parser.add_argument(
        "-o",
        "--output",
        help="Output .fhm2d; defaults to PROJECT/build/PACKAGE.fhm2d",
    )
    build_parser.add_argument("--texconv", help="Path to texconv.exe")
    build_parser.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            input_path = Path(args.input)
            if not input_path.is_file():
                raise FileNotFoundError(f"missing input: {input_path}")
            texconv = find_texconv(args.texconv)
            project, project_dir = export_project(
                input_path,
                Path(args.output),
                texconv,
                args.force,
            )
            print(
                f"project={project_dir} textures={project['texture_count']} "
                f"editable_png={project_dir / 'png_edit'}"
            )
        elif args.command == "status":
            project_dir = Path(args.project)
            _, _, _, rows = project_status(project_dir)
            changed = [row for row in rows if row["modified"]]
            print(f"textures={len(rows)} modified={len(changed)}")
            for row in changed:
                print(
                    f"modified {row['group_label']}:{row['embedded_index']:05d} "
                    f"{row['width']}x{row['height']} {row['png_file']}"
                )
        elif args.command == "build":
            project_dir = Path(args.project)
            project, _, _, _ = load_project(project_dir)
            texconv = find_texconv(args.texconv)
            output_path = (
                Path(args.output)
                if args.output
                else project_dir / "build" / project["source_name"]
            )
            report = build_project(
                project_dir, output_path, texconv, args.force
            )
            print(
                f"output={report['output']} "
                f"modified={report['modified_texture_count']} "
                f"byte_identical={report['byte_identical_to_source']}"
            )
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
