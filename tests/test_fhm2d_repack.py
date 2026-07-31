import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


CORE_DIR = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE_DIR))

from fhm2d_repack import (
    decode_container,
    find_trailing_offset_references,
    partition_bulk_tables,
)


class ImplicitBulkTableTests(unittest.TestCase):
    def test_accepts_unique_full_payload_size_table_without_boundaries(self):
        index_data = bytearray(b"\xAA" * 0x100)
        table_offset = 0x40
        blocks = [
            {"index": 1, "file_offset": 0x1000, "compressed_size": 10},
            {"index": 2, "file_offset": 0x100A, "compressed_size": 20},
        ]
        for index, block in enumerate(blocks):
            struct.pack_into(
                "<H",
                index_data,
                table_offset + 8 * index,
                block["compressed_size"],
            )

        with self.assertRaisesRegex(
            ValueError, "could not identify one bulk table"
        ):
            partition_bulk_tables(index_data, blocks, data_base=0x1000)

        partitions = partition_bulk_tables(
            index_data,
            blocks,
            data_base=0x1000,
            allow_implicit_boundaries=True,
        )
        self.assertEqual(len(partitions), 1)
        self.assertEqual(partitions[0][1], table_offset)
        self.assertEqual(partitions[0][2], (None, None))


class TrailingOffsetReferenceTests(unittest.TestCase):
    def make_record(self, value):
        data = bytearray(32)
        struct.pack_into("<I", data, 0, 0x100)
        struct.pack_into("<I", data, 4, 0)
        struct.pack_into("<I", data, 9, value)
        return data

    def test_finds_terminal_reference_without_trailing_data(self):
        compressed_size = 0x1BBFD2
        index_data = self.make_record(compressed_size)

        self.assertEqual(
            find_trailing_offset_references(
                index_data, compressed_size, trailing_size=0
            ),
            [(9, 0)],
        )

    def test_finds_offsets_inside_and_at_end_of_trailing_data(self):
        compressed_size = 0x1000
        trailing_size = 0x20

        for trailing_offset in (0, 0x10, 0x20):
            with self.subTest(trailing_offset=trailing_offset):
                index_data = self.make_record(
                    compressed_size + trailing_offset
                )
                self.assertEqual(
                    find_trailing_offset_references(
                        index_data, compressed_size, trailing_size
                    ),
                    [(9, trailing_offset)],
                )

    def test_rejects_reference_past_trailing_data(self):
        compressed_size = 0x1000
        index_data = self.make_record(compressed_size + 0x21)

        self.assertEqual(
            find_trailing_offset_references(
                index_data, compressed_size, trailing_size=0x20
            ),
            [],
        )


class DecodeTrailingDataTests(unittest.TestCase):
    def compress_raw(self, data):
        compressor = zlib.compressobj(level=9, wbits=-15)
        return compressor.compress(data) + compressor.flush()

    def test_preserves_non_deflate_trailing_data(self):
        payload = b"payload"
        trailing = b"a1a2b1b2c1c2d1e1\0metadata"
        index = bytearray(0x40)
        struct.pack_into("<Q", index, 0x34, len(payload))
        index_compressed = self.compress_raw(index)
        payload_compressed = self.compress_raw(payload)
        file_size = (
            0x30
            + len(index_compressed)
            + len(payload_compressed)
            + len(trailing)
        )
        header = bytearray(0x30)
        header[:4] = bytes.fromhex("b9b7b2cd")
        struct.pack_into("<Q", header, 0x10, file_size)
        struct.pack_into("<Q", header, 0x18, len(index))
        struct.pack_into("<Q", header, 0x20, len(index_compressed))
        blob = (
            bytes(header)
            + index_compressed
            + payload_compressed
            + trailing
        )

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.fhm2d"
            source.write_bytes(blob)
            decoded = decode_container(source)

        self.assertEqual(decoded["payload"], payload)
        self.assertEqual(decoded["trailing"], trailing)


if __name__ == "__main__":
    unittest.main()
