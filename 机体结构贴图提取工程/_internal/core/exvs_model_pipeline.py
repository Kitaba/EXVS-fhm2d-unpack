#!/usr/bin/env python3
"""Build model, texture, material, and Blender projects from EXVS RenderDoc exports."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from .fhm2d_runtime_texture_match import parse_dds
except ImportError:
    from fhm2d_runtime_texture_match import parse_dds


CORE_DIR = Path(__file__).resolve().parent
TOOLKIT_ROOT = CORE_DIR.parent.parent
RAW_DECODE = CORE_DIR / "renderdoc_raw_mesh_decode.py"
ASSEMBLE = CORE_DIR / "renderdoc_mesh_assemble.py"
RUNTIME_MATCH = CORE_DIR / "fhm2d_runtime_texture_match.py"
TEXTURE_WORKFLOW = CORE_DIR / "fhm2d_texture_workflow.py"
BLENDER_RUNTIME = TOOLKIT_ROOT / "_internal" / "blender" / "exvs_blender_import.py"
PBR_NAMES = (
    "BaseColorMap",
    "NormalMap",
    "MetallicMap",
    "RoughnessMap",
    "AmbientOcclusionMap",
    "EmissiveMap",
)


def run(command: list[str]) -> None:
    print("+ {}".format(" ".join(str(item) for item in command)), flush=True)
    subprocess.run(command, check=True)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def event_texture_rows(batch_root: Path, event_ids: list[int]) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for event_id in event_ids:
        event_path = batch_root / "E{}".format(event_id) / "event.json"
        if not event_path.is_file():
            raise FileNotFoundError("full RenderDoc export missing: {}".format(event_path))
        event = json.loads(event_path.read_text(encoding="utf-8"))
        for binding in event.get("ps_resources", []):
            semantic = binding.get("name")
            if semantic not in PBR_NAMES:
                continue
            resources = binding.get("resources", [])
            exports = binding.get("exports", [])
            for index, resource in enumerate(resources):
                export = exports[index] if index < len(exports) else (exports[0] if exports else None)
                if not export:
                    raise ValueError("E{} {} has no exported DDS".format(event_id, semantic))
                key = (str(resource), str(semantic))
                if key in seen:
                    continue
                seen.add(key)
                dds_path = Path(export["path"]).resolve()
                parsed = parse_dds(dds_path)
                rows.append(
                    {
                        "event_id": event_id,
                        "semantic": semantic,
                        "resource": str(resource),
                        "resource_id": str(resource).rsplit(":", 1)[-1],
                        "dds_path": str(dds_path),
                        "pixel_sha256": parsed["pixel_sha256"],
                    }
                )
    return rows


def decode_and_assemble(
    group: dict[str, Any], batch_root: Path, model_root: Path
) -> tuple[Path, Path]:
    decoded_root = model_root / "parts"
    assembly_root = model_root / "assembly"
    for event_id in group["events"]:
        output = decoded_root / "E{}".format(event_id)
        summary = output / "mesh_summary.json"
        if not summary.is_file():
            run([
                sys.executable,
                str(RAW_DECODE),
                str(batch_root / "E{}".format(event_id) / "event.json"),
                "--output",
                str(output),
            ])
    run([
        sys.executable,
        str(ASSEMBLE),
        "--batch-root", str(batch_root),
        "--decoded-root", str(decoded_root),
        "--events", *[str(item) for item in group["events"]],
        "--anchor-event", str(group["anchor_event"]),
        "--model-name", group["name"],
        "--output", str(assembly_root),
    ])
    obj_path = assembly_root / "{}_anchor_local.obj".format(group["name"])
    return decoded_root, obj_path


def run_runtime_match(
    texture_rows: list[dict[str, Any]], fhm_root: Path, output: Path, workers: int
) -> Path:
    unique_dds = sorted({row["dds_path"] for row in texture_rows})
    run([
        sys.executable,
        str(RUNTIME_MATCH),
        *unique_dds,
        "--fhm-root", str(fhm_root),
        "--output", str(output),
        "--workers", str(workers),
    ])
    return output / "matches.csv"


def choose_package(
    texture_rows: list[dict[str, Any]], matches: list[dict[str, str]]
) -> tuple[Path, dict[str, Any]]:
    target_hashes = {row["pixel_sha256"] for row in texture_rows}
    dds_hashes = {str(Path(row["dds_path"]).resolve()): row["pixel_sha256"] for row in texture_rows}
    package_hashes: dict[str, set[str]] = defaultdict(set)
    for match in matches:
        dds = str(Path(match["dds"]).resolve())
        digest = dds_hashes.get(dds) or match.get("pixel_sha256")
        if digest in target_hashes:
            package_hashes[match["source"]].add(str(digest))
    if not package_hashes:
        raise ValueError("no FHM2D package matched this model's runtime textures")
    source, hashes = max(package_hashes.items(), key=lambda item: (len(item[1]), item[0]))
    selected_matches = {
        str(Path(match["dds"]).resolve()): int(match["payload_offset"])
        for match in matches
        if match["source"] == source
    }
    return Path(source).resolve(), {
        "matched_texture_hashes": len(hashes),
        "target_texture_hashes": len(target_hashes),
        "coverage": len(hashes) / max(1, len(target_hashes)),
        "payload_offsets_by_dds": selected_matches,
        "candidate_packages": [
            {"source": path, "matched_texture_hashes": len(values)}
            for path, values in sorted(package_hashes.items(), key=lambda item: (-len(item[1]), item[0]))
        ],
    }


def ensure_texture_project(package: Path, project_root: Path, texconv: Path) -> Path:
    project_dir = project_root / package.stem
    if not (project_dir / "project.json").is_file():
        run([
            sys.executable,
            str(TEXTURE_WORKFLOW),
            "export",
            str(package),
            "--output", str(project_root),
            "--texconv", str(texconv),
        ])
    return project_dir


def map_runtime_textures(
    texture_rows: list[dict[str, Any]], texture_project: Path, package_evidence: dict[str, Any]
) -> list[dict[str, Any]]:
    project_textures = read_csv(texture_project / "textures.csv")
    by_hash: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in project_textures:
        by_hash[row["pixel_sha256"]].append(row)
    mapped = []
    offsets = package_evidence.get("payload_offsets_by_dds", {})
    for runtime in texture_rows:
        candidates = by_hash.get(runtime["pixel_sha256"], [])
        expected_offset = offsets.get(str(Path(runtime["dds_path"]).resolve()))
        if expected_offset is not None:
            exact_offset = [
                row for row in candidates
                if int(row["payload_data_offset"]) == int(expected_offset)
            ]
            if exact_offset:
                candidates = exact_offset
        if not candidates:
            raise ValueError("package project has no exact texture for {}".format(runtime["dds_path"]))
        source = candidates[0]
        mapped.append(
            {
                **runtime,
                "texture_index": int(source["texture_index"]),
                "group_label": source["group_label"],
                "embedded_name": source["embedded_name"],
                "embedded_index": int(source["embedded_index"]),
                "width": int(source["width"]),
                "height": int(source["height"]),
                "storage_format": source["storage_format"],
                "dds_output": source["dds_output"],
            }
        )
    fields = [
        "event_id", "semantic", "resource", "resource_id", "dds_path", "pixel_sha256",
        "texture_index", "group_label", "embedded_name", "embedded_index", "width", "height",
        "storage_format", "dds_output",
    ]
    write_csv(texture_project / "runtime_texture_map.csv", mapped, fields)
    return mapped


def variable_value(state_root: Path, event_id: int, block_name: str, variable_name: str):
    path = state_root / "E{}".format(event_id) / "render_state.json"
    if not path.is_file():
        return None
    event = json.loads(path.read_text(encoding="utf-8"))
    pixel = next((stage for stage in event.get("stages", []) if str(stage.get("stage", "")).endswith("Pixel")), {})
    block = next((item for item in pixel.get("constant_buffers", []) if item.get("name") == block_name), {})
    variable = next((item for item in block.get("decoded", {}).get("variables", []) if item.get("name") == variable_name), {})
    values = variable.get("value", {})
    variable_type = int(variable.get("type", -1))
    selected = values.get("u32" if variable_type == 11 else "f32", [])
    return selected[0] if selected else None


def label_from_embedded(name: str, fallback: str) -> str:
    lowered = name.lower()
    if lowered.endswith("_basecolor"):
        name = name[: -len("_basecolor")]
    tail = name.rsplit("_", 1)[-1]
    return tail if tail and len(tail) <= 32 else fallback


def build_blender_project(
    group: dict[str, Any],
    obj_path: Path,
    texture_project: Path,
    mapped: list[dict[str, Any]],
    batch_root: Path,
    state_root: Path,
    model_root: Path,
) -> Path:
    mapped_by_key = {(row["resource"], row["semantic"]): row for row in mapped}
    materials = {}
    draw_rows = []
    for event_id in group["events"]:
        event = json.loads((batch_root / "E{}".format(event_id) / "event.json").read_text(encoding="utf-8"))
        resources = {
            item["name"]: item["resources"][0]
            for item in event.get("ps_resources", [])
            if item.get("name") in PBR_NAMES and item.get("resources")
        }
        base = resources["BaseColorMap"]
        embedded = mapped_by_key[(base, "BaseColorMap")]["embedded_name"]
        material_name = "R{}".format(base.rsplit(":", 1)[-1])
        label = label_from_embedded(embedded, "material_{:02d}".format(len(materials) + 1))
        emissive_scale = variable_value(state_root, event_id, "UpdatePerObject", "EmissiveScale")
        shadow_receiver = variable_value(state_root, event_id, "UpdatePerObject", "IsShadowReceiver")
        materials.setdefault(
            material_name,
            {
                "label": label,
                "base_resource": base.rsplit(":", 1)[-1],
                "emissive_scale": float(emissive_scale or 0.0),
                "shadow_receiver": bool(shadow_receiver) if shadow_receiver is not None else True,
            },
        )
        draw_rows.append({
            "event_id": event_id,
            "material": material_name,
            "material_label": label,
            **{name: resources.get(name, "") for name in PBR_NAMES},
        })

    write_csv(
        texture_project / "draw_materials.csv",
        draw_rows,
        ["event_id", "material", "material_label", *PBR_NAMES],
    )
    manifest = {
        "schema": "exvs-blender-project/v1",
        "model_name": group["name"],
        "obj_path": str(obj_path.resolve()),
        "texture_project_dir": str(texture_project.resolve()),
        "output_blend": str((model_root / "{}_pbr.blend".format(group["name"])).resolve()),
        "materials": materials,
        "import_model": True,
        "pack_resources": True,
        "save_blend": True,
        "invert_normal_green": True,
        "set_standard_color_management": True,
    }
    manifest_path = model_root / "blender_project.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    runner = model_root / "run_in_blender.py"
    runner.write_text(
        "EXVS_BLENDER_PROJECT = r{!r}\n"
        "exec(open(r{!r}, encoding='utf-8').read())\n".format(
            str(manifest_path.resolve()), str(BLENDER_RUNTIME.resolve())
        ),
        encoding="utf-8",
    )
    return runner


def build_group(
    group: dict[str, Any],
    capture_root: Path,
    output_root: Path,
    package: Path,
    package_evidence: dict[str, Any],
    texconv: Path,
) -> dict[str, Any]:
    batch_root = capture_root / "batch_export"
    state_root = capture_root / "render_state"
    model_root = output_root / group["name"]
    model_root.mkdir(parents=True, exist_ok=True)
    _, obj_path = decode_and_assemble(group, batch_root, model_root)
    texture_rows = event_texture_rows(batch_root, group["events"])
    texture_project = ensure_texture_project(package, model_root / "texture_project", texconv)
    mapped = map_runtime_textures(texture_rows, texture_project, package_evidence)
    runner = build_blender_project(
        group, obj_path, texture_project, mapped, batch_root, state_root, model_root
    )
    report = {
        "schema": "exvs-automated-model-project/v1",
        "model_name": group["name"],
        "group_id": group["group_id"],
        "events": group["events"],
        "anchor_event": group["anchor_event"],
        "source_package": str(package.resolve()),
        "package_evidence": package_evidence,
        "obj_path": str(obj_path.resolve()),
        "texture_project": str(texture_project.resolve()),
        "runtime_texture_count": len(mapped),
        "blender_project": str((model_root / "blender_project.json").resolve()),
        "blender_script": str(runner.resolve()),
    }
    (model_root / "model_project.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_root", type=Path)
    parser.add_argument("--fhm-root", type=Path)
    parser.add_argument("--package", type=Path, help="Known package override; valid when building one group")
    parser.add_argument("--group", type=int, action="append", help="Group id; repeatable. Default: all")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--texconv", type=Path, default=CORE_DIR / "tools" / "texconv.exe")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--min-package-coverage", type=float, default=1.0)
    args = parser.parse_args(argv)

    capture_root = args.capture_root.resolve()
    output_root = (args.output_root or capture_root / "model_projects").resolve()
    groups_path = capture_root / "automation" / "model_groups.json"
    groups_report = json.loads(groups_path.read_text(encoding="utf-8"))
    selected = [
        group for group in groups_report["groups"]
        if not args.group or int(group["group_id"]) in set(args.group)
    ]
    if not selected:
        raise ValueError("no selected model groups")
    if args.package and len(selected) != 1:
        raise ValueError("--package override requires exactly one --group")
    if not args.texconv.is_file():
        raise FileNotFoundError(args.texconv)

    all_texture_rows = []
    for group in selected:
        all_texture_rows.extend(event_texture_rows(capture_root / "batch_export", group["events"]))
    matches = None
    if not args.package:
        if args.fhm_root is None:
            raise ValueError("--fhm-root is required without --package")
        matches_path = run_runtime_match(
            all_texture_rows, args.fhm_root.resolve(), output_root / "runtime_match", args.workers
        )
        matches = read_csv(matches_path)

    reports = []
    for group in selected:
        texture_rows = event_texture_rows(capture_root / "batch_export", group["events"])
        if args.package:
            package = args.package.resolve()
            evidence = {"override": True, "coverage": None}
        else:
            package, evidence = choose_package(texture_rows, matches or [])
            if evidence["coverage"] < args.min_package_coverage:
                raise ValueError(
                    "{} package coverage {:.1%} is below required {:.1%}".format(
                        group["name"], evidence["coverage"], args.min_package_coverage
                    )
                )
        reports.append(build_group(
            group, capture_root, output_root, package, evidence, args.texconv.resolve()
        ))

    summary = {
        "schema": "exvs-automated-model-pipeline/v1",
        "capture_root": str(capture_root),
        "model_count": len(reports),
        "models": reports,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "pipeline_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for report in reports:
        print("built {} obj={} blender={}".format(
            report["model_name"], report["obj_path"], report["blender_script"]
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
