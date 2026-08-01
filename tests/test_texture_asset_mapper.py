import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image


CORE_DIR = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE_DIR))

from texture_asset_mapper import build_command, validate_command


class SingleTextureMappingTests(unittest.TestCase):
    def test_builds_lazy_single_texture_composition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texture_root = root / "all-textures"
            inventory = texture_root / "inventory"
            png = (
                texture_root
                / "packages"
                / "pending"
                / "0x12345678"
                / "png"
                / "g00_00000_458x680_bc7.png"
            )
            inventory.mkdir(parents=True)
            png.parent.mkdir(parents=True)
            Image.new("RGBA", (458, 680), (10, 20, 30, 255)).save(png)
            fields = [
                "package", "group_label", "texture_index", "embedded_index",
                "embedded_name", "width", "height", "storage_format", "pixel_sha256",
                "png_output",
            ]
            row = {
                "package": "0x12345678",
                "group_label": "g00",
                "texture_index": "0",
                "embedded_index": "0",
                "embedded_name": "46XTms_card_018_001_001",
                "width": "458",
                "height": "680",
                "storage_format": "bc7",
                "pixel_sha256": "a" * 64,
                "png_output": str(png.relative_to(texture_root)),
            }
            with (inventory / "textures.csv").open(
                "w", encoding="utf-8-sig", newline=""
            ) as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerow(row)

            mapping_root = root / "asset-mapping"
            result = build_command(
                SimpleNamespace(
                    texture_root=str(texture_root),
                    output=str(mapping_root),
                    force=False,
                    preview_size=128,
                )
            )
            mapping = json.loads(
                (mapping_root / "mapping.json").read_text(encoding="utf-8")
            )
            composition_path = (
                mapping_root / mapping["compositions"][0]["composition"]
            )
            composition = json.loads(
                composition_path.read_text(encoding="utf-8")
            )
            with (mapping_root / "groups.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                group = next(csv.DictReader(stream))

            self.assertEqual(result, 0)
            self.assertEqual(mapping["mapping_version"], 2)
            self.assertEqual(mapping["previews_mode"], "lazy")
            self.assertEqual(
                mapping["category_counts"], {"favorite_mobile_suit": 1}
            )
            self.assertEqual(composition["presentation"], "single_texture")
            self.assertEqual(composition["body"]["source_group"], "g00")
            self.assertFalse((mapping_root / group["preview"]).is_file())

            validation = validate_command(
                SimpleNamespace(
                    mapping=str(mapping_root / "mapping.json"),
                    output=str(mapping_root / "validation.json"),
                    preview_size=128,
                )
            )
            self.assertEqual(validation, 0)


if __name__ == "__main__":
    unittest.main()
