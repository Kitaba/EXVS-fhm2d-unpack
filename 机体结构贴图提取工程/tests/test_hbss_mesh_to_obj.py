import struct
import tempfile
import unittest
from pathlib import Path

from _internal.core.hbss_mesh_to_obj import decode_mesh, write_obj


class HbssMeshToObjTests(unittest.TestCase):
    def test_decodes_packed_streams_and_u16_indices(self):
        data = bytearray(0x300)
        data[:4] = b"HBSS"
        data[16:20] = b"HSEM"
        struct.pack_into("<I", data, 0x90, 1)
        struct.pack_into("<III", data, 0x110, 3, 3, 2)
        struct.pack_into("<II", data, 0x12C, 24, 8)
        cursor = 0x200
        vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        for position in vertices:
            struct.pack_into("<6f", data, cursor, *position, 0, 0, 1)
            cursor += 24
        for uv in ((0, 0), (1, 0), (0, 1)):
            struct.pack_into("<2f", data, cursor, *uv)
            cursor += 8
        struct.pack_into("<3H", data, cursor, 0, 1, 2)
        struct.pack_into("<Q", data, 0xD0, cursor + 0x30)
        mesh = decode_mesh(bytes(data[: cursor + 6]))
        self.assertEqual(mesh["vertex_count"], 3)
        self.assertEqual(mesh["indices"], [0, 1, 2])
        self.assertEqual(mesh["bbox"]["max"], [1.0, 1.0, 0.0])
        self.assertEqual(mesh["sections"][0]["index_output_start"], 0)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "mesh.obj"
            write_obj(output, mesh, "test", ["pbr1Mtl"])
            text = output.read_text(encoding="utf-8")
            self.assertIn("usemtl pbr1Mtl", text)
            self.assertIn("g section_00", text)


if __name__ == "__main__":
    unittest.main()
