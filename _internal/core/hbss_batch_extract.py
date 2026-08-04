#!/usr/bin/env python3
"""Find and fully extract every model-bearing FHM2D below a directory."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

try:
    from .fhm2d_unpack import iter_deflate_blocks
    from .hbss_first_model_extract import model_name
except ImportError:
    from fhm2d_unpack import iter_deflate_blocks
    from hbss_first_model_extract import model_name


MODEL_TYPES = (b"LEKS", b"HSEM", b"LDOM")


def scan_package(path_string: str) -> dict:
    path = Path(path_string)
    counts = Counter()
    model_names = []
    try:
        for block_index, _, _, data in iter_deflate_blocks(path.read_bytes()):
            if block_index is None:
                break
            if len(data) >= 20 and data[:4] == b"HBSS":
                counts[data[16:20].decode("ascii", "replace")] += 1
                if data[16:20] == b"LDOM":
                    model_names.append(model_name(data, len(model_names)))
    except Exception as exc:
        return {"package": str(path), "error": str(exc), "counts": {}}
    model_counts = [counts[value.decode("ascii")] for value in MODEL_TYPES]
    candidate = bool(model_counts[0]) and len(set(model_counts)) == 1
    return {
        "package": str(path), "counts": dict(counts), "candidate": candidate,
        "model_names": model_names,
        "has_body_normal": any("body_normal" in name.lower() for name in model_names),
    }


def execute(command: list[str], timeout: int) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        captured = "".join(
            value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")
            for value in (exc.stdout, exc.stderr)
        )
        return False, "TIMEOUT after {}s\n{}".format(timeout, captured).strip()
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def extract_candidate(job: tuple[int, dict, str, str, int]) -> dict:
    ordinal, candidate, output_string, script_dir_string, stage_timeout = job
    package = Path(candidate["package"])
    output = Path(output_string)
    script_dir = Path(script_dir_string)
    package_output = output / "packages" / package.stem
    model_dir = package_output / "models"
    relations = package_output / "material_relations.json"
    blender_dir = package_output / "blender"
    commands = [
        [sys.executable, str(script_dir / "hbss_bundle_to_obj.py"), str(package), "--output", str(model_dir)],
        [sys.executable, str(script_dir / "hbss_material_relation.py"), str(package), "--output", str(relations)],
        [
            sys.executable, str(script_dir / "hbss_blender_project.py"),
            str(model_dir / "bundle_models.json"), "--material-relations", str(relations),
            "--output", str(blender_dir),
        ],
    ]
    record = {"ordinal": ordinal, "package": str(package), "output": str(package_output), "status": "ok"}
    for stage, command in zip(("models", "relations", "blender"), commands):
        ok, log = execute(command, stage_timeout)
        record[stage + "_log"] = log
        if not ok:
            record["status"] = "error"
            record["failed_stage"] = stage
            break
    if record["status"] == "ok":
        try:
            model_report = json.loads((model_dir / "bundle_models.json").read_text(encoding="utf-8"))
            if model_report.get("failed_count", 0):
                record["status"] = "partial"
                record["decoded_models"] = model_report.get("decoded_count", 0)
                record["failed_models"] = model_report.get("failed_count", 0)
        except (OSError, ValueError):
            pass
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fhm-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--scan-only", action="store_true")
    parser.add_argument("--stage-timeout", type=int, default=30)
    parser.add_argument("--extract-workers", type=int, default=4)
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument(
        "--require-body-normal", action="store_true",
        help="Extract only packages whose MODL names contain body_normal.",
    )
    args = parser.parse_args()

    root = args.fhm_root.resolve()
    packages = sorted(root.rglob("*.fhm2d"))
    scan_results = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(scan_package, str(path)) for path in packages]
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            scan_results.append(future.result())
            if completed % 500 == 0:
                print("scanned {}/{}".format(completed, len(packages)))
    candidates = sorted(
        (item for item in scan_results if item.get("candidate")),
        key=lambda item: item["package"],
    )
    if args.require_body_normal:
        candidates = [item for item in candidates if item.get("has_body_normal")]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    scan_report = {
        "schema": "exvs-hbss-batch-scan/v1", "root": str(root),
        "package_count": len(packages), "candidate_count": len(candidates),
        "candidates": candidates,
        "scan_errors": [item for item in scan_results if item.get("error")],
    }
    (output / "model_package_scan.json").write_text(
        json.dumps(scan_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("packages={} model_candidates={}".format(len(packages), len(candidates)))
    if args.scan_only:
        return 0

    script_dir = Path(__file__).resolve().parent
    manifest_path = output / "batch_manifest.json"
    extracted = []
    if manifest_path.exists():
        try:
            extracted = json.loads(manifest_path.read_text(encoding="utf-8")).get("results", [])
        except (OSError, ValueError):
            extracted = []
    completed_packages = {
        item["package"] for item in extracted
        if not args.retry_failures or item.get("status") in {"ok", "partial"}
    }
    jobs = [
        (ordinal, candidate, str(output), str(script_dir), args.stage_timeout)
        for ordinal, candidate in enumerate(candidates)
        if candidate["package"] not in completed_packages
    ]
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.extract_workers) as executor:
        futures = [executor.submit(extract_candidate, job) for job in jobs]
        for future in concurrent.futures.as_completed(futures):
            record = future.result()
            extracted = [item for item in extracted if item["package"] != record["package"]]
            extracted.append(record)
            print("[{}/{}] {} {}".format(
                len(extracted), len(candidates), record["status"], Path(record["package"]).name
            ))
            manifest_path.write_text(
                json.dumps({"schema": "exvs-hbss-batch-extract/v1", "scan": scan_report,
                            "results": extracted}, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    failures = sum(item["status"] == "error" for item in extracted)
    print("complete={} failed={} output={}".format(len(extracted) - failures, failures, output))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
