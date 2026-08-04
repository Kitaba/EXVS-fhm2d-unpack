import sys
import unittest
from pathlib import Path


CORE = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE))

from fhm2d_runtime_texture_match import choose_probe, find_exact  # noqa: E402


class RuntimeTextureMatchTests(unittest.TestCase):
    def test_probe_recovers_full_payload_offset(self):
        pixels = bytes(range(256)) * 4
        probe_offset, probe = choose_probe(pixels, 16)
        target = {
            "probe": probe,
            "probe_offset": probe_offset,
            "pixel_size": len(pixels),
            "pixels": pixels,
        }
        self.assertEqual(find_exact(b"header" + pixels + b"tail", target), 6)
        self.assertIsNone(find_exact(b"header" + pixels[:-1], target))


if __name__ == "__main__":
    unittest.main()
