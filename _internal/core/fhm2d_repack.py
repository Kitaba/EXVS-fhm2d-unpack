#!/usr/bin/env python3
import argparse
import hashlib
import struct
import sys
import zlib
from pathlib import Path

from fhm2d_deep_unpack import find_catalog_record
from fhm2d_unpack import iter_deflate_blocks, read_header


HEADER_SIZE = 0x30
PAYLOAD_PADDING_TOLERANCE = 0x100


def compress_raw(data):
    compressor = zlib.compressobj(level=9, wbits=-15)
    return compressor.compress(data) + compressor.flush()


def decode_container(input_path):
    blob = input_path.read_bytes()
    header = read_header(blob)
    blocks = []
    trailing_offset = len(blob)

    for index, file_offset, compressed_size, data in iter_deflate_blocks(blob):
        if index is None:
            trailing_offset = file_offset
            break
        blocks.append(
            {
                "index": index,
                "file_offset": file_offset,
                "compressed_size": compressed_size,
                "uncompressed_size": len(data),
                "data": data,
                "compressed_data": blob[
                    file_offset : file_offset + compressed_size
                ],
            }
        )

    if len(blocks) < 2:
        raise ValueError("fhm2d contains no payload blocks")

    index_block = blocks[0]
    declared_payload_size = struct.unpack_from("<Q", index_block["data"], 0x34)[0]
    payload_blocks = []
    payload_size = 0
    trailing_offset = len(blob)
    for position, block in enumerate(blocks[1:], 1):
        if payload_size + block["uncompressed_size"] > declared_payload_size:
            break
        payload_blocks.append(block)
        payload_size += block["uncompressed_size"]
        if payload_size == declared_payload_size:
            trailing_offset = (
                blocks[position + 1]["file_offset"]
                if position + 1 < len(blocks)
                else len(blob)
            )
            break
    tolerated_padding = (
        declared_payload_size > payload_size
        and declared_payload_size - payload_size <= PAYLOAD_PADDING_TOLERANCE
        and trailing_offset == len(blob)
    )
    if declared_payload_size != payload_size and not tolerated_padding:
        raise ValueError(
            f"payload size mismatch: index={declared_payload_size}, "
            f"decoded={payload_size}"
        )

    data_base = payload_blocks[0]["file_offset"]
    for block in payload_blocks:
        block["catalog_offsets"] = find_catalog_record(
            index_block["data"], block, data_base
        )

    payload = b"".join(block["data"] for block in payload_blocks)
    return {
        "blob": blob,
        "header": header,
        "header_data": blob[:HEADER_SIZE],
        "index_data": index_block["data"],
        "index_compressed_data": index_block["compressed_data"],
        "payload_blocks": payload_blocks,
        "payload": payload,
        "trailing": blob[trailing_offset:],
    }


def write_u24(buffer, offset, value):
    if not 0 <= value <= 0xFFFFFF:
        raise ValueError(f"value does not fit u24: {value}")
    buffer[offset : offset + 3] = value.to_bytes(3, "little")


def consecutive_runs(values):
    if not values:
        return []
    runs = []
    start = previous = values[0]
    for value in values[1:]:
        if value != previous + 1:
            runs.append((start, previous))
            start = value
        previous = value
    runs.append((start, previous))
    return runs


def find_bulk_size_table(index_data, blocks):
    matches = []
    required = 8 * (len(blocks) - 1) + 2
    for offset in range(0, len(index_data) - required + 1):
        if all(
            int.from_bytes(
                index_data[offset + 8 * index : offset + 8 * index + 2],
                "little",
            )
            == block["compressed_size"]
            for index, block in enumerate(blocks)
        ):
            matches.append(offset)
    if len(matches) != 1:
        raise ValueError(
            f"expected one bulk size table for blocks "
            f"{blocks[0]['index']}..{blocks[-1]['index']}, found {matches}"
        )
    return matches[0]


