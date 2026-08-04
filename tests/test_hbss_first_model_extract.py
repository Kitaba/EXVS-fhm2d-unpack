import unittest

from _internal.core.hbss_first_model_extract import resource_groups, select_triplet


def hbss(inner, body=b""):
    return b"HBSS" + bytes(12) + inner + body


class HbssFirstModelExtractTests(unittest.TestCase):
    def test_pairs_resources_and_keeps_mesh_continuation(self):
        blocks = [
            (1, hbss(b"LEKS")),
            (2, hbss(b"HSEM", b"mesh").ljust(65536, b"x")),
            (3, b"-continuation"),
            (4, hbss(b"LDOM", b"\\unit_x.numdlx\0")),
        ]
        groups = resource_groups(blocks)
        selected = select_triplet(groups, "unit_x")
        self.assertEqual(selected["name"], "unit_x")
        self.assertEqual(selected["mesh"]["block_indices"], [2, 3])


if __name__ == "__main__":
    unittest.main()
