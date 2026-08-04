import json
import tempfile
import unittest
from pathlib import Path

from _internal.core.renderdoc_render_state_summarize import canonical_hash, pbr_bindings, stable_value, summarize


def event(event_id, base_color, cbuffer_hash="same"):
    resources = []
    for name, resource in (
        ("BaseColorMap", base_color),
        ("NormalMap", "ResourceId::20"),
        ("MetallicMap", "ResourceId::21"),
        ("RoughnessMap", "ResourceId::22"),
        ("AmbientOcclusionMap", "ResourceId::23"),
        ("EmissiveMap", "ResourceId::24"),
    ):
        resources.append({"name": name, "descriptors": [{"resource": resource}]})
    return {
        "event_id": event_id,
        "pipeline": {"rasterizer": {"cullMode": "Back"}, "depth_stencil": {}, "color_blends": []},
        "stages": [
            {
                "stage": "ShaderStage.Pixel",
                "shader": {"resource_id": "ResourceId::99"},
                "read_only_resources": resources,
                "samplers": [],
                "constant_buffers": [
                    {"name": "UpdatePerObject", "raw": {"sha256": cbuffer_hash}},
                ],
            }
        ],
    }


class RenderStateSummarizeTests(unittest.TestCase):
    def test_pbr_bindings_supports_descriptor_resource(self):
        result = pbr_bindings(event(1, "ResourceId::134898"))
        self.assertEqual(result["BaseColorMap"], "ResourceId::134898")
        self.assertEqual(len(result), 6)

    def test_canonical_hash_is_key_order_independent(self):
        self.assertEqual(canonical_hash({"a": 1, "b": 2}), canonical_hash({"b": 2, "a": 1}))

    def test_stable_value_removes_swig_pointer_text(self):
        left = stable_value({
            "text": "Swig at 0x1",
            "nested": [{"text": "Swig at 0x2", "value": 3}],
            "legacy": "<Swig Object of type 'BlendEquation *' at 0x111>",
        })
        right = stable_value({
            "text": "Swig at 0x9",
            "nested": [{"text": "Swig at 0xA", "value": 3}],
            "legacy": "<Swig Object of type 'BlendEquation *' at 0x999>",
        })
        self.assertEqual(left, right)

    def test_summarize_groups_identical_material_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = [event(10, "ResourceId::134898"), event(11, "ResourceId::134898")]
            manifest_rows = []
            for item in events:
                path = root / "E{}".format(item["event_id"]) / "render_state.json"
                path.parent.mkdir()
                path.write_text(json.dumps(item), encoding="utf-8")
                manifest_rows.append({"event_id": item["event_id"], "path": str(path.relative_to(root))})
            (root / "render_state_manifest.json").write_text(
                json.dumps({"events": manifest_rows}), encoding="utf-8"
            )

            report = summarize(root)

        self.assertEqual(report["event_count"], 2)
        self.assertEqual(report["material_count"], 1)
        self.assertEqual(report["materials"][0]["name"], "pbr1")
        self.assertEqual(report["materials"][0]["event_ids"], [10, 11])


if __name__ == "__main__":
    unittest.main()
