#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import multiprocessing as mp
import os
import time
from pathlib import Path

import numpy as np

from fhm2d_extract_textures import (
    FHM2D_BC7_FORMAT,
    FHM2D_RGBA8_FORMAT,
    decode_payload,
    scan_textures,
)
from fhm2d_parallel_dds_scan import parse_dds


_TARGET = None


def normalized_rgba_hash(pixels):
    array = np.frombuffer(pixels, dtype=np.uint8).reshape(-1, 4).copy()
    array[array[:, 3] == 0, :3] = 0
    return hashlib.sha256(array.tobytes()).hexdigest()


def init_worker(target_pixels, target_info):
    global _TARGET
    rgba = np.frombuffer(target_pixels, dtype=np.uint8).reshape(-1, 4)
    alpha = rgba[:, 3]
    visible = alpha > 0
    _TARGET = {
        **target_info,
        "rgba": rgba,
        "alpha": alpha,
        "visible": visible,
        "visible_count": int(visible.sum()),
        "normalized_sha256": normalized_rgba_hash(target_pixels),
    }


def score_rgba(candidate):
    source = np.frombuffer(candidate, dtype=np.uint8).reshape(-1, 4)
    target = _TARGET["rgba"]
    visible = _TARGET["visible"]

    source_alpha_i = source[:, 3].astype(np.int16)
    target_alpha_i = _TARGET["alpha"].astype(np.int16)
    alpha_delta = np.abs(source_alpha_i - target_alpha_i)
    alpha_exact_ratio = float(np.mean(alpha_delta == 0))
    alpha_mae = float(np.mean(alpha_delta))

    source_visible = source[visible].astype(np.int16)
    target_visible = target[visible].astype(np.int16)
    visible_delta = np.abs(source_visible - target_visible)
    visible_rgba_mae = float(np.mean(visible_delta))
    visible_exact_pixel_ratio = float(
        np.mean(np.all(source_visible == target_visible, axis=1))
    )

    source_rgb = source[:, :3].astype(np.float32)
    target_rgb = target[:, :3].astype(np.float32)
    source_a = source[:, 3:4].astype(np.float32) / 255.0
    target_a = target[:, 3:4].astype(np.float32) / 255.0
    premul_mae = float(np.mean(np.abs(source_rgb * source_a - target_rgb * target_a)))

    normalized_sha256 = normalized_rgba_hash(candidate)
    return {
        "normalized_sha256": normalized_sha256,
        "normalized_exact": normalized_sha256 == _TARGET["normalized_sha256"],
        "alpha_exact_ratio": alpha_exact_ratio,
        "alpha_mae": alpha_mae,
        "visible_exact_pixel_ratio": visible_exact_pixel_ratio,
        "visible_rgba_mae": visible_rgba_mae,
        "premultiplied_rgb_mae": premul_mae,
    }


def scan_package(path_text):
    path = Path(path_text)
    started = time.perf_counter()
    rows = []
    error = ""
    try:
        _, _, payload, _ = decode_payload(path, strict=False)
        textures = scan_textures(
            payload, supported_formats={FHM2D_BC7_FORMAT, FHM2D_RGBA8_FORMAT}
        )
        for texture in textures:
            if (
                texture["width"] != _TARGET["width"]
                or texture["height"] != _TARGET["height"]
                or texture["fhm2d_format"] != _TARGET["fhm2d_format"]
                or texture["data_size"] != _TARGET["pixel_size"]
            ):
                continue
            start = texture["payload_data_offset"]
            end = start + texture["data_size"]
            rows.append(
                {
                    "source": str(path),
                    "texture_index": texture["texture_index"],
                    "embedded_name": texture["embedded_name"],
                    "group_index": texture.get("group_index", ""),
                    "group_label": texture.get("group_label", ""),
                    "payload_data_offset": start,
                    "data_size": texture["data_size"],
                    **score_rgba(payload[start:end]),
                }
            )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    return {
        "source": str(path),
        "rows": rows,
        "error": error,
        "seconds": time.perf_counter() - started,
    }


def candidate_packages(packages_csv):
    with packages_csv.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream)
        return [
            row["source"]
            for row in rows
            if int(row.get("dimension_candidates") or 0) > 0
        ]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Rank RGBA8 FHM2D candidates while ignoring RGB under alpha=0."
    )
    parser.add_argument("dds")
    parser.add_argument("packages_csv")
    parser.add_argument("-o", "--output", default="workspace/dds_alpha_match")
    parser.add_argument("-j", "--jobs", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    args = parser.parse_args(argv)

    target = parse_dds(Path(args.dds))
    if target["storage_format"] != "rgba8":
        raise SystemExit("alpha-aware matcher currently requires RGBA8 DDS")
    target_pixels = target.pop("pixels")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    packages = candidate_packages(Path(args.packages_csv))
    started = time.perf_counter()
    results = []
    errors = []

    context = mp.get_context("spawn")
    with context.Pool(
        args.jobs,
        initializer=init_worker,
        initargs=(target_pixels, target),
    ) as pool:
        for index, package in enumerate(
            pool.imap_unordered(scan_package, packages, chunksize=1), 1
        ):
            results.extend(package["rows"])
            if package["error"]:
                errors.append(
                    {"source": package["source"], "error": package["error"]}
                )
            if index % 25 == 0 or index == len(packages):
                print(
                    f"progress={index}/{len(packages)} candidates={len(results)} "
                    f"errors={len(errors)} elapsed={time.perf_counter()-started:.1f}s",
                    flush=True,
                )

    results.sort(
        key=lambda row: (
            not row["normalized_exact"],
            row["premultiplied_rgb_mae"],
            row["alpha_mae"],
            row["visible_rgba_mae"],
        )
    )
    fields = [
        "source", "texture_index", "embedded_name", "group_index", "group_label",
        "payload_data_offset", "data_size", "normalized_sha256", "normalized_exact",
        "alpha_exact_ratio", "alpha_mae", "visible_exact_pixel_ratio",
        "visible_rgba_mae", "premultiplied_rgb_mae",
    ]
    with (output / "ranked_matches.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)
    with (output / "errors.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=["source", "error"])
        writer.writeheader()
        writer.writerows(errors)

    summary = {
        "dds": args.dds,
        "packages_csv": args.packages_csv,
        "jobs": args.jobs,
        "package_count": len(packages),
        "candidate_count": len(results),
        "error_count": len(errors),
        "normalized_exact_count": sum(row["normalized_exact"] for row in results),
        "elapsed_seconds": time.perf_counter() - started,
        "target": {
            **target,
            "normalized_sha256": normalized_rgba_hash(target_pixels),
        },
        "top_matches": results[:20],
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