def bulk_table_layout(index_data, table_offset, block_count,
                      expected_start, expected_end):
    end_offset = table_offset + 8 * block_count + 0x20
    if (
        struct.unpack_from("<I", index_data, table_offset - 0x0E)[0]
        == expected_start
        and struct.unpack_from("<I", index_data, end_offset)[0]
        == expected_end
    ):
        return table_offset - 0x0E, end_offset
    for start_delta in (0x13, 0x0D):
        shifted_start_offset = table_offset - start_delta
        if (
            struct.unpack_from("<I", index_data, shifted_start_offset)[0]
            == expected_start
        ):
            shifted_end = (
                end_offset
                if struct.unpack_from("<I", index_data, end_offset)[0]
                == expected_end
                else None
            )
            return shifted_start_offset, shifted_end
    raise ValueError("bulk table offset fields are not recognized")


def find_trailing_offset_references(index_data, compressed_size, trailing_size):
    references = []
    upper_bound = compressed_size + trailing_size
    for offset in range(9, len(index_data) - 3):
        value = struct.unpack_from("<I", index_data, offset)[0]
        if not compressed_size <= value < upper_bound:
            continue
        record_start = offset - 9
        if (
            struct.unpack_from("<I", index_data, record_start)[0] == 0x100
            and struct.unpack_from("<I", index_data, record_start + 4)[0]
            in (0, 1)
        ):
            references.append((offset, value - compressed_size))
    return references


def rebuild_container(container, payload):
    if len(payload) != len(container["payload"]):
        raise ValueError(
            f"replacement payload size {len(payload)} differs from original "
            f"{len(container['payload'])}"
        )

    compressed_payload_blocks = []
    payload_offset = 0
    relative_compressed_offset = 0
    block_updates = []

    for block in container["payload_blocks"]:
        block_size = block["uncompressed_size"]
        block_data = payload[payload_offset : payload_offset + block_size]
        if len(block_data) != block_size:
            raise ValueError(f"payload ends inside block {block['index']}")
        compressed = compress_raw(block_data)
        compressed_payload_blocks.append(compressed)
        block_updates.append(
            {
                "index": block["index"],
                "relative_compressed_offset": relative_compressed_offset,
                "compressed_size": len(compressed),
                "catalog_offsets": block["catalog_offsets"],
            }
        )
        payload_offset += block_size
        relative_compressed_offset += len(compressed)

    if payload_offset != len(payload):
        raise ValueError("payload block sizes do not cover the replacement payload")

    index_data = bytearray(container["index_data"])
    updates_by_index = {update["index"]: update for update in block_updates}
    for update in block_updates:
        for record_offset in update["catalog_offsets"]:
            struct.pack_into(
                "<I",
                index_data,
                record_offset + 0x09,
                update["relative_compressed_offset"],
            )
            write_u24(
                index_data,
                record_offset + 0x16,
                update["compressed_size"],
            )

    blocks_without_catalog = [
        block["index"]
        for block in container["payload_blocks"]
        if not block["catalog_offsets"]
    ]
    blocks_by_index = {
        block["index"]: block for block in container["payload_blocks"]
    }
    for first, last in consecutive_runs(blocks_without_catalog):
        bulk_blocks = [
            blocks_by_index[index] for index in range(first, last + 1)
        ]
        table_offset = find_bulk_size_table(
            container["index_data"], bulk_blocks
        )
        original_data_base = container["payload_blocks"][0]["file_offset"]
        original_start = (
            bulk_blocks[0]["file_offset"] - original_data_base
        )
        original_end = (
            bulk_blocks[-1]["file_offset"]
            + bulk_blocks[-1]["compressed_size"]
            - original_data_base
        )
        start_offset, end_offset = bulk_table_layout(
            container["index_data"], table_offset,
            len(bulk_blocks),
            original_start, original_end,
        )
        first_update = updates_by_index[first]
        relative_start = first_update["relative_compressed_offset"]
        relative_end = relative_start + sum(
            updates_by_index[index]["compressed_size"]
            for index in range(first, last + 1)
        )
        struct.pack_into("<I", index_data, start_offset, relative_start)
        for table_index, block_index in enumerate(range(first, last + 1)):
            compressed_size = updates_by_index[block_index]["compressed_size"]
            if compressed_size > 0xFFFF:
                raise ValueError(
                    f"bulk block {block_index} compressed size does not fit u16"
                )
            struct.pack_into(
                "<H",
                index_data,
                table_offset + 8 * table_index,
                compressed_size,
            )
        if end_offset is not None:
            struct.pack_into("<I", index_data, end_offset, relative_end)

    original_compressed_size = sum(
        block["compressed_size"] for block in container["payload_blocks"]
    )
    rebuilt_compressed_size = sum(
        update["compressed_size"] for update in block_updates
    )
    trailing_references = find_trailing_offset_references(
        container["index_data"],
        original_compressed_size,
        len(container["trailing"]),
    )
    for offset, trailing_offset in trailing_references:
        struct.pack_into(
            "<I",
            index_data,
            offset,
            rebuilt_compressed_size + trailing_offset,
        )

    index_compressed = compress_raw(index_data)
    header_data = bytearray(container["header_data"])
    final_size = (
        HEADER_SIZE
        + len(index_compressed)
        + sum(len(data) for data in compressed_payload_blocks)
        + len(container["trailing"])
    )
    struct.pack_into("<Q", header_data, 0x10, final_size)
    struct.pack_into("<Q", header_data, 0x18, len(index_data))
    struct.pack_into("<Q", header_data, 0x20, len(index_compressed))

    rebuilt = (
        bytes(header_data)
        + index_compressed
        + b"".join(compressed_payload_blocks)
        + container["trailing"]
    )
    if len(rebuilt) != final_size:
        raise AssertionError("rebuilt file size calculation failed")
    return rebuilt


