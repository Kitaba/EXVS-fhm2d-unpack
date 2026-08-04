#!/usr/bin/env python3
"""Locate RenderDoc-exported raw GPU buffers in decoded FHM2D payloads."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import glob
import hashlib
import json
from collections import Counter
from pathlib import Path

try:
    from .fhm2d_extract_textures import decode_payload
except ImportError:
    from fhm2d_extract_textures import decode_payload


def useful(chunk: bytes) -> bool:
    return len(chunk) > 0 and len(set(chunk)) >= 8 and chunk.count(0) < len(chunk) * 0.8


def make_samples(data: bytes, block_size: int, count: int) -> list[tuple[int, bytes]]:
    if len(data) <= block_size:
        return [(0, data)] if useful(data) else []
    candidates = max(count * 4, count)
    offsets = sorted({round(index * (len(data) - block_size) / max(1, candidates - 1)) for index in range(candidates)})
    samples = [(offset, data[offset : offset + block_size]) for offset in offsets]
    samples = [item for item in samples if useful(item[1])]
    return samples[:count]


def match_payload(payload: bytes, samples: list[tuple[int, bytes]], full: bytes | None = None) -> dict[str, object]:
    full_offset = payload.find(full) if full else -1
    deltas: list[int] = []
    hits = []
    for sample_offset, sample in samples:
        found = payload.find(sample)
        if found >= 0:
            delta = found - sample_offset
            deltas.append(delta)
            hits.append({"sample_offset": sample_offset, "payload_offset": found, "candidate_base": delta})
    consensus = Counter(deltas).most_common(1)[0] if deltas else (None, 0)
    return {
        "exact_full_offset": full_offset if full_offset >= 0 else None,
        "sample_hits": hits,
        "hit_count": len(hits),
        "sample_count": len(samples),
        "consensus_base": consensus[0],
        "consensus_hits": consensus[1],
    }


def scan_one(job):
    package_string, buffer_name, full_data, samples, min_hits = job
    package = Path(package_string)
    try:
        _, _, payload, _ = decode_payload(package, strict=False)
        result = match_payload(payload, samples, full_data)
        if result["exact_full_offset"] is None and result["consensus_hits"] < min_hits:
            return None
        return {
            "package": str(package),
            "buffer": buffer_name,
            "payload_size": len(payload),
            **result,
        }
    except Exception as exc:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("buffers", nargs="+", help="Raw .bin buffers exported by exvs_batch_export.py")
    parser.add_argument("--fhm-root", required=True)
    parser.add_argument("--pattern", default="*.fhm2d")
    parser.add_argument("--block-size", type=int, default=96)
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--min-hits", type=int, default=2)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.fhm_root).resolve()
    packages = [root] if root.is_file() else sorted(root.rglob(args.pattern))
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    jobs = []
    buffer_manifest = []
    buffer_paths = []
    for buffer_string in args.buffers:
        expanded = glob.glob(buffer_string)
        buffer_paths.extend(Path(item).resolve() for item in (expanded or [buffer_string]))
    for buffer_path in buffer_paths:
        data = buffer_path.read_bytes()
        samples = make_samples(data, args.block_size, args.samples)
        buffer_manifest.append(
            {
                "path": str(buffer_path),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "samples": len(samples),
            }
        )
        for package in packages:
            jobs.append((str(package), buffer_path.name, data, samples, args.min_hits))

    results = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as executor:
        for result in executor.map(scan_one, jobs, chunksize=1):
            if result is not None:
                results.append(result)

    report = {
        "schema": "exvs-fhm2d-buffer-match/v1",
        "fhm_root": str(root),
        "package_count": len(packages),
        "buffers": buffer_manifest,
        "matches": results,
    }
    (output / "matches.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["buffer", "package", "payload_size", "exact_full_offset", "hit_count", "sample_count", "consensus_base", "consensus_hits", "error"]
    with (output / "matches.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print("packages={} buffers={} matches={} output={}".format(len(packages), len(buffer_manifest), len(results), output))


if __name__ == "__main__":
    main()
