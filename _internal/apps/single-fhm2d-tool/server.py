#!/usr/bin/env python3
import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import threading
import webbrowser
from collections import deque
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image


MAX_UPLOAD_BYTES = 32 * 1024 * 1024


class ToolManager:
    def __init__(self, workspace, core_root):
        self.workspace = Path(workspace).resolve()
        self.core_root = Path(core_root).resolve()
        self.project_root = self.workspace / "single-fhm2d-projects"
        self.log_root = self.workspace / "logs"
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.log_root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.process = None
        self.lines = deque(maxlen=600)
        self.state = {
            "running": False,
            "action": None,
            "label": "等待操作",
            "started_at": None,
            "finished_at": None,
            "return_code": None,
            "message": "请选择要处理的 FHM2D 或工程文件夹",
            "result_path": None,
        }

    @staticmethod
    def clean_path(value):
        return str(value or "").strip().strip('"')

    def resolve_source(self, value):
        value = self.clean_path(value)
        if not value:
            raise ValueError("请选择一个 FHM2D 文件")
        path = Path(value).resolve()
        if path.suffix.lower() != ".fhm2d":
            raise ValueError("输入文件扩展名必须是 .fhm2d")
        if not path.is_file():
            raise FileNotFoundError(f"未找到 FHM2D：{path}")
        return path

    def resolve_project(self, value):
        value = self.clean_path(value)
        if not value:
            raise ValueError("请选择一个提取工程文件夹")
        folder = Path(value).resolve()
        if not folder.is_dir():
            raise FileNotFoundError(f"文件夹不存在：{folder}")
        if (folder / "project.json").is_file():
            return folder
        candidates = list(folder.glob("*/project.json"))
        if len(candidates) == 1:
            return candidates[0].parent.resolve()
        if candidates:
            raise ValueError("该目录包含多个工程，请选择具体的包名子目录")
        raise FileNotFoundError(
            f"未找到 project.json；请选择本工具生成的工程目录：{folder}"
        )

    def project_info(self, value):
        project_dir = self.resolve_project(value)
        manifest = json.loads(
            (project_dir / "project.json").read_text(encoding="utf-8")
        )
        source_name = manifest.get(
            "source_name", f"{project_dir.name}.fhm2d"
        )
        return {
            "project": str(project_dir),
            "source_name": source_name,
            "source": manifest.get("source", ""),
            "texture_count": int(manifest.get("texture_count", 0)),
            "png_directory": str(project_dir / "png_edit"),
            "default_output": str(
                project_dir / "build" / source_name
            ),
        }

    @staticmethod
    def read_csv(path):
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    def project_textures(self, value):
        project_dir = self.resolve_project(value)
        project = json.loads(
            (project_dir / "project.json").read_text(encoding="utf-8")
        )
        textures = {
            row["texture_index"]: row
            for row in self.read_csv(project_dir / project["textures_manifest"])
        }
        rows = []
        for item in self.read_csv(project_dir / project["png_manifest"]):
            texture = textures[item["texture_index"]]
            png_path = project_dir / project["editable_png_directory"] / item["png_file"]
            rows.append(
                {
                    "texture_index": int(item["texture_index"]),
                    "png_file": item["png_file"],
                    "width": int(item["width"]),
                    "height": int(item["height"]),
                    "format": int(texture["fhm2d_format"]),
                    "modified": self.file_sha256(png_path) != item["sha256"],
                }
            )
        return {
            "project": str(project_dir),
            "textures": sorted(rows, key=lambda row: row["texture_index"]),
        }

    @staticmethod
    def file_sha256(path):
        import hashlib

        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def texture_path(self, value, texture_index):
        project_dir = self.resolve_project(value)
        project = json.loads(
            (project_dir / "project.json").read_text(encoding="utf-8")
        )
        for row in self.read_csv(project_dir / project["png_manifest"]):
            if int(row["texture_index"]) == int(texture_index):
                path = (
                    project_dir
                    / project["editable_png_directory"]
                    / row["png_file"]
                ).resolve()
                if project_dir not in path.parents or not path.is_file():
                    raise FileNotFoundError(path)
                return path, row
        raise KeyError(f"纹理不存在：{texture_index}")

    def save_texture(self, value, texture_index, source_stream, length):
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            raise ValueError("图片为空或超过 32 MB")
        destination, row = self.texture_path(value, texture_index)
        temporary = None
        normalized = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, suffix=".upload", delete=False
            ) as stream:
                temporary = Path(stream.name)
                remaining = length
                while remaining:
                    chunk = source_stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    stream.write(chunk)
                    remaining -= len(chunk)
            if temporary.stat().st_size != length:
                raise ValueError("上传内容长度不完整")
            with Image.open(temporary) as opened:
                opened.load()
                expected = (int(row["width"]), int(row["height"]))
                source_format = opened.format or "UNKNOWN"
                source_size = opened.size
                alpha_added = "A" not in opened.getbands()
                prepared = opened.convert("RGBA")
                if prepared.size != expected:
                    scale = min(
                        expected[0] / prepared.width,
                        expected[1] / prepared.height,
                    )
                    resized = (
                        max(1, round(prepared.width * scale)),
                        max(1, round(prepared.height * scale)),
                    )
                    prepared = prepared.resize(resized, Image.Resampling.LANCZOS)
                    canvas = Image.new("RGBA", expected, (0, 0, 0, 0))
                    canvas.alpha_composite(
                        prepared,
                        (
                            (expected[0] - resized[0]) // 2,
                            (expected[1] - resized[1]) // 2,
                        ),
                    )
                    prepared = canvas
                normalized = temporary.with_suffix(".png")
                prepared.save(normalized, "PNG", optimize=False)
                prepared.close()
            os.replace(normalized, destination)
            return {
                "texture_index": int(texture_index),
                "source_format": source_format,
                "source_size": list(source_size),
                "normalized_size": list(expected),
                "resized": source_size != expected,
                "alpha_added": alpha_added,
                "modified": True,
            }
        finally:
            for path in (temporary, normalized):
                if path and path.exists():
                    path.unlink()

    def start(self, payload):
        action = payload.get("action")
        if action == "extract":
            return self.start_extract(payload)
        if action == "build":
            return self.start_build(payload)
        raise ValueError("未知操作")

    def start_extract(self, payload):
        source = self.resolve_source(payload.get("source"))
        output_value = self.clean_path(payload.get("output_root"))
        output_root = (
            Path(output_value).resolve()
            if output_value
            else self.project_root
        )
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
            "extract",
            f"提取 {source.name}",
            command,
            project_dir,
        )

    def start_build(self, payload):
        project_dir = self.resolve_project(payload.get("project"))
        info = self.project_info(project_dir)
        output_value = self.clean_path(payload.get("output"))
        if output_value:
            output = Path(output_value)
            if not output.is_absolute():
                output = project_dir / "build" / output
            output = output.resolve()
        else:
            output = Path(info["default_output"])
        if output.suffix.lower() != ".fhm2d":
            raise ValueError("输出文件扩展名必须是 .fhm2d")
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
            "build",
            f"回包 {project_dir.name}",
            command,
            output,
        )

    def start_command(self, action, label, command, result_path):
        with self.lock:
            if self.state["running"]:
                raise ValueError("已有任务正在运行")
            now = datetime.now()
            log_path = self.log_root / (
                f"{now.strftime('%Y%m%d_%H%M%S')}_single_{action}.log"
            )
            self.lines.clear()
            self.state.update(
                {
                    "running": True,
                    "action": action,
                    "label": label,
                    "started_at": now.isoformat(timespec="seconds"),
                    "finished_at": None,
                    "return_code": None,
                    "message": "正在准备任务",
                    "result_path": str(result_path),
                }
            )
        threading.Thread(
            target=self.worker,
            args=(command, log_path, Path(result_path)),
            daemon=True,
        ).start()
        return self.status()

    def append(self, line, stream=None):
        line = line.rstrip()
        if not line:
            return
        stamped = f"[{datetime.now().strftime('%H:%M:%S')}] {line}"
        with self.lock:
            self.lines.append(stamped)
            self.state["message"] = line
        if stream:
            stream.write(stamped + "\n")
            stream.flush()

    def worker(self, command, log_path, result_path):
        return_code = 0
        with log_path.open("w", encoding="utf-8") as stream:
            self.append(f"目标：{result_path}", stream)
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            try:
                process = subprocess.Popen(
                    command,
                    cwd=self.workspace.parent,
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
                    self.append(line, stream)
                return_code = process.wait()
            except Exception as exc:
                self.append(f"{type(exc).__name__}: {exc}", stream)
                return_code = 2
            finally:
                with self.lock:
                    self.process = None
            self.append(
                (
                    f"任务完成：{result_path}"
                    if return_code == 0
                    else f"任务失败，返回码 {return_code}"
                ),
                stream,
            )
        with self.lock:
            self.state.update(
                {
                    "running": False,
                    "finished_at": datetime.now().isoformat(
                        timespec="seconds"
                    ),
                    "return_code": return_code,
                    "message": (
                        f"任务完成：{result_path}"
                        if return_code == 0
                        else f"任务失败，返回码 {return_code}"
                    ),
                }
            )

    def cancel(self):
        with self.lock:
            process = self.process
            running = self.state["running"]
        if running and process and process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                ),
            )
            self.append("已请求中止任务")
        return self.status()

    @staticmethod
    def browse(kind):
        if os.name != "nt":
            raise OSError("当前系统不支持原生路径选择")
        prefix = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$OutputEncoding=[Console]::OutputEncoding="
            "[System.Text.UTF8Encoding]::new();"
        )
        if kind == "fhm2d":
            script = prefix + (
                "$d=New-Object System.Windows.Forms.OpenFileDialog;"
                "$d.Filter='FHM2D (*.fhm2d)|*.fhm2d|All files (*.*)|*.*';"
                "if($d.ShowDialog() -eq "
                "[System.Windows.Forms.DialogResult]::OK){$d.FileName}"
            )
        elif kind == "folder":
            script = prefix + (
                "$d=New-Object System.Windows.Forms.OpenFileDialog;"
                "$d.Title='选择文件夹（可在地址栏粘贴完整路径）';"
                "$d.Filter='文件夹|*.folder';"
                "$d.FileName='选择当前文件夹';"
                "$d.ValidateNames=$false;"
                "$d.CheckFileExists=$false;"
                "$d.CheckPathExists=$true;"
                "$d.DereferenceLinks=$true;"
                "if($d.ShowDialog() -eq "
                "[System.Windows.Forms.DialogResult]::OK){"
                "[IO.Path]::GetDirectoryName($d.FileName)}"
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
            state["log_lines"] = list(self.lines)
        state.update(
            {
                "app_id": "exvs_single_fhm2d_tool",
                "workspace": str(self.workspace),
                "default_project_root": str(self.project_root),
                "ui_api_version": 3,
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
        if parsed.path == "/api/texture":
            try:
                query = parse_qs(parsed.query)
                path, _ = self.manager.texture_path(
                    query.get("project", [""])[0],
                    query.get("texture_index", [""])[0],
                )
                data = path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            except (ValueError, FileNotFoundError, KeyError) as exc:
                return self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if parsed.path.startswith("/api/"):
            return self.json_response(
                {"error": "API 不存在"}, HTTPStatus.NOT_FOUND
            )
        if self.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/start":
                return self.json_response(
                    self.manager.start(self.read_json()),
                    HTTPStatus.ACCEPTED,
                )
            if path == "/api/cancel":
                return self.json_response(self.manager.cancel())
            if path == "/api/project-info":
                return self.json_response(
                    self.manager.project_info(
                        self.read_json().get("project")
                    )
                )
            if path == "/api/textures":
                return self.json_response(
                    self.manager.project_textures(
                        self.read_json().get("project")
                    )
                )
            if path == "/api/browse":
                return self.json_response(
                    {
                        "path": self.manager.browse(
                            self.read_json().get("kind")
                        )
                    }
                )
            if path == "/api/open-folder":
                folder = Path(
                    self.read_json().get("path", "")
                ).resolve()
                if not folder.is_dir():
                    raise FileNotFoundError(f"目录不存在：{folder}")
                os.startfile(folder)
                return self.json_response({"ok": True})
            return self.json_response(
                {"error": "API 不存在"}, HTTPStatus.NOT_FOUND
            )
        except FileNotFoundError as exc:
            return self.json_response(
                {"error": str(exc)}, HTTPStatus.NOT_FOUND
            )
        except (ValueError, OSError, KeyError) as exc:
            return self.json_response(
                {"error": str(exc)}, HTTPStatus.BAD_REQUEST
            )

    def do_PUT(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/texture":
            return self.json_response(
                {"error": "API 不存在"}, HTTPStatus.NOT_FOUND
            )
        try:
            query = parse_qs(parsed.query)
            length = int(self.headers.get("Content-Length", "0"))
            result = self.manager.save_texture(
                query.get("project", [""])[0],
                query.get("texture_index", [""])[0],
                self.rfile,
                length,
            )
            return self.json_response(result)
        except (ValueError, OSError, KeyError) as exc:
            return self.json_response(
                {"error": str(exc)}, HTTPStatus.BAD_REQUEST
            )


def main():
    parser = argparse.ArgumentParser(
        description="EXVS single FHM2D unpack/repack tool"
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--core", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17885)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()

    Handler.web_root = Path(__file__).resolve().parent
    Handler.manager = ToolManager(args.workspace, args.core)
    server = None
    bind_errors = []
    for port in range(args.port, args.port + 10):
        try:
            server = ThreadingHTTPServer((args.host, port), Handler)
            if port != args.port:
                print(
                    f"Port {args.port} is unavailable; using {port}.",
                    flush=True,
                )
            break
        except OSError as exc:
            bind_errors.append(f"{port}: {exc}")
    if server is None:
        raise OSError(
            "No available single FHM2D tool port in "
            f"{args.port}..{args.port + 9}: {'; '.join(bind_errors)}"
        )
    actual_port = server.server_address[1]
    url = f"http://{args.host}:{actual_port}"
    print(f"EXVS single FHM2D tool: {url}", flush=True)
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
