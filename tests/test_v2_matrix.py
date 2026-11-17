from copy import deepcopy
import csv
import io
import unittest

from aip.v2.matrix import build_matrix, matrix_csv


def component(ident, tag=None, typ="tank", **fields):
    return {"id": ident, "tag": tag if tag is not None else ident.upper(), "type": typ,
            "review_status": "confirmed", **fields}


def edge(source, target, **fields):
    return {"id": f"{source}-{target}", "source": source, "target": target,
            "kind": "direct_undirected", "review_status": "confirmed", **fields}


def sheet(ident, components, connections=(), **fields):
    return {"id": ident, "facility_id": "collection", "drawing_number": ident.upper(),
            "components": components, "connections": list(connections), "active": True, **fields}


def state(*sheets, links=()):
    return {"facilities": [{"id": "collection"}, {"id": "other"}], "sheets": list(sheets), "links": list(links)}


class DirectMatrixTests(unittest.TestCase):
    def chain(self):
        return state(sheet("drawing", [component("first", "Q-91"), component("second", "B-6", "valve"),
            component("third", "Z-4", "pump"), component("fourth", "Q-22")],
            [edge("first", "second"), edge("second", "third"), edge("third", "fourth")]))

    def test_four_equipment_chain_has_symmetric_immediate_adjacency(self):
        source = self.chain()
        before = deepcopy(source)
        result = build_matrix(source, "collection", "drawing")
        self.assertEqual(["Q-91", "B-6", "Z-4", "Q-22"], result["order"])
        self.assertEqual([[0,1,0,0], [1,0,1,0], [0,1,0,1], [0,0,1,0]], result["matrix"])
        self.assertEqual(3, result["stats"]["direct_connections"])
        self.assertEqual([], result["diagnostics"])
        self.assertEqual(source, before)

    def test_changed_tags_and_connections_change_output_without_truth(self):
        source = self.chain()
        source["sheets"][0]["components"][0]["tag"] = "NEW-805"
        source["sheets"][0]["connections"] = [edge("first", "third")]
        result = build_matrix(source, "collection", "drawing")
        self.assertEqual("NEW-805", result["order"][0])
        self.assertEqual([[0,0,1,0], [0,0,0,0], [1,0,0,0], [0,0,0,0]], result["matrix"])

    def test_reverse_parallel_and_repeated_records_do_not_inflate_binary_edge(self):
        source = self.chain()
        source["sheets"][0]["connections"] = [edge("first","second"), edge("second","first", id="reverse"),
            edge("first","second", id="parallel", path=[[1,1],[1,5],[9,5]])]
        result = build_matrix(source, "collection", "drawing")
        self.assertEqual(1, result["stats"]["direct_connections"])
        self.assertEqual(2, result["stats"]["duplicate_connections"])
        self.assertEqual(3, len(result["direct_edges"][0]["observations"]))
        self.assertEqual(2, sum(map(sum, result["matrix"])))

    def test_bad_unknown_signal_and_rejected_edges_stay_explicit(self):
        source = self.chain()
        source["sheets"][0]["connections"] = [edge("first","missing"), edge("first","first"),
            edge("first","second",kind="unknown"), edge("second","third",kind="signal"),
            edge("third","fourth",review_status="rejected"), edge("first","fourth",kind=None)]
        result = build_matrix(source, "collection", "drawing")
        self.assertEqual(0, sum(map(sum, result["matrix"])))
        self.assertEqual(6, result["stats"]["excluded_connections"])
        self.assertEqual({"missing_endpoint", "self_connection", "unclassified_connection", "signal_connection", "rejected_connection"},
                         {item["code"] for item in result["diagnostics"]})
        self.assertEqual(2, result["stats"]["invalid_observations"])

    def test_unread_and_duplicate_names_remain_distinct_rows(self):
        source = state(sheet("s", [component("a", ""), component("b", "unreadable"), component("c", " p- 7 "),
            component("d", "P-7")], [edge("a","b"), edge("c","d")]))
        result = build_matrix(source, "collection", "s")
        self.assertEqual(4, len(set(result["order"])))
        self.assertEqual(["unresolved:s/a", "unresolved:s/b", "P-7 [s/c]", "P-7 [s/d]"], result["order"])
        self.assertEqual(2, result["stats"]["unread_labels"])
        self.assertEqual(2, result["stats"]["duplicate_labels"])
        self.assertEqual(2, result["stats"]["direct_connections"])
        self.assertEqual(" p- 7 ", result["nodes"][2]["tag"])

    def test_junction_branch_is_not_transitively_contracted(self):
        source = state(sheet("s", [component("p",typ="pump"), component("j",typ="junction"),
            component("left",typ="valve"), component("right",typ="valve")],
            [edge("p","j"),edge("j","left"),edge("j","right")]))
        result = build_matrix(source, "collection", "s")
        self.assertEqual(["P","LEFT","RIGHT"], result["order"])
        self.assertEqual([[0,0,0],[0,0,0],[0,0,0]], result["matrix"])
        self.assertEqual(1, result["stats"]["excluded_components"])
        self.assertEqual(3, result["stats"]["excluded_connections"])
        self.assertEqual(3, sum(d["code"]=="excluded_endpoint" for d in result["diagnostics"]))

    def test_sheet_scope_and_explicit_facility_matrix_do_not_join_pages(self):
        source = state(sheet("one", [component("a"), component("b")], [edge("a","b")]),
            sheet("two", [component("a"), component("c")], [edge("a","c")]),
            sheet("old", [component("old")], active=False),
            sheet("foreign", [component("foreign")], facility_id="other"),
            links=[{"id":"continuation", "facility_id":"collection", "status":"confirmed",
                "from":{"sheet_id":"one","component_id":"a"}, "to":{"sheet_id":"two","component_id":"a"}}])
        single = build_matrix(source, "collection", "two")
        self.assertEqual(["A","C"], single["order"])
        self.assertEqual("sheet", single["scope"]["kind"])
        combined = build_matrix(source, "collection")
        self.assertEqual(["one", "two"], combined["scope"]["sheet_ids"])
        self.assertEqual([[0,1,0,0],[1,0,0,0],[0,0,0,1],[0,0,1,0]],combined["matrix"])
        self.assertEqual(1, combined["stats"]["excluded_sheet_links"])
        self.assertFalse(combined["scope"]["cross_sheet"])
        archived = build_matrix(source,"collection","old")
        self.assertEqual(["OLD"], archived["order"])
        self.assertEqual("archived_revision",archived["diagnostics"][0]["code"])
        with self.assertRaises(ValueError):
            build_matrix(source,"collection","foreign")

    def test_rejected_and_duplicate_id_equipment_does_not_get_arbitrary_edges(self):
        source = state(sheet("s", [component("same","A"),component("same","B"), component("rejected",review_status="rejected"),
                                  component("okay")], [edge("same","okay"),edge("rejected","okay")]))
        result = build_matrix(source,"collection","s")
        self.assertEqual(["OKAY"],result["order"])
        self.assertEqual([[0]],result["matrix"])
        self.assertEqual(3,result["stats"]["excluded_components"])
        self.assertEqual(2,result["stats"]["invalid_observations"])

    def test_review_warnings_do_not_remove_unread_observations_or_observed_edges(self):
        source = state(sheet("s", [component("a","",review_status="unreviewed"),component("b")],
                             [edge("a","b",review_status="unreviewed",direction="target_to_source")]))
        result = build_matrix(source,"collection","s")
        self.assertEqual([[0,1],[1,0]],result["matrix"])
        self.assertEqual(1,result["stats"]["unreviewed_components"])
        self.assertEqual(1,result["stats"]["unreviewed_connections"])
        self.assertEqual("unreviewed",result["direct_edges"][0]["review_status"])
        self.assertEqual("target_to_source",result["direct_edges"][0]["observations"][0]["direction"])
        self.assertGreaterEqual(len(result["warnings"]),3)

    def test_csv_uses_identical_explicit_axis_order_and_quotes_names(self):
        source = state(sheet("s", [component("a","A,ONE"),component("b","=FORMULA")], [edge("a","b")]))
        result = build_matrix(source,"collection","s")
        rows = list(csv.reader(io.StringIO(matrix_csv(result))))
        self.assertEqual(["Equipment","A,ONE","'=FORMULA"],rows[0])
        self.assertEqual(["A,ONE","0","1"],rows[1])
        self.assertEqual(["'=FORMULA","1","0"],rows[2])
        self.assertEqual("=FORMULA",result["order"][1])

    def test_empty_input_and_invalid_collection_are_explicit(self):
        result = build_matrix(state(sheet("s",[])),"collection","s")
        self.assertEqual([],result["matrix"])
        self.assertEqual([],result["order"])
        self.assertEqual(0,result["stats"]["components"])
        with self.assertRaises(ValueError):
            build_matrix(state(),"not-a-collection")


if __name__ == "__main__":
    unittest.main()
