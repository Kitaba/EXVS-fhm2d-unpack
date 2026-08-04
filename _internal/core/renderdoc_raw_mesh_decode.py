#!/usr/bin/env python3
"""Decode a RenderDoc batch-export event into local/world OBJ meshes."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import struct
from pathlib import Path


def transform_row_vector(point, matrix):
    vector = (point[0], point[1], point[2], 1.0)
    return tuple(sum(vector[i] * matrix[i][j] for i in range(4)) for j in range(3))


def component_stats(blob: bytes, stride: int):
    count = len(blob) // stride
    components = stride // 4
    columns = [[] for _ in range(components)]
    for index in range(count):
        values = struct.unpack_from("<{}f".format(components), blob, index * stride)
        for column, value in zip(columns, values):
            if math.isfinite(value):
                column.append(value)
    return [
        {
            "finite": len(values),
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "nonzero": sum(value != 0.0 for value in values),
        }
        for values in columns
    ]


def write_obj(path, positions, normals, uvs, indices, material_name, object_name, world_matrix=None):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("mtllib material.mtl\n")
        stream.write("o {}\n".format(object_name))
        for position in positions:
            value = transform_row_vector(position, world_matrix) if world_matrix else position
            stream.write("v {:.9g} {:.9g} {:.9g}\n".format(*value))
        for u, v in uvs:
            stream.write("vt {:.9g} {:.9g}\n".format(u, 1.0 - v))
        for normal in normals:
            if world_matrix:
                value = tuple(sum(normal[i] * world_matrix[i][j] for i in range(3)) for j in range(3))
                length = math.sqrt(sum(component * component for component in value))
                normal = tuple(component / length for component in value) if length else value
            stream.write("vn {:.9g} {:.9g} {:.9g}\n".format(*normal))
        stream.write("usemtl {}\n".format(material_name))
        for start in range(0, len(indices), 3):
            face = []
            for index in indices[start : start + 3]:
                obj_index = index + 1
                face.append("{0}/{0}/{0}".format(obj_index))
            stream.write("f {}\n".format(" ".join(face)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event_json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--texture-preview")
    args = parser.parse_args()

    event_path = Path(args.event_json).resolve()
    event_dir = event_path.parent
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    event = json.loads(event_path.read_text(encoding="utf-8"))

    base_color = next(
        (
            item["resources"][0]
            for item in event.get("ps_resources", [])
            if item.get("name") == "BaseColorMap" and item.get("resources")
        ),
        "ResourceId::unknown",
    )
    material_name = "R{}".format(base_color.rsplit(":", 1)[-1])

    vertex_input = event["vertex_input"]
    buffers = {item["slot"]: item for item in vertex_input["vertex_buffers"]}
    required = {0, 1}
    if not required.issubset(buffers):
        raise ValueError("event is missing vertex buffer slot 0 or 1")

    blobs = {}
    for slot, item in buffers.items():
        path = Path(item["export"]["path"])
        blobs[slot] = path.read_bytes()
        if len(blobs[slot]) % item["byte_stride"]:
            raise ValueError("VB slot {} length is not divisible by stride".format(slot))

    vertex_counts = {slot: len(blobs[slot]) // item["byte_stride"] for slot, item in buffers.items()}
    # D3D12 bindings expose the remaining allocation from each binding offset.
    # Separate POSITION and TEXCOORD allocations can therefore have different
    # tail capacities even though every index used by this draw exists in both.
    source_vertex_count = vertex_counts[0]
    vertex_count = min(vertex_counts[0], vertex_counts[1])

    positions = []
    normals = []
    uvs = []
    stride0 = buffers[0]["byte_stride"]
    stride1 = buffers[1]["byte_stride"]
    for index in range(vertex_count):
        positions.append(struct.unpack_from("<3f", blobs[0], index * stride0))
        normals.append(struct.unpack_from("<3f", blobs[0], index * stride0 + 12))
        uvs.append(struct.unpack_from("<2f", blobs[1], index * stride1))

    index_item = vertex_input["index_buffer"]
    index_blob = Path(index_item["export"]["path"]).read_bytes()
    index_stride = int(index_item["byte_stride"])
    if index_stride == 2:
        indices = struct.unpack("<{}H".format(len(index_blob) // 2), index_blob)
    elif index_stride == 4:
        indices = struct.unpack("<{}I".format(len(index_blob) // 4), index_blob)
    else:
        raise ValueError("unsupported index stride: {}".format(index_stride))
    if len(indices) % 3:
        raise ValueError("triangle-list index count is not divisible by 3")
    if max(indices) >= vertex_count:
        raise ValueError("index {} exceeds vertex count {}".format(max(indices), vertex_count))

    used_source_indices = sorted(set(indices))
    remap = {source_index: new_index for new_index, source_index in enumerate(used_source_indices)}
    positions = [positions[index] for index in used_source_indices]
    normals = [normals[index] for index in used_source_indices]
    uvs = [uvs[index] for index in used_source_indices]
    indices = tuple(remap[index] for index in indices)
    vertex_count = len(used_source_indices)

    world_buffer = next(item for item in event["constant_buffers"] if item["name"] == "nuPerWorldCBuffer")
    world_blob = Path(world_buffer["export"]["path"]).read_bytes()
    world_values = struct.unpack_from("<16f", world_blob, 0)
    world_matrix = [list(world_values[row * 4 : row * 4 + 4]) for row in range(4)]

    texture_name = None
    if args.texture_preview:
        texture_source = Path(args.texture_preview).resolve()
        texture_copy = output / texture_source.name
        if texture_source != texture_copy:
            shutil.copy2(texture_source, texture_copy)
        texture_name = texture_copy.name

    (output / "material.mtl").write_text(
        "newmtl {}\nKd 1 1 1\n".format(material_name)
        + ("map_Kd {}\n".format(texture_name) if texture_name else ""),
        encoding="utf-8",
    )
    object_name = "E{}".format(event["event_id"])
    local_obj = output / "{}_local.obj".format(object_name)
    world_obj = output / "{}_world.obj".format(object_name)
    write_obj(local_obj, positions, normals, uvs, indices, material_name, object_name)
    write_obj(world_obj, positions, normals, uvs, indices, material_name, object_name, world_matrix)

    with (output / "vertices.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["index", "x", "y", "z", "nx", "ny", "nz", "u", "v"])
        for index, (source_index, position, normal, uv) in enumerate(zip(used_source_indices, positions, normals, uvs)):
            writer.writerow([source_index, *position, *normal, *uv])

    local_min = [min(point[axis] for point in positions) for axis in range(3)]
    local_max = [max(point[axis] for point in positions) for axis in range(3)]
    world_positions = [transform_row_vector(point, world_matrix) for point in positions]
    world_min = [min(point[axis] for point in world_positions) for axis in range(3)]
    world_max = [max(point[axis] for point in world_positions) for axis in range(3)]
    summary = {
        "schema": "exvs-renderdoc-raw-mesh/v1",
        "event_id": event["event_id"],
        "vertex_count": vertex_count,
        "bound_vertex_count": source_vertex_count,
        "common_bound_vertex_count": min(vertex_counts[0], vertex_counts[1]),
        "unused_bound_vertices": source_vertex_count - vertex_count,
        "vertex_counts_by_slot": vertex_counts,
        "index_count": len(indices),
        "triangle_count": len(indices) // 3,
        "source_index_min": min(used_source_indices),
        "source_index_max": max(used_source_indices),
        "index_min": min(indices),
        "index_max": max(indices),
        "unique_indices": len(set(indices)),
        "local_bbox": {"min": local_min, "max": local_max},
        "world_bbox": {"min": world_min, "max": world_max},
        "uv_bbox": {
            "min": [min(uv[0] for uv in uvs), min(uv[1] for uv in uvs)],
            "max": [max(uv[0] for uv in uvs), max(uv[1] for uv in uvs)],
        },
        "world_matrix": world_matrix,
        "stream_float_component_stats": {
            str(slot): component_stats(blobs[slot], buffers[slot]["byte_stride"])
            for slot in sorted(blobs)
        },
        "outputs": {
            "local_obj": str(local_obj),
            "world_obj": str(world_obj),
            "vertices_csv": str(output / "vertices.csv"),
        },
    }
    (output / "mesh_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("vertex_count", "index_count", "triangle_count", "index_min", "index_max", "unique_indices", "local_bbox", "world_bbox", "uv_bbox")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
