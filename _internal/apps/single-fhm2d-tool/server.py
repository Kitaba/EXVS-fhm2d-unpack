#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import threading
import webbrowser
from collections import deque
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


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
            state["log_lines"] = list(self.lines)
        state.update(
            {
                "workspace": str(self.workspace),
                "default_project_root": str(self.project_root),
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
        if urlparse(self.path).path == "/api/status":
            return self.json_response(self.manager.status())
        if urlparse(self.path).path.startswith("/api/"):
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


def main():
    parser = argparse.ArgumentParser(
        description="EXVS single FHM2D unpack/repack tool"
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--core", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()

    Handler.web_root = Path(__file__).resolve().parent
    Handler.manager = ToolManager(args.workspace, args.core)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
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
