#!/usr/bin/env python3
"""实验性批量入口：尽可能提取全部可识别模型、贴图和 Blender 工程。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CORE = ROOT / "_internal" / "core"
BATCH_MODELS = CORE / "hbss_batch_extract.py"
BLENDER_AND_TEXTURES = CORE / "hbss_rebuild_blender_projects.py"


def run_partial(command: list[str]) -> int:
    rendered = [sys.executable, *command]
    print("+ " + " ".join(rendered), flush=True)
    result = subprocess.run(rendered)
    if result.returncode not in (0, 1):
        raise subprocess.CalledProcessError(result.returncode, rendered)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "实验性一键提取：扫描 FHM2D，提取当前可识别的 LEKS/HSEM/MODL、"
            "46XT 贴图和 Blender 工程。不能保证覆盖所有模型、格式或材质关系。"
        )
    )
    parser.add_argument(
        "--source", type=Path,
        default=Path(r"E:\game\ob\25\data\x64\dplcache_release"),
        help="游戏 dplcache_release 目录",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "workspace" / "全部模型与贴图",
        help="输出目录",
    )
    parser.add_argument("--scan-workers", type=int, default=8)
    parser.add_argument("--extract-workers", type=int, default=4)
    parser.add_argument("--stage-timeout", type=int, default=30)
    parser.add_argument(
        "--require-body-normal", action="store_true",
        help="只保留名称中含 body_normal 的机体主体包",
    )
    parser.add_argument(
        "--skip-textures", action="store_true",
        help="只提取模型并生成无贴图 Blender 工程",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    if not source.is_dir():
        raise FileNotFoundError(source)
    output.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("EXVS 实验性一键提取入口")
    print("该工具只提取当前已识别格式，结果可能不完整。")
    print("模型网格回包尚未支持；纹理回包请使用 exvs_workflow.py repack。")
    print("source={}".format(source))
    print("output={}".format(output))
    print("=" * 72)

    model_command = [
        str(BATCH_MODELS), "--fhm-root", str(source), "--output", str(output),
        "--workers", str(args.scan_workers),
        "--extract-workers", str(args.extract_workers),
        "--stage-timeout", str(args.stage_timeout),
        "--retry-failures",
    ]
    if args.require_body_normal:
        model_command.append("--require-body-normal")
    model_result = run_partial(model_command)

    rebuild_command = [
        str(BLENDER_AND_TEXTURES), str(output),
        "--workers", str(args.scan_workers),
    ]
    if not args.skip_textures:
        rebuild_command.append("--extract-textures")
    rebuild_result = run_partial(rebuild_command)

    batch_manifest = output / "batch_manifest.json"
    assembly_manifest = output / "assembly_manifest.json"
    summary = {
        "schema": "exvs-experimental-one-click-extract/v1",
        "warning": "Experimental partial extraction; unsupported formats and models may be absent.",
        "source": str(source),
        "output": str(output),
        "model_stage_partial": model_result == 1,
        "blender_stage_partial": rebuild_result == 1,
        "batch_manifest": str(batch_manifest),
        "assembly_manifest": str(assembly_manifest),
    }
    summary_path = output / "一键提取结果.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("完成。结果可能不完整，请检查：{}".format(summary_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
