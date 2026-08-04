import sys
import unittest
from pathlib import Path


CORE = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE))

from fhm2d_buffer_match import make_samples, match_payload  # noqa: E402


class BufferMatchTests(unittest.TestCase):
    def test_consensus_base(self):
        data = b"".join(index.to_bytes(2, "little") for index in range(1, 513))
        payload = b"header" * 13 + data + b"tail"
        samples = make_samples(data, 32, 8)
        result = match_payload(payload, samples, data)
        self.assertEqual(result["exact_full_offset"], 78)
        self.assertGreaterEqual(result["consensus_hits"], 2)
        self.assertEqual(result["consensus_base"], 78)


if __name__ == "__main__":
    unittest.main()
