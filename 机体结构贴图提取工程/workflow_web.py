#!/usr/bin/env python3
"""Local-only web UI for the portable EXVS workflow."""

from __future__ import annotations

import json
import argparse
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
WORKFLOW = ROOT / "exvs_workflow.py"

lock = threading.Lock()
state = {"running": False, "returncode": None, "lines": [], "command": "", "process": None}


def add_value(command: list[str], data: dict, key: str, flag: str | None = None) -> None:
    value = str(data.get(key, "")).strip()
    if value:
        if flag:
            command.append(flag)
        command.append(value)


def build_command(data: dict) -> list[str]:
    action = data.get("action")
    command = [sys.executable]
    if action == "renderdoc":
        command += [str(WORKFLOW), "renderdoc"]
        add_value(command, data, "capture_root")
        add_value(command, data, "fhm_root", "--fhm-root")
        add_value(command, data, "package", "--package")
        add_value(command, data, "output", "--output")
        add_value(command, data, "workers", "--workers")
        add_value(command, data, "min_package_coverage", "--min-package-coverage")
        groups = str(data.get("groups", "")).replace(",", " ").split()
        for group in groups:
            command += ["--group", group]
    elif action == "repack":
        command += [str(WORKFLOW), "repack"]
        add_value(command, data, "folder")
        add_value(command, data, "output", "--output")
        if data.get("force"):
            command.append("--force")
    else:
        raise ValueError("未知操作")
    return command


def run_job(command: list[str]) -> None:
    with lock:
        state.update(running=True, returncode=None, lines=[], command=subprocess.list2cmdline(command))
    try:
        process = subprocess.Popen(
            command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        with lock:
            state["process"] = process
        assert process.stdout is not None
        for line in process.stdout:
            with lock:
                state["lines"].append(line.rstrip("\r\n"))
                if len(state["lines"]) > 5000:
                    del state["lines"][:1000]
        code = process.wait()
    except Exception as exc:
        code = -1
        with lock:
            state["lines"].append("启动失败：{}".format(exc))
    with lock:
        state.update(running=False, returncode=code, process=None)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def send_json(self, value: object, status: int = 200) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            with lock:
                snapshot = {key: value for key, value in state.items() if key != "process"}
            self.send_json(snapshot)
            return
        if path == "/api/config":
            self.send_json({
                "root": str(ROOT),
                "renderdoc_script": str(ROOT / "_internal" / "renderdoc" / "exvs_auto_capture.py"),
            })
            return
        file_path = WEB_ROOT / ("index.html" if path == "/" else path.lstrip("/"))
        if not file_path.is_file() or WEB_ROOT not in file_path.resolve().parents:
            self.send_error(404)
            return
        payload = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if self.path == "/api/run":
                with lock:
                    if state["running"]:
                        self.send_json({"error": "已有任务正在运行"}, 409)
                        return
                command = build_command(data)
                threading.Thread(target=run_job, args=(command,), daemon=True).start()
                self.send_json({"ok": True})
            elif self.path == "/api/stop":
                with lock:
                    process = state.get("process")
                if process and process.poll() is None:
                    process.terminate()
                self.send_json({"ok": True})
            elif self.path == "/api/shutdown":
                self.send_json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.send_error(404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = "http://127.0.0.1:{}/".format(server.server_port)
    print("EXVS 网页工作流：{}".format(url), flush=True)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
