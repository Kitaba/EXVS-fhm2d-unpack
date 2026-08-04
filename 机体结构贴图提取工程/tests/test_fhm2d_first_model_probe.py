import tempfile
import unittest
from pathlib import Path

from _internal.core.fhm2d_first_model_probe import build_profiles, credible


class FirstModelProbeTests(unittest.TestCase):
    def test_requires_multiple_distinct_buffers(self):
        result = {"matches": [{"buffer": "ib.bin"}, {"buffer": "vb.bin"}]}
        self.assertTrue(credible(result, 2))
        self.assertFalse(credible(result, 3))

    def test_builds_common_index_variants(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ib.bin"
            path.write_bytes(bytes.fromhex("000001000200"))
            profiles = build_profiles([path], 2, 2, index_variants=True)
        names = {profile["name"] for profile in profiles}
        self.assertIn("ib.bin:u16be", names)
        self.assertIn("ib.bin:u32le", names)
        self.assertIn("ib.bin:u16le_swap_winding", names)


if __name__ == "__main__":
    unittest.main()
