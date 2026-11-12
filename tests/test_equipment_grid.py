import unittest

from aip.equipment_grid import equipment_grid


def equipment(item_id, kind, box):
    return {"id": item_id, "type": kind, "bbox": box}


class EquipmentGridTests(unittest.TestCase):
    def test_pump_routes_start_at_first_column_in_visual_order(self):
        symbols = [
            equipment("pump-b", "pump", [10, 105, 30, 125]),
            equipment("valve-a2", "valve", [25, 10, 45, 30]),
            equipment("pump-a", "pump", [65, 10, 85, 30]),
            equipment("valve-b", "valve", [45, 105, 65, 125]),
            equipment("valve-a1", "valve", [45, 10, 65, 30]),
            equipment("tank", "tank", [95, 3, 115, 23]),
        ]
        routes = [
            {"component_ids": ["pump-b", "valve-b"]},
            {"component_ids": ["pump-a", "valve-a1", "valve-a2"]},
        ]
        expected = {
            "pump-a": [1, 1], "valve-a1": [2, 1], "valve-a2": [3, 1],
            "pump-b": [1, 2], "valve-b": [2, 2], "tank": [1, 3],
        }
        self.assertEqual(equipment_grid(symbols, routes), expected)
        self.assertEqual(equipment_grid(reversed(symbols), reversed(routes)), expected)

    def test_shared_nodes_have_one_primary_coordinate(self):
        symbols = [
            equipment("a", "pump", [1, 1, 5, 5]),
            equipment("b", "pump", [1, 11, 5, 15]),
            equipment("shared", "valve", [6, 3, 10, 7]),
            equipment("end", "valve", [11, 3, 15, 7]),
            equipment("short-branch", "valve", [11, 19, 15, 23]),
        ]
        routes = [
            {"component_ids": ["a", "short-branch"]},
            {"component_ids": ["b", "shared"]},
            {"component_ids": ["a", "shared", "end"]},
        ]
        self.assertEqual(equipment_grid(symbols, routes), {
            "a": [1, 1], "shared": [2, 1], "end": [3, 1],
            "b": [1, 2], "short-branch": [1, 3],
        })

    def test_remaining_rows_use_symbol_centers_and_put_unboxed_last(self):
        symbols = [
            equipment("unknown-b", "unknown", None),
            equipment("lower", "valve", [1, 39, 9, 51]),
            equipment("upper-right", "valve", [31, 12, 39, 24]),
            equipment("upper-left", "valve", [11, 14, 19, 26]),
            equipment("unknown-a", "unknown", None),
        ]
        self.assertEqual(equipment_grid(symbols, []), {
            "upper-left": [1, 1], "upper-right": [2, 1], "lower": [1, 2],
            "unknown-a": [1, 3], "unknown-b": [2, 3],
        })
        self.assertEqual(equipment_grid([], []), {})
