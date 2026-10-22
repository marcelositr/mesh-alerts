import unittest

from shapely.geometry import box

from mesh_alerts_cap import polygon_from_cap


class InvalidPolygonRepairTests(unittest.TestCase):
    def test_bow_tie_repair_preserves_both_polygon_lobes(self):
        # This self-intersecting ring has one lobe in the northwest and one
        # in the southeast. buffer(0) can silently discard one of those lobes.
        geometry = polygon_from_cap(
            "-20,-47 -20.1,-47.1 -20,-47.1 -20.1,-47 -20,-47"
        )

        self.assertIsNotNone(geometry)
        northwest_lobe = box(-47.09, -20.09, -47.07, -20.07)
        southeast_lobe = box(-47.03, -20.03, -47.01, -20.01)
        self.assertTrue(geometry.intersects(northwest_lobe))
        self.assertTrue(geometry.intersects(southeast_lobe))


if __name__ == "__main__":
    unittest.main()
