#!/usr/bin/env python3
"""Unified entry point for the EXVS capture, extraction, Blender, and repack workflow."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CORE = ROOT / "_internal" / "core"
TEXTURE = CORE / "fhm2d_texture_workflow.py"
DIRECT_MODEL = CORE / "hbss_model_pipeline.py"
RENDERDOC_MODEL = CORE / "exvs_model_pipeline.py"
FOLDER_REPACK = CORE / "fhm2d_folder_repack.py"
DEFAULT_TEXCONV = CORE / "tools" / "texconv.exe"


def run(arguments: list[str]) -> None:
    command = [sys.executable, *arguments]
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, check=True)


def direct(args: argparse.Namespace) -> None:
    package = args.package.resolve()
    output = args.output.resolve()
    texture_root = output / "texture_project"
    texture_project = texture_root / package.stem
    if args.skip_texture_export:
        if not (texture_project / "project.json").exists():
            raise FileNotFoundError(texture_project / "project.json")
    elif not (texture_project / "project.json").exists() or args.force:
        command = [str(TEXTURE), "export", str(package), "--output", str(texture_root)]
        if args.texconv:
            command.extend(["--texconv", str(args.texconv.resolve())])
        if args.force:
            command.append("--force")
        run(command)
    command = [
        str(DIRECT_MODEL), str(package), "--texture-project", str(texture_project),
        "--output", str(output),
    ]
    if args.blender:
        command.extend(["--blender", str(args.blender.resolve())])
    run(command)
    print("ready: {}".format(output / "blender" / "run_in_blender.py"))


def renderdoc(args: argparse.Namespace) -> None:
    command = [
        str(RENDERDOC_MODEL), str(args.capture_root.resolve()),
        "--workers", str(args.workers),
        "--min-package-coverage", str(args.min_package_coverage),
    ]
    if args.fhm_root:
        command.extend(["--fhm-root", str(args.fhm_root.resolve())])
    if args.package:
        command.extend(["--package", str(args.package.resolve())])
    for group in args.group or []:
        command.extend(["--group", str(group)])
    if args.output:
        command.extend(["--output-root", str(args.output.resolve())])
    if args.texconv:
        command.extend(["--texconv", str(args.texconv.resolve())])
    run(command)


def texture_status(args: argparse.Namespace) -> None:
    run([str(TEXTURE), "status", str(args.project.resolve())])


def repack(args: argparse.Namespace) -> None:
    command = [str(FOLDER_REPACK), str(args.folder.resolve())]
    if args.output:
        command.extend(["--output", str(args.output.resolve())])
    if args.texconv:
        command.extend(["--texconv", str(args.texconv.resolve())])
    if args.force:
        command.append("--force")
    run(command)


def inspect(args: argparse.Namespace) -> None:
    root = args.project.resolve()
    candidates = {
        "renderdoc_complete": root / "automation" / "renderdoc_capture_complete.json",
        "pipeline_summary": root / "model_projects" / "pipeline_summary.json",
        "direct_models": root / "models" / "bundle_models.json",
        "blender_project": root / "blender" / "blender_project.json",
        "texture_project": root / "texture_project" / args.package_name / "project.json"
        if args.package_name else None,
    }
    report = {
        key: {"path": str(path), "exists": path.is_file()}
        for key, path in candidates.items() if path is not None
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    direct_parser = commands.add_parser("direct", help="Known FHM2D -> textures, models, Blender")
    direct_parser.add_argument("package", type=Path)
    direct_parser.add_argument("--output", required=True, type=Path)
    direct_parser.add_argument("--texconv", type=Path, default=DEFAULT_TEXCONV)
    direct_parser.add_argument("--blender", type=Path)
    direct_parser.add_argument("--force", action="store_true")
    direct_parser.add_argument("--skip-texture-export", action="store_true")
    direct_parser.set_defaults(func=direct)

    rd_parser = commands.add_parser("renderdoc", help="RenderDoc exports -> source match -> Blender")
    rd_parser.add_argument("capture_root", type=Path)
    rd_parser.add_argument("--fhm-root", type=Path)
    rd_parser.add_argument("--package", type=Path)
    rd_parser.add_argument("--group", type=int, action="append")
    rd_parser.add_argument("--output", type=Path)
    rd_parser.add_argument("--texconv", type=Path, default=DEFAULT_TEXCONV)
    rd_parser.add_argument("--workers", type=int, default=8)
    rd_parser.add_argument("--min-package-coverage", type=float, default=1.0)
    rd_parser.set_defaults(func=renderdoc)

    status_parser = commands.add_parser("status", help="Validate editable texture PNGs")
    status_parser.add_argument("project", type=Path)
    status_parser.set_defaults(func=texture_status)

    repack_parser = commands.add_parser("repack", help="Rebuild texture-editable FHM2D projects")
    repack_parser.add_argument("folder", type=Path)
    repack_parser.add_argument("--output", type=Path)
    repack_parser.add_argument("--texconv", type=Path, default=DEFAULT_TEXCONV)
    repack_parser.add_argument("--force", action="store_true")
    repack_parser.set_defaults(func=repack)

    inspect_parser = commands.add_parser("inspect", help="Show workflow completion markers")
    inspect_parser.add_argument("project", type=Path)
    inspect_parser.add_argument("--package-name")
    inspect_parser.set_defaults(func=inspect)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