def verify_rebuilt(original_container, rebuilt, expected_payload):
    all_rebuilt_blocks = []
    for index, file_offset, compressed_size, data in iter_deflate_blocks(rebuilt):
        if index is None:
            break
        all_rebuilt_blocks.append(
            {
                "index": index,
                "file_offset": file_offset,
                "compressed_size": compressed_size,
                "uncompressed_size": len(data),
                "data": data,
            }
        )

    expected_block_count = len(original_container["payload_blocks"]) + 1
    if len(all_rebuilt_blocks) < expected_block_count:
        raise ValueError("rebuilt block count differs from original")
    rebuilt_blocks = all_rebuilt_blocks[:expected_block_count]
    trailing_offset = (
        all_rebuilt_blocks[expected_block_count]["file_offset"]
        if len(all_rebuilt_blocks) > expected_block_count
        else len(rebuilt)
    )
    rebuilt_payload_blocks = rebuilt_blocks[1:]
    rebuilt_payload = b"".join(block["data"] for block in rebuilt_payload_blocks)
    if rebuilt_payload != expected_payload:
        raise ValueError("rebuilt payload does not match requested payload")
    if rebuilt[trailing_offset:] != original_container["trailing"]:
        raise ValueError("rebuilt trailing data differs from original")
    header = read_header(rebuilt)
    if header["file_size_header"] != len(rebuilt):
        raise ValueError("rebuilt header file size is incorrect")

    index_data = rebuilt_blocks[0]["data"]
    data_base = rebuilt_payload_blocks[0]["file_offset"]
    for original, rebuilt_block in zip(
        original_container["payload_blocks"], rebuilt_payload_blocks
    ):
        if rebuilt_block["uncompressed_size"] != original["uncompressed_size"]:
            raise ValueError(
                f"rebuilt block {rebuilt_block['index']} size differs from original"
            )
        for record_offset in original["catalog_offsets"]:
            relative_offset = struct.unpack_from(
                "<I", index_data, record_offset + 0x09
            )[0]
            compressed_size = int.from_bytes(
                index_data[record_offset + 0x16 : record_offset + 0x19],
                "little",
            )
            expected_offset = rebuilt_block["file_offset"] - data_base
            if relative_offset != expected_offset:
                raise ValueError(
                    f"rebuilt block {rebuilt_block['index']} catalog offset "
                    "is incorrect"
                )
            if compressed_size != rebuilt_block["compressed_size"]:
                raise ValueError(
                    f"rebuilt block {rebuilt_block['index']} catalog size "
                    "is incorrect"
                )

    original_without_catalog = [
        block["index"]
        for block in original_container["payload_blocks"]
        if not block["catalog_offsets"]
    ]
    original_by_index = {
        block["index"]: block for block in original_container["payload_blocks"]
    }
    rebuilt_by_index = {
        block["index"]: block for block in rebuilt_payload_blocks
    }
    for first, last in consecutive_runs(original_without_catalog):
        original_bulk = [
            original_by_index[index] for index in range(first, last + 1)
        ]
        table_offset = find_bulk_size_table(
            original_container["index_data"], original_bulk
        )
        original_data_base = original_container["payload_blocks"][0]["file_offset"]
        original_start = original_bulk[0]["file_offset"] - original_data_base
        original_end = (
            original_bulk[-1]["file_offset"]
            + original_bulk[-1]["compressed_size"]
            - original_data_base
        )
        start_offset, end_offset = bulk_table_layout(
            original_container["index_data"], table_offset,
            len(original_bulk),
            original_start, original_end,
        )
        relative_start = (
            rebuilt_by_index[first]["file_offset"] - data_base
        )
        relative_end = (
            rebuilt_by_index[last]["file_offset"]
            - data_base
            + rebuilt_by_index[last]["compressed_size"]
        )
        if struct.unpack_from("<I", index_data, start_offset)[0] != relative_start:
            raise ValueError(f"bulk run {first}..{last} start offset is incorrect")
        for table_index, block_index in enumerate(range(first, last + 1)):
            encoded_size = struct.unpack_from(
                "<H", index_data, table_offset + 8 * table_index
            )[0]
            if encoded_size != rebuilt_by_index[block_index]["compressed_size"]:
                raise ValueError(
                    f"bulk block {block_index} compressed size is incorrect"
                )
        if end_offset is not None and struct.unpack_from(
            "<I", index_data, end_offset
        )[0] != relative_end:
            raise ValueError(f"bulk run {first}..{last} end offset is incorrect")

    original_compressed_size = sum(
        block["compressed_size"] for block in original_container["payload_blocks"]
    )
    original_trailing_references = find_trailing_offset_references(
        original_container["index_data"],
        original_compressed_size,
        len(original_container["trailing"]),
    )
    rebuilt_compressed_size = sum(
        block["compressed_size"] for block in rebuilt_payload_blocks
    )
    rebuilt_trailing_references = find_trailing_offset_references(
        index_data,
        rebuilt_compressed_size,
        len(original_container["trailing"]),
    )
    if rebuilt_trailing_references != original_trailing_references:
        raise ValueError("rebuilt trailing offset references are incorrect")


