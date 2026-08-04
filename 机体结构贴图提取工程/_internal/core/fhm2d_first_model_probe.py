#!/usr/bin/env python3
"""Stop at the first FHM2D package matching several RenderDoc model buffers."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import struct
from pathlib import Path
from typing import Any

try:
    from .fhm2d_buffer_match import make_samples, match_payload
    from .fhm2d_extract_textures import decode_payload
except ImportError:
    from fhm2d_buffer_match import make_samples, match_payload
    from fhm2d_extract_textures import decode_payload


def build_profiles(
    buffer_paths: list[Path], block_size: int, sample_count: int, index_variants: bool = False
) -> list[dict[str, Any]]:
    profiles = []
    for path in buffer_paths:
        data = path.read_bytes()
        variants = [(path.name, data)]
        if index_variants and len(data) % 2 == 0:
            values = list(struct.unpack("<{}H".format(len(data) // 2), data))
            swapped_winding = values[:]
            for offset in range(0, len(swapped_winding) - 2, 3):
                swapped_winding[offset + 1], swapped_winding[offset + 2] = (
                    swapped_winding[offset + 2], swapped_winding[offset + 1]
                )
            variants.extend(
                [
                    (path.name + ":u16be", struct.pack(">{}H".format(len(values)), *values)),
                    (path.name + ":u32le", struct.pack("<{}I".format(len(values)), *values)),
                    (
                        path.name + ":u16le_swap_winding",
                        struct.pack("<{}H".format(len(swapped_winding)), *swapped_winding),
                    ),
                    (
                        path.name + ":u32le_swap_winding",
                        struct.pack("<{}I".format(len(swapped_winding)), *swapped_winding),
                    ),
                ]
            )
        for variant_name, variant_data in variants:
            profiles.append(
            {
                "name": variant_name,
                "path": str(path),
                "size": len(variant_data),
                "sha256": hashlib.sha256(variant_data).hexdigest(),
                "data": variant_data,
                "samples": make_samples(variant_data, block_size, sample_count),
            }
            )
    return profiles


_WORKER_PROFILES: list[dict[str, Any]] = []


def init_worker(profiles: list[dict[str, Any]]) -> None:
    global _WORKER_PROFILES
    _WORKER_PROFILES = profiles


def probe_package(job: tuple[str, int]) -> dict[str, Any]:
    package_string, min_sample_hits = job
    package = Path(package_string)
    try:
        _, _, payload, metadata = decode_payload(package, strict=False)
    except Exception as exc:
        return {"package": str(package), "error": str(exc), "matches": []}

    matches = []
    for profile in _WORKER_PROFILES:
        result = match_payload(payload, profile["samples"], profile["data"])
        if result["exact_full_offset"] is not None or result["consensus_hits"] >= min_sample_hits:
            matches.append(
                {
                    "buffer": profile["name"],
                    "buffer_size": profile["size"],
                    **result,
                }
            )
    return {
        "package": str(package),
        "payload_size": len(payload),
        "metadata": metadata,
        "matches": matches,
    }


def credible(result: dict[str, Any], minimum_buffers: int) -> bool:
    return len(result.get("matches", [])) >= minimum_buffers


def write_hit(
    output: Path,
    hit: dict[str, Any],
    public_profiles: list[dict[str, Any]],
    scanned: int,
    package_count: int,
    export_payload: bool,
) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    package = Path(hit["package"])
    report = {
        "schema": "exvs-fhm2d-first-model-probe/v1",
        "status": "match",
        "scanned_completed": scanned,
        "package_count": package_count,
        "selected_package": str(package),
        "payload_size": hit.get("payload_size"),
        "buffers": public_profiles,
        "matches": hit["matches"],
        "metadata": hit.get("metadata"),
    }
    report_path = output / "first_model_match.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if export_payload:
        _, _, payload, _ = decode_payload(package, strict=False)
        (output / "first_model_payload.bin").write_bytes(payload)
    return report_path


def write_miss(
    output: Path,
    public_profiles: list[dict[str, Any]],
    package_count: int,
) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "first_model_match.json"
    report_path.write_text(
        json.dumps(
            {
                "schema": "exvs-fhm2d-first-model-probe/v1",
                "status": "not_found",
                "scanned_completed": package_count,
                "package_count": package_count,
                "buffers": public_profiles,
                "matches": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("buffers", nargs="+", type=Path)
    parser.add_argument("--fhm-root", required=True, type=Path)
    parser.add_argument("--pattern", default="*.fhm2d")
    parser.add_argument("--block-size", type=int, default=96)
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--min-sample-hits", type=int, default=2)
    parser.add_argument("--min-buffer-matches", type=int, default=2)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--no-export-payload", action="store_true")
    parser.add_argument(
        "--index-variants",
        action="store_true",
        help="Also test u16 byte order, u32 expansion, and swapped triangle winding.",
    )
    args = parser.parse_args()

    root = args.fhm_root.resolve()
    packages = [root] if root.is_file() else sorted(root.rglob(args.pattern))
    profiles = build_profiles(
        [path.resolve() for path in args.buffers], args.block_size, args.samples,
        args.index_variants,
    )
    public_profiles = [
        {key: value for key, value in profile.items() if key not in {"data", "samples"}}
        | {"sample_count": len(profile["samples"])}
        for profile in profiles
    ]
    jobs = [(str(package), args.min_sample_hits) for package in packages]
    scanned = 0
    hit = None
    executor = concurrent.futures.ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=init_worker,
        initargs=(profiles,),
    )
    futures = [executor.submit(probe_package, job) for job in jobs]
    try:
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            scanned += 1
            if credible(result, args.min_buffer_matches):
                hit = result
                break
    finally:
        if hit is not None:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
        else:
            executor.shutdown(wait=True)

    if hit is None:
        report = write_miss(args.output.resolve(), public_profiles, len(packages))
        print("model candidate not found: packages={} report={}".format(len(packages), report))
        return 1

    report = write_hit(
        args.output.resolve(), hit, public_profiles, scanned, len(packages),
        not args.no_export_payload,
    )
    print(
        "first model candidate: package={} buffers={} completed={} report={}".format(
            hit["package"], len(hit["matches"]), scanned, report
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
