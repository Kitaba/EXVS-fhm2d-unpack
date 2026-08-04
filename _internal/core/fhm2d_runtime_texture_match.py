#!/usr/bin/env python3
"""Match several RenderDoc DDS top mips against all decoded FHM2D payloads."""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import multiprocessing as mp
import os
import time
from pathlib import Path

try:
    from .fhm2d_extract_textures import decode_payload
except ImportError:
    from fhm2d_extract_textures import decode_payload


BLOCK8_FORMATS = {70, 71, 72, 79, 80, 81}  # BC1, BC4
BLOCK16_FORMATS = {
    73, 74, 75,  # BC2
    76, 77, 78,  # BC3
    82, 83, 84,  # BC5
    94, 95, 96,  # BC6H
    97, 98, 99,  # BC7
}
RGBA8_FORMATS = {27, 28, 29}


def parse_dds(path):
    data = path.read_bytes()
    if len(data) < 128 or data[:4] != b"DDS ":
        raise ValueError("{} is not a DDS file".format(path))
    width = int.from_bytes(data[16:20], "little")
    height = int.from_bytes(data[12:16], "little")
    mip_count = max(1, int.from_bytes(data[28:32], "little"))
    fourcc = data[84:88]
    if fourcc == b"DX10":
        if len(data) < 148:
            raise ValueError("{} has a truncated DX10 header".format(path))
        dxgi_format = int.from_bytes(data[128:132], "little")
        pixel_offset = 148
    else:
        legacy_formats = {
            b"DXT1": 71,
            b"DXT3": 74,
            b"DXT5": 77,
            b"ATI1": 80,
            b"BC4U": 80,
            b"ATI2": 83,
            b"BC5U": 83,
        }
        if fourcc not in legacy_formats:
            raise ValueError("{} uses unsupported DDS FourCC {!r}".format(path, fourcc))
        dxgi_format = legacy_formats[fourcc]
        pixel_offset = 128
    if dxgi_format in BLOCK8_FORMATS:
        top_size = ((width + 3) // 4) * ((height + 3) // 4) * 8
        alignment = 8
    elif dxgi_format in BLOCK16_FORMATS:
        top_size = ((width + 3) // 4) * ((height + 3) // 4) * 16
        alignment = 16
    elif dxgi_format in RGBA8_FORMATS:
        top_size = width * height * 4
        alignment = 4
    else:
        raise ValueError("{} uses unsupported DXGI format {}".format(path, dxgi_format))
    pixels = data[pixel_offset : pixel_offset + top_size]
    if len(pixels) != top_size:
        raise ValueError("{} has a truncated top mip".format(path))
    probe_offset, probe = choose_probe(pixels, alignment)
    return {
        "path": str(path.resolve()),
        "name": path.name,
        "width": width,
        "height": height,
        "mip_count": mip_count,
        "dxgi_format": dxgi_format,
        "pixel_size": len(pixels),
        "pixel_sha256": hashlib.sha256(pixels).hexdigest(),
        "probe_offset": probe_offset,
        "probe": probe,
        "pixels": pixels,
    }


def choose_probe(pixels, alignment, probe_size=64, candidates=64):
    if len(pixels) <= probe_size:
        return 0, pixels
    maximum = len(pixels) - probe_size
    offsets = {
        ((index * maximum // max(1, candidates - 1)) // alignment) * alignment
        for index in range(candidates)
    }
    def score(offset):
        chunk = pixels[offset : offset + probe_size]
        return (len(set(chunk)), sum(value != 0 for value in chunk))
    offset = max(offsets, key=score)
    return offset, pixels[offset : offset + probe_size]


def find_exact(payload, target):
    probe = target["probe"]
    search = 0
    while True:
        found = payload.find(probe, search)
        if found < 0:
            return None
        start = found - target["probe_offset"]
        end = start + target["pixel_size"]
        if start >= 0 and end <= len(payload) and payload[start:end] == target["pixels"]:
            return start
        search = found + 1


_TARGETS = []


def init_worker(targets):
    global _TARGETS
    _TARGETS = targets


def scan_one(path_text):
    path = Path(path_text)
    started = time.perf_counter()
    try:
        _, _, payload, _ = decode_payload(path, strict=False)
        matches = []
        for target in _TARGETS:
            offset = find_exact(payload, target)
            if offset is not None:
                matches.append(
                    {
                        "source": str(path),
                        "dds": target["path"],
                        "dds_name": target["name"],
                        "payload_offset": offset,
                        "pixel_size": target["pixel_size"],
                        "pixel_sha256": target["pixel_sha256"],
                    }
                )
        return {"source": str(path), "status": "ok", "matches": matches,
                "seconds": time.perf_counter() - started, "error": ""}
    except Exception as exc:
        return {"source": str(path), "status": "error", "matches": [],
                "seconds": time.perf_counter() - started,
                "error": "{}: {}".format(type(exc).__name__, exc)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dds", nargs="+")
    parser.add_argument("--fhm-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    parser.add_argument("--checkpoint", type=int, default=500)
    args = parser.parse_args()

    paths = []
    for item in args.dds:
        paths.extend(
            [Path(path) for path in sorted(glob.glob(item))]
            if any(char in item for char in "*?")
            else [Path(item)]
        )
    targets_by_hash = {}
    for path in paths:
        target = parse_dds(path.resolve())
        targets_by_hash.setdefault(target["pixel_sha256"], target)
    targets = list(targets_by_hash.values())
    fhm_root = Path(args.fhm_root).resolve()
    files = [fhm_root] if fhm_root.is_file() else sorted(fhm_root.rglob("*.fhm2d"))
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)

    worker_targets = targets
    started = time.perf_counter()
    rows, matches = [], []
    context = mp.get_context("spawn")
    with context.Pool(args.workers, initializer=init_worker, initargs=(worker_targets,)) as pool:
        for completed, result in enumerate(
            pool.imap_unordered(scan_one, [str(path) for path in files], chunksize=1), 1
        ):
            matches.extend(result.pop("matches"))
            rows.append(result)
            if completed % args.checkpoint == 0 or completed == len(files):
                elapsed = time.perf_counter() - started
                print(
                    "progress={}/{} elapsed={:.1f}s matches={} errors={}".format(
                        completed, len(files), elapsed, len(matches),
                        sum(row["status"] == "error" for row in rows),
                    ),
                    flush=True,
                )

    fields = ["source", "dds", "dds_name", "payload_offset", "pixel_size", "pixel_sha256"]
    with (output / "matches.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(matches)
    summary = {
        "schema": "exvs-fhm2d-runtime-texture-match/v1",
        "fhm_root": str(Path(args.fhm_root).resolve()),
        "package_count": len(files),
        "target_count": len(targets),
        "error_count": sum(row["status"] == "error" for row in rows),
        "exact_match_count": len(matches),
        "elapsed_seconds": time.perf_counter() - started,
        "targets": [
            {key: value for key, value in target.items() if key not in {"pixels", "probe"}}
            for target in targets
        ],
        "matches": matches,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: summary[key] for key in (
        "package_count", "target_count", "error_count", "exact_match_count", "elapsed_seconds"
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    mp.freeze_support()
    main()
