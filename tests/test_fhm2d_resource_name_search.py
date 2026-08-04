import unittest

from _internal.core import fhm2d_resource_name_search as subject


class ResourceNameSearchTests(unittest.TestCase):
    def test_searches_ascii_case_and_utf16(self):
        subject._WORKER_PATTERNS = subject.pattern_variants(["Leos"])
        data = b"leos\0" + "Leos".encode("utf-16le")
        hits = subject.search_data(data, 3, 100)
        encodings = {hit["encoding"] for hit in hits}
        self.assertIn("ascii_lower", encodings)
        self.assertIn("utf16le", encodings)

    def test_strong_hit_counts_unique_tokens(self):
        self.assertTrue(subject.strong_hit({"unique_token_count": 2}, 2))
        self.assertFalse(subject.strong_hit({"unique_token_count": 1}, 2))


if __name__ == "__main__":
    unittest.main()
