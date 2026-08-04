#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import re
import struct
import sys
import zlib
from pathlib import Path


MAGIC = bytes.fromhex("b9b7b2cd")


def printable_magic(data: bytes) -> str:
    head = data[:4]
    if len(head) < 4:
        return ""
    return "".join(chr(c) if 32 <= c < 127 else "." for c in head)


def safe_magic_name(magic: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", magic).strip("_")
    return cleaned or "bin"


def classify_block(data: bytes) -> str:
    magic = data[:4]
    if magic == b"NUS3":
        return "nus3audio"
    if magic == b"KPKP":
        return "kpkp"
    if magic == b"STL1":
        return "stl1"
    if magic == b"DDS ":
        return "dds"
    if magic == b"HBSS":
        return "hbss"
    if magic == b"EFXB":
        return "efxb"
    if magic == b"DIR\x00":
        return "dir"
    return "bin"


def block_extension(kind: str) -> str:
    return {
        "nus3audio": ".nus3audio",
        "dds": ".dds",
        "kpkp": ".kpkp",
        "stl1": ".stl1",
        "hbss": ".hbss",
        "efxb": ".efxb",
        "dir": ".dirbin",
    }.get(kind, ".bin")


def read_header(blob: bytes) -> dict:
    if len(blob) < 0x30:
        raise ValueError("file is shorter than fhm2d header")
    if blob[:4] != MAGIC:
        raise ValueError("bad fhm2d magic")
    return {
        "magic": blob[:4].hex(),
        "unknown_04": struct.unpack_from("<I", blob, 0x04)[0],
        "version": struct.unpack_from("<I", blob, 0x08)[0],
        "unknown_0c": struct.unpack_from("<I", blob, 0x0C)[0],
        "file_size_header": struct.unpack_from("<Q", blob, 0x10)[0],
        "index_uncompressed_size": struct.unpack_from("<Q", blob, 0x18)[0],
        "index_compressed_size": struct.unpack_from("<Q", blob, 0x20)[0],
        "unknown_28": struct.unpack_from("<Q", blob, 0x28)[0],
    }


def iter_deflate_blocks(blob: bytes, start_offset: int = 0x30, chunk_size: int = 64 * 1024):
    offset = start_offset
    index = 0
    while offset < len(blob):
        decomp = zlib.decompressobj(wbits=-15)
        output = []
        position = offset
        try:
            while position < len(blob) and not decomp.eof:
                chunk_end = min(position + chunk_size, len(blob))
                chunk = blob[position:chunk_end]
                output.append(decomp.decompress(chunk))
                if decomp.eof:
                    position = chunk_end - len(decomp.unused_data)
                else:
                    position = chunk_end
        except zlib.error as exc:
            if index == 0:
                raise ValueError(f"deflate decode failed at 0x{offset:x}: {exc}") from exc
            yield None, offset, 0, b""
            break
        if not decomp.eof:
            if index == 0:
                raise ValueError(f"incomplete deflate stream at 0x{offset:x}")
            yield None, offset, 0, b""
            break
        consumed = position - offset
        if consumed <= 0:
            break
        data = b"".join(output)
        yield index, offset, consumed, data
        offset += consumed
        index += 1


def unpack_file(input_path: Path, output_dir: Path, write_blocks: bool = True) -> dict:
    blob = input_path.read_bytes()
    header = read_header(blob)

    output_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir = output_dir / "blocks"
    if write_blocks:
        blocks_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "source": str(input_path),
        "source_name": input_path.name,
        "source_size": len(blob),
        "header": header,
        "blocks": [],
    }

    for block_index, offset, compressed_size, data in iter_deflate_blocks(blob):
        if block_index is None:
            manifest["trailing_offset"] = offset
            manifest["trailing_size"] = len(blob) - offset
            break
        kind = classify_block(data)
        magic = printable_magic(data)
        sha256 = hashlib.sha256(data).hexdigest()
        ext = block_extension(kind)
        block_name = (
            f"block_{block_index:04d}_off_{offset:08X}_"
            f"c{compressed_size}_u{len(data)}_{safe_magic_name(magic)}{ext}"
        )
        block_path = blocks_dir / block_name
        if write_blocks:
            block_path.write_bytes(data)
        manifest["blocks"].append(
            {
                "index": block_index,
                "file_offset": offset,
                "compressed_size": compressed_size,
                "uncompressed_size": len(data),
                "magic": magic,
                "kind": kind,
                "sha256": sha256,
                "output": str(block_path.relative_to(output_dir)) if write_blocks else "",
            }
        )

    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with (output_dir / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "index",
            "file_offset",
            "compressed_size",
            "uncompressed_size",
            "magic",
            "kind",
            "sha256",
            "output",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(manifest["blocks"])

    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract raw-deflate blocks from EXVSIB .fhm2d cache files."
    )
    parser.add_argument("inputs", nargs="+", help="Input .fhm2d file(s)")
    parser.add_argument(
        "-o",
        "--output",
        default="patch/fhm2d_unpacked",
        help="Output root directory. Default: patch/fhm2d_unpacked",
    )
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="Parse blocks and write manifests without exporting block payloads.",
    )
    args = parser.parse_args(argv)

    out_root = Path(args.output)
    for item in args.inputs:
        input_path = Path(item)
        if not input_path.exists():
            print(f"missing input: {input_path}", file=sys.stderr)
            return 2
        target = out_root / input_path.stem
        manifest = unpack_file(input_path, target, write_blocks=not args.manifest_only)
        print(
            f"{input_path} -> {target} "
            f"blocks={len(manifest['blocks'])} size={manifest['source_size']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
