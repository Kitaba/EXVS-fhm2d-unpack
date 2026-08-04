#!/usr/bin/env python3
"""Rebuild one or more editable FHM2D projects from an arbitrary folder."""

import argparse
import json
import sys
from pathlib import Path

from fhm2d_texture_workflow import build_project, find_texconv, load_project


def find_projects(root):
    root = Path(root).resolve()
    if (root / "project.json").is_file():
        return [root]
    return sorted(
        path.parent
        for path in root.rglob("project.json")
        if path.is_file()
    )


def safe_output_path(project, output_root):
    source_name = project["source_name"]
    package_name = Path(source_name).stem
    return Path(output_root) / f"{package_name}.fhm2d"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Repack editable FHM2D projects found in any folder."
    )
    parser.add_argument(
        "folder",
        help="A project folder or a folder containing project.json files",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output folder; defaults to <folder>/repacked",
    )
    parser.add_argument("--texconv", help="Path to texconv.exe")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow replacing existing output files",
    )
    args = parser.parse_args(argv)
    try:
        root = Path(args.folder).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"missing folder: {root}")
        projects = find_projects(root)
        if not projects:
            raise FileNotFoundError(
                f"no project.json found under: {root}"
            )
        output_root = Path(args.output).resolve() if args.output else root / "repacked"
        output_root.mkdir(parents=True, exist_ok=True)
        texconv = find_texconv(args.texconv)
        reports = []
        for project_dir in projects:
            project, _, _, _ = load_project(project_dir)
            output = safe_output_path(project, output_root)
            report = build_project(
                project_dir, output, texconv, force=args.force
            )
            reports.append(report)
            print(
                f"built {project_dir} -> {output} "
                f"modified={report['modified_texture_count']}"
            )
        print(json.dumps({"count": len(reports), "output": str(output_root)}, ensure_ascii=False))
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

