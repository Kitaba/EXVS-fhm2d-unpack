import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = (
    ROOT / "_internal" / "apps" / "portrait-editor" / "server.py"
)
SPEC = importlib.util.spec_from_file_location(
    "portrait_editor_server", SERVER_PATH
)
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)
PortraitData = SERVER.PortraitData


def group(category, package, name, width=800, height=1000):
    return {
        "category": category,
        "package": package,
        "group": name,
        "status": "mapped",
        "body_width": str(width),
        "body_height": str(height),
        "overlay_family_count": "0",
        "overlay_texture_count": "0",
        "preview": f"previews/{package}_{name}.png",
        "notes": "",
    }


class AwakeningGalleryTests(unittest.TestCase):
    def portrait_data(self, root, modified=None):
        data = PortraitData.__new__(PortraitData)
        data.mapping_root = root
        data.groups = [
            group("awakening", "0xAAAA0001", "g01_00001"),
            group("awakening", "0xAAAA0001", "g01_00002", 760),
            group("awakening", "0xAAAA0001", "g01_00003", 1064, 1180),
            group("awakening", "0xBBBB0002", "g01_00001"),
            group("match_mobile_suit", "0xCCCC0003", "g00_00000", 840, 432),
        ]
        data.modified_group_keys = lambda: set(modified or ())
        return data

    def test_awakening_package_uses_one_gallery_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            data = self.portrait_data(Path(directory))
            result = data.list_groups("awakening", "", False, 1, 48)

        self.assertEqual(result["total"], 2)
        first = result["items"][0]
        self.assertTrue(first["collection"])
        self.assertEqual(first["package"], "0xAAAA0001")
        self.assertEqual(first["member_count"], 3)
        self.assertEqual(len(first["members"]), 3)
        self.assertEqual(
            first["members"][1]["canvas"], [760, 1000]
        )

    def test_modified_filter_keeps_the_whole_awakening_package(self):
        modified = {
            ("awakening", "0xAAAA0001", "g01_00002"),
        }
        with tempfile.TemporaryDirectory() as directory:
            data = self.portrait_data(Path(directory), modified)
            result = data.list_groups("awakening", "", True, 1, 48)

        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        self.assertTrue(item["modified"])
        self.assertEqual(len(item["members"]), 3)
        self.assertEqual(
            [member["modified"] for member in item["members"]],
            [False, True, False],
        )

    def test_other_categories_remain_one_composition_per_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            data = self.portrait_data(Path(directory))
            result = data.list_groups(
                "match_mobile_suit", "", False, 1, 48
            )

        self.assertEqual(result["total"], 1)
        self.assertNotIn("collection", result["items"][0])

    def test_meta_counts_awakening_packages_instead_of_images(self):
        with tempfile.TemporaryDirectory() as directory:
            data = self.portrait_data(Path(directory))
            data.mapping = {"mapping_version": 2, "layer_count": 5}
            data.layer_index = {}
            result = data.meta()

        self.assertEqual(result["category_counts"]["awakening"], 2)
        self.assertEqual(
            result["category_counts"]["match_mobile_suit"], 1
        )
        self.assertEqual(result["group_count"], 3)


if __name__ == "__main__":
    unittest.main()
