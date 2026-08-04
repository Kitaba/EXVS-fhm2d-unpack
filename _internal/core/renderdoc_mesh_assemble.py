#!/usr/bin/env python3
"""Assemble decoded RenderDoc draw meshes in world or anchor-local space."""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path


def transform_point(point, matrix):
    value = (point[0], point[1], point[2], 1.0)
    return tuple(sum(value[i] * matrix[i][j] for i in range(4)) for j in range(3))


def transform_direction(direction, matrix):
    value = tuple(sum(direction[i] * matrix[i][j] for i in range(3)) for j in range(3))
    length = math.sqrt(sum(component * component for component in value))
    return tuple(component / length for component in value) if length else value


def multiply(left, right):
    return [
        [sum(left[row][k] * right[k][column] for k in range(4)) for column in range(4)]
        for row in range(4)
    ]


def inverse_affine_row(matrix):
    a, b, c = matrix[0][:3]
    d, e, f = matrix[1][:3]
    g, h, i = matrix[2][:3]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise ValueError("anchor world matrix is singular")
    inverse3 = [
        [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
        [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
        [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det],
    ]
    translation = matrix[3][:3]
    inverse_translation = [
        -sum(translation[k] * inverse3[k][column] for k in range(3)) for column in range(3)
    ]
    return [
        inverse3[0] + [0.0],
        inverse3[1] + [0.0],
        inverse3[2] + [0.0],
        inverse_translation + [1.0],
    ]


def read_obj(path):
    positions, uvs, normals, faces = [], [], [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "v":
            positions.append(tuple(map(float, fields[1:4])))
        elif fields[0] == "vt":
            uvs.append(tuple(map(float, fields[1:3])))
        elif fields[0] == "vn":
            normals.append(tuple(map(float, fields[1:4])))
        elif fields[0] == "f":
            faces.append([tuple(int(value) for value in item.split("/")) for item in fields[1:]])
    return positions, uvs, normals, faces


def base_color_resource(event):
    for item in event.get("ps_resources", []):
        if item.get("name") == "BaseColorMap" and item.get("resources"):
            return item["resources"][0]
    return "ResourceId::unknown"


def resource_number(resource):
    return resource.rsplit(":", 1)[-1]


def write_assembly(path, draws, target_space, anchor_inverse=None):
    vertex_base = uv_base = normal_base = 0
    minimum = [float("inf")] * 3
    maximum = [float("-inf")] * 3
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("mtllib materials.mtl\n")
        for draw in draws:
            matrix = draw["world_matrix"]
            if target_space == "anchor_local":
                matrix = multiply(matrix, anchor_inverse)
            positions, uvs, normals, faces = read_obj(draw["local_obj"])
            stream.write("o E{}\n".format(draw["event_id"]))
            for value in positions:
                value = transform_point(value, matrix)
                for axis in range(3):
                    minimum[axis] = min(minimum[axis], value[axis])
                    maximum[axis] = max(maximum[axis], value[axis])
                stream.write("v {:.9g} {:.9g} {:.9g}\n".format(*value))
            for value in uvs:
                stream.write("vt {:.9g} {:.9g}\n".format(*value))
            for value in normals:
                stream.write("vn {:.9g} {:.9g} {:.9g}\n".format(*transform_direction(value, matrix)))
            stream.write("usemtl R{}\n".format(resource_number(draw["base_color"])))
            for face in faces:
                converted = []
                for vertex, uv, normal in face:
                    converted.append(
                        "{}/{}/{}".format(vertex + vertex_base, uv + uv_base, normal + normal_base)
                    )
                stream.write("f {}\n".format(" ".join(converted)))
            vertex_base += len(positions)
            uv_base += len(uvs)
            normal_base += len(normals)
    return {"min": minimum, "max": maximum}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", required=True)
    parser.add_argument("--decoded-root", required=True)
    parser.add_argument("--events", nargs="+", type=int, required=True)
    parser.add_argument("--anchor-event", type=int, required=True)
    parser.add_argument("--model-name", default="model")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    batch_root = Path(args.batch_root).resolve()
    decoded_root = Path(args.decoded_root).resolve()
    output = Path(args.output).resolve()
    texture_output = output / "textures"
    texture_output.mkdir(parents=True, exist_ok=True)

    draws = []
    for event_id in args.events:
        event_path = batch_root / "E{}".format(event_id) / "event.json"
        event = json.loads(event_path.read_text(encoding="utf-8"))
        summary_path = decoded_root / "E{}".format(event_id) / "mesh_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        draws.append(
            {
                "event_id": event_id,
                "base_color": base_color_resource(event),
                "world_matrix": summary["world_matrix"],
                "local_obj": decoded_root / "E{}".format(event_id) / "E{}_local.obj".format(event_id),
                "vertex_count": summary["vertex_count"],
                "triangle_count": summary["triangle_count"],
            }
        )

    anchor = next(draw for draw in draws if draw["event_id"] == args.anchor_event)
    anchor_inverse = inverse_affine_row(anchor["world_matrix"])
    model_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in args.model_name)
    world_name = "{}_world.obj".format(model_name)
    anchor_local_name = "{}_anchor_local.obj".format(model_name)
    world_bbox = write_assembly(output / world_name, draws, "world")
    anchor_local_bbox = write_assembly(
        output / anchor_local_name, draws, "anchor_local", anchor_inverse
    )

    material_lines = []
    materials = {}
    for draw in draws:
        resource = draw["base_color"]
        if resource in materials:
            continue
        number = resource_number(resource)
        candidates = list(batch_root.rglob("t00_BaseColorMap_ResourceId__{}_mip0.dds".format(number)))
        texture_name = None
        if candidates:
            destination = texture_output / candidates[0].name
            shutil.copy2(candidates[0], destination)
            texture_name = "textures/{}".format(destination.name)
        materials[resource] = texture_name
        material_lines.extend(
            [
                "newmtl R{}".format(number),
                "Kd 1 1 1",
                *( ["map_Kd {}".format(texture_name)] if texture_name else [] ),
                "",
            ]
        )
    (output / "materials.mtl").write_text("\n".join(material_lines), encoding="utf-8")

    report = {
        "schema": "exvs-renderdoc-assembly/v1",
        "model_name": model_name,
        "events": args.events,
        "anchor_event": args.anchor_event,
        "vertex_count": sum(draw["vertex_count"] for draw in draws),
        "triangle_count": sum(draw["triangle_count"] for draw in draws),
        "material_count": len(materials),
        "world_bbox": world_bbox,
        "anchor_local_bbox": anchor_local_bbox,
        "base_color_resources": materials,
        "outputs": [anchor_local_name, world_name, "materials.mtl"],
    }
    (output / "assembly.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
