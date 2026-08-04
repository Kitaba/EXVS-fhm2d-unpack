#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import multiprocessing as mp
import os
import time
from pathlib import Path

from fhm2d_extract_textures import (
    FHM2D_BC7_FORMAT,
    FHM2D_RGBA8_FORMAT,
    decode_payload,
    scan_textures,
)


def parse_dds(path):
    data = path.read_bytes()
    if len(data) < 128 or data[:4] != b"DDS ":
        raise ValueError("input is not a DDS file")
    if int.from_bytes(data[4:8], "little") != 124:
        raise ValueError("unsupported DDS header")
    height = int.from_bytes(data[12:16], "little")
    width = int.from_bytes(data[16:20], "little")
    mip_count = max(1, int.from_bytes(data[28:32], "little"))
    fourcc = data[84:88]
    if fourcc == b"DX10":
        if len(data) < 148:
            raise ValueError("truncated DX10 DDS header")
        dxgi_format = int.from_bytes(data[128:132], "little")
        pixel_offset = 148
    elif fourcc == b"\0\0\0\0" and int.from_bytes(data[88:92], "little") == 32:
        dxgi_format = 28
        pixel_offset = 128
    else:
        raise ValueError(f"unsupported DDS pixel format: fourcc={fourcc!r}")
    if dxgi_format in {97, 98, 99}:
        storage_format = "bc7"
        fhm2d_format = FHM2D_BC7_FORMAT
        top_size = ((width + 3) // 4) * ((height + 3) // 4) * 16
    elif dxgi_format in {27, 28, 29}:
        storage_format = "rgba8"
        fhm2d_format = FHM2D_RGBA8_FORMAT
        top_size = width * height * 4
    else:
        raise ValueError(f"unsupported DXGI format {dxgi_format}")
    if pixel_offset + top_size > len(data):
        raise ValueError("DDS does not contain a complete top mip")
    pixels = data[pixel_offset : pixel_offset + top_size]
    return {
        "width": width,
        "height": height,
        "mip_count": mip_count,
        "dxgi_format": dxgi_format,
        "storage_format": storage_format,
        "fhm2d_format": fhm2d_format,
        "pixel_size": len(pixels),
        "pixel_sha256": hashlib.sha256(pixels).hexdigest(),
        "pixels": pixels,
    }


_TARGET = None


def init_worker(target):
    global _TARGET
    _TARGET = target


def scan_one(path_text):
    started = time.perf_counter()
    path = Path(path_text)
    result = {
        "source": str(path),
        "name": path.name,
        "file_size": path.stat().st_size,
        "status": "ok",
        "texture_count": 0,
        "dimension_candidates": 0,
        "exact_matches": [],
        "error": "",
        "seconds": 0.0,
    }
    try:
        _, _, payload, _ = decode_payload(path, strict=False)
        textures = scan_textures(
            payload, supported_formats={FHM2D_BC7_FORMAT, FHM2D_RGBA8_FORMAT}
        )
        result["texture_count"] = len(textures)
        for texture in textures:
            if (
                texture["width"] != _TARGET["width"]
                or texture["height"] != _TARGET["height"]
                or texture["fhm2d_format"] != _TARGET["fhm2d_format"]
                or texture["data_size"] != _TARGET["pixel_size"]
            ):
                continue
            result["dimension_candidates"] += 1
            start = texture["payload_data_offset"]
            end = start + texture["data_size"]
            digest = hashlib.sha256(payload[start:end]).hexdigest()
            if digest == _TARGET["pixel_sha256"]:
                result["exact_matches"].append(
                    {
                        "texture_index": texture["texture_index"],
                        "embedded_name": texture["embedded_name"],
                        "group_index": texture["group_index"],
                        "group_label": texture["group_label"],
                        "payload_data_offset": start,
                        "data_size": texture["data_size"],
                        "pixel_sha256": digest,
                    }
                )
    except Exception as exc:
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["seconds"] = time.perf_counter() - started
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Parallel full-library DDS-to-FHM2D exact pixel scanner."
    )
    parser.add_argument("dds")
    parser.add_argument("source")
    parser.add_argument("-o", "--output", default="workspace/dds_parallel_scan")
    parser.add_argument("-j", "--jobs", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    parser.add_argument("--pattern", default="*.fhm2d")
    parser.add_argument("--checkpoint", type=int, default=250)
    args = parser.parse_args(argv)

    dds_path = Path(args.dds)
    source = Path(args.source)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    target = parse_dds(dds_path)
    worker_target = {key: value for key, value in target.items() if key != "pixels"}
    files = sorted(str(path) for path in source.rglob(args.pattern) if path.is_file())
    if not files:
        raise SystemExit(f"no {args.pattern} files below {source}")

    started = time.perf_counter()
    completed = errors = textures = candidates = 0
    matches = []
    package_rows = []
    context = mp.get_context("spawn")
    with context.Pool(args.jobs, initializer=init_worker, initargs=(worker_target,)) as pool:
        for result in pool.imap_unordered(scan_one, files, chunksize=1):
            completed += 1
            errors += result["status"] == "error"
            textures += result["texture_count"]
            candidates += result["dimension_candidates"]
            for match in result.pop("exact_matches"):
                matches.append({"source": result["source"], **match})
            package_rows.append(result)
            if completed % args.checkpoint == 0 or completed == len(files):
                elapsed = time.perf_counter() - started
                print(
                    f"progress={completed}/{len(files)} elapsed={elapsed:.1f}s "
                    f"rate={completed/elapsed:.1f}pkg/s textures={textures} "
                    f"candidates={candidates} matches={len(matches)} errors={errors}",
                    flush=True,
                )

    elapsed = time.perf_counter() - started
    package_rows.sort(key=lambda row: row["source"])
    fields = [
        "source", "name", "file_size", "status", "texture_count",
        "dimension_candidates", "error", "seconds",
    ]
    with (output / "packages.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(package_rows)
    match_fields = [
        "source", "texture_index", "embedded_name", "group_index", "group_label",
        "payload_data_offset", "data_size", "pixel_sha256",
    ]
    with (output / "matches.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=match_fields)
        writer.writeheader()
        writer.writerows(matches)
    summary = {
        "dds": str(dds_path),
        "source": str(source),
        "jobs": args.jobs,
        "package_count": len(files),
        "completed": completed,
        "errors": errors,
        "texture_count": textures,
        "dimension_candidates": candidates,
        "exact_match_count": len(matches),
        "elapsed_seconds": elapsed,
        "packages_per_second": completed / elapsed,
        "input_bytes": sum(row["file_size"] for row in package_rows),
        "target": worker_target,
        "matches": matches,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
