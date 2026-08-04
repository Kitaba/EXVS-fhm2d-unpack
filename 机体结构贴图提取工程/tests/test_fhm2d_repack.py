import sys
import unittest
from pathlib import Path


CORE = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE))

from fhm2d_repack import bulk_size_table_matches  # noqa: E402


class RepackTests(unittest.TestCase):
    def test_sparse_bulk_size_table_search(self):
        blocks = [{"compressed_size": value} for value in (1234, 2345, 3456)]
        data = bytearray(80)
        start = 17
        for index, block in enumerate(blocks):
            data[start + index * 8 : start + index * 8 + 2] = block[
                "compressed_size"
            ].to_bytes(2, "little")
        self.assertEqual(bulk_size_table_matches(bytes(data), blocks), [start])


if __name__ == "__main__":
    unittest.main()
