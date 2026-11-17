"""Source-symbol intersections, exact axes and source-evidence preservation."""
from copy import deepcopy
import json
import math
import unittest

from aip.v2.grid import equipment_positions, grid_settings


def component(ident, x, y, tag=None):
    return {"id":ident,"tag":tag or ident,"type":"pump","bbox":[x-5,y-5,x+5,y+5]}


def sheet(items, columns=4, rows=3):
    return {"id":"drawing","width":1800,"height":800,"grid":{"columns":columns,"rows":rows},
            "components":items,"connections":[{"id":"edge","source":"a","target":"b","kind":"direct_undirected"}]}


class GridPointTests(unittest.TestCase):
    def assert_source_intersections(self, result):
        xs,ys=result["axes"]["x"],result["axes"]["y"]
        self.assertEqual(sorted(set(xs)),xs)
        self.assertEqual(sorted(set(ys)),ys)
        self.assertEqual(len(xs),result["grid"]["columns"])
        self.assertEqual(len(ys),result["grid"]["rows"])
        coordinates=[]
        for record in result["coordinates"]:
            coordinate=record["coordinate"]
            if coordinate is None:
                self.assertIsNone(record["display_point"])
                continue
            column,row=coordinate
            self.assertIsInstance(column,int)
            self.assertIsInstance(row,int)
            self.assertTrue(1<=column<=len(xs))
            self.assertTrue(1<=row<=len(ys))
            self.assertEqual([xs[column-1],ys[row-1]],record["display_point"])
            self.assertEqual(record["source_anchor"],record["display_point"])
            coordinates.append(tuple(coordinate))
        self.assertEqual(len(coordinates)!=len(set(coordinates)),result["shared_cells"])

    def test_original_chain_keeps_every_exact_symbol_center(self):
        drawing=sheet([component("a",280,380),component("b",720,410.5),
                       component("c",1100,410),component("d",1520,380)],4,1)
        result=equipment_positions(drawing)
        self.assertEqual({"x":[280,720,1100,1520],"y":[380,410,410.5]},result["axes"])
        self.assertEqual([[1,1],[2,3],[3,2],[4,1]],[r["coordinate"] for r in result["coordinates"]])
        self.assertEqual([[280,380],[720,410.5],[1100,410],[1520,380]],
                         [r["display_point"] for r in result["coordinates"]])
        self.assertEqual("source-intersections",result["layout_kind"])
        self.assert_source_intersections(result)

    def test_grid_spacing_follows_source_positions_instead_of_uniform_cells(self):
        result=equipment_positions(sheet([component("a",100,200),component("b",200,200),component("c",1000,200)]))
        self.assertEqual([100,200,1000],result["axes"]["x"])
        self.assertEqual([[1,1],[2,1],[3,1]],[r["coordinate"] for r in result["coordinates"]])
        self.assertEqual({"width":1800,"height":800},result["display"])
        self.assert_source_intersections(result)

    def test_nearby_distinct_source_positions_are_not_clustered(self):
        result=equipment_positions(sheet([component("a",100,200),component("b",100.5,200.5)]))
        self.assertEqual([100,100.5],result["axes"]["x"])
        self.assertEqual([200,200.5],result["axes"]["y"])
        self.assertEqual([[1,1],[2,2]],[r["coordinate"] for r in result["coordinates"]])
        self.assert_source_intersections(result)

    def test_branches_keep_source_intersections(self):
        result=equipment_positions(sheet([component("left",200,400),component("junction",850,400),
            component("upper",1300,180),component("lower",1300,650)]))
        records={r["component_id"]:r for r in result["coordinates"]}
        self.assertEqual([1,2],records["left"]["coordinate"])
        self.assertEqual([2,2],records["junction"]["coordinate"])
        self.assertEqual([3,1],records["upper"]["coordinate"])
        self.assertEqual([3,3],records["lower"]["coordinate"])
        self.assert_source_intersections(result)

    def test_labels_types_and_observation_order_do_not_move_points(self):
        drawing=sheet([component("a",850,400),component("b",852,400),component("c",854,400)])
        original=equipment_positions(drawing)
        changed=deepcopy(drawing)
        changed["components"].reverse()
        for item in changed["components"]:
            item.update(tag="Different readable label",type="valve")
        later=equipment_positions(changed)
        by_id=lambda result:{r["component_id"]:(r["coordinate"],r["display_point"]) for r in result["coordinates"]}
        self.assertEqual(by_id(original),by_id(later))
        self.assertEqual(original["axes"],later["axes"])

    def test_legacy_cell_counts_do_not_override_exact_source_axes(self):
        drawing=sheet([component("a",100,200),component("b",200,200),component("c",1000,200)],1,1)
        result=equipment_positions(drawing)
        self.assertEqual({"columns":1,"rows":1,"origin":"top-left"},result["requested_grid"])
        self.assertEqual({"columns":3,"rows":1,"origin":"top-left"},result["grid"])
        changed=equipment_positions({**drawing,"grid":{"columns":200,"rows":200}})
        self.assertEqual(result["axes"],changed["axes"])
        self.assertEqual(result["coordinates"],changed["coordinates"])

    def test_coincident_centers_are_shared_and_reported_without_fake_offsets(self):
        result=equipment_positions(sheet([component("a",900,400),component("b",900,400)]))
        self.assertEqual([[1,1],[1,1]],[r["coordinate"] for r in result["coordinates"]])
        self.assertEqual([[900,400],[900,400]],[r["display_point"] for r in result["coordinates"]])
        self.assertTrue(result["shared_cells"])
        self.assertTrue(any("share the same source center" in warning for warning in result["warnings"]))
        self.assert_source_intersections(result)

    def test_invalid_and_missing_boxes_remain_unplaced(self):
        boxes=[None,[],[1,2,3],[0,0,0,1],[-1,0,1,1],[0,0,1801,1],[0,0,math.nan,1],
               [0,0,math.inf,1],[0,0,10**500,1],[False,0,1,1],"not a box"]
        result=equipment_positions(sheet([{"id":str(i),"bbox":box} for i,box in enumerate(boxes)]))
        self.assertTrue(all(r["coordinate"] is None for r in result["coordinates"]))
        self.assertTrue(all(r["source_anchor"] is None for r in result["coordinates"]))
        self.assertEqual(len(boxes),len(result["warnings"]))

    def test_source_geometry_connections_and_state_are_never_mutated(self):
        drawing=sheet([component("a",280,380),component("b",720,410.5)],2,1)
        original=deepcopy(drawing)
        result=equipment_positions(drawing)
        self.assertEqual(original,drawing)
        self.assertEqual([280,380],result["coordinates"][0]["source_anchor"])
        changed={**drawing,"grid":{"columns":5,"rows":4}}
        equipment_positions(changed)
        self.assertEqual(original["components"],changed["components"])
        self.assertEqual(original["connections"],changed["connections"])

    def test_empty_drawing_has_no_invented_axes(self):
        result=equipment_positions(sheet([],1,1))
        self.assertEqual([],result["coordinates"])
        self.assertEqual([],result["warnings"])
        self.assertEqual({"x":[],"y":[]},result["axes"])
        self.assertEqual({"columns":0,"rows":0,"origin":"top-left"},result["grid"])
        self.assertEqual({"width":1800,"height":800},result["display"])

    def test_invalid_image_dimensions_do_not_invent_locations(self):
        for width in (0,-1,True,math.inf,math.nan,10**500):
            drawing=sheet([component("a",100,100)])
            drawing["width"]=width
            result=equipment_positions(drawing)
            self.assertIsNone(result["coordinates"][0]["coordinate"])
            self.assertEqual(0,result["display"]["width"])
            json.dumps(result,allow_nan=False)

    def test_duplicate_component_ids_are_explicitly_unplaced(self):
        result=equipment_positions(sheet([component("a",280,380),component("a",720,410.5)]))
        self.assertEqual([None,None],[r["coordinate"] for r in result["coordinates"]])
        self.assertTrue(any("repeated" in warning for warning in result["warnings"]))

    def test_finite_bounds_do_not_overflow_when_the_center_is_calculated(self):
        drawing=sheet([{"id":"a","bbox":[8e307,10,1e308,20]}])
        drawing["width"]=1e308
        result=equipment_positions(drawing)
        self.assertTrue(all(math.isfinite(v) for v in result["coordinates"][0]["source_anchor"]))
        self.assertEqual([1,1],result["coordinates"][0]["coordinate"])
        json.dumps(result,allow_nan=False)

    def test_grid_settings_are_validated(self):
        for value in (0,False,"grid",[],{"columns":0},{"rows":201},{"columns":True},{"rows":1.5}):
            with self.assertRaises(ValueError):
                grid_settings(value)
        self.assertEqual({"columns":12,"rows":16,"origin":"top-left"},grid_settings())


if __name__=="__main__":
    unittest.main()
