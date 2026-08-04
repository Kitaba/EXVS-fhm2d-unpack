#!/usr/bin/env python3
"""Parse string-level material and texture relationships from HBSS/LTAM."""

from __future__ import annotations

import re
from typing import Any


TEXTURE_SEMANTICS = (
    ("_roughnessandmask", "roughness_mask"),
    ("_ambientocclusion", "ao"),
    ("_basecolor", "base_color"),
    ("_metallic", "metallic"),
    ("_roughness", "roughness"),
    ("_emissive", "emissive"),
    ("_normal", "normal"),
    ("_cubemap", "cubemap"),
)


def printable_strings(data: bytes) -> list[str]:
    return [value.decode("ascii") for value in re.findall(rb"[ -~]{4,}", data)]


def texture_semantic(path: str) -> str:
    lower = path.lower()
    for suffix, semantic in TEXTURE_SEMANTICS:
        if lower.endswith(suffix):
            return semantic
    return "unknown"


def decode_material_table(data: bytes) -> dict[str, Any]:
    if len(data) < 20 or data[:4] != b"HBSS" or data[16:20] != b"LTAM":
        raise ValueError("not an HBSS/LTAM material table")
    strings = printable_strings(data)
    materials = []
    current = None
    for value in strings[2:]:
        if value.endswith("Mtl"):
            current = {"name": value, "textures": [], "shaders": [], "other_strings": []}
            materials.append(current)
        elif current is not None and "textures/" in value.lower():
            current["textures"].append({"path": value, "semantic": texture_semantic(value)})
        elif current is not None and value.lower().startswith(("vsng", "psng")):
            current["shaders"].append(value)
        elif current is not None:
            current["other_strings"].append(value)
    return {"materials": materials, "material_count": len(materials)}


def material_signature(table: dict[str, Any]) -> tuple:
    return tuple(
        (
            material["name"],
            tuple((texture["semantic"], texture["path"].lower()) for texture in material["textures"]),
            tuple(material["shaders"]),
        )
        for material in table["materials"]
    )
