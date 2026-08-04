import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


CORE_ROOT = Path(__file__).resolve().parents[1] / "_internal" / "core"
sys.path.insert(0, str(CORE_ROOT))

import fhm2d_texture_workflow as workflow


class PrepareProjectTests(unittest.TestCase):
    def test_reuses_project_and_restores_only_modified_pngs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "0x12345678.fhm2d"
            source.write_bytes(b"source")
            project_dir = root / "projects" / source.stem
            project_dir.mkdir(parents=True)
            (project_dir / "project.json").write_text("{}", encoding="utf-8")
            project = {"editable_png_directory": "png_edit"}
            textures = [
                {"texture_index": "0", "dds_output": "dds/original.dds"},
                {"texture_index": "1", "dds_output": "dds/unchanged.dds"},
            ]
            status_rows = [
                {"texture_index": 0, "modified": True},
                {"texture_index": 1, "modified": False},
            ]

            with patch.object(
                workflow,
                "project_status",
                return_value=(project, source.resolve(), textures, status_rows),
            ), patch.object(workflow, "convert_dds_to_png") as convert:
                result, result_dir, reused = workflow.prepare_project(
                    source, root / "projects", Path("texconv.exe")
                )

            self.assertTrue(reused)
            self.assertEqual(result, project)
            self.assertEqual(result_dir, project_dir)
            convert.assert_called_once_with(
                Path("texconv.exe"),
                [project_dir / "dds/original.dds"],
                project_dir / "png_edit",
            )


if __name__ == "__main__":
    unittest.main()
