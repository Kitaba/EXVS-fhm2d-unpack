from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


PBR_SEMANTICS = (
    "BaseColorMap",
    "NormalMap",
    "MetallicMap",
    "RoughnessMap",
    "AmbientOcclusionMap",
    "EmissiveMap",
)

KNOWN_MATERIALS = {
    "ResourceId::134920": "emi",
    "ResourceId::134898": "pbr1",
    "ResourceId::134923": "pbr2",
    "ResourceId::134997": "pbr3",
}

MATERIAL_CBUFFERS = {
    "nuUVTransformCBuffer",
    "UpdatePerObject",
    "vsngCharaGBufferControl",
}


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_value(value: Any) -> Any:
    """Remove qrenderdoc SWIG pointer text before state comparison."""
    if isinstance(value, dict):
        return {
            key: stable_value(item)
            for key, item in value.items()
            if key != "text"
        }
    if isinstance(value, list):
        return [stable_value(item) for item in value]
    if isinstance(value, str) and "Swig Object" in value and " at 0x" in value:
        return "<Swig Object>"
    return value


def first_resource(binding: dict[str, Any]) -> str | None:
    for descriptor in binding.get("descriptors", []):
        if not isinstance(descriptor, dict):
            continue
        for field in ("resource", "resourceId"):
            value = descriptor.get(field)
            if value and value != "ResourceId::0" and "Null" not in str(value):
                return str(value)
    return None


def pixel_stage(event: dict[str, Any]) -> dict[str, Any]:
    for stage in event.get("stages", []):
        if str(stage.get("stage", "")).endswith("Pixel"):
            return stage
    return {}


def pbr_bindings(event: dict[str, Any]) -> dict[str, str]:
    rows = pixel_stage(event).get("read_only_resources", [])
    if not isinstance(rows, list):
        return {}
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("name") not in PBR_SEMANTICS:
            continue
        resource = first_resource(row)
        if resource:
            result[str(row["name"])] = resource
    return result


def material_constant_hashes(event: dict[str, Any]) -> dict[str, str]:
    blocks = pixel_stage(event).get("constant_buffers", [])
    if not isinstance(blocks, list):
        return {}
    result: dict[str, str] = {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("name") not in MATERIAL_CBUFFERS:
            continue
        raw = block.get("raw", {})
        digest = raw.get("sha256") if isinstance(raw, dict) else None
        if not digest:
            decoded = block.get("decoded", {})
            digest = canonical_hash(decoded)
        result[str(block["name"])] = str(digest)
    return result


def shader_key(event: dict[str, Any]) -> str | None:
    shader = pixel_stage(event).get("shader")
    if isinstance(shader, dict):
        value = shader.get("resource_id")
        return str(value) if value else None
    return None


def material_signature_payload(event: dict[str, Any]) -> dict[str, Any]:
    pipeline = event.get("pipeline", {})
    stage = pixel_stage(event)
    return {
        "pixel_shader": shader_key(event),
        "textures": pbr_bindings(event),
        "constant_buffers": material_constant_hashes(event),
        "samplers": stable_value(stage.get("samplers", [])),
        "rasterizer": stable_value(pipeline.get("rasterizer")),
        "depth_stencil": stable_value(pipeline.get("depth_stencil")),
        "color_blends": stable_value(pipeline.get("color_blends")),
        "d3d12": stable_value(pipeline.get("d3d12")),
    }


def summarize(root: Path) -> dict[str, Any]:
    manifest_path = root / "render_state_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    event_rows = []

    for entry in manifest.get("events", []):
        if "error" in entry or not entry.get("path"):
            continue
        event_path = root / str(entry["path"])
        event = json.loads(event_path.read_text(encoding="utf-8"))
        payload = material_signature_payload(event)
        signature = canonical_hash(payload)
        base_color = payload["textures"].get("BaseColorMap")
        material_name = KNOWN_MATERIALS.get(base_color, "material_{}".format(base_color or signature[:8]))
        row = {
            "event_id": int(event["event_id"]),
            "material": material_name,
            "signature": signature,
            "textures": payload["textures"],
            "constant_buffers": payload["constant_buffers"],
            "pixel_shader": payload["pixel_shader"],
            "source": str(entry["path"]),
        }
        groups[signature].append(row)
        event_rows.append(row)

    materials = []
    for signature, rows in sorted(groups.items(), key=lambda item: min(row["event_id"] for row in item[1])):
        first = rows[0]
        materials.append(
            {
                "name": first["material"],
                "signature": signature,
                "event_ids": sorted(row["event_id"] for row in rows),
                "textures": first["textures"],
                "constant_buffers": first["constant_buffers"],
                "pixel_shader": first["pixel_shader"],
            }
        )

    return {
        "schema": "exvs-renderdoc-material-signatures/v1",
        "source_manifest": str(manifest_path),
        "event_count": len(event_rows),
        "material_count": len(materials),
        "materials": materials,
        "events": sorted(event_rows, key=lambda row: row["event_id"]),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize EXVS RenderDoc render-state exports")
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path(r"E:\rendercapture\leos_model\render_state"),
        help="directory containing render_state_manifest.json",
    )
    parser.add_argument("--output", type=Path, help="output JSON; defaults to <root>/material_signatures.json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = args.output or args.root / "material_signatures.json"
    report = summarize(args.root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("events={} materials={} output={}".format(report["event_count"], report["material_count"], output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
