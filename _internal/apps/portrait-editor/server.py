#!/usr/bin/env python3
import argparse
import csv
import json
import os
import shutil
import sys
import tempfile
import threading
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from PIL import Image


CATEGORY_LABELS = {
    "outgame_navigator": "局外领航员",
    "ingame_navigator": "局内领航员",
    "combat_portrait": "战斗人员立绘",
}
MAX_UPLOAD_BYTES = 32 * 1024 * 1024


class PortraitData:
    def __init__(self, editor_root, workspace=None):
        self.editor_root = editor_root.resolve()
        self.patch_edit_root = self.editor_root.parent
        self.workspace = Path(workspace).resolve() if workspace else None
        self.mapping_root = (
            self.workspace / "asset-mapping"
            if self.workspace
            else self.patch_edit_root / "asset-mapping"
        )
        self.mapping_path = self.mapping_root / "mapping.json"
        self.mapping = json.loads(self.mapping_path.read_text(encoding="utf-8"))
        self.texture_root = (
            self.workspace / "all-textures"
            if self.workspace
            else Path(self.mapping["texture_root"]).resolve()
        )
        self.replacement_root = self.mapping_root / "replacements"
        self.preview_cache_root = self.mapping_root / "preview-cache"
        self.replacement_root.mkdir(parents=True, exist_ok=True)
        self.preview_cache_root.mkdir(parents=True, exist_ok=True)
        self.preview_lock = threading.Lock()
        self.groups = self._read_groups()
        self.composition_index = {
            (item["category"], item["package"], item["group"]): item
            for item in self.mapping["compositions"]
        }
        self.composition_cache = {}
        self.layer_index = {}
        self._index_layers()
        self.replacement_keys = set()
        self.scan_replacements()

    def _read_groups(self):
        path = self.mapping_root / self.mapping["groups_manifest"]
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        return rows

    def _load_composition(self, key):
        if key not in self.composition_cache:
            item = self.composition_index[key]
            path = self.mapping_root / item["composition"]
            self.composition_cache[key] = json.loads(
                path.read_text(encoding="utf-8")
            )
        return self.composition_cache[key]

    def _index_layers(self):
        for key in self.composition_index:
            composition = self._load_composition(key)
            self.layer_index[composition["body"]["texture_id"]] = (
                key,
                composition["body"],
            )
            for family in composition["families"]:
                for state in family["states"]:
                    self.layer_index[state["texture_id"]] = (key, state)

    @staticmethod
    def safe_join(root, relative):
        root = root.resolve()
        candidate = (root / Path(relative)).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("路径超出允许的资源目录")
        return candidate

    def replacement_path(self, layer):
        return self.safe_join(
            self.replacement_root, layer["replacement_path"]
        )

    @staticmethod
    def normalized_relative(path):
        return str(Path(path)).replace("\\", "/")

    def scan_replacements(self):
        self.replacement_keys = {
            self.normalized_relative(path.relative_to(self.replacement_root))
            for path in self.replacement_root.rglob("*.png")
            if path.is_file()
        }
        return len(self.replacement_keys)

    def source_path(self, layer):
        return self.safe_join(self.texture_root, layer["source_png"])

    def is_replaced(self, layer):
        return (
            self.normalized_relative(layer["replacement_path"])
            in self.replacement_keys
        )

    def modified_group_keys(self):
        result = set()
        for texture_id, (key, layer) in self.layer_index.items():
            if self.is_replaced(layer):
                result.add(key)
        return result

    def has_package(self, package):
        return any(row["package"] == package for row in self.groups)

    def replacement_records(self):
        self.scan_replacements()
        records = []
        for texture_id, (key, layer) in self.layer_index.items():
            if not self.is_replaced(layer):
                continue
            category, package, group = key
            records.append(
                {
                    "texture_id": texture_id,
                    "category": category,
                    "package": package,
                    "group": group,
                    "embedded_index": int(layer["embedded_index"]),
                    "source_png": layer["source_png"],
                    "replacement_file": str(
                        self.replacement_path(layer)
                    ),
                }
            )
        return sorted(records, key=lambda row: row["texture_id"])

    def meta(self):
        modified = self.modified_group_keys()
        replacement_count = sum(
            1 for _, layer in self.layer_index.values()
            if self.is_replaced(layer)
        )
        return {
            "app_id": "exvs_portrait_editor",
            "title": "EXVSIB 立绘编辑器",
            "patch_api_version": 2,
            "workspace": str(self.mapping_root.parent),
            "mapping_version": self.mapping["mapping_version"],
            "group_count": self.mapping["group_count"],
            "layer_count": self.mapping["layer_count"],
            "category_counts": self.mapping["category_counts"],
            "category_labels": CATEGORY_LABELS,
            "modified_group_count": len(modified),
            "replacement_count": replacement_count,
        }

    def list_groups(self, category, query, modified_only, page, page_size):
        query = query.casefold().strip()
        modified_keys = self.modified_group_keys()
        filtered = []
        for row in self.groups:
            key = (row["category"], row["package"], row["group"])
            if category and row["category"] != category:
                continue
            if modified_only and key not in modified_keys:
                continue
            haystack = f"{row['package']} {row['group']}".casefold()
            if query and query not in haystack:
                continue
            preview = self.mapping_root / row["preview"]
            preview_url = (
                self.file_url("preview", row["preview"])
                if preview.is_file()
                else f"/api/group-preview?id={quote('/'.join(key))}"
            )
            filtered.append(
                {
                    "id": "/".join(key),
                    "category": row["category"],
                    "category_label": CATEGORY_LABELS[row["category"]],
                    "package": row["package"],
                    "group": row["group"],
                    "status": row["status"],
                    "canvas": [
                        int(row["body_width"]),
                        int(row["body_height"]),
                    ],
                    "family_count": int(row["overlay_family_count"]),
                    "state_count": int(row["overlay_texture_count"]),
                    "modified": key in modified_keys,
                    "preview_url": preview_url,
                    "notes": row["notes"],
                }
            )
        total = len(filtered)
        start = (page - 1) * page_size
        return {
            "items": filtered[start:start + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    @staticmethod
    def file_url(kind, path):
        return (
            f"/api/file?kind={quote(kind)}&path="
            f"{quote(str(path).replace(os.sep, '/'))}"
        )

    def layer_payload(self, layer):
        replaced = self.is_replaced(layer)
        result = dict(layer)
        result["replaced"] = replaced
        result["source_url"] = self.file_url("source", layer["source_png"])
        result["current_url"] = self.file_url(
            "replacement" if replaced else "source",
            layer["replacement_path"] if replaced else layer["source_png"],
        )
        return result

    def composition(self, identifier):
        parts = identifier.split("/")
        if len(parts) != 3:
            raise KeyError(identifier)
        key = tuple(parts)
        composition = self._load_composition(key)
        result = {
            key: value for key, value in composition.items()
            if key not in {"body", "families"}
        }
        result["id"] = identifier
        result["category_label"] = CATEGORY_LABELS[result["category"]]
        result["body"] = self.layer_payload(composition["body"])
        result["families"] = []
        for family in composition["families"]:
            family_result = {
                key: value for key, value in family.items()
                if key != "states"
            }
            family_result["states"] = [
                self.layer_payload(state) for state in family["states"]
            ]
            result["families"].append(family_result)
        return result

    def resolve_file(self, kind, relative):
        roots = {
            "source": self.texture_root,
            "replacement": self.replacement_root,
            "preview": self.mapping_root,
        }
        if kind not in roots:
            raise ValueError("未知资源类型")
        path = self.safe_join(roots[kind], unquote(relative))
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def preview_cache_path(self, key):
        category, package, group = key
        return (
            self.preview_cache_root
            / category
            / f"{package}_{group}.png"
        )

    def invalidate_preview(self, key):
        path = self.preview_cache_path(key)
        if path.is_file():
            path.unlink()

    def render_group_preview(self, identifier):
        parts = identifier.split("/")
        if len(parts) != 3:
            raise KeyError(identifier)
        key = tuple(parts)
        if key not in self.composition_index:
            raise KeyError(identifier)
        cached = self.preview_cache_path(key)
        if cached.is_file():
            return cached
        with self.preview_lock:
            if cached.is_file():
                return cached
            composition = self._load_composition(key)
            body = composition["body"]
            body_path = (
                self.replacement_path(body)
                if self.is_replaced(body)
                else self.source_path(body)
            )
            with Image.open(body_path) as image:
                canvas = image.convert("RGBA")
            for family in composition["families"]:
                baseline_id = family["baseline_texture_id"]
                state = next(
                    item
                    for item in family["states"]
                    if item["texture_id"] == baseline_id
                )
                state_path = (
                    self.replacement_path(state)
                    if self.is_replaced(state)
                    else self.source_path(state)
                )
                with Image.open(state_path) as image:
                    overlay = image.convert("RGBA")
                anchor = family["anchor"]
                canvas.alpha_composite(
                    overlay, (int(anchor["x"]), int(anchor["y"]))
                )
            canvas.thumbnail((480, 480), Image.Resampling.LANCZOS)
            cached.parent.mkdir(parents=True, exist_ok=True)
            temporary = cached.with_suffix(".tmp.png")
            canvas.save(temporary, "PNG", optimize=False)
            os.replace(temporary, cached)
        return cached

    def save_replacement(self, texture_id, source_stream, length):
        if texture_id not in self.layer_index:
            raise KeyError(texture_id)
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            raise ValueError("PNG 文件为空或超过 32 MB")
        key, layer = self.layer_index[texture_id]
        destination = self.replacement_path(layer)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, suffix=".png", delete=False
            ) as temp:
                temp_path = Path(temp.name)
                remaining = length
                while remaining:
                    chunk = source_stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    temp.write(chunk)
                    remaining -= len(chunk)
            if temp_path.stat().st_size != length:
                raise ValueError("上传内容长度不完整")
            with Image.open(temp_path) as image:
                image.load()
                expected = (int(layer["width"]), int(layer["height"]))
                source_format = image.format or "UNKNOWN"
                source_size = image.size
                source_mode = image.mode
                alpha_added = "A" not in image.getbands()
                prepared = image.convert("RGBA")
                if prepared.size != expected:
                    scale = min(
                        expected[0] / prepared.width,
                        expected[1] / prepared.height,
                    )
                    resized_size = (
                        max(1, round(prepared.width * scale)),
                        max(1, round(prepared.height * scale)),
                    )
                    prepared = prepared.resize(
                        resized_size, Image.Resampling.LANCZOS
                    )
                    canvas = Image.new("RGBA", expected, (0, 0, 0, 0))
                    canvas.paste(
                        prepared,
                        (
                            (expected[0] - resized_size[0]) // 2,
                            (expected[1] - resized_size[1]) // 2,
                        ),
                    )
                    prepared = canvas
                temporary_png = temp_path.with_suffix(".normalized.png")
                prepared.save(temporary_png, "PNG", optimize=False)
                prepared.close()
            os.replace(temporary_png, temp_path)
            os.replace(temp_path, destination)
            self.replacement_keys.add(
                self.normalized_relative(layer["replacement_path"])
            )
            self.invalidate_preview(key)
            result = self.layer_payload(layer)
            result.update(
                {
                    "source_format": source_format,
                    "source_mode": source_mode,
                    "source_size": list(source_size),
                    "normalized_size": list(expected),
                    "resized": list(source_size) != list(expected),
                    "alpha_added": alpha_added,
                }
            )
            return result
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()
            if "temporary_png" in locals() and temporary_png.exists():
                temporary_png.unlink()

    def delete_replacement(self, texture_id):
        if texture_id not in self.layer_index:
            raise KeyError(texture_id)
        key, layer = self.layer_index[texture_id]
        path = self.replacement_path(layer)
        existed = path.is_file()
        if existed:
            path.unlink()
        self.replacement_keys.discard(
            self.normalized_relative(layer["replacement_path"])
        )
        self.invalidate_preview(key)
        return {"texture_id": texture_id, "deleted": existed}


class PortraitHandler(SimpleHTTPRequestHandler):
    data = None
    patch_manager = None

    def __init__(self, *args, **kwargs):
        super().__init__(
            *args, directory=str(self.data.editor_root), **kwargs
        )

    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}")

    def json_response(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def error_response(self, message, status=HTTPStatus.BAD_REQUEST):
        self.json_response({"error": str(message)}, status)

    @staticmethod
    def single(query, name, default=""):
        return query.get(name, [default])[0]

    def do_GET(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            if parsed.path == "/":
                self.path = "/index.html"
            return super().do_GET()
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/meta":
                return self.json_response(self.data.meta())
            if parsed.path == "/api/patch/status":
                return self.json_response(self.patch_manager.summary())
            if parsed.path == "/api/groups":
                page = max(1, int(self.single(query, "page", "1")))
                page_size = min(
                    96, max(12, int(self.single(query, "page_size", "48")))
                )
                payload = self.data.list_groups(
                    self.single(query, "category"),
                    self.single(query, "q"),
                    self.single(query, "modified") == "1",
                    page,
                    page_size,
                )
                return self.json_response(payload)
            if parsed.path == "/api/composition":
                identifier = self.single(query, "id")
                return self.json_response(self.data.composition(identifier))
            if parsed.path == "/api/group-preview":
                identifier = self.single(query, "id")
                return self.serve_file(
                    self.data.render_group_preview(identifier)
                )
            if parsed.path == "/api/file":
                path = self.data.resolve_file(
                    self.single(query, "kind"),
                    self.single(query, "path"),
                )
                return self.serve_file(path)
            return self.error_response("API 不存在", HTTPStatus.NOT_FOUND)
        except FileNotFoundError:
            return self.error_response("资源文件不存在", HTTPStatus.NOT_FOUND)
        except KeyError:
            return self.error_response("映射对象不存在", HTTPStatus.NOT_FOUND)
        except (ValueError, OSError) as exc:
            return self.error_response(exc)

    def serve_file(self, path):
        content_type = self.guess_type(str(path))
        stat = path.stat()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(stat.st_size))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with path.open("rb") as stream:
            shutil.copyfileobj(stream, self.wfile)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/patch/selection/clear":
            try:
                return self.json_response(
                    self.patch_manager.clear_package_selection()
                )
            except ValueError as exc:
                return self.error_response(exc, HTTPStatus.CONFLICT)
        if parsed.path == "/api/patch/selection":
            try:
                if self.patch_manager.is_running():
                    return self.error_response(
                        "补丁任务运行期间不能修改勾选包",
                        HTTPStatus.CONFLICT,
                    )
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(
                    self.rfile.read(length).decode("utf-8")
                    if length > 0 else "{}"
                )
                packages = payload.get("packages", [])
                if not isinstance(packages, list):
                    raise ValueError("packages 必须是数组")
                excluded_packages = payload.get("excluded_packages", [])
                if not isinstance(excluded_packages, list):
                    raise ValueError("excluded_packages 必须是数组")
                unknown = [
                    item for item in packages + excluded_packages
                    if not self.data.has_package(str(item).strip())
                ]
                if unknown:
                    raise ValueError(
                        "存在未识别的包：" + "、".join(map(str, unknown[:8]))
                    )
                return self.json_response(
                    self.patch_manager.update_selected_packages(
                        packages, excluded_packages
                    )
                )
            except ValueError as exc:
                return self.error_response(exc)
        if parsed.path.startswith("/api/patch/"):
            action = parsed.path.rsplit("/", 1)[-1]
            try:
                return self.json_response(
                    self.patch_manager.start(action),
                    HTTPStatus.ACCEPTED,
                )
            except ValueError as exc:
                return self.error_response(exc, HTTPStatus.CONFLICT)
            except (FileNotFoundError, OSError) as exc:
                return self.error_response(exc)
        if parsed.path == "/api/rescan":
            count = self.data.scan_replacements()
            return self.json_response({"replacement_count": count})
        if parsed.path != "/api/replacement":
            return self.error_response("API 不存在", HTTPStatus.NOT_FOUND)
        query = parse_qs(parsed.query)
        texture_id = self.single(query, "texture_id")
        content_type = self.headers.get("Content-Type", "").split(";")[0]
        allowed_content_types = {
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/bmp",
            "image/vnd-ms.dds",
            "application/octet-stream",
        }
        if content_type not in allowed_content_types:
            return self.error_response(
                "只支持 PNG、JPEG、WEBP、BMP 或 DDS 图片"
            )
        if self.patch_manager.is_running():
            return self.error_response(
                "补丁任务运行期间不能修改替换图",
                HTTPStatus.CONFLICT,
            )
        try:
            length = int(self.headers.get("Content-Length", "0"))
            result = self.data.save_replacement(
                texture_id, self.rfile, length
            )
            return self.json_response(result, HTTPStatus.CREATED)
        except KeyError:
            return self.error_response("纹理 ID 不存在", HTTPStatus.NOT_FOUND)
        except (ValueError, OSError) as exc:
            return self.error_response(exc)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/replacement":
            return self.error_response("API 不存在", HTTPStatus.NOT_FOUND)
        query = parse_qs(parsed.query)
        if self.patch_manager.is_running():
            return self.error_response(
                "补丁任务运行期间不能修改替换图",
                HTTPStatus.CONFLICT,
            )
        try:
            result = self.data.delete_replacement(
                self.single(query, "texture_id")
            )
            return self.json_response(result)
        except KeyError:
            return self.error_response("纹理 ID 不存在", HTTPStatus.NOT_FOUND)
        except OSError as exc:
            return self.error_response(exc)


def main():
    parser = argparse.ArgumentParser(description="EXVSIB portrait editor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17865)
    parser.add_argument("--workspace")
    parser.add_argument("--game-root")
    parser.add_argument("--core")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    editor_root = Path(__file__).resolve().parent
    workspace = (
        Path(args.workspace).resolve()
        if args.workspace
        else editor_root.parent / "workspace"
    )
    packaged_core = editor_root.parents[1] / "core"
    development_core = editor_root.parents[1] / "patch"
    core_root = (
        Path(args.core).resolve()
        if args.core
        else (
            packaged_core
            if packaged_core.is_dir()
            else development_core
        )
    )
    game_root = (
        Path(args.game_root).resolve()
        if args.game_root
        else workspace.parent.parent.resolve()
    )
    if str(core_root) not in sys.path:
        sys.path.insert(0, str(core_root))
    from portrait_patch_manager import PortraitPatchManager

    PortraitHandler.data = PortraitData(editor_root, workspace)
    PortraitHandler.patch_manager = PortraitPatchManager(
        game_root,
        workspace,
        core_root,
        PortraitHandler.data.replacement_records,
    )
    server = None
    bind_errors = []
    for port in range(args.port, args.port + 10):
        try:
            server = ThreadingHTTPServer(
                (args.host, port), PortraitHandler
            )
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
            "No available portrait editor port in "
            f"{args.port}..{args.port + 9}: {'; '.join(bind_errors)}"
        )
    actual_port = server.server_address[1]
    url = f"http://{args.host}:{actual_port}"
    print(
        f"EXVSIB portrait editor: {url}",
        flush=True,
    )
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
