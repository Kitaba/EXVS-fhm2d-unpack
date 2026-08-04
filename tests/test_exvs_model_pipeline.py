import csv
import tempfile
import unittest
from pathlib import Path

from _internal.core.exvs_model_pipeline import choose_package, map_runtime_textures


class ExvsModelPipelineTests(unittest.TestCase):
    def test_choose_package_uses_unique_hash_coverage(self):
        textures = [
            {"dds_path": "A.dds", "pixel_sha256": "hash-a"},
            {"dds_path": "B.dds", "pixel_sha256": "hash-b"},
        ]
        matches = [
            {"dds": "A.dds", "source": "partial.fhm2d", "payload_offset": "16"},
            {"dds": "A.dds", "source": "complete.fhm2d", "payload_offset": "32"},
            {"dds": "B.dds", "source": "complete.fhm2d", "payload_offset": "64"},
        ]

        package, evidence = choose_package(textures, matches)

        self.assertEqual(package.name, "complete.fhm2d")
        self.assertEqual(evidence["coverage"], 1.0)
        self.assertEqual(evidence["matched_texture_hashes"], 2)

    def test_runtime_map_uses_payload_offset_to_resolve_duplicate_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            fields = [
                "texture_index", "group_label", "embedded_name", "embedded_index",
                "width", "height", "storage_format", "dds_output", "pixel_sha256",
                "payload_data_offset",
            ]
            with (project / "textures.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows([
                    {
                        "texture_index": 0, "group_label": "g0", "embedded_name": "wrong",
                        "embedded_index": 0, "width": 4, "height": 4,
                        "storage_format": "BC7", "dds_output": "wrong.dds",
                        "pixel_sha256": "same", "payload_data_offset": 100,
                    },
                    {
                        "texture_index": 1, "group_label": "g1", "embedded_name": "correct",
                        "embedded_index": 1, "width": 4, "height": 4,
                        "storage_format": "BC7", "dds_output": "correct.dds",
                        "pixel_sha256": "same", "payload_data_offset": 200,
                    },
                ])
            dds = str((project / "runtime.dds").resolve())
            runtime = [{
                "event_id": 1, "semantic": "BaseColorMap", "resource": "ResourceId::1",
                "resource_id": "1", "dds_path": dds, "pixel_sha256": "same",
            }]

            mapped = map_runtime_textures(
                runtime,
                project,
                {"payload_offsets_by_dds": {dds: 200}},
            )

            self.assertEqual(mapped[0]["embedded_name"], "correct")
            self.assertEqual(mapped[0]["texture_index"], 1)


if __name__ == "__main__":
    unittest.main()
