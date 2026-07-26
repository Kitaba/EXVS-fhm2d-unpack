#!/usr/bin/env python3
import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ACTION_LABELS = {
    "quick": "VSAC29 快速准备",
    "install": "安装内置 VSAC29 映射",
    "scan": "扫描 FHM2D",
    "extract": "提取并转换 PNG",
    "color": "修复 PNG 显示颜色",
    "catalog": "重建纹理目录",
    "validate": "验证全部图片",
    "map": "分类并生成组合映射",
    "full": "一键建立完整立绘库",
}


class JobManager:
    def __init__(self, game_root, workspace, core_root):
        self.game_root = Path(game_root).resolve()
        self.workspace = Path(workspace).resolve()
        self.core_root = Path(core_root).resolve()
        self.texture_root = self.workspace / "all-textures"
        self.mapping_root = self.workspace / "asset-mapping"
        self.single_project_root = self.workspace / "single-projects"
        self.log_root = self.workspace / "logs"
        self.source_root = (
            self.game_root / "data" / "x64" / "dplcache_release"
        )
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.single_project_root.mkdir(parents=True, exist_ok=True)
        self.log_root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.process = None
        self.thread = None
        self.state = {
            "running": False,
            "action": None,
            "label": None,
            "stage": None,
            "stage_index": 0,
            "stage_count": 0,
            "started_at": None,
            "finished_at": None,
            "return_code": None,
            "message": "等待操作",
            "log_file": None,
            "result_path": None,
        }
        self.lines = deque(maxlen=500)
        self.prebuilt_database = self.read_prebuilt_database()

    def read_prebuilt_database(self):
        database_root = self.core_root / "databases"
        manifests = []
        for path in sorted(database_root.glob("*/database.json")):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
                manifests.append(
                    {
                        "game_version": manifest.get("game_version", "未知"),
                        "texture_count": int(manifest.get("texture_count", 0)),
                        "group_count": int(manifest.get("group_count", 0)),
                        "layer_count": int(manifest.get("layer_count", 0)),
                    }
                )
            except (OSError, ValueError, TypeError):
                continue
        return manifests

    def command(self, stage):
        batch = self.core_root / "fhm2d_batch_textures.py"
        mapper = self.core_root / "texture_asset_mapper.py"
        common = [
            sys.executable,
            str(batch),
            "--output",
            str(self.texture_root),
        ]
        commands = {
            "scan": [
                *common,
                "scan",
                "--source",
                str(self.source_root),
            ],
            "extract": [
                *common,
                "extract",
                "--compact",
                "--texconv",
                str(self.core_root / "tools" / "texconv.exe"),
            ],
            "color": [*common, "retag-srgb"],
            "catalog": [*common, "catalog"],
            "validate": [*common, "validate"],
            "map": [
                sys.executable,
                str(self.core_root / "mapping_database.py"),
                "apply",
                "--catalog",
                str(self.texture_root / "inventory" / "textures.csv"),
                "--texture-root",
                str(self.texture_root),
                "--database-root",
                str(self.core_root / "databases"),
                "--output",
                str(self.mapping_root),
                "--mapper",
                str(mapper),
            ],
            "install": [
                sys.executable,
                str(self.core_root / "mapping_database.py"),
                "apply",
                "--catalog",
                str(self.texture_root / "inventory" / "textures.csv"),
                "--texture-root",
                str(self.texture_root),
                "--database-root",
                str(self.core_root / "databases"),
                "--output",
                str(self.mapping_root),
                "--mapper",
                str(mapper),
                "--require-database",
            ],
            "map_validate": [
                sys.executable,
                str(mapper),
                "validate",
                "--mapping",
                str(self.mapping_root / "mapping.json"),
            ],
        }
        return commands[stage]

    @staticmethod
    def stages(action):
        if action == "quick":
            return ["scan", "extract", "color", "install", "map_validate"]
        if action == "full":
            return [
                "scan",
                "extract",
                "color",
                "catalog",
                "validate",
                "map",
                "map_validate",
            ]
        if action == "map":
            return ["map", "map_validate"]
        if action == "install":
            return ["install", "map_validate"]
        return [action]

    def append(self, line, log_stream=None):
        line = line.rstrip()
        if not line:
            return
        stamped = f"[{datetime.now().strftime('%H:%M:%S')}] {line}"
        with self.lock:
            self.lines.append(stamped)
            self.state["message"] = line
        if log_stream:
            log_stream.write(stamped + "\n")
            log_stream.flush()

    def start(self, action):
        if action not in ACTION_LABELS:
            raise ValueError("未知操作")
        if action != "color" and not self.source_root.is_dir():
            raise FileNotFoundError(
                f"未找到游戏资源目录：{self.source_root}"
            )
        with self.lock:
            if self.state["running"]:
                raise ValueError("已有任务正在运行")
            now = datetime.now()
            log_path = self.log_root / (
                f"{now.strftime('%Y%m%d_%H%M%S')}_{action}.log"
            )
            stages = self.stages(action)
            self.lines.clear()
            self.state.update(
                {
                    "running": True,
                    "action": action,
                    "label": ACTION_LABELS[action],
                    "stage": None,
                    "stage_index": 0,
                    "stage_count": len(stages),
                    "started_at": now.isoformat(timespec="seconds"),
                    "finished_at": None,
                    "return_code": None,
                    "message": "正在准备任务",
                    "log_file": str(log_path),
                    "result_path": None,
                }
            )
        self.thread = threading.Thread(
            target=self._worker,
            args=(action, stages, log_path),
            daemon=True,
        )
        self.thread.start()
        return self.status()

    def resolve_single_source(self, value):
        value = str(value or "").strip().strip('"')
        if not value:
            raise ValueError("请选择或输入一个 FHM2D 文件")
        path = Path(value)
        if not path.is_absolute():
            path = self.source_root / path
        path = path.resolve()
        if path.suffix.lower() != ".fhm2d":
            raise ValueError("源文件扩展名必须是 .fhm2d")
        if not path.is_file():
            raise FileNotFoundError(f"未找到 FHM2D：{path}")
        return path

    def resolve_project_root(self, value):
        value = str(value or "").strip().strip('"')
        root = (
            Path(value).resolve()
            if value
            else self.single_project_root
        )
        source_root = self.source_root.resolve()
        if root == source_root or source_root in root.parents:
            raise ValueError("工程根目录不能放在游戏资源目录内")
        return root

    def resolve_single_project(self, value):
        value = str(value or "").strip().strip('"')
        if not value:
            raise ValueError("请选择或输入一个回包工程")
        path = Path(value)
        if not path.is_absolute():
            path = self.single_project_root / path
        path = path.resolve()
        if not (path / "project.json").is_file():
            raise FileNotFoundError(f"工程缺少 project.json：{path}")
        return path

    def start_single_extract(self, payload):
        source = self.resolve_single_source(payload.get("source"))
        output_root = self.resolve_project_root(payload.get("output_root"))
        project_dir = output_root / source.stem
        command = [
            sys.executable,
            str(self.core_root / "fhm2d_texture_workflow.py"),
            "export",
            str(source),
            "-o",
            str(output_root),
            "--texconv",
            str(self.core_root / "tools" / "texconv.exe"),
        ]
        if payload.get("overwrite"):
            command.append("--force")
        return self.start_command(
            "single_extract",
            f"提取 {source.name}",
            "提取 DDS 并生成可编辑 PNG",
            command,
            project_dir,
        )

    def start_single_build(self, payload):
        project_dir = self.resolve_single_project(payload.get("project"))
        project = json.loads(
            (project_dir / "project.json").read_text(encoding="utf-8")
        )
        source = Path(project.get("source", "")).resolve()
        output_value = str(payload.get("output") or "").strip().strip('"')
        if output_value:
            output = Path(output_value)
            if not output.is_absolute():
                output = project_dir / "build" / output
            output = output.resolve()
        else:
            output = project_dir / "build" / project["source_name"]
        if output.suffix.lower() != ".fhm2d":
            raise ValueError("回包输出扩展名必须是 .fhm2d")
        if output.resolve() == source:
            raise ValueError("不能覆盖工程记录的原始 FHM2D")
        command = [
            sys.executable,
            str(self.core_root / "fhm2d_texture_workflow.py"),
            "build",
            str(project_dir),
            "-o",
            str(output),
            "--texconv",
            str(self.core_root / "tools" / "texconv.exe"),
        ]
        if payload.get("overwrite"):
            command.append("--force")
        return self.start_command(
            "single_build",
            f"回包 {project_dir.name}",
            "验证 PNG 并重建 FHM2D",
            command,
            output,
        )

    def start_command(self, action, label, stage, command, result_path):
        with self.lock:
            if self.state["running"]:
                raise ValueError("已有任务正在运行")
            now = datetime.now()
            log_path = self.log_root / (
                f"{now.strftime('%Y%m%d_%H%M%S')}_{action}.log"
            )
            self.lines.clear()
            self.state.update(
                {
                    "running": True,
                    "action": action,
                    "label": label,
                    "stage": stage,
                    "stage_index": 1,
                    "stage_count": 1,
                    "started_at": now.isoformat(timespec="seconds"),
                    "finished_at": None,
                    "return_code": None,
                    "message": "正在准备任务",
                    "log_file": str(log_path),
                    "result_path": str(result_path),
                }
            )
        self.thread = threading.Thread(
            target=self._command_worker,
            args=(label, command, log_path, Path(result_path)),
            daemon=True,
        )
        self.thread.start()
        return self.status()

    def _command_worker(self, label, command, log_path, result_path):
        final_code = 0
        with log_path.open("w", encoding="utf-8") as log_stream:
            self.append(f"开始：{label}", log_stream)
            self.append(f"目标：{result_path}", log_stream)
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            try:
                process = subprocess.Popen(
                    command,
                    cwd=self.game_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    creationflags=(
                        subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                    ),
                )
                with self.lock:
                    self.process = process
                for line in process.stdout:
                    self.append(line, log_stream)
                final_code = process.wait()
            except Exception as exc:
                self.append(f"{type(exc).__name__}: {exc}", log_stream)
                final_code = 2
            finally:
                with self.lock:
                    self.process = None
            if final_code == 0:
                self.append(f"任务完成：{result_path}", log_stream)
            else:
                self.append(f"任务失败，返回码 {final_code}", log_stream)
        with self.lock:
            self.state.update(
                {
                    "running": False,
                    "finished_at": datetime.now().isoformat(
                        timespec="seconds"
                    ),
                    "return_code": final_code,
                    "message": (
                        f"任务完成：{result_path}"
                        if final_code == 0
                        else f"任务失败，返回码 {final_code}"
                    ),
                }
            )

    def _worker(self, action, stages, log_path):
        final_code = 0
        with log_path.open("w", encoding="utf-8") as log_stream:
            self.append(
                f"开始：{ACTION_LABELS[action]}", log_stream
            )
            self.append(f"游戏目录：{self.game_root}", log_stream)
            self.append(f"工作区：{self.workspace}", log_stream)
            for index, stage in enumerate(stages, 1):
                with self.lock:
                    self.state["stage"] = stage
                    self.state["stage_index"] = index
                command = self.command(stage)
                self.append(
                    f"阶段 {index}/{len(stages)}：{stage}", log_stream
                )
                env = os.environ.copy()
                env["PYTHONUTF8"] = "1"
                env["PYTHONUNBUFFERED"] = "1"
                try:
                    process = subprocess.Popen(
                        command,
                        cwd=self.game_root,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=env,
                        creationflags=(
                            subprocess.CREATE_NO_WINDOW
                            if os.name == "nt"
                            else 0
                        ),
                    )
                    with self.lock:
                        self.process = process
                    for line in process.stdout:
                        self.append(line, log_stream)
                    final_code = process.wait()
                except Exception as exc:
                    self.append(
                        f"{type(exc).__name__}: {exc}", log_stream
                    )
                    final_code = 2
                finally:
                    with self.lock:
                        self.process = None
                if final_code:
                    self.append(
                        f"阶段失败，返回码 {final_code}", log_stream
                    )
                    break
            if final_code == 0:
                self.append("全部阶段已完成", log_stream)
        with self.lock:
            self.state.update(
                {
                    "running": False,
                    "finished_at": datetime.now().isoformat(
                        timespec="seconds"
                    ),
                    "return_code": final_code,
                    "message": (
                        "任务完成"
                        if final_code == 0
                        else f"任务失败，返回码 {final_code}"
                    ),
                }
            )

    def cancel(self):
        with self.lock:
            process = self.process
            running = self.state["running"]
        if not running:
            return self.status()
        if process and process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                process.terminate()
        self.append("已请求中止任务")
        return self.status()

    def inventory_counts(self):
        packages_path = self.texture_root / "inventory" / "packages.csv"
        textures_path = self.texture_root / "inventory" / "textures.csv"
        package_count = texture_count = 0
        supported = 0
        if packages_path.is_file():
            with packages_path.open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                rows = list(csv.DictReader(stream))
            package_count = len(rows)
            supported = sum(
                row.get("status") == "supported_textures" for row in rows
            )
        if textures_path.is_file():
            with textures_path.open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                texture_count = sum(1 for _ in csv.DictReader(stream))
        return package_count, supported, texture_count

    def single_projects(self):
        projects = []
        for manifest_path in sorted(
            self.single_project_root.glob("*/project.json")
        ):
            try:
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
                project_dir = manifest_path.parent.resolve()
                projects.append(
                    {
                        "name": project_dir.name,
                        "path": str(project_dir),
                        "source_name": manifest.get("source_name", ""),
                        "texture_count": int(
                            manifest.get("texture_count", 0)
                        ),
                        "png_directory": str(project_dir / "png_edit"),
                        "default_output": str(
                            project_dir
                            / "build"
                            / manifest.get(
                                "source_name", f"{project_dir.name}.fhm2d"
                            )
                        ),
                    }
                )
            except (OSError, ValueError, TypeError):
                continue
        return projects

    def package_choices(self, query=""):
        if not self.source_root.is_dir():
            return []
        query = query.strip().lower()
        choices = []
        for path in self.source_root.glob("*.fhm2d"):
            if query and query not in path.name.lower():
                continue
            choices.append(
                {"name": path.name, "path": str(path.resolve())}
            )
            if len(choices) >= 100:
                break
        return sorted(choices, key=lambda item: item["name"])

    @staticmethod
    def native_browse(kind):
        if os.name != "nt":
            raise OSError("当前系统不支持原生路径选择")
        if kind == "fhm2d":
            script = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$OutputEncoding=[Console]::OutputEncoding="
                "[System.Text.UTF8Encoding]::new();"
                "$d=New-Object System.Windows.Forms.OpenFileDialog;"
                "$d.Filter='FHM2D (*.fhm2d)|*.fhm2d|All files (*.*)|*.*';"
                "if($d.ShowDialog() -eq "
                "[System.Windows.Forms.DialogResult]::OK){$d.FileName}"
            )
        elif kind == "folder":
            script = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$OutputEncoding=[Console]::OutputEncoding="
                "[System.Text.UTF8Encoding]::new();"
                "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
                "if($d.ShowDialog() -eq "
                "[System.Windows.Forms.DialogResult]::OK){$d.SelectedPath}"
            )
        else:
            raise ValueError("未知路径选择类型")
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-WindowStyle",
                "Hidden",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode:
            raise OSError(result.stderr.strip() or "无法打开路径选择器")
        return result.stdout.strip()

    def status(self):
        with self.lock:
            state = dict(self.state)
            lines = list(self.lines)
        package_count, supported, texture_count = self.inventory_counts()
        mapping_path = self.mapping_root / "mapping.json"
        group_count = 0
        mapping_source = None
        mapping_game_version = None
        if mapping_path.is_file():
            try:
                mapping = json.loads(
                    mapping_path.read_text(encoding="utf-8")
                )
                group_count = mapping.get("group_count", 0)
                mapping_source = mapping.get("mapping_source")
                mapping_game_version = mapping.get("game_version")
            except (OSError, ValueError):
                pass
        usage = shutil.disk_usage(self.workspace)
        state.update(
            {
                "game_root": str(self.game_root),
                "source_root": str(self.source_root),
                "workspace": str(self.workspace),
                "single_project_root": str(self.single_project_root),
                "source_exists": self.source_root.is_dir(),
                "package_count": package_count,
                "supported_package_count": supported,
                "texture_count": texture_count,
                "mapped_group_count": group_count,
                "mapping_source": mapping_source,
                "mapping_game_version": mapping_game_version,
                "prebuilt_databases": self.prebuilt_database,
                "free_gib": round(usage.free / 1024**3, 2),
                "log_lines": lines,
            }
        )
        return state