def repack_file(input_path, output_path, payload=None):
    container = decode_container(input_path)
    replacement_payload = container["payload"] if payload is None else payload
    if replacement_payload == container["payload"]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(container["blob"])
        digest = hashlib.sha256(container["blob"]).hexdigest()
        return {
            "source": str(input_path),
            "output": str(output_path),
            "source_size": len(container["blob"]),
            "output_size": len(container["blob"]),
            "source_sha256": digest,
            "output_sha256": digest,
            "payload_sha256": hashlib.sha256(replacement_payload).hexdigest(),
            "byte_identical": True,
        }
    rebuilt = rebuild_container(container, replacement_payload)
    verify_rebuilt(container, rebuilt, replacement_payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(rebuilt)
    return {
        "source": str(input_path),
        "output": str(output_path),
        "source_size": len(container["blob"]),
        "output_size": len(rebuilt),
        "source_sha256": hashlib.sha256(container["blob"]).hexdigest(),
        "output_sha256": hashlib.sha256(rebuilt).hexdigest(),
        "payload_sha256": hashlib.sha256(replacement_payload).hexdigest(),
        "byte_identical": rebuilt == container["blob"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild an EXVSIB fhm2d from its original uncompressed payload "
            "layout. Without --payload this performs a no-change round trip."
        )
    )
    parser.add_argument("input", help="Original .fhm2d")
    parser.add_argument("output", help="Rebuilt .fhm2d output")
    parser.add_argument(
        "--payload",
        help="Optional replacement decompressed payload with identical length",
    )
    args = parser.parse_args(argv)

    try:
        input_path = Path(args.input)
        if not input_path.is_file():
            raise FileNotFoundError(f"missing input: {input_path}")
        payload = Path(args.payload).read_bytes() if args.payload else None
        report = repack_file(input_path, Path(args.output), payload)
    except (FileNotFoundError, ValueError, zlib.error) as exc:
        print(exc, file=sys.stderr)
        return 2

    print(
        f"{input_path.name} -> {report['output']} "
        f"size={report['output_size']} "
        f"byte_identical={report['byte_identical']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
