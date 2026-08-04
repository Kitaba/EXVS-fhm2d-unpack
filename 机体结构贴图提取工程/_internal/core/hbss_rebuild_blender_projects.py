#!/usr/bin/env python3
"""Regenerate Blender assembly manifests for already decoded HBSS packages."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
from pathlib import Path

try:
    from .hbss_blender_project import build_project
    from .fhm2d_extract_textures import extract_file
except ImportError:
    from hbss_blender_project import build_project
    from fhm2d_extract_textures import extract_file


def rebuild(package_dir: Path, runtime: Path, extract_textures: bool = False) -> dict:
    report = package_dir / "models" / "bundle_models.json"
    relations = package_dir / "material_relations.json"
    output = package_dir / "blender"
    try:
        texture_project = package_dir / "textures_extracted" / package_dir.name
        texture_warning = None
        if extract_textures and not (texture_project / "textures.csv").exists():
            bundle = json.loads(report.read_text(encoding="utf-8"))
            source = Path(bundle["source"])
            try:
                extract_file(source, package_dir / "textures_extracted")
            except ValueError as exc:
                texture_warning = str(exc)
        manifest = build_project(
            report, texture_project if (texture_project / "textures.csv").exists() else None,
            output, runtime,
            relations if relations.exists() else None,
        )
        project = json.loads(manifest.read_text(encoding="utf-8"))
        assembly = project.get("assembly", {})
        return {
            "package": package_dir.name,
            "status": "ok",
            "manifest": str(manifest),
            "resolved_parts": assembly.get("resolved_parts", 0),
            "standalone_parts": assembly.get("standalone_parts", 0),
            "unresolved_parts": len(assembly.get("unresolved_parts", [])),
            "texture_warning": texture_warning,
            "unresolved_material_textures": len(project.get("unresolved_material_textures", {})),
        }
    except Exception as exc:
        return {"package": package_dir.name, "status": "error", "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Batch output containing packages/")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--extract-textures", action="store_true",
        help="Extract DDS textures from each source FHM2D before rebuilding Blender projects.",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    runtime = Path(__file__).resolve().parents[1] / "blender" / "exvs_hbss_import.py"
    packages = sorted(
        report.parent.parent
        for report in (root / "packages").glob("*/models/bundle_models.json")
    )
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(rebuild, package, runtime, args.extract_textures)
            for package in packages
        ]
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            results.append(future.result())
            if completed % 100 == 0:
                print("rebuilt {}/{}".format(completed, len(packages)))
    results.sort(key=lambda item: item["package"])
    summary = {
        "schema": "exvs-hbss-assembly-rebuild/v1",
        "package_count": len(packages),
        "ok_count": sum(item["status"] == "ok" for item in results),
        "error_count": sum(item["status"] == "error" for item in results),
        "resolved_parts": sum(item.get("resolved_parts", 0) for item in results),
        "standalone_parts": sum(item.get("standalone_parts", 0) for item in results),
        "unresolved_parts": sum(item.get("unresolved_parts", 0) for item in results),
        "results": results,
    }
    output = root / "assembly_manifest.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("assembly rebuild: ok={} error={} output={}".format(
        summary["ok_count"], summary["error_count"], output
    ))
    return 1 if summary["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
