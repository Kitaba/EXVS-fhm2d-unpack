import json
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path


CORE_DIR = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE_DIR))

from replan_texture_packages import (
    discover_packages,
    move_package,
    rewrite_composition_text,
    rewrite_json_paths,
    rewrite_known_package_path,
    rewrite_package_path,
)


class ReclassificationMoveTests(unittest.TestCase):
    def test_existing_category_is_moved_when_mapping_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "pending" / "0x12345678"
            source.mkdir(parents=True)
            categories = defaultdict(set)
            categories["0x12345678"].add("awakening")

            package_to_category, moves = discover_packages(root, categories)

        self.assertEqual(package_to_category["0x12345678"], "awakening")
        self.assertEqual(moves, [(source, "awakening")])

    def test_current_category_is_not_moved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "match_mobile_suit" / "0x12345678"
            source.mkdir(parents=True)
            categories = defaultdict(set)
            categories["0x12345678"].add("match_mobile_suit")

            package_to_category, moves = discover_packages(root, categories)

        self.assertEqual(
            package_to_category["0x12345678"], "match_mobile_suit"
        )
        self.assertEqual(moves, [])

    def test_rewrites_flat_and_previously_categorized_paths(self):
        flat = r"packages\0x12345678\png\image.png"
        old = r"packages\pending\0x12345678\png\image.png"
        absolute = (
            r"D:\workspace\packages\combat_portrait"
            r"\0x12345678\png\image.png"
        )
        expected_tail = r"packages\awakening\0x12345678\png\image.png"

        self.assertEqual(
            rewrite_package_path(flat, "0x12345678", "awakening"),
            expected_tail,
        )
        self.assertEqual(
            rewrite_package_path(old, "0x12345678", "awakening"),
            expected_tail,
        )
        self.assertTrue(
            rewrite_package_path(
                absolute, "0x12345678", "awakening"
            ).endswith(expected_tail)
        )

    def test_directly_rewrites_only_known_package(self):
        categories = {"0x12345678": "awakening"}
        known = r"packages\pending\0x12345678\png\image.png"
        unknown = r"packages\pending\0x87654321\png\image.png"

        self.assertEqual(
            rewrite_known_package_path(known, categories),
            r"packages\awakening\0x12345678\png\image.png",
        )
        self.assertEqual(
            rewrite_known_package_path(unknown, categories), unknown
        )

    def test_json_rewrite_uses_nested_source_paths(self):
        data = {
            "body": {
                "source_png": (
                    r"packages\pending\0x12345678\png\body.png"
                )
            },
            "states": [
                {
                    "layers": [
                        {
                            "source_png": (
                                r"packages\pending\0x12345678"
                                r"\png\face.png"
                            )
                        }
                    ]
                }
            ],
        }

        changed = rewrite_json_paths(
            data, {"0x12345678": "awakening"}
        )

        self.assertEqual(changed, 2)
        self.assertIn(r"packages\awakening", data["body"]["source_png"])

    def test_stream_rewrites_only_source_png_values(self):
        text = json.dumps(
            {
                "source_png": (
                    r"packages\pending\0x12345678\png\body.png"
                ),
                "unrelated": (
                    r"packages\pending\0x12345678\png\keep.png"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )

        rewritten, changed = rewrite_composition_text(
            text, {"0x12345678": "awakening"}
        )
        data = json.loads(rewritten)

        self.assertEqual(changed, 1)
        self.assertIn("awakening", data["source_png"])
        self.assertIn("pending", data["unrelated"])

    def test_atomic_directory_move(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "pending" / "0x12345678"
            target = root / "awakening" / "0x12345678"
            source.mkdir(parents=True)
            target.parent.mkdir(parents=True)
            (source / "texture.png").write_bytes(b"png")

            move_package(source, target)

            self.assertFalse(source.exists())
            self.assertEqual((target / "texture.png").read_bytes(), b"png")


if __name__ == "__main__":
    unittest.main()
