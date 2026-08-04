import sys
import tempfile
import unittest
from pathlib import Path
import struct


CORE = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE))

from fhm2d_runtime_texture_match import choose_probe, find_exact, parse_dds  # noqa: E402


class RuntimeTextureMatchTests(unittest.TestCase):
    def test_parse_legacy_rgba8_dds(self):
        pixels = bytes(range(64))
        header = bytearray(128)
        header[:4] = b"DDS "
        struct.pack_into("<I", header, 4, 124)
        struct.pack_into("<I", header, 12, 4)
        struct.pack_into("<I", header, 16, 4)
        struct.pack_into("<I", header, 28, 1)
        struct.pack_into("<I", header, 76, 32)
        struct.pack_into("<I", header, 80, 0x41)
        struct.pack_into("<I", header, 88, 32)
        struct.pack_into("<4I", header, 92, 0xFF, 0xFF00, 0xFF0000, 0xFF000000)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rgba8.dds"
            path.write_bytes(header + pixels)
            parsed = parse_dds(path)
        self.assertEqual(parsed["dxgi_format"], 28)
        self.assertEqual(parsed["pixel_size"], 64)
        self.assertEqual(parsed["pixels"], pixels)

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
