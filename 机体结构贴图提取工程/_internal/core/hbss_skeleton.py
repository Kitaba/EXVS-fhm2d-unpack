#!/usr/bin/env python3
"""Read the stable hierarchy portion of an EXVS HBSS/LEKS skeleton."""

from __future__ import annotations

import re
import struct
from typing import Any


def decode_skeleton(data: bytes) -> dict[str, Any]:
    if len(data) < 0x70 or data[:4] != b"HBSS" or data[16:20] != b"LEKS":
        raise ValueError("not an HBSS/LEKS skeleton")
    count = struct.unpack_from("<I", data, 0x20)[0]
    if not 1 <= count <= 4096:
        raise ValueError("implausible bone count {}".format(count))

    records = []
    for index in range(count):
        offset = 0x70 + index * 0x10
        if offset + 8 > len(data):
            raise ValueError("truncated bone records")
        bone_index, parent_index, enabled = struct.unpack_from("<HhI", data, offset)
        records.append({
            "index": bone_index,
            "parent_index": parent_index,
            "enabled": bool(enabled),
        })

    # Names are stored in the same metadata region and retain hierarchy order.
    first_payload = min(
        (struct.unpack_from("<Q", data, offset)[0] for offset in (0x28, 0x38, 0x48, 0x58)),
        default=len(data),
    )
    name_region = data[0x70:min(len(data), first_payload + 0x10)]
    names = [
        match.decode("ascii")
        for match in re.findall(rb"[A-Z][A-Z0-9_]{2,}", name_region)
        if match not in {b"HBSS", b"LEKS"}
    ]
    if len(names) < count:
        names.extend("BONE_{:03d}".format(index) for index in range(len(names), count))
    names = names[:count]
    for index, record in enumerate(records):
        record["name"] = names[index]
        parent = record["parent_index"]
        record["parent_name"] = names[parent] if 0 <= parent < count else None

    # The first LEKS transform array is the model-space bind/global matrix.
    # Its table pointer is relative to the LEKS payload; observed files place
    # the first 4x4 row-major matrix 0x28 bytes after that pointer.
    matrix_pointer = struct.unpack_from("<Q", data, 0x28)[0]
    matrix_start = matrix_pointer + 0x28
    matrix_end = matrix_start + count * 0x40
    if matrix_pointer and matrix_start >= 0x70 and matrix_end <= len(data):
        for index, record in enumerate(records):
            values = struct.unpack_from("<16f", data, matrix_start + index * 0x40)
            if all(abs(value) < 1.0e8 for value in values):
                record["bind_matrix_row_major"] = [list(values[row * 4:(row + 1) * 4]) for row in range(4)]
                record["bind_translation"] = [values[12], values[13], values[14]]
    return {
        "bone_count": count,
        "bones": records,
        "bind_matrix_pointer": matrix_pointer,
        "bind_matrix_start": matrix_start,
    }
