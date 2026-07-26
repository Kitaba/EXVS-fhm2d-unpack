#!/usr/bin/env python3
import csv
import hashlib
import json
import os
import shutil
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from fhm2d_texture_workflow import (
    build_project,
    export_project,
    find_texconv,
    project_status,
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_json_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


class PortraitPatchManager:
    def __init__(
        self,
        game_root,
        workspace,
        core_root,
        replacement_provider,
    ):
        self.game_root = Path(game_root).resolve()
        self.workspace = Path(workspace).resolve()
        self.core_root = Path(core_root).resolve()
        self.replacement_provider = replacement_provider
        self.texture_root = self.workspace / "all-textures"
        self.root = self.workspace / "patch-build"
        self.projects_root = self.root / "projects"
        self.outputs_root = self.root / "outputs"
        self.backups_root = self.root / "backups"
        self.baselines_root = self.root / "baselines"
        self.manifests_root = self.root / "manifests"
        self.latest_build_path = self.root / "latest-build.json"
        self.latest_deployment_path = self.root / "latest-deployment.json"
        self.selection_path = self.root / "selected-packages.json"
        self.exclusion_path = self.root / "excluded-packages.json"
        self.texconv = find_texconv(
            self.core_root / "tools" / "texconv.exe"
        )
        self.lock = threading.Lock()
        self.thread = None
        self.lines = deque(maxlen=300)
        self.state = {
            "running": False,
            "action": None,
            "label": None,
            "started_at": None,
            "finished_at": None,
            "message": "等待操作",
            "error": None,
        }

    @staticmethod
    def _identifier(prefix):
        return datetime.now().strftime(f"{prefix}-%Y%m%d-%H%M%S-%f")

    @staticmethod
    def _read_json(path):
        path = Path(path)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _selected_packages(self):
        payload = self._read_json(self.selection_path)
        if not isinstance(payload, dict):
            return []
        packages = payload.get("packages", [])
        if not isinstance(packages, list):
            return []
        result = []
        seen = set()
        for item in packages:
            name = str(item).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            result.append(name)
        return sorted(result)

    def _excluded_packages(self):
        payload = self._read_json(self.exclusion_path)
        if not isinstance(payload, dict):
            return []
        packages = payload.get("packages", [])
        if not isinstance(packages, list):
            return []
        return sorted({str(item).strip() for item in packages if str(item).strip()})

    def update_selected_packages(self, packages, excluded_packages=None):
        if self.is_running():
            raise ValueError("补丁任务运行期间不能修改勾选包")
        result = []
        seen = set()
        for item in packages:
            name = str(item).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            result.append(name)
        write_json_atomic(
            self.selection_path,
            {"packages": sorted(result)},
        )
        excluded = sorted(
            {
                str(item).strip()
                for item in (excluded_packages or [])
                if str(item).strip()
            }
        )
        write_json_atomic(self.exclusion_path, {"packages": excluded})
        return self.summary()

    def clear_package_selection(self):
        """Exclude every package currently represented by a replacement record."""
        if self.is_running():
            raise ValueError("补丁任务运行期间不能修改勾选包")
        packages = set(self._selected_packages())
        packages.update(self._excluded_packages())
        packages.update(
            str(record.get("package", "")).strip()
            for record in self.replacement_provider()
            if str(record.get("package", "")).strip()
        )
        write_json_atomic(self.selection_path, {"packages": []})
        write_json_atomic(self.exclusion_path, {"packages": sorted(packages)})
        return self.summary()

    def _pointer_manifest(self, pointer_path):
        pointer = self._read_json(pointer_path)
        if not pointer:
            return None, None
        relative = pointer.get("manifest")
        if not relative:
            return None, None
        manifest_path = self.safe_join(self.workspace, relative)
        manifest = self._read_json(manifest_path)
        return manifest_path, manifest

    @staticmethod
    def safe_join(root, relative):
        root = Path(root).resolve()
        candidate = (root / Path(relative)).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"路径超出允许目录：{candidate}")
        return candidate

    @staticmethod
    def relative_to(path, root):
        path = Path(path).resolve()
        root = Path(root).resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"路径超出允许目录：{path}")
        return str(path.relative_to(root))

    def append(self, message):
        stamped = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        with self.lock:
            self.lines.append(stamped)
            self.state["message"] = message

    def is_running(self):
        with self.lock:
            return bool(self.state["running"])

    def _inventory(self):
        packages_path = self.texture_root / "inventory" / "packages.csv"
        textures_path = self.texture_root / "inventory" / "textures.csv"
        if not packages_path.is_file() or not textures_path.is_file():
            raise FileNotFoundError("缺少纹理目录，请先运行解包工具")
        packages = {
            Path(row["name"]).stem: row for row in read_csv(packages_path)
        }
        textures = {}
        for row in read_csv(textures_path):
            key = (
                row["package"],
                row["group_label"],
                int(row["embedded_index"]),
            )
            textures[key] = row
        return packages, textures

    def collect_plan(self):
        records = self.replacement_provider()
        excluded_packages = set(self._excluded_packages())
        packages, textures = self._inventory()
        grouped = {}
        for record in records:
            package = record["package"]
            if package in excluded_packages:
                continue
            key = (
                package,
                record["group"],
                int(record["embedded_index"]),
            )
            texture = textures.get(key)
            if texture is None:
                raise ValueError(
                    f"替换图没有对应纹理目录记录：{record['texture_id']}"
                )
            if texture.get("storage_format") != "bc7":
                raise ValueError(
                    f"暂不支持部署 {texture.get('storage_format')} 纹理："
                    f"{record['texture_id']}"
                )
            package_row = packages.get(package)
            if package_row is None:
                raise ValueError(f"纹理包不在扫描目录中：{package}")
            source = (
                self.game_root
                / "data"
                / "x64"
                / "dplcache_release"
                / f"{package}.fhm2d"
            ).resolve()
            self.relative_to(source, self.game_root)
            if not source.is_file():
                raise FileNotFoundError(f"游戏纹理包不存在：{source}")
            replacement = Path(record["replacement_file"]).resolve()
            if not replacement.is_file():
                raise FileNotFoundError(f"替换图不存在：{replacement}")
            item = grouped.setdefault(
                package,
                {
                    "package": package,
                    "source": source,
                    "source_sha256": None,
                    "replacements": [],
                },
            )
            item["replacements"].append(
                {
                    **record,
                    "replacement_file": replacement,
                    "replacement_sha256": sha256_file(replacement),
                    "png_file": Path(record["source_png"]).name,
                }
            )
        target_packages = (set(grouped) | set(self._selected_packages())) - excluded_packages
        if not target_packages:
            return []
        result = []
        for package in sorted(target_packages):
            item = grouped.get(
                package,
                {
                    "package": package,
                    "source": (
                        self.game_root
                        / "data"
                        / "x64"
                        / "dplcache_release"
                        / f"{package}.fhm2d"
                    ).resolve(),
                    "source_sha256": None,
                    "replacements": [],
                },
            )
            self.relative_to(item["source"], self.game_root)
            if not item["source"].is_file():
                raise FileNotFoundError(
                    f"游戏纹理包不存在：{item['source']}"
                )
            item["replacements"].sort(key=lambda row: row["texture_id"])
            result.append(item)
        return result

    @staticmethod
    def _fingerprint(plan):
        digest = hashlib.sha256()
        for package in plan:
            digest.update(package["package"].encode("utf-8"))
            for replacement in package["replacements"]:
                digest.update(replacement["texture_id"].encode("utf-8"))
                digest.update(
                    replacement["replacement_sha256"].encode("ascii")
                )
        return digest.hexdigest()

    def _project_group_aliases(self, package, project_dir):
        """Map stable database group labels to labels emitted by extraction."""
        project_rows = read_csv(project_dir / "textures.csv")
        project_groups = []
        for row in project_rows:
            label = row["group_label"]
            if label not in project_groups:
                project_groups.append(label)
        mapping_groups = sorted(
            {
                row["group"]
                for row in read_csv(self.workspace / "asset-mapping" / "groups.csv")
                if row["package"] == package
            }
        )
        if len(mapping_groups) == len(project_groups):
            return dict(zip(mapping_groups, project_groups))
        return {label: label for label in mapping_groups}

    def _assert_game_stopped(self):
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_snapshot = kernel32.CreateToolhelp32Snapshot
        create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        create_snapshot.restype = wintypes.HANDLE
        process_first = kernel32.Process32FirstW
        process_first.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessEntry),
        ]
        process_first.restype = wintypes.BOOL
        process_next = kernel32.Process32NextW
        process_next.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessEntry),
        ]
        process_next.restype = wintypes.BOOL
        snapshot = create_snapshot(0x00000002, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if snapshot == invalid_handle:
            raise ValueError(
                "无法检查游戏进程，请手动关闭游戏后重试"
            )
        running = []
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(ProcessEntry)
            success = process_first(snapshot, ctypes.byref(entry))
            while success:
                name = entry.szExeFile
                lowered = name.lower()
                if (
                    lowered.startswith("vsac")
                    and lowered.endswith("_release.exe")
                ):
                    running.append(name)
                success = process_next(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        if running:
            raise ValueError(
                "请先关闭游戏进程：" + "、".join(sorted(set(running)))
            )

    def _active_deployment(self):
        path, manifest = self._pointer_manifest(
            self.latest_deployment_path
        )
        if manifest and manifest.get("status") in {
            "deploying",
            "deployed",
            "rollback_failed",
        }:
            return path, manifest
        return None, None

    def _latest_build(self):
        return self._pointer_manifest(self.latest_build_path)

    def _build_uses_current_baselines(self, build):
        if not build:
            return False
        for item in build.get("packages", []):
            expected = item.get("build_source_sha256")
            baseline = self.baselines_root / f"{item.get('package', '')}.fhm2d"
            if not expected or not baseline.is_file():
                return False
            if sha256_file(baseline) != expected:
                return False
        return True

    def summary(self):
        selected_packages = self._selected_packages()
        excluded_packages = self._excluded_packages()
        try:
            plan = self.collect_plan()
            plan_error = None
        except (FileNotFoundError, ValueError, OSError) as exc:
            plan = []
            plan_error = str(exc)
        build_path, build = self._latest_build()
        deployment_path, deployment = self._pointer_manifest(
            self.latest_deployment_path
        )
        with self.lock:
            state = dict(self.state)
            lines = list(self.lines)
        current_fingerprint = self._fingerprint(plan) if plan else None
        build_current = bool(
            build
            and build.get("status") == "built"
            and build.get("replacement_fingerprint")
            == current_fingerprint
            and self._build_uses_current_baselines(build)
        )
        active_deployment = bool(
            deployment
            and deployment.get("status")
            in {"deploying", "deployed", "rollback_failed"}
        )
        can_build_while_deployed = (
            deployment
            and deployment.get("status") == "deployed"
        )
        return {
            **state,
            "log_lines": lines,
            "selected_packages": selected_packages,
            "excluded_packages": excluded_packages,
            "replacement_count": sum(
                len(item["replacements"]) for item in plan
            ),
            "affected_packages": [item["package"] for item in plan],
            "affected_package_count": len(plan),
            "plan_error": plan_error,
            "latest_build": (
                {
                    "id": build.get("id"),
                    "created_utc": build.get("created_utc"),
                    "package_count": len(build.get("packages", [])),
                    "replacement_count": build.get(
                        "replacement_count", 0
                    ),
                    "current": build_current,
                    "manifest": str(build_path) if build_path else None,
                }
                if build
                else None
            ),
            "latest_deployment": (
                {
                    "id": deployment.get("id"),
                    "created_utc": deployment.get("created_utc"),
                    "restored_utc": deployment.get("restored_utc"),
                    "status": deployment.get("status"),
                    "package_count": len(deployment.get("packages", [])),
                    "manifest": (
                        str(deployment_path) if deployment_path else None
                    ),
                }
                if deployment
                else None
            ),
            # A completed deployment can serve as the base for preparing the
            # next build. Deploying it still requires restoring the current
            # backup first; interrupted or failed rollbacks remain locked.
            "can_build": bool(plan)
            and (not active_deployment or can_build_while_deployed),
            "can_deploy": build_current and not active_deployment,
            "can_restore": active_deployment,
        }

    def start(self, action):
        actions = {
            "build": ("构建补丁", self._build),
            "deploy": ("备份并部署", self._deploy),
            "restore": ("恢复备份", self._restore),
        }
        if action not in actions:
            raise ValueError("未知补丁操作")
        with self.lock:
            if self.state["running"]:
                raise ValueError("已有补丁任务正在运行")
        readiness = self.summary()
        allowed = {
            "build": readiness["can_build"],
            "deploy": readiness["can_deploy"],
            "restore": readiness["can_restore"],
        }
        if not allowed[action]:
            reasons = {
                "build": "没有可构建的替换图，或当前部署未完成恢复",
                "deploy": "没有与当前替换图匹配的最新构建",
                "restore": "没有可恢复的活动备份",
            }
            raise ValueError(readiness.get("plan_error") or reasons[action])
        with self.lock:
            if self.state["running"]:
                raise ValueError("已有补丁任务正在运行")
            self.lines.clear()
            self.state.update(
                {
                    "running": True,
                    "action": action,
                    "label": actions[action][0],
                    "started_at": utc_now(),
                    "finished_at": None,
                    "message": "正在准备",
                    "error": None,
                }
            )
        self.thread = threading.Thread(
            target=self._worker,
            args=(actions[action][1],),
            daemon=True,
        )
        self.thread.start()
        return self.summary()

    def _worker(self, function):
        error = None
        try:
            function()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.append(error)
        with self.lock:
            self.state.update(
                {
                    "running": False,
                    "finished_at": utc_now(),
                    "error": error,
                    "message": "任务完成" if not error else "任务失败",
                }
            )

    def _build(self):
        _, active = self._active_deployment()
        if active and active.get("status") != "deployed":
            raise ValueError("当前部署未完成恢复，暂不能构建新补丁")
        plan = self.collect_plan()
        if not plan:
            raise ValueError("没有替换图可供构建")
        build_id = self._identifier("build")
        output_root = self.outputs_root / build_id
        output_root.mkdir(parents=True, exist_ok=False)
        self.projects_root.mkdir(parents=True, exist_ok=True)
        fingerprint = self._fingerprint(plan)
        packages = []
        replacement_count = sum(
            len(item["replacements"]) for item in plan
        )
        self.append(
            f"定位到 {replacement_count} 张替换图，"
            f"涉及 {len(plan)} 个 FHM2D 包"
        )
        for index, item in enumerate(plan, 1):
            package = item["package"]
            source = item["source"]
            installed_source = source
            if active and active.get("status") == "deployed":
                deployed_item = next(
                    (
                        row
                        for row in active.get("packages", [])
                        if row.get("package") == package
                    ),
                    None,
                )
                if deployed_item:
                    installed_source = self.safe_join(
                        self.workspace, deployed_item["backup"]
                    )
                    if not installed_source.is_file():
                        raise FileNotFoundError(
                            f"活动部署的备份文件不存在：{package}"
                        )
            installed_source_hash = sha256_file(installed_source)
            self.baselines_root.mkdir(parents=True, exist_ok=True)
            build_source = self.baselines_root / f"{package}.fhm2d"
            if not build_source.is_file():
                self._atomic_install(
                    installed_source, build_source, installed_source_hash
                )
                self.append(f"{package}：已保存首次构建基底")
            build_source_hash = sha256_file(build_source)
            self.append(
                f"[{index}/{len(plan)}] 从固定基底导出 {package}"
            )
            _, project_dir = export_project(
                build_source,
                self.projects_root,
                self.texconv,
                force=True,
            )
            group_aliases = self._project_group_aliases(package, project_dir)
            project_rows = read_csv(project_dir / "textures.csv")
            for replacement in item["replacements"]:
                project_group = group_aliases.get(
                    replacement["group"], replacement["group"]
                )
                matching_rows = [
                    row
                    for row in project_rows
                    if row["group_label"] == project_group
                    and int(row["embedded_index"])
                    == int(replacement["embedded_index"])
                ]
                if len(matching_rows) == 1:
                    png_name = (
                        Path(matching_rows[0]["dds_output"]).stem + ".png"
                    )
                else:
                    png_name = replacement["png_file"]
                target = project_dir / "png_edit" / png_name
                if not target.is_file():
                    raise FileNotFoundError(
                        f"回包工程缺少目标纹理：{target.name}"
                    )
                shutil.copy2(replacement["replacement_file"], target)
            _, _, _, status_rows = project_status(project_dir)
            modified = [row for row in status_rows if row["modified"]]
            expected_ids = {
                replacement["texture_id"]
                for replacement in item["replacements"]
            }
            modified_keys = set()
            for row in modified:
                mapping_group = next(
                    (
                        candidate
                        for candidate, project_group in group_aliases.items()
                        if project_group == row["group_label"]
                    ),
                    row["group_label"],
                )
                modified_keys.add(
                    f"{package}/{mapping_group}/{int(row['embedded_index']):05d}"
                )
            unchanged = sorted(expected_ids - modified_keys)
            if unchanged:
                self.append(
                    f"{package}：{len(unchanged)} 张替换图与原图相同"
                )
            output = output_root / f"{package}.fhm2d"
            report = build_project(
                project_dir, output, self.texconv, force=False
            )
            packages.append(
                {
                    "package": package,
                    "source": self.relative_to(source, self.game_root),
                    "source_sha256": installed_source_hash,
                    "build_source": self.relative_to(
                        build_source, self.workspace
                    ),
                    "build_source_sha256": build_source_hash,
                    "output": self.relative_to(output, self.workspace),
                    "output_sha256": sha256_file(output),
                    "modified_texture_count": report[
                        "modified_texture_count"
                    ],
                    "replacement_texture_ids": sorted(expected_ids),
                }
            )
            self.append(
                f"{package} 构建完成，修改 "
                f"{report['modified_texture_count']} 张纹理"
            )
        current_plan = self.collect_plan()
        if self._fingerprint(current_plan) != fingerprint:
            raise ValueError("构建期间替换图发生变化，请重新构建")
        manifest = {
            "version": 1,
            "id": build_id,
            "status": "built",
            "created_utc": utc_now(),
            "game_root": str(self.game_root),
            "replacement_fingerprint": fingerprint,
            "replacement_count": replacement_count,
            "packages": packages,
        }
        manifest_path = self.manifests_root / f"{build_id}.json"
        write_json_atomic(manifest_path, manifest)
        write_json_atomic(
            self.latest_build_path,
            {"manifest": self.relative_to(manifest_path, self.workspace)},
        )
        self.append(f"补丁构建完成：{len(packages)} 个包")

    def _atomic_install(self, source, destination, expected_hash):
        source = Path(source)
        destination = Path(destination)
        temporary = destination.with_name(
            destination.name + ".exvs-patch.tmp"
        )
        try:
            shutil.copy2(source, temporary)
            if sha256_file(temporary) != expected_hash:
                raise ValueError(f"复制校验失败：{destination.name}")
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _deploy(self):
        self._assert_game_stopped()
        if self._active_deployment()[1]:
            raise ValueError("已有补丁处于部署状态，请先恢复备份")
        build_path, build = self._latest_build()
        if not build or build.get("status") != "built":
            raise ValueError("没有可部署的构建结果")
        if not self._build_uses_current_baselines(build):
            raise ValueError("构建基底已变化，请重新构建补丁")
        plan = self.collect_plan()
        if build.get("replacement_fingerprint") != self._fingerprint(plan):
            raise ValueError("替换图已变化，请重新构建补丁")
        deploy_id = self._identifier("deploy")
        backup_root = self.backups_root / deploy_id
        backup_root.mkdir(parents=True, exist_ok=False)
        packages = []
        self.append(f"开始备份 {len(build['packages'])} 个游戏文件")
        for item in build["packages"]:
            original = self.safe_join(self.game_root, item["source"])
            output = self.safe_join(self.workspace, item["output"])
            if sha256_file(original) != item["source_sha256"]:
                raise ValueError(
                    f"游戏文件已变化，拒绝部署：{item['package']}"
                )
            if sha256_file(output) != item["output_sha256"]:
                raise ValueError(
                    f"构建文件校验失败：{item['package']}"
                )
            backup = backup_root / item["source"]
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, backup)
            if sha256_file(backup) != item["source_sha256"]:
                raise ValueError(f"备份校验失败：{item['package']}")
            packages.append(
                {
                    **item,
                    "backup": self.relative_to(backup, self.workspace),
                }
            )
        manifest = {
            "version": 1,
            "id": deploy_id,
            "status": "deploying",
            "created_utc": utc_now(),
            "build_manifest": self.relative_to(
                build_path, self.workspace
            ),
            "packages": packages,
        }
        manifest_path = self.manifests_root / f"{deploy_id}.json"
        write_json_atomic(manifest_path, manifest)
        write_json_atomic(
            self.latest_deployment_path,
            {"manifest": self.relative_to(manifest_path, self.workspace)},
        )
        deployed = []
        try:
            for item in packages:
                original = self.safe_join(self.game_root, item["source"])
                output = self.safe_join(self.workspace, item["output"])
                self._atomic_install(
                    output, original, item["output_sha256"]
                )
                deployed.append(item)
                self.append(f"已部署 {item['package']}")
        except Exception:
            rollback_error = None
            for item in reversed(deployed):
                try:
                    original = self.safe_join(
                        self.game_root, item["source"]
                    )
                    backup = self.safe_join(
                        self.workspace, item["backup"]
                    )
                    self._atomic_install(
                        backup, original, item["source_sha256"]
                    )
                except Exception as exc:
                    rollback_error = str(exc)
            manifest["status"] = (
                "rollback_failed" if rollback_error else "rolled_back"
            )
            manifest["failed_utc"] = utc_now()
            manifest["rollback_error"] = rollback_error
            write_json_atomic(manifest_path, manifest)
            raise
        manifest["status"] = "deployed"
        manifest["deployed_utc"] = utc_now()
        write_json_atomic(manifest_path, manifest)
        self.append("备份和部署完成")

    def _restore(self):
        self._assert_game_stopped()
        manifest_path, deployment = self._active_deployment()
        if not deployment:
            raise ValueError("没有可恢复的活动备份")
        for item in deployment["packages"]:
            original = self.safe_join(self.game_root, item["source"])
            backup = self.safe_join(self.workspace, item["backup"])
            if not backup.is_file():
                raise FileNotFoundError(
                    f"备份文件不存在：{item['package']}"
                )
            if sha256_file(backup) != item["source_sha256"]:
                raise ValueError(f"备份校验失败：{item['package']}")
            current_hash = sha256_file(original)
            if current_hash not in {
                item["source_sha256"],
                item["output_sha256"],
            }:
                raise ValueError(
                    f"当前游戏文件不是本工具部署的版本，拒绝覆盖："
                    f"{item['package']}"
                )
        restored = []
        try:
            for item in deployment["packages"]:
                original = self.safe_join(
                    self.game_root, item["source"]
                )
                backup = self.safe_join(
                    self.workspace, item["backup"]
                )
                self._atomic_install(
                    backup, original, item["source_sha256"]
                )
                restored.append(item)
                self.append(f"已恢复 {item['package']}")
        except Exception:
            for item in reversed(restored):
                try:
                    original = self.safe_join(
                        self.game_root, item["source"]
                    )
                    output = self.safe_join(
                        self.workspace, item["output"]
                    )
                    self._atomic_install(
                        output, original, item["output_sha256"]
                    )
                except Exception:
                    pass
            raise
        deployment["status"] = "restored"
        deployment["restored_utc"] = utc_now()
        write_json_atomic(manifest_path, deployment)
        self.append(f"已恢复 {len(restored)} 个游戏文件")
