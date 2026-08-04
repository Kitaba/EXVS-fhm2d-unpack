#!/usr/bin/env python3
"""Build a multi-part Blender project manifest from a decoded HBSS bundle."""

from __future__ import annotations

import argparse
import csv
import json
import re
import struct
from pathlib import Path

try:
    from .hbss_skeleton import decode_skeleton
except ImportError:
    from hbss_skeleton import decode_skeleton


TEXTURE_SUFFIXES = {
    "basecolor": "base_color", "normal": "normal", "metallic": "metallic",
    "roughness": "roughness", "ambientocclusion": "ao", "emissive": "emissive",
}


def discover_texture_sets(texture_project: Path) -> dict[str, list[dict]]:
    """Inventory texture families without assuming pbr count or channel count."""
    sets = {}
    with (texture_project / "textures.csv").open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            embedded = row["embedded_name"]
            for suffix, semantic in TEXTURE_SUFFIXES.items():
                marker = "_" + suffix
                if embedded.lower().endswith(marker):
                    prefix = embedded[:-len(marker)]
                    item = sets.setdefault(prefix, {"prefix": prefix, "channels": {}})
                    item["channels"][semantic] = {
                        "embedded_name": embedded,
                        "width": int(row["width"]), "height": int(row["height"]),
                        "storage_format": row["storage_format"],
                    }
                    break
    by_label = {}
    for item in sets.values():
        label = item["prefix"].rsplit("_", 1)[-1].lower()
        by_label.setdefault(label, []).append(item)
    return by_label


def classify_part(name: str) -> dict:
    lower = name.lower()
    hand = re.search(r"_hand([lr])_([a-z]+)\d+$", lower)
    if hand:
        side, state = hand.groups()
        return {
            "kind": "hand",
            "side": "left" if side == "l" else "right",
            "state": state,
            "collection": "Hands/{}/{}".format(state.upper(), "Left" if side == "l" else "Right"),
            "attachment_bone": "TE_L" if side == "l" else "TE_R",
            "default_visible": state == "ngr",
        }
    if "form" in lower:
        return {
            "kind": "standalone", "state": Path(name).stem,
            "collection": "Standalone/Forms", "default_visible": False,
        }
    if "body_" in lower:
        return {"kind": "body", "collection": "Base", "default_visible": True}
    if "_wep_wing" in lower:
        return {
            "kind": "body_attachment", "collection": "Base/Attachments",
            "attachment_bone": "ATH_WING", "default_visible": True,
        }
    return {"kind": "standalone", "collection": "Standalone/Components", "default_visible": False}


