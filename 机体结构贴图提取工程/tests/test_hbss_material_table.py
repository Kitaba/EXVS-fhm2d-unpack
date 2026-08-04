import unittest

from _internal.core.hbss_material_table import decode_material_table


class HbssMaterialTableTests(unittest.TestCase):
    def test_maps_material_texture_semantics_and_shader(self):
        data = bytearray(16)
        data[:4] = b"HBSS"
        data += b"LTAM\0pbr7Mtl\0../../textures/unit_pbr7_basecolor\0"
        data += b"../../textures/unit_pbr7_roughnessandmask\0"
        data += b"../../../share/textures/specular_cubemap\0vsngCharaBasic\0"
        table = decode_material_table(bytes(data))
        self.assertEqual(table["material_count"], 1)
        material = table["materials"][0]
        self.assertEqual(material["name"], "pbr7Mtl")
        self.assertEqual(
            [item["semantic"] for item in material["textures"]],
            ["base_color", "roughness_mask", "cubemap"],
        )
        self.assertEqual(material["shaders"], ["vsngCharaBasic"])


if __name__ == "__main__":
    unittest.main()
