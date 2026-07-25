#!/usr/bin/env python3
import struct
import zlib
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SRGB_GAMMA = 45455


def _chunk(chunk_type, payload):
    checksum = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", checksum)
    )


def png_color_metadata(path):
    path = Path(path)
    with path.open("rb") as stream:
        if stream.read(8) != PNG_SIGNATURE:
            raise ValueError(f"not a PNG file: {path}")
        gamma = None
        has_srgb = False
        while True:
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError(f"truncated PNG file: {path}")
            length, chunk_type = struct.unpack(">I4s", header)
            payload = stream.read(length)
            checksum = stream.read(4)
            if len(payload) != length or len(checksum) != 4:
                raise ValueError(f"truncated PNG chunk: {path}")
            if chunk_type == b"gAMA" and length == 4:
                gamma = struct.unpack(">I", payload)[0]
            elif chunk_type == b"sRGB":
                has_srgb = True
            if chunk_type in {b"IDAT", b"IEND"}:
                break
    return {"gamma": gamma, "has_srgb": has_srgb}


def retag_png_srgb(path):
    """Mark raw texture RGB values as sRGB without changing any pixel bytes."""
    path = Path(path)
    with path.open("r+b") as stream:
        if stream.read(8) != PNG_SIGNATURE:
            raise ValueError(f"not a PNG file: {path}")
        gamma_position = None
        gamma_value = None
        insert_position = None
        has_srgb = False
        while True:
            position = stream.tell()
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError(f"truncated PNG file: {path}")
            length, chunk_type = struct.unpack(">I4s", header)
            payload = stream.read(length)
            checksum = stream.read(4)
            if len(payload) != length or len(checksum) != 4:
                raise ValueError(f"truncated PNG chunk: {path}")
            if chunk_type == b"IHDR":
                insert_position = stream.tell()
            elif chunk_type == b"gAMA" and length == 4:
                gamma_position = position
                gamma_value = struct.unpack(">I", payload)[0]
            elif chunk_type == b"sRGB":
                has_srgb = True
            if chunk_type in {b"IDAT", b"IEND"}:
                break

    if gamma_position is not None:
        if gamma_value == SRGB_GAMMA or has_srgb:
            return "already_srgb"
        payload = struct.pack(">I", SRGB_GAMMA)
        checksum = struct.pack(
            ">I", zlib.crc32(b"gAMA" + payload) & 0xFFFFFFFF
        )
        with path.open("r+b") as stream:
            stream.seek(gamma_position + 8)
            stream.write(payload + checksum)
        return "gamma_updated"

    if has_srgb:
        return "already_srgb"
    if insert_position is None:
        raise ValueError(f"PNG is missing IHDR: {path}")
    data = path.read_bytes()
    gamma_chunk = _chunk(b"gAMA", struct.pack(">I", SRGB_GAMMA))
    path.write_bytes(
        data[:insert_position] + gamma_chunk + data[insert_position:]
    )
    return "gamma_inserted"
