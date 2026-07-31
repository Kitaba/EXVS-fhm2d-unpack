import sys
import unittest
from pathlib import Path


CORE_DIR = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE_DIR))

from texture_classification import (
    awakening_common_group,
    classify_package_assets,
)


def texture(group, index, width, height, storage_format="bc7"):
    return {
        "group_label": group,
        "texture_index": index,
        "embedded_index": index,
        "width": width,
        "height": height,
        "storage_format": storage_format,
    }


def awakening_rows(include_all_anchors=True, third_group=False):
    rows = [
        texture("g00", 0, 800, 1000),
        texture("g00", 1, 800, 1000),
        texture("g00", 2, 800, 1000),
        texture("g00", 3, 760, 1000),
        texture("g00", 4, 1064, 1180),
        texture("g00", 5, 320, 160),
        texture("g01", 6, 8, 8),
        texture("g01", 7, 1536, 592),
        texture("g01", 8, 468, 492),
    ]
    if not include_all_anchors:
        rows = [row for row in rows if row["texture_index"] != 3]
    if third_group:
        rows.append(texture("g02", 9, 300, 300))
    return rows


class TextureClassificationTests(unittest.TestCase):
    def test_dimension_categories(self):
        cases = {
            (458, 680): "favorite_mobile_suit",
            (1020, 680): "select_navigator",
            (1920, 1080): "select_mobile_suit",
            (840, 432): "match_mobile_suit",
        }
        for dimensions, expected in cases.items():
            with self.subTest(dimensions=dimensions):
                rows = [texture("g00", 0, *dimensions)]
                result = classify_package_assets("0x12345678", rows)
                self.assertEqual(result["category"], expected)
                self.assertEqual(result["rows"], rows)

    def test_special_thumbnail_package_uses_package_id(self):
        rows = [
            texture("g00", 0, 228, 104, "rgba8"),
            texture("g00", 1, 228, 104, "rgba8"),
        ]
        result = classify_package_assets("0x49235031", rows)
        self.assertEqual(
            result["category"], "select_mobile_suit_thumbnail"
        )
        self.assertEqual(len(result["rows"]), 2)

    def test_awakening_rule_allows_one_missing_anchor_and_third_group(self):
        rows = awakening_rows(include_all_anchors=False, third_group=True)
        self.assertEqual(awakening_common_group(rows), "g00")
        result = classify_package_assets("0x12345678", rows)
        self.assertEqual(result["category"], "awakening")
        self.assertEqual(result["common_group"], "g00")
        self.assertTrue(all(row["group_label"] != "g00" for row in result["rows"]))
        self.assertTrue(
            all((row["width"], row["height"]) != (8, 8) for row in result["rows"])
        )

    def test_awakening_rule_rejects_two_missing_anchors(self):
        rows = awakening_rows(include_all_anchors=False)
        rows = [row for row in rows if row["texture_index"] != 5]
        self.assertIsNone(awakening_common_group(rows))


if __name__ == "__main__":
    unittest.main()
