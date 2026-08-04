#!/usr/bin/env python3
"""Build a triangle/UV/material manifest from a RenderDoc mesh CSV export.

The script intentionally uses only the Python standard library.  It accepts the
index-expanded CSV produced by RenderDoc's Mesh Viewer and preserves one row per
draw vertex so UV seams are never lost.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import struct
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dds_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:32]
    if len(data) < 20 or data[:4] != b"DDS ":
        raise ValueError(f"not a DDS file: {path}")
    return struct.unpack_from("<I", data, 16)[0], struct.unpack_from("<I", data, 12)[0]


def number(row: dict[str, str], name: str) -> float:
    return float(row[name].strip())


def integer(row: dict[str, str], name: str) -> int:
    return int(row[name].strip(), 0)


def read_mesh(path: Path) -> tuple[list[str], list[dict[str, float | int]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, skipinitialspace=True)
        fields = [field.strip() for field in (reader.fieldnames or [])]
        required = {"VTX", "IDX", "TEXCOORD.x", "TEXCOORD.y"}
        missing = sorted(required - set(fields))
        if missing:
            raise ValueError(f"missing CSV columns: {', '.join(missing)}")

        if all(name in fields for name in ("POSITION.x", "POSITION.y", "POSITION.z")):
            position = ("POSITION.x", "POSITION.y", "POSITION.z")
            position_space = "vertex-input/model-or-skinned-space"
        elif all(name in fields for name in ("SV_Position.x", "SV_Position.y", "SV_Position.z")):
            position = ("SV_Position.x", "SV_Position.y", "SV_Position.z")
            position_space = "post-VS homogeneous clip-space"
        else:
            position = ()
            position_space = "unavailable"

        rows: list[dict[str, float | int]] = []
        for source in reader:
            row: dict[str, float | int] = {
                "vtx": integer(source, "VTX"),
                "idx": integer(source, "IDX"),
                "u": number(source, "TEXCOORD.x"),
                "v": number(source, "TEXCOORD.y"),
            }
            if position:
                row.update(
                    x=number(source, position[0]),
                    y=number(source, position[1]),
                    z=number(source, position[2]),
                )
                if "SV_Position.w" in fields:
                    row["w"] = number(source, "SV_Position.w")
            rows.append(row)
    if not rows:
        raise ValueError("mesh CSV has no data rows")
    if len(rows) % 3:
        raise ValueError(f"triangle-list row count is not divisible by 3: {len(rows)}")
    return [position_space], rows


def read_hex_cbuffer(path: Path) -> list[list[float]]:
    values: list[list[float]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, skipinitialspace=True)
        for row in reader:
            words = []
            for name in ("data.x", "data.y", "data.z", "data.w"):
                bits = int(row[name].strip(), 16)
                words.append(struct.unpack("<f", struct.pack("<I", bits))[0])
            values.append(words)
    return values


def finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


def write_uv_svg(
    path: Path,
    rows: list[dict[str, float | int]],
    width: int,
    height: int,
    background_name: str | None = None,
) -> None:
    lines = []
    for start in range(0, len(rows), 3):
        points = []
        for row in rows[start : start + 3]:
            x = float(row["u"]) * width
            y = (1.0 - float(row["v"])) * height
            points.append(f"{x:.3f},{y:.3f}")
        lines.append(f'<polygon points="{" ".join(points)}"/>')
    body = "\n".join(lines)
    background = (
        f'<image href="{background_name}" x="0" y="0" width="{width}" height="{height}"/>'
        if background_name
        else '<rect width="100%" height="100%" fill="none"/>'
    )
    path.write_text(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
{background}
<g fill="none" stroke="#00ff66" stroke-width="0.65" stroke-opacity="0.72" vector-effect="non-scaling-stroke">
{body}
</g>
</svg>\n''',
        encoding="utf-8",
    )


def write_obj(path: Path, rows: list[dict[str, float | int]]) -> bool:
    if "x" not in rows[0]:
        return False
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("# RenderDoc index-expanded mesh; coordinate space is recorded in summary.json\n")
        for row in rows:
            stream.write(f'v {row["x"]:.9g} {row["y"]:.9g} {row["z"]:.9g}\n')
        for row in rows:
            stream.write(f'vt {row["u"]:.9g} {1.0 - float(row["v"]):.9g}\n')
        for start in range(1, len(rows) + 1, 3):
            stream.write(f"f {start}/{start} {start + 1}/{start + 1} {start + 2}/{start + 2}\n")
    return True


def build(args: argparse.Namespace) -> dict[str, object]:
    mesh = Path(args.mesh_csv).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    position_info, rows = read_mesh(mesh)
    position_space = position_info[0]

    texture_width, texture_height = args.width, args.height
    texture_path = Path(args.texture).resolve() if args.texture else None
    if texture_path:
        texture_width, texture_height = dds_size(texture_path)

    uv_values = [(float(row["u"]), float(row["v"])) for row in rows]
    out_of_range = sum(1 for u, v in uv_values if u < 0 or u > 1 or v < 0 or v > 1)
    repeated_index_conflicts = 0
    index_values: dict[int, tuple[float, float]] = {}
    for row in rows:
        key = int(row["idx"])
        uv = (float(row["u"]), float(row["v"]))
        if key in index_values and index_values[key] != uv:
            repeated_index_conflicts += 1
        index_values.setdefault(key, uv)

    triangles_path = output / "triangles.csv"
    with triangles_path.open("w", encoding="utf-8-sig", newline="") as stream:
        fields = ["triangle", "corner", "vtx", "idx", "u", "v", "image_x", "image_y", "x", "y", "z", "w"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for ordinal, row in enumerate(rows):
            writer.writerow(
                {
                    "triangle": ordinal // 3,
                    "corner": ordinal % 3,
                    **row,
                    "image_x": float(row["u"]) * texture_width,
                    "image_y": (1.0 - float(row["v"])) * texture_height,
                }
            )

    write_uv_svg(output / "uv_wireframe.svg", rows, texture_width, texture_height)
    combined_svg = None
    if args.background:
        background_source = Path(args.background).resolve()
        background_copy = output / ("texture_preview" + background_source.suffix.lower())
        if background_source != background_copy:
            shutil.copy2(background_source, background_copy)
        combined_svg = output / "uv_on_texture.svg"
        write_uv_svg(combined_svg, rows, texture_width, texture_height, background_copy.name)
    obj_written = write_obj(output / "capture_space_mesh.obj", rows)

    constants: dict[str, object] = {}
    if args.per_view:
        values = read_hex_cbuffer(Path(args.per_view))
        constants["nuPerViewCBuffer"] = {"source": str(Path(args.per_view).resolve()), "float4_rows": values}
    if args.per_world:
        values = read_hex_cbuffer(Path(args.per_world))
        constants["nuPerWorldCBuffer"] = {
            "source": str(Path(args.per_world).resolve()),
            "float4_rows": values,
            "world_matrix_candidate_rows_0_3": values[:4],
            "world_inverse_matrix_candidate_rows_4_7": values[4:8],
        }

    duplicate = None
    if args.compare_csv:
        peer = Path(args.compare_csv).resolve()
        duplicate = {"path": str(peer), "sha256": sha256(peer), "byte_identical": sha256(mesh) == sha256(peer)}

    summary: dict[str, object] = {
        "schema": "exvs-renderdoc-mesh-map/v1",
        "event_id": args.event,
        "source": {"mesh_csv": str(mesh), "sha256": sha256(mesh), "comparison": duplicate},
        "draw": {
            "topology": "triangle-list",
            "expanded_vertex_rows": len(rows),
            "triangles": len(rows) // 3,
            "unique_index_values": len(index_values),
        },
        "position": {
            "space": position_space,
            "obj_written": obj_written,
            "warning": (
                "SV_Position is homogeneous clip-space. It is unsuitable as an editable model-local mesh; "
                "automated raw vertex-buffer export is required for original POSITION."
                if position_space.startswith("post-VS")
                else None
            ),
        },
        "uv": {
            "texture_size": [texture_width, texture_height],
            "min": [min(u for u, _ in uv_values), min(v for _, v in uv_values)],
            "max": [max(u for u, _ in uv_values), max(v for _, v in uv_values)],
            "out_of_0_1_rows": out_of_range,
            "same_index_different_uv_rows": repeated_index_conflicts,
            "image_coordinate_rule": "x=u*width, y=(1-v)*height",
        },
        "texture": ({"path": str(texture_path), "sha256": sha256(texture_path)} if texture_path else None),
        "constant_buffers": constants,
        "outputs": {
            "triangle_map": str(triangles_path),
            "uv_wireframe": str(output / "uv_wireframe.svg"),
            "uv_on_texture": str(combined_svg) if combined_svg else None,
            "capture_space_obj": str(output / "capture_space_mesh.obj") if obj_written else None,
        },
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesh-csv", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--texture", help="Optional DDS used to obtain atlas dimensions")
    parser.add_argument("--background", help="Optional PNG preview copied beside a combined SVG")
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--event", type=int)
    parser.add_argument("--compare-csv", help="Optional peer export used to detect accidental duplicate stages")
    parser.add_argument("--per-view")
    parser.add_argument("--per-world")
    args = parser.parse_args()
    result = build(args)
    print(json.dumps({"event_id": result["event_id"], "draw": result["draw"], "position": result["position"], "uv": result["uv"], "outputs": result["outputs"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
