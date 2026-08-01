import csv
import sys
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock, patch


CORE_ROOT = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE_ROOT))

from portrait_patch_manager import PortraitPatchManager


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class PortraitPatchManagerTests(unittest.TestCase):
    def test_collect_plan_accepts_rgba8_texture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_root = root / "game"
            workspace = root / "workspace"
            texture_root = workspace / "all-textures"
            package = "0x49235031"
            source = (
                game_root
                / "data"
                / "x64"
                / "dplcache_release"
                / f"{package}.fhm2d"
            )
            source.parent.mkdir(parents=True)
            source.write_bytes(b"fhm2d")
            replacement = root / "replacement.png"
            replacement.write_bytes(b"png")

            write_csv(
                texture_root / "inventory" / "packages.csv",
                ["name"],
                [{"name": f"{package}.fhm2d"}],
            )
            write_csv(
                texture_root / "inventory" / "textures.csv",
                ["package", "group_label", "embedded_index", "storage_format"],
                [
                    {
                        "package": package,
                        "group_label": "g00",
                        "embedded_index": "46",
                        "storage_format": "rgba8",
                    }
                ],
            )

            manager = PortraitPatchManager.__new__(PortraitPatchManager)
            manager.game_root = game_root.resolve()
            manager.workspace = workspace.resolve()
            manager.texture_root = texture_root.resolve()
            manager.root = workspace / "patch-build"
            manager.selection_path = manager.root / "selected-packages.json"
            manager.exclusion_path = manager.root / "excluded-packages.json"
            manager.replacement_provider = lambda: [
                {
                    "package": package,
                    "group": "g00",
                    "embedded_index": 46,
                    "texture_id": f"{package}/g00/00046",
                    "replacement_file": str(replacement),
                    "source_png": "00046.png",
                }
            ]

            plan = manager.collect_plan()

            self.assertEqual(len(plan), 1)
            self.assertEqual(plan[0]["package"], package)
            self.assertEqual(
                plan[0]["replacements"][0]["texture_id"],
                f"{package}/g00/00046",
            )

    def test_collect_plan_skips_selected_package_without_replacements(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_root = root / "game"
            workspace = root / "workspace"
            texture_root = workspace / "all-textures"
            package = "0xF8812363"
            source = (
                game_root
                / "data"
                / "x64"
                / "dplcache_release"
                / f"{package}.fhm2d"
            )
            source.parent.mkdir(parents=True)
            source.write_bytes(b"fhm2d")
            write_csv(
                texture_root / "inventory" / "packages.csv",
                ["name"],
                [{"name": f"{package}.fhm2d"}],
            )
            write_csv(
                texture_root / "inventory" / "textures.csv",
                ["package", "group_label", "embedded_index", "storage_format"],
                [],
            )

            manager = PortraitPatchManager.__new__(PortraitPatchManager)
            manager.game_root = game_root.resolve()
            manager.workspace = workspace.resolve()
            manager.texture_root = texture_root.resolve()
            manager.root = workspace / "patch-build"
            manager.selection_path = manager.root / "selected-packages.json"
            manager.exclusion_path = manager.root / "excluded-packages.json"
            manager.selection_path.parent.mkdir(parents=True)
            manager.selection_path.write_text(
                '{"packages":["0xF8812363"]}', encoding="utf-8"
            )
            manager.replacement_provider = lambda: []

            self.assertEqual(manager.collect_plan(), [])

    def test_running_summary_does_not_rebuild_plan(self):
        manager = PortraitPatchManager.__new__(PortraitPatchManager)
        manager.lock = threading.Lock()
        manager.lines = deque(["building"])
        manager.state = {
            "running": True,
            "action": "build",
            "label": "build",
            "started_at": "now",
            "finished_at": None,
            "message": "building",
            "error": None,
        }
        manager._running_plan_summary = {
            "plan_error": None,
            "replacement_count": 2,
            "affected_packages": ["0xAAAA0001"],
        }
        manager.collect_plan = Mock(side_effect=AssertionError("full scan"))
        manager._selected_packages = lambda: ["0xAAAA0001"]
        manager._excluded_packages = lambda: []
        manager._latest_build = lambda: (None, None)
        manager._pointer_manifest = lambda path: (None, None)
        manager.latest_deployment_path = Path("latest-deployment.json")

        summary = manager.summary()

        manager.collect_plan.assert_not_called()
        self.assertEqual(summary["replacement_count"], 2)
        self.assertEqual(summary["affected_package_count"], 1)

    def test_inventory_reuses_index_until_manifest_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            texture_root = Path(temporary)
            inventory = texture_root / "inventory"
            inventory.mkdir()
            packages_path = inventory / "packages.csv"
            textures_path = inventory / "textures.csv"
            packages_path.write_text("name\n", encoding="utf-8")
            textures_path.write_text(
                "package,group_label,embedded_index\n", encoding="utf-8"
            )
            manager = PortraitPatchManager.__new__(PortraitPatchManager)
            manager.texture_root = texture_root
            manager._inventory_cache = None

            with patch(
                "portrait_patch_manager.read_csv",
                side_effect=[
                    [{"name": "0x12345678.fhm2d"}],
                    [
                        {
                            "package": "0x12345678",
                            "group_label": "g00",
                            "embedded_index": "0",
                        }
                    ],
                ],
            ) as reader:
                first = manager._inventory()
                second = manager._inventory()

            self.assertIs(first[0], second[0])
            self.assertIs(first[1], second[1])
            self.assertEqual(reader.call_count, 2)


if __name__ == "__main__":
    unittest.main()