class Handler(SimpleHTTPRequestHandler):
    manager = None
    web_root = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.web_root), **kwargs)

    def log_message(self, format_string, *args):
        pass

    def json_response(self, payload, status=HTTPStatus.OK):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 64 * 1024:
            raise ValueError("请求过大")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            return self.json_response(self.manager.status())
        if parsed.path == "/api/single/projects":
            return self.json_response(
                {"projects": self.manager.single_projects()}
            )
        if parsed.path == "/api/single/packages":
            query = parse_qs(parsed.query).get("q", [""])[0]
            return self.json_response(
                {"packages": self.manager.package_choices(query)}
            )
        if parsed.path.startswith("/api/"):
            return self.json_response(
                {"error": "API 不存在"}, HTTPStatus.NOT_FOUND
            )
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/start":
                action = self.read_json().get("action", "")
                return self.json_response(
                    self.manager.start(action), HTTPStatus.ACCEPTED
                )
            if parsed.path == "/api/cancel":
                return self.json_response(self.manager.cancel())
            if parsed.path == "/api/single/extract":
                return self.json_response(
                    self.manager.start_single_extract(self.read_json()),
                    HTTPStatus.ACCEPTED,
                )
            if parsed.path == "/api/single/build":
                return self.json_response(
                    self.manager.start_single_build(self.read_json()),
                    HTTPStatus.ACCEPTED,
                )
            if parsed.path == "/api/open-folder":
                path = Path(self.read_json().get("path", "")).resolve()
                if not path.is_dir():
                    raise FileNotFoundError(f"目录不存在：{path}")
                if os.name != "nt":
                    raise OSError("当前系统不支持打开资源管理器")
                os.startfile(path)
                return self.json_response({"ok": True})
            if parsed.path == "/api/browse":
                kind = self.read_json().get("kind", "")
                return self.json_response(
                    {"path": self.manager.native_browse(kind)}
                )
            return self.json_response(
                {"error": "API 不存在"}, HTTPStatus.NOT_FOUND
            )
        except FileNotFoundError as exc:
            return self.json_response(
                {"error": str(exc)}, HTTPStatus.NOT_FOUND
            )
        except (ValueError, OSError) as exc:
            return self.json_response(
                {"error": str(exc)}, HTTPStatus.BAD_REQUEST
            )


def main():
    parser = argparse.ArgumentParser(
        description="EXVS portable single-file unpack/repack tool"
    )
    parser.add_argument("--game-root", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--core", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()

    Handler.web_root = Path(__file__).resolve().parent
    Handler.manager = JobManager(
        args.game_root, args.workspace, args.core
    )
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"EXVS single-file unpack/repack tool: {url}", flush=True)
    if args.open_browser and not os.environ.get("EXVSIB_NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
