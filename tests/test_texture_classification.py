import sys
import unittest
from pathlib import Path


CORE_DIR = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE_DIR))

from texture_classification import (
    awakening_common_group,
    classify_package_assets,
)


def texture(
    group,
    index,
    width,
    height,
    storage_format="bc7",
    embedded_name="",
):
    return {
        "group_label": group,
        "texture_index": index,
        "embedded_index": index,
        "width": width,
        "height": height,
        "storage_format": storage_format,
        "embedded_name": embedded_name,
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
    def test_embedded_name_categories(self):
        cases = (
            ((458, 680), "46XTms_card_018_001_001", "favorite_mobile_suit"),
            ((1020, 680), "46XTvs_p_r_059_003_c04", "select_navigator"),
            ((1920, 1080), "46XTms_ms_l_002_013_001", "select_mobile_suit"),
            ((840, 432), "46XTms_sticker_014_021_001_T08_001", "match_mobile_suit"),
            ((840, 432), "46XTsticker_frm_0322", "match_card_frame"),
            ((840, 432), "46XTsticker_bg_0364", "match_card_background"),
            ((840, 432), "46XTsticker_emb_0097", "match_symbol"),
        )
        for dimensions, embedded_name, expected in cases:
            with self.subTest(embedded_name=embedded_name):
                rows = [
                    texture(
                        "g00",
                        0,
                        *dimensions,
                        embedded_name=embedded_name,
                    )
                ]
                result = classify_package_assets("0x12345678", rows)
                self.assertEqual(result["category"], expected)
                self.assertEqual(result["method"], "embedded_name")
                self.assertEqual(result["rows"], rows)

    def test_dimensions_alone_do_not_claim_semantic_category(self):
        for dimensions in ((458, 680), (1020, 680), (1920, 1080), (840, 432)):
            with self.subTest(dimensions=dimensions):
                rows = [
                    texture(
                        "g00",
                        0,
                        *dimensions,
                        embedded_name="46XTimg-00000",
                    )
                ]
                self.assertIsNone(
                    classify_package_assets("0x12345678", rows)
                )

    def test_match_mobile_suit_portrait_uses_960x900_canvas(self):
        portrait = texture(
            "g00", 0, 960, 900, "bc7", "46XTimg-00000"
        )
        effect = texture(
            "g00", 1, 320, 180, "bc7", "46XTimg-00001"
        )

        result = classify_package_assets(
            "0x12345678", [portrait, effect]
        )

        self.assertEqual(
            result["category"], "match_mobile_suit_portrait"
        )
        self.assertEqual(result["method"], "canvas_dimensions")
        self.assertEqual(result["rows"], [portrait])

    def test_match_background_effect_sequence_is_kept_together(self):
        rows = [
            texture(
                "g00",
                0,
                840,
                432,
                "rgba8",
                "46XTimg-00000",
            )
        ]
        rows.extend(
            texture(
                "g00",
                index,
                420,
                216,
                "rgba8",
                f"46XTimg-{index:05d}",
            )
            for index in range(1, 10)
        )
        result = classify_package_assets("0x42C5CF02", rows)
        self.assertEqual(result["category"], "match_card_background")
        self.assertEqual(result["method"], "match_background_sequence")
        self.assertEqual(result["rows"], rows)

    def test_mixed_generic_package_remains_pending(self):
        rows = [
            texture("g00", 0, 840, 432, "rgba8", "46XTimg-00000"),
            texture("g00", 1, 841, 432, "rgba8", "46XTimg-00001"),
            texture("g00", 2, 960, 432, "rgba8", "46XTimg-00002"),
        ]
        self.assertIsNone(classify_package_assets("0x514982BD", rows))

    def test_conflicting_named_categories_remain_pending(self):
        rows = [
            texture("g00", 0, 840, 432, "rgba8", "46XTsticker_bg_0001"),
            texture("g00", 1, 840, 432, "rgba8", "46XTsticker_frm_0001"),
        ]
        self.assertIsNone(classify_package_assets("0x12345678", rows))

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
