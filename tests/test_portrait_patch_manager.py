import csv
import sys
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
