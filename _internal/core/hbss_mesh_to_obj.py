#!/usr/bin/env python3
"""Decode an EXVS HBSS/HSEM mesh into OBJ using its packed vertex streams."""

from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path
from typing import Any


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def finite(values) -> bool:
    return all(math.isfinite(value) and abs(value) < 1.0e7 for value in values)


def find_index_buffer(
    data: bytes, vertex_count: int, index_count: int, minimum_offset: int
) -> tuple[int, int, list[int]]:
    candidates = []
    for stride, code in ((2, "H"), (4, "I")):
        size = index_count * stride
        if size <= 0 or size > len(data):
            continue
        for offset in range(max(minimum_offset, len(data) - size - 0x400), len(data) - size + 1, stride):
            indices = list(struct.unpack_from("<{}{}".format(index_count, code), data, offset))
            if not indices or max(indices) >= vertex_count:
                continue
            triangles = zip(indices[0::3], indices[1::3], indices[2::3])
            nondegenerate = sum(1 for a, b, c in triangles if a != b and b != c and a != c)
            if nondegenerate < max(1, index_count // 12):
                continue
            trailing = len(data) - (offset + size)
            zero_padding = data[offset + size :].count(0)
            unique_count = len(set(indices))
            candidates.append(
                (-unique_count, -nondegenerate, trailing - zero_padding, trailing, offset, stride, indices)
            )
    if not candidates:
        raise ValueError("could not locate a valid index buffer")
    _, _, _, _, offset, stride, indices = min(candidates, key=lambda item: item[:6])
    return offset, stride, indices


def find_sectioned_index_buffer(
    data: bytes, sections: list[dict[str, Any]], minimum_offset: int
) -> int | None:
    if not sections:
        return None
    total_index_span = max(
        section["index_base_offset"] + section["index_count"] * 2
        for section in sections
    )
    hint = min(u64(data, 0xC0), u64(data, 0xD0))
    search_start = max(minimum_offset, hint - 0x20000)
    search_end = len(data) - total_index_span
    first = sections[0]
    first_format = "<{}H".format(first["index_count"])
    candidates = []
    for offset in range(search_start + (search_start & 1), search_end + 1, 2):
        first_indices = struct.unpack_from(first_format, data, offset + first["index_base_offset"])
        if max(first_indices) >= first["vertex_count"] or len(set(first_indices)) < 3:
            continue
        all_indices = list(first_indices)
        valid = True
        for section in sections[1:]:
            indices = struct.unpack_from(
                "<{}H".format(section["index_count"]),
                data,
                offset + section["index_base_offset"],
            )
            if max(indices) >= section["vertex_count"] or len(set(indices)) < 3:
                valid = False
                break
            all_indices.extend(indices)
        if valid:
            triangles = zip(all_indices[0::3], all_indices[1::3], all_indices[2::3])
            nondegenerate = sum(1 for a, b, c in triangles if a != b and b != c and a != c)
            candidates.append((-len(set(all_indices)), -nondegenerate, abs(offset - hint), offset))
    return min(candidates)[3] if candidates else None


def decode_mesh(data: bytes) -> dict[str, Any]:
    if len(data) < 0x180 or data[:4] != b"HBSS" or data[16:20] != b"HSEM":
        raise ValueError("not an HBSS/HSEM mesh")
    section_count = u32(data, 0x90)
    if not (1 <= section_count <= 1024):
        section_count = 1
    sections = []
    for section_index in range(section_count):
        base = 0x110 + section_index * 0xD0
        vertex_count = u32(data, base)
        index_count = u32(data, base + 4)
        stream_count = u32(data, base + 8)
        if not (0 < vertex_count < 10_000_000 and 0 < index_count < 100_000_000):
            raise ValueError("implausible mesh counts in section {}".format(section_index))
        if not (1 <= stream_count <= 8):
            raise ValueError("unsupported stream count {} in section {}".format(stream_count, section_index))
        stream_base_offsets = [u32(data, base + 0x0C + index * 4) for index in range(stream_count)]
        strides = [u32(data, base + 0x1C + index * 4) for index in range(stream_count)]
        if any(stride <= 0 or stride > 1024 for stride in strides):
            raise ValueError("implausible stream strides {}".format(strides))
        sections.append({
            "section_index": section_index,
            "vertex_count": vertex_count,
            "index_count": index_count,
            "stream_count": stream_count,
            "stream_base_offsets": stream_base_offsets,
            "stream_strides": strides,
            "index_base_offset": u32(data, base + 0x2C),
        })

    stream_count = max(section["stream_count"] for section in sections)
    stream_sizes = []
    for stream_index in range(stream_count):
        stream_sizes.append(max(
            section["stream_base_offsets"][stream_index]
            + section["vertex_count"] * section["stream_strides"][stream_index]
            for section in sections if stream_index < section["stream_count"]
        ))
    def valid_sectioned_indices(candidate: int, section_padding: int = 0) -> bool:
        if candidate < 0:
            return False
        for section_index, section in enumerate(sections):
            offset = (
                candidate + section["index_base_offset"]
                + section_index * section_padding
            )
            size = section["index_count"] * 2
            if offset < 0 or offset + size > len(data):
                return False
            values = struct.unpack_from("<{}H".format(section["index_count"]), data, offset)
            if not values or max(values) >= section["vertex_count"]:
                return False
        return True

    # Observed layouts either point C0 directly at the index data, place a
    # 0x40 table entry per section before it, or expose D0-0x30. Some large
    # multi-section meshes also insert a 0xC0-byte descriptor before every
    # index block after the first; index_base_offset excludes that descriptor.
    index_candidates = [
        u64(data, 0xC0),
        u64(data, 0xC0) + section_count * 0x40,
        u64(data, 0xD0) - 0x30,
    ]
    resolved_layout = next(
        (
            (candidate, section_padding)
            for candidate in index_candidates
            for section_padding in (0, 0xC0)
            if valid_sectioned_indices(candidate, section_padding)
        ),
        None,
    )
    explicit_index_offset, section_index_padding = (
        resolved_layout if resolved_layout is not None else (index_candidates[0], 0)
    )
    if resolved_layout is None:
        located = find_sectioned_index_buffer(data, sections, sum(stream_sizes) + 0x100)
        if located is not None:
            explicit_index_offset = located
    data_offset = explicit_index_offset - sum(stream_sizes)
    if data_offset < 0x100 or explicit_index_offset > len(data):
        first = sections[0]
        explicit_index_offset, _, _ = find_index_buffer(
            data, first["vertex_count"], first["index_count"], sum(stream_sizes)
        )
        data_offset = explicit_index_offset - sum(stream_sizes)
    stream_offsets = []
    cursor = data_offset
    for size in stream_sizes:
        stream_offsets.append(cursor)
        cursor += size

    vertices = []
    normals = []
    uvs = []
    indices = []
    section_summaries = []
    for section_index, section in enumerate(sections):
        vertex_base = len(vertices)
        index_output_start = len(indices)
        for index in range(section["vertex_count"]):
            base0 = (
                stream_offsets[0] + section["stream_base_offsets"][0]
                + index * section["stream_strides"][0]
            )
            position = struct.unpack_from("<3f", data, base0)
            normal = (
                struct.unpack_from("<3f", data, base0 + 12)
                if section["stream_strides"][0] >= 24 else (0.0, 0.0, 1.0)
            )
            if not finite(position) or not finite(normal):
                raise ValueError("non-finite vertex data in section {} at {}".format(section_index, index))
            vertices.append(position)
            normals.append(normal)
            if section["stream_count"] >= 2 and section["stream_strides"][1] >= 8:
                uv_offset = (
                    stream_offsets[1] + section["stream_base_offsets"][1]
                    + index * section["stream_strides"][1]
                )
                uv = struct.unpack_from("<2f", data, uv_offset)
                uvs.append(uv if finite(uv) else (0.0, 0.0))
            else:
                uvs.append((0.0, 0.0))

        index_offset = (
            explicit_index_offset + section["index_base_offset"]
            + section_index * section_index_padding
        )
        index_stride = 2
        if section_index + 1 < len(sections):
            stored_size = sections[section_index + 1]["index_base_offset"] - section["index_base_offset"]
            if stored_size == section["index_count"] * 4:
                index_stride = 4
        code = "H" if index_stride == 2 else "I"
        local_indices = list(struct.unpack_from(
            "<{}{}".format(section["index_count"], code), data, index_offset
        ))
        if local_indices and max(local_indices) >= section["vertex_count"]:
            alternate_stride = 4 if index_stride == 2 else 2
            alternate_code = "I" if alternate_stride == 4 else "H"
            alternate = list(struct.unpack_from(
                "<{}{}".format(section["index_count"], alternate_code), data, index_offset
            ))
            if alternate and max(alternate) < section["vertex_count"]:
                index_stride, local_indices = alternate_stride, alternate
            else:
                raise ValueError("indices exceed vertices in section {}".format(section_index))
        indices.extend(index + vertex_base for index in local_indices)
        section_summaries.append({
            **section,
            "vertex_output_base": vertex_base,
            "index_output_start": index_output_start,
            "index_output_count": len(local_indices),
            "index_stride": index_stride,
            "index_data_offset": index_offset,
            "index_section_padding": section_index_padding,
        })

    vertex_count = len(vertices)
    index_count = len(indices)
    bbox = {
        "min": [min(value[axis] for value in vertices) for axis in range(3)],
        "max": [max(value[axis] for value in vertices) for axis in range(3)],
    }
    return {
        "vertex_count": vertex_count,
        "index_count": index_count,
        "triangle_count": index_count // 3,
        "section_count": section_count,
        "sections": section_summaries,
        "stream_count": stream_count,
        "stream_strides": sections[0]["stream_strides"],
        "stream_sizes": stream_sizes,
        "stream_offsets": stream_offsets,
        "vertex_data_offset": data_offset,
        "index_data_offset": explicit_index_offset,
        "index_stride": section_summaries[0]["index_stride"],
        "bbox": bbox,
        "vertices": vertices,
        "normals": normals,
        "uvs": uvs,
        "indices": indices,
    }


def write_obj(
    path: Path, mesh: dict[str, Any], name: str, materials: list[str] | None = None
) -> None:
    lines = ["# EXVS HBSS mesh", "o {}".format(name)]
    lines.extend("v {:.9g} {:.9g} {:.9g}".format(*value) for value in mesh["vertices"])
    lines.extend("vt {:.9g} {:.9g}".format(value[0], 1.0 - value[1]) for value in mesh["uvs"])
    lines.extend("vn {:.9g} {:.9g} {:.9g}".format(*value) for value in mesh["normals"])
    sections = mesh.get("sections") or [{
        "section_index": 0,
        "index_output_start": 0,
        "index_output_count": len(mesh["indices"]),
    }]
    for section in sections:
        section_index = section["section_index"]
        material = (
            materials[section_index]
            if materials and section_index < len(materials)
            else "section_{:02d}".format(section_index)
        )
        lines.extend(("g section_{:02d}".format(section_index), "usemtl {}".format(material)))
        start = section["index_output_start"]
        end = start + section["index_output_count"]
        for offset in range(start, end - 2, 3):
            face = [mesh["indices"][offset + part] + 1 for part in range(3)]
            lines.append("f " + " ".join("{0}/{0}/{0}".format(index) for index in face))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mesh", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.mesh.resolve()
    output = (args.output or source.with_suffix(".obj")).resolve()
    mesh = decode_mesh(source.read_bytes())
    write_obj(output, mesh, source.parent.name)
    summary = {key: value for key, value in mesh.items() if key not in {
        "vertices", "normals", "uvs", "indices"
    }}
    summary["source"] = str(source)
    summary["output"] = str(output)
    output.with_suffix(".json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
