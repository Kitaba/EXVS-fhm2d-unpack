import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


CORE_DIR = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE_DIR))

from fhm2d_batch_textures import (
    TEXTURE_SCANNER_VERSION,
    extraction_manifest_is_current,
    inventory_row_is_current,
    scan_one,
)
from fhm2d_extract_textures import (
    FHM2D_RGBA8_FORMAT,
    TEXTURE_ARRAY_SIZE_OFFSET,
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
    TEXTURE_WIDTH_OFFSET,
)


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


class BatchTextureScanTests(unittest.TestCase):
    def test_generic_texture_is_supported(self):
        payload = bytes(make_rgba8_payload("46XTms_ms_l_018_008_001"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.fhm2d"
            source.write_bytes(b"sample")
            with patch(
                "fhm2d_batch_textures.decode_payload",
                return_value=(None, None, payload, b""),
            ):
                row = scan_one(source, root / "details")

        self.assertEqual(row["status"], "supported_textures")
        self.assertEqual(row["texture_count"], 1)
        self.assertEqual(
            row["texture_scanner_version"], TEXTURE_SCANNER_VERSION
        )

    def test_non_texture_46xt_name_is_not_an_error(self):
        payload = b"\0" * 0x80 + b"46XTnot_texture" + b"\0" * 0x100
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.fhm2d"
            source.write_bytes(b"sample")
            with patch(
                "fhm2d_batch_textures.decode_payload",
                return_value=(None, None, payload, b""),
            ):
                row = scan_one(source, root / "details")

        self.assertEqual(row["status"], "no_supported_textures")
        self.assertEqual(row["error"], "")

    def test_old_inventory_row_is_invalidated_by_scanner_version(self):
        stat = SimpleNamespace(st_size=10, st_mtime_ns=20)
        old = {"size": "10", "mtime_ns": "20"}
        current = {
            **old,
            "texture_scanner_version": str(TEXTURE_SCANNER_VERSION),
        }

        self.assertFalse(inventory_row_is_current(old, stat))
        self.assertTrue(inventory_row_is_current(current, stat))

    def test_old_extract_manifest_is_invalidated(self):
        row = {"texture_count": "1"}
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "extract_manifest.json"
            manifest.write_text(
                json.dumps({"texture_count": 1}), encoding="utf-8"
            )
            current, _ = extraction_manifest_is_current(manifest, row)
            self.assertFalse(current)

            manifest.write_text(
                json.dumps(
                    {
                        "texture_scanner_version": TEXTURE_SCANNER_VERSION,
                        "texture_count": 1,
                    }
                ),
                encoding="utf-8",
            )
            current, _ = extraction_manifest_is_current(manifest, row)
            self.assertTrue(current)


if __name__ == "__main__":
    unittest.main()
