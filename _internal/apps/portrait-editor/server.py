#!/usr/bin/env python3
import argparse
import csv
import json
import os
import shutil
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

    def meta(self):
        modified = self.modified_group_keys()
        replacement_count = sum(
            1 for _, layer in self.layer_index.values()
            if self.is_replaced(layer)
        )
        return {
            "title": "EXVSIB 立绘编辑器",
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
                if image.format != "PNG":
                    raise ValueError("只接受 PNG 文件")
                if image.mode != "RGBA":
                    raise ValueError(
                        f"图片模式为 {image.mode}，要求 RGBA"
                    )
                if image.size != expected:
                    raise ValueError(
                        f"图片尺寸为 {image.size[0]}x{image.size[1]}，"
                        f"要求 {expected[0]}x{expected[1]}"
                    )
            os.replace(temp_path, destination)
            self.replacement_keys.add(
                self.normalized_relative(layer["replacement_path"])
            )
            self.invalidate_preview(key)
            return self.layer_payload(layer)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()

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
        if parsed.path == "/api/rescan":
            count = self.data.scan_replacements()
            return self.json_response({"replacement_count": count})
        if parsed.path != "/api/replacement":
            return self.error_response("API 不存在", HTTPStatus.NOT_FOUND)
        query = parse_qs(parsed.query)
        texture_id = self.single(query, "texture_id")
        content_type = self.headers.get("Content-Type", "").split(";")[0]
        if content_type != "image/png":
            return self.error_response("Content-Type 必须为 image/png")
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
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    editor_root = Path(__file__).resolve().parent
    PortraitHandler.data = PortraitData(editor_root, args.workspace)
    server = ThreadingHTTPServer((args.host, args.port), PortraitHandler)
    url = f"http://{args.host}:{args.port}"
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
