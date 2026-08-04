import unittest

from _internal.core.renderdoc_model_cluster import cluster_draws


def draw(event, translation, resource, indices=1000):
    return {
        "event_id": event,
        "num_indices": indices,
        "num_instances": 1,
        "translation": list(translation),
        "resources": {"BaseColorMap": "ResourceId::{}".format(resource)},
        "resource_numbers": [resource],
        "marker_path": [],
        "vertex_buffers": [],
        "index_buffer": None,
        "shaders": {},
    }


class RenderDocModelClusterTests(unittest.TestCase):
    def test_separates_nearby_models_by_resource_range(self):
        draws = [
            draw(10, (0, 0, 0), 1000, 900),
            draw(11, (8, 0, 0), 1010, 3000),
            draw(20, (10, 0, 0), 4000, 800),
            draw(21, (18, 0, 0), 4010, 5000),
        ]
        groups = cluster_draws(draws, spatial_threshold=15, resource_gap=100, min_total_indices=1)
        self.assertEqual([group["events"] for group in groups], [[10, 11], [20, 21]])
        self.assertEqual([group["anchor_event"] for group in groups], [11, 21])

    def test_connected_components_retain_detached_parts(self):
        draws = [
            draw(1, (0, 0, 0), 1000),
            draw(2, (10, 0, 0), 1010),
            draw(3, (20, 0, 0), 1020),
        ]
        groups = cluster_draws(draws, spatial_threshold=11, resource_gap=100, min_total_indices=1)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["events"], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
