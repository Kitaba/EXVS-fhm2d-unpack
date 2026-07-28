import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


CORE_DIR = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE_DIR))

from mapping_database import (
    catalog_signatures,
    find_database,
    refresh_mapping_source_paths,
)


FIELDS = (
    "package",
    "group_label",
    "embedded_index",
    "width",
    "height",
    "storage_format",
    "pixel_sha256",
)


class MappingDatabaseSignatureTests(unittest.TestCase):
    def write_catalog(self, path, pixel_hash):
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerow(
                {
                    "package": "0x12345678",
                    "group_label": "g00",
                    "embedded_index": "0",
                    "width": "872",
                    "height": "960",
                    "storage_format": "bc7",
                    "pixel_sha256": pixel_hash,
                }
            )

    def test_layout_signature_ignores_pixel_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.csv"
            second = root / "second.csv"
            self.write_catalog(first, "a" * 64)
            self.write_catalog(second, "b" * 64)

            first_signatures = catalog_signatures(first)
            second_signatures = catalog_signatures(second)

        self.assertNotEqual(
            first_signatures["catalog"], second_signatures["catalog"]
        )
        self.assertEqual(
            first_signatures["layout"], second_signatures["layout"]
        )
        self.assertEqual(first_signatures["texture_count"], 1)

    def test_database_falls_back_to_matching_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "vsac29"
            database.mkdir()
            (database / "database.json").write_text(
                json.dumps(
                    {
                        "catalog_signature": "original-pixels",
                        "catalog_layout_signature": "same-layout",
                        "texture_count": 1,
                    }
                ),
                encoding="utf-8",
            )

            selected, manifest, match_mode = find_database(
                root,
                signature="modified-pixels",
                layout_signature="same-layout",
                texture_count=1,
            )

        self.assertEqual(selected.name, "vsac29")
        self.assertEqual(manifest["catalog_signature"], "original-pixels")
        self.assertEqual(match_mode, "layout")

    def test_database_falls_back_to_sorted_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "vsac29"
            database.mkdir()
            (database / "database.json").write_text(
                json.dumps(
                    {
                        "catalog_signature": "original-pixels",
                        "catalog_layout_signature": "different-order",
                        "catalog_layout_sorted_signature": "same-layout",
                        "texture_count": 1,
                    }
                ),
                encoding="utf-8",
            )

            selected, manifest, match_mode = find_database(
                root,
                signature="modified-pixels",
                layout_signature="different-layout",
                layout_sorted_signature="same-layout",
                texture_count=1,
            )

        self.assertEqual(selected.name, "vsac29")
        self.assertEqual(match_mode, "layout_sorted")

    def test_database_rejects_incomplete_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "vsac29"
            database.mkdir()
            (database / "database.json").write_text(
                json.dumps(
                    {
                        "catalog_signature": "exact",
                        "catalog_layout_signature": "layout",
                        "texture_count": 2,
                    }
                ),
                encoding="utf-8",
            )

            selected, manifest, match_mode = find_database(
                root,
                signature="exact",
                layout_signature="layout",
                texture_count=1,
            )

        self.assertIsNone(selected)
        self.assertIsNone(manifest)
        self.assertIsNone(match_mode)

    def test_install_refreshes_classified_source_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "textures.csv"
            mapping_root = root / "mapping"
            composition_path = (
                mapping_root
                / "projects"
                / "outgame_navigator"
                / "0x12345678"
                / "g00"
                / "composition.json"
            )
            composition_path.parent.mkdir(parents=True)
            self.write_catalog(catalog, "a" * 64)
            with catalog.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            rows[0]["png_output"] = (
                "packages\\outgame_navigator\\0x12345678\\png\\body.png"
            )
            fields = list(rows[0])
            with catalog.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            layer = {
                "texture_id": "0x12345678/g00/00000",
                "source_png": "packages\\0x12345678\\png\\body.png",
            }
            with (mapping_root / "layers.csv").open(
                "w", encoding="utf-8", newline=""
            ) as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=["texture_id", "source_png"]
                )
                writer.writeheader()
                writer.writerow(layer)
            composition_path.write_text(
                json.dumps({"body": layer, "families": []}),
                encoding="utf-8",
            )
            (mapping_root / "mapping.json").write_text(
                json.dumps(
                    {
                        "compositions": [
                            {
                                "composition": str(
                                    composition_path.relative_to(mapping_root)
                                )
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            refresh_mapping_source_paths(mapping_root, catalog)

            composition = json.loads(
                composition_path.read_text(encoding="utf-8")
            )
            with (mapping_root / "layers.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                installed_layer = next(csv.DictReader(stream))

        expected = (
            "packages\\outgame_navigator\\0x12345678\\png\\body.png"
        )
        self.assertEqual(composition["body"]["source_png"], expected)
        self.assertEqual(installed_layer["source_png"], expected)


if __name__ == "__main__":
    unittest.main()
