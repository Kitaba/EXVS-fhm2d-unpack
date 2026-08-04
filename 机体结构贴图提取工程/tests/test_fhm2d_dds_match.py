import sys
import tempfile
import unittest
from pathlib import Path


CORE_DIR = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE_DIR))

from fhm2d_dds_match import parse_dds
from fhm2d_extract_textures import make_rgba8_dds


class DdsParsingTests(unittest.TestCase):
    def test_rgba8_requires_explicit_allowed_format(self):
        width = 8
        height = 4
        data = bytes(range(width * height * 4))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rgba8.dds"
            path.write_bytes(make_rgba8_dds(width, height, data))

            with self.assertRaisesRegex(ValueError, "DXGI format 28"):
                parse_dds(path)

            parsed = parse_dds(path, allowed_formats={28})

        self.assertEqual(parsed["width"], width)
        self.assertEqual(parsed["height"], height)
        self.assertEqual(parsed["dxgi_format"], 28)
        self.assertEqual(parsed["top_mip"], data)


if __name__ == "__main__":
    unittest.main()
