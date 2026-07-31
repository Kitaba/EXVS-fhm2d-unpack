import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path


CORE_DIR = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE_DIR))

from replan_texture_packages import (
    discover_packages,
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


if __name__ == "__main__":
    unittest.main()
