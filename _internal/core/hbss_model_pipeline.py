#!/usr/bin/env python3
"""Run direct FHM2D model extraction through Blender project generation."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run(command: list[str]) -> None:
    print("+ " + " ".join(command))
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--texture-project", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--blender", type=Path, help="Optional Blender 4.5 executable")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    output = args.output.resolve()
    model_dir = output / "models"
    blender_dir = output / "blender"
    relations = output / "material_relations.json"
    run([
        sys.executable, str(script_dir / "hbss_bundle_to_obj.py"), str(args.package.resolve()),
        "--output", str(model_dir),
    ])
    run([
        sys.executable, str(script_dir / "hbss_material_relation.py"), str(args.package.resolve()),
        "--output", str(relations),
    ])
    run([
        sys.executable, str(script_dir / "hbss_blender_project.py"),
        str(model_dir / "bundle_models.json"),
        "--texture-project", str(args.texture_project.resolve()),
        "--material-relations", str(relations),
        "--output", str(blender_dir),
    ])
    runner = blender_dir / "run_in_blender.py"
    if args.blender:
        blender = args.blender.resolve()
        if not blender.is_file():
            raise FileNotFoundError(blender)
        run([str(blender), "--background", "--python", str(runner)])
    else:
        print("Blender runner ready: {}".format(runner))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
