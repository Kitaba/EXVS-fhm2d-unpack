import struct
import sys
import unittest
from pathlib import Path


CORE_DIR = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE_DIR))

from fhm2d_extract_textures import (
    FHM2D_RGBA8_FORMAT,
    NoSupportedTexturesError,
    TEXTURE_BLOCK_DIMENSION_OFFSET,
    TEXTURE_DATA_SIZE_COPY_OFFSET,
    TEXTURE_DEPTH_OFFSET,
    TEXTURE_FORMAT_OFFSET,
    TEXTURE_HEIGHT_OFFSET,
    TEXTURE_MARKER,
    TEXTURE_MARKER_OFFSET,
    TEXTURE_METADATA_SIZE,
    TEXTURE_MIP_COUNT_OFFSET,
    TEXTURE_NAME_OFFSET,
    TEXTURE_ARRAY_SIZE_OFFSET,
    TEXTURE_WIDTH_OFFSET,
    scan_textures,
)


class StickerTextureTests(unittest.TestCase):
    @staticmethod
    def make_rgba8_payload(name, width=8, height=4):
        data_size = width * height * 4
        metadata_offset = data_size
        payload = bytearray(data_size + TEXTURE_METADATA_SIZE)
        struct.pack_into("<I", payload, metadata_offset, data_size)
        encoded_name = name.encode("ascii")
        name_offset = metadata_offset + TEXTURE_NAME_OFFSET
        payload[name_offset : name_offset + len(encoded_name)] = encoded_name
        values = {
            TEXTURE_WIDTH_OFFSET: width,
            TEXTURE_HEIGHT_OFFSET: height,
            TEXTURE_MIP_COUNT_OFFSET: 1,
            TEXTURE_FORMAT_OFFSET: FHM2D_RGBA8_FORMAT,
            TEXTURE_BLOCK_DIMENSION_OFFSET: 4,
            TEXTURE_ARRAY_SIZE_OFFSET: 1,
            TEXTURE_DEPTH_OFFSET: 1,
            TEXTURE_DATA_SIZE_COPY_OFFSET: data_size,
            TEXTURE_MARKER_OFFSET: TEXTURE_MARKER,
        }
        for offset, value in values.items():
            struct.pack_into("<I", payload, metadata_offset + offset, value)
        return payload

    def test_scans_rgba8_sticker_texture(self):
        width = 436
        height = 432
        data_size = width * height * 4
        metadata_offset = data_size
        payload = bytearray(data_size + TEXTURE_METADATA_SIZE)
        struct.pack_into("<I", payload, metadata_offset, data_size)
        name = b"46XTsticker_ex_p_068_001_c01"
        name_offset = metadata_offset + TEXTURE_NAME_OFFSET
        payload[name_offset : name_offset + len(name)] = name
        values = {
            TEXTURE_WIDTH_OFFSET: width,
            TEXTURE_HEIGHT_OFFSET: height,
            TEXTURE_MIP_COUNT_OFFSET: 1,
            TEXTURE_FORMAT_OFFSET: FHM2D_RGBA8_FORMAT,
            TEXTURE_BLOCK_DIMENSION_OFFSET: 4,
            TEXTURE_ARRAY_SIZE_OFFSET: 1,
            TEXTURE_DEPTH_OFFSET: 1,
            TEXTURE_DATA_SIZE_COPY_OFFSET: data_size,
            TEXTURE_MARKER_OFFSET: TEXTURE_MARKER,
        }
        for offset, value in values.items():
            struct.pack_into("<I", payload, metadata_offset + offset, value)

        textures = scan_textures(
            bytes(payload), supported_formats={FHM2D_RGBA8_FORMAT}
        )

        self.assertEqual(len(textures), 1)
        self.assertEqual(textures[0]["embedded_name"], name.decode("ascii"))
        self.assertEqual(textures[0]["embedded_index"], 0)
        self.assertEqual(textures[0]["width"], width)
        self.assertEqual(textures[0]["height"], height)
        self.assertEqual(textures[0]["storage_format"], "rgba8")

    def test_scans_generic_46xt_texture_name(self):
        name = "46XTms_001_002_basecolor"
        payload = self.make_rgba8_payload(name)

        textures = scan_textures(
            bytes(payload), supported_formats={FHM2D_RGBA8_FORMAT}
        )

        self.assertEqual(len(textures), 1)
        self.assertEqual(textures[0]["embedded_name"], name)
        self.assertEqual(textures[0]["embedded_index"], 0)

    def test_scans_discovered_ms_and_vs_texture_names(self):
        for name in (
            "46XTms_ms_l_018_008_001",
            "46XTvs_s_l_207_01",
        ):
            with self.subTest(name=name):
                textures = scan_textures(
                    bytes(self.make_rgba8_payload(name)),
                    supported_formats={FHM2D_RGBA8_FORMAT},
                )
                self.assertEqual(len(textures), 1)
                self.assertEqual(textures[0]["embedded_name"], name)

    def test_preserves_numeric_index_for_legacy_img_names(self):
        textures = scan_textures(
            bytes(self.make_rgba8_payload("46XTimg-00123")),
            supported_formats={FHM2D_RGBA8_FORMAT},
        )

        self.assertEqual(textures[0]["embedded_index"], 123)

    def test_ignores_non_texture_46xt_resource_name(self):
        payload = bytearray(0x200)
        payload[0x80:0x8F] = b"46XTnot_texture"

        with self.assertRaisesRegex(
            NoSupportedTexturesError,
            "no supported 46XT texture records found",
        ):
            scan_textures(
                bytes(payload), supported_formats={FHM2D_RGBA8_FORMAT}
            )


if __name__ == "__main__":
    unittest.main()
