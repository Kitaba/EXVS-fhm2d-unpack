#!/usr/bin/env python3
"""Extract the first named SKEL/MESH/MODL triplet from an FHM2D HBSS bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    from .fhm2d_unpack import iter_deflate_blocks
except ImportError:
    from fhm2d_unpack import iter_deflate_blocks


INNER_TYPES = {b"LEKS": "skeleton", b"HSEM": "mesh", b"LDOM": "model"}


def resource_groups(blocks: list[tuple[int, bytes]]) -> dict[str, list[dict[str, Any]]]:
    groups = {name: [] for name in INNER_TYPES.values()}
    current = None
    for block_index, data in blocks:
        is_hbss = data[:4] == b"HBSS" and len(data) >= 20
        if is_hbss:
            inner = data[16:20]
            kind = INNER_TYPES.get(inner)
            current = None
            if kind:
                current = {
                    "kind": kind,
                    "start_block": block_index,
                    "block_indices": [block_index],
                    "data": bytearray(data),
                    "continues": kind == "mesh" and len(data) == 65536,
                }
                groups[kind].append(current)
            continue
        if current is not None and current["kind"] == "mesh" and current["continues"]:
            current["block_indices"].append(block_index)
            current["data"].extend(data)
            current["continues"] = len(data) == 65536
    return groups


def printable_strings(data: bytes) -> list[str]:
    return [value.decode("ascii") for value in re.findall(rb"[ -~]{4,}", data)]


def model_name(model_data: bytes, ordinal: int) -> str:
    strings = printable_strings(model_data)
    numdlx = next((value for value in strings if ".numdlx" in value), "")
    if numdlx:
        return Path(numdlx.lstrip("\\/")).stem
    asset_name = next(
        (
            value for value in strings
            if value not in {"HBSS@", "LDOM"}
            and not value.startswith(("nusubf/", "./nusubf/"))
            and "_" in value
        ),
        "",
    )
    if asset_name:
        return Path(asset_name.lstrip("\\/")).stem
    return "model_{:03d}".format(ordinal)


def model_materials(model_data: bytes) -> list[str]:
    """Return MODL material names in mesh-section order."""
    return [value for value in printable_strings(model_data) if value.endswith("Mtl")]


def select_triplet(groups: dict[str, list[dict[str, Any]]], contains: str) -> dict[str, Any]:
    counts = {kind: len(values) for kind, values in groups.items()}
    if len(set(counts.values())) != 1 or not counts.get("model"):
        raise ValueError("unbalanced HBSS resources: {}".format(counts))
    needle = contains.lower()
    for ordinal, model in enumerate(groups["model"]):
        name = model_name(bytes(model["data"]), ordinal)
        if not needle or needle in name.lower():
            return {
                "ordinal": ordinal,
                "name": name,
                "skeleton": groups["skeleton"][ordinal],
                "mesh": groups["mesh"][ordinal],
                "model": model,
            }
    raise ValueError("no model name contains {!r}".format(contains))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--name-contains", default="")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    package = args.package.resolve()
    blocks = []
    for block_index, _, _, data in iter_deflate_blocks(package.read_bytes()):
        if block_index is None:
            break
        blocks.append((block_index, data))
    groups = resource_groups(blocks)
    selected = select_triplet(groups, args.name_contains)
    output = args.output.resolve() / selected["name"]
    output.mkdir(parents=True, exist_ok=True)
    resources = []
    for kind in ("skeleton", "mesh", "model"):
        resource = selected[kind]
        data = bytes(resource["data"])
        filename = "{}.hbss".format(kind)
        (output / filename).write_bytes(data)
        resources.append(
            {
                "kind": kind,
                "file": filename,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "start_block": resource["start_block"],
                "block_indices": resource["block_indices"],
                "inner_magic": data[16:20].decode("ascii"),
                "strings": printable_strings(data)[:64],
            }
        )
    report = {
        "schema": "exvs-hbss-model-triplet/v1",
        "source": str(package),
        "model_count": len(groups["model"]),
        "selected_ordinal": selected["ordinal"],
        "selected_name": selected["name"],
        "resources": resources,
    }
    report_path = output / "model_triplet.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("models={} selected={} output={}".format(
        len(groups["model"]), selected["name"], output
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
