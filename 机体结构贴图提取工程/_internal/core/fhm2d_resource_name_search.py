#!/usr/bin/env python3
"""Search decoded FHM2D streams for known asset names and stop on a strong hit."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
from pathlib import Path
from typing import Any

try:
    from .fhm2d_unpack import iter_deflate_blocks
except ImportError:
    from fhm2d_unpack import iter_deflate_blocks


_WORKER_PATTERNS: list[tuple[str, str, bytes]] = []


def pattern_variants(tokens: list[str]) -> list[tuple[str, str, bytes]]:
    variants = []
    seen = set()
    for token in tokens:
        for encoding, value in (
            ("ascii", token.encode("ascii")),
            ("ascii_lower", token.lower().encode("ascii")),
            ("ascii_upper", token.upper().encode("ascii")),
            ("utf16le", token.encode("utf-16le")),
        ):
            key = (encoding, value)
            if key not in seen:
                seen.add(key)
                variants.append((token, encoding, value))
    return variants


def init_worker(patterns: list[tuple[str, str, bytes]]) -> None:
    global _WORKER_PATTERNS
    _WORKER_PATTERNS = patterns


def search_data(data: bytes, block_index: int, decoded_base: int) -> list[dict[str, Any]]:
    hits = []
    for token, encoding, pattern in _WORKER_PATTERNS:
        start = 0
        while True:
            offset = data.find(pattern, start)
            if offset < 0:
                break
            hits.append(
                {
                    "token": token,
                    "encoding": encoding,
                    "block_index": block_index,
                    "block_offset": offset,
                    "decoded_offset": decoded_base + offset,
                }
            )
            start = offset + max(1, len(pattern))
    return hits


def scan_package(package_string: str) -> dict[str, Any]:
    package = Path(package_string)
    decoded_base = 0
    hits = []
    try:
        blob = package.read_bytes()
        for block_index, _, _, data in iter_deflate_blocks(blob):
            if block_index is None:
                break
            hits.extend(search_data(data, block_index, decoded_base))
            decoded_base += len(data)
    except Exception as exc:
        return {"package": str(package), "error": str(exc), "hits": []}
    unique_tokens = sorted({hit["token"] for hit in hits})
    return {
        "package": str(package),
        "decoded_size": decoded_base,
        "unique_token_count": len(unique_tokens),
        "unique_tokens": unique_tokens,
        "hits": hits,
    }


def strong_hit(result: dict[str, Any], minimum_tokens: int) -> bool:
    return int(result.get("unique_token_count", 0)) >= minimum_tokens


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fhm-root", required=True, type=Path)
    parser.add_argument("--token", action="append", required=True)
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--pattern", default="*.fhm2d")
    parser.add_argument("--min-tokens", type=int, default=2)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--stop-first", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    root = args.fhm_root.resolve()
    excluded = {item.lower() for item in args.exclude}
    packages = [root] if root.is_file() else sorted(root.rglob(args.pattern))
    packages = [
        package for package in packages
        if package.name.lower() not in excluded and str(package).lower() not in excluded
    ]
    patterns = pattern_variants(args.token)
    results = []
    completed = 0
    stopped_early = False
    executor = concurrent.futures.ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=init_worker,
        initargs=(patterns,),
    )
    futures = [executor.submit(scan_package, str(package)) for package in packages]
    try:
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            completed += 1
            if result.get("hits"):
                results.append(result)
            if args.stop_first and strong_hit(result, args.min_tokens):
                stopped_early = True
                break
    finally:
        if stopped_early:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
        else:
            executor.shutdown(wait=True)

    results.sort(key=lambda item: (-item["unique_token_count"], item["package"]))
    report = {
        "schema": "exvs-fhm2d-resource-name-search/v1",
        "tokens": args.token,
        "excluded": args.exclude,
        "package_count": len(packages),
        "completed": completed,
        "stopped_early": stopped_early,
        "candidate_count": len(results),
        "candidates": results,
    }
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "resource_name_matches.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "packages={} completed={} candidates={} stopped={} report={}".format(
            len(packages), completed, len(results), stopped_early, report_path
        )
    )
    for result in results[:10]:
        print("{} tokens={} {}".format(
            result["package"], result["unique_token_count"], ",".join(result["unique_tokens"])
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
