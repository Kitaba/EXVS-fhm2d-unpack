import csv
import tempfile
import unittest
from pathlib import Path

from _internal.core.hbss_blender_project import classify_part, discover_texture_sets


class HbssBlenderProjectTests(unittest.TestCase):
    def test_classifies_hand_state_and_attachment(self):
        part = classify_part("034extgun_004exleos_001_wep_handl_ngr00")
        self.assertEqual(part["kind"], "hand")
        self.assertEqual(part["attachment_bone"], "TE_L")
        self.assertTrue(part["default_visible"])

    def test_hides_form_variant_by_default(self):
        part = classify_part("034extgun_004exleos_001_wep_pbitform00")
        self.assertEqual(part["collection"], "Standalone/Forms")
        self.assertFalse(part["default_visible"])

    def test_classifies_known_attachment_parts(self):
        self.assertEqual(classify_part("unit_wep_wing00")["attachment_bone"], "ATH_WING")
        rifle = classify_part("unit_wep_vrifle00")
        self.assertEqual(rifle["kind"], "standalone")
        self.assertNotIn("attachment_bone", rifle)

    def test_unknown_component_is_kept_standalone(self):
        part = classify_part("unit_wep_unknown00")
        self.assertEqual(part["kind"], "standalone")
        self.assertFalse(part["default_visible"])

    def test_discovers_arbitrary_material_labels_and_partial_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "textures.csv"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=[
                    "embedded_name", "width", "height", "storage_format"
                ])
                writer.writeheader()
                writer.writerow({
                    "embedded_name": "unit_glass7_basecolor", "width": 64,
                    "height": 64, "storage_format": "bc7_srgb",
                })
                writer.writerow({
                    "embedded_name": "unit_glass7_normal", "width": 64,
                    "height": 64, "storage_format": "bc7",
                })
            inventory = discover_texture_sets(Path(directory))
            self.assertEqual(inventory["glass7"][0]["prefix"], "unit_glass7")
            self.assertEqual(sorted(inventory["glass7"][0]["channels"]), ["base_color", "normal"])


if __name__ == "__main__":
    unittest.main()