def build_project(
    report_path: Path, texture_project: Path | None, output: Path, runtime_script: Path,
    material_relations_path: Path | None = None,
) -> Path:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    material_relations = (
        json.loads(material_relations_path.read_text(encoding="utf-8"))
        if material_relations_path and material_relations_path.exists() else None
    )
    relations_by_model = {
        model["name"]: model for model in (material_relations or {}).get("models", [])
    }
    parts = []
    all_material_slots = set()
    for model in report["models"]:
        if model.get("status") != "ok":
            continue
        directory = Path(model["directory"])
        skeleton_path = directory / model["resources"]["skeleton"]["file"]
        skeleton_data = skeleton_path.read_bytes()
        try:
            hierarchy = decode_skeleton(skeleton_data)
            hierarchy["status"] = "ok"
        except (ValueError, IndexError, struct.error) as exc:
            # Some packages use a skeleton/control resource that is not the
            # currently understood HBSS/LEKS layout.  The mesh remains useful
            # and can be imported without an armature, so preserve diagnostics
            # in the project instead of rejecting the complete package.
            hierarchy = {
                "status": "unsupported",
                "error": str(exc),
                "size": len(skeleton_data),
                "outer_magic": skeleton_data[:4].decode("ascii", errors="replace"),
                "inner_magic": skeleton_data[0x10:0x14].decode("ascii", errors="replace"),
                "bone_count": 0,
                "bones": [],
            }
        part = {
            "ordinal": model["ordinal"],
            "name": model["name"],
            "obj_path": model["obj"],
            "materials": model.get("materials", []),
            "skeleton_path": str(skeleton_path),
            "skeleton": hierarchy,
            "section_bindings": relations_by_model.get(model["name"], {}).get("sections", []),
            **classify_part(model["name"]),
        }
        all_material_slots.update(part["materials"])
        parts.append(part)

    anchor = next((part for part in parts if part["kind"] == "body" and part["skeleton"].get("status") == "ok"), None)
    anchor_bones = {
        bone["name"]: bone for bone in (anchor or {}).get("skeleton", {}).get("bones", [])
    }
    unresolved_placements = []
    for part in parts:
        bone_name = part.get("attachment_bone")
        if part["kind"] == "body":
            part["placement"] = {"mode": "model_space", "resolved": True}
        elif bone_name and bone_name in anchor_bones and "bind_matrix_row_major" in anchor_bones[bone_name]:
            part["placement"] = {
                "mode": "bone_bind",
                "resolved": True,
                "anchor_part": anchor["name"],
                "bone": bone_name,
                "matrix_row_major": anchor_bones[bone_name]["bind_matrix_row_major"],
                "confidence": "name_rule+leks_bind",
            }
        elif part["kind"] == "standalone":
            part["placement"] = {
                "mode": "standalone_local",
                "resolved": True,
                "included_in_assembly": False,
            }
        else:
            part["placement"] = {
                "mode": "local_unresolved",
                "resolved": False,
                "bone": bone_name,
            }
            unresolved_placements.append({"part": part["name"], "bone": bone_name})

    texture_sets = discover_texture_sets(texture_project) if texture_project else {}
    material_settings = {}
    unresolved = {}
    for material_name in sorted(all_material_slots):
        label = re.sub(r"(?:exb)?Mtl$", "", material_name, flags=re.IGNORECASE).lower()
        candidates = texture_sets.get(label, [])
        is_placeholder = material_name.lower().startswith("section_")
        if texture_project and len(candidates) != 1:
            unresolved[material_name] = [item["prefix"] for item in candidates]
        candidate = candidates[0] if len(candidates) == 1 else None
        if candidate and "base_color" not in candidate["channels"]:
            unresolved[material_name] = [candidate["prefix"] + " (missing basecolor)"]
            candidate = None
        preferred = (material_relations or {}).get("preferred_materials", {}).get(material_name, {})
        logical_textures = {
            item["semantic"]: item["path"] for item in preferred.get("textures", [])
        }
        material_settings[material_name] = {
            "label": label,
            "material_name": material_name,
            "texture_prefix": candidate["prefix"] if candidate else None,
            "available_channels": sorted(candidate["channels"]) if candidate else [],
            "logical_textures": logical_textures,
            "shader": preferred.get("shaders", []),
            "has_extended_binding": material_name.lower().endswith("exbmtl"),
            "emissive_scale": 9.0 if label == "emi" or (
                material_name.lower().endswith("exbmtl") and "emissive" in logical_textures
            ) else 0.0,
            "shadow_receiver": label != "emi",
            "placeholder": is_placeholder,
            "texture_binding_complete": candidate is not None or texture_project is None,
        }
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "exvs-hbss-blender-project/v1",
        "model_name": Path(report["source"]).stem,
        "source_package": report["source"],
        "bundle_report": str(report_path.resolve()),
        "material_relations": str(material_relations_path.resolve()) if material_relations_path else None,
        "texture_project_dir": str(texture_project.resolve()) if texture_project else None,
        "material_runtime": str((runtime_script.resolve().with_name("exvs_blender_import.py"))),
        "output_blend": str((output / (Path(report["source"]).stem + "_assembled.blend")).resolve()),
        "materials": material_settings,
        "unresolved_material_textures": unresolved,
        "texture_inventory": {
            label: [item["prefix"] for item in items]
            for label, items in sorted(texture_sets.items())
        },
        "parts": parts,
        "assembly": {
            "anchor_part": anchor["name"] if anchor else None,
            "resolved_parts": sum(
                1 for part in parts
                if part["placement"]["resolved"]
                and part["placement"].get("included_in_assembly", True)
            ),
            "standalone_parts": sum(1 for part in parts if part["kind"] == "standalone"),
            "unresolved_parts": unresolved_placements,
        },
        "pack_resources": True,
        "save_blend": True,
        "invert_normal_green": True,
    }
    manifest_path = output / "blender_project.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    runner = output / "run_in_blender.py"
    runner.write_text(
        "EXVS_HBSS_PROJECT = r{!r}\n"
        "EXVS_PART_FILTER = []\n"
        "EXVS_OUTPUT_BLEND = None\n"
        "exec(open(r{!r}, encoding='utf-8').read())\n".format(
            str(manifest_path.resolve()), str(runtime_script.resolve())
        ),
        encoding="utf-8",
    )
    package_dir = report_path.parent.parent
    package_runner = package_dir / "import_complete_to_blender.py"
    package_runner.write_text(
        "# Run this file in Blender 4.5 Scripting workspace.\n"
        "EXVS_HBSS_PROJECT = r{!r}\n"
        "EXVS_PART_FILTER = []\n"
        "EXVS_OUTPUT_BLEND = None\n"
        "exec(open(r{!r}, encoding='utf-8').read())\n".format(
            str(manifest_path.resolve()), str(runtime_script.resolve())
        ),
        encoding="utf-8",
    )
    for part in parts:
        part_dir = Path(next(
            model["directory"] for model in report["models"]
            if model.get("status") == "ok" and model["name"] == part["name"]
        ))
        part_runner = part_dir / "import_this_structure_to_blender.py"
        part_runner.write_text(
            "# Run this file in Blender 4.5 Scripting workspace.\n"
            "EXVS_HBSS_PROJECT = r{!r}\n"
            "EXVS_PART_FILTER = [{!r}]\n"
            "EXVS_OUTPUT_BLEND = r{!r}\n"
            "exec(open(r{!r}, encoding='utf-8').read())\n".format(
                str(manifest_path.resolve()), part["name"],
                str((part_dir / (part["name"] + ".blend")).resolve()),
                str(runtime_script.resolve())
            ),
            encoding="utf-8",
        )
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--texture-project", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--material-relations", type=Path)
    parser.add_argument(
        "--blender-runtime",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "blender" / "exvs_hbss_import.py",
    )
    args = parser.parse_args()
    manifest = build_project(
        args.report.resolve(), args.texture_project.resolve() if args.texture_project else None,
        args.output.resolve(), args.blender_runtime.resolve(),
        args.material_relations.resolve() if args.material_relations else None,
    )
    print("EXVS HBSS Blender project: {}".format(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
