import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


CORE = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE))

from renderdoc_mesh_map import build  # noqa: E402


class Args:
    event = 7
    texture = None
    width = 100
    height = 200
    compare_csv = None
    per_view = None
    per_world = None
    background = None


class MeshMapTests(unittest.TestCase):
    def test_build_uv_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            mesh = tmp_path / "mesh.csv"
            with mesh.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["VTX", "IDX", "SV_Position.x", "SV_Position.y", "SV_Position.z", "SV_Position.w", "TEXCOORD.x", "TEXCOORD.y"])
                writer.writerows(
                    [
                        [0, 0, 1, 2, 3, 4, 0.0, 0.0],
                        [1, 1, 2, 3, 4, 5, 1.0, 0.0],
                        [2, 2, 3, 4, 5, 6, 0.0, 1.0],
                    ]
                )
            args = Args()
            args.mesh_csv = str(mesh)
            args.output = str(tmp_path / "out")
            result = build(args)
            self.assertEqual(result["draw"]["triangles"], 1)
            self.assertEqual(result["position"]["space"], "post-VS homogeneous clip-space")
            self.assertEqual(result["uv"]["texture_size"], [100, 200])
            self.assertTrue((tmp_path / "out" / "uv_wireframe.svg").is_file())
            self.assertEqual(json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))["event_id"], 7)


if __name__ == "__main__":
    unittest.main()
