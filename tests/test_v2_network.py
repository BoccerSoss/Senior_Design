from __future__ import annotations

import copy
import unittest

from aip.v2.network import scope_isolation, search, trace_network


def component(ident, typ="junction", **fields):
    return {"id": ident, "tag": ident.upper(), "type": typ,
            "review_status": "confirmed", **fields}


def valve(ident, **fields):
    return component(ident, "manual valve", isolation_credit="eligible", normal_position="open", **fields)


def sheet(ident, nodes, pairs=(), facility="lab"):
    return {"id": ident, "facility_id": facility, "title": f"Drawing {ident}",
            "drawing_number": f"DWG-{ident}", "revision": "A", "components": nodes,
            "connections": [{"id": f"{ident}-{i}", "source": a, "target": b,
                             "kind": "pipe", "review_status": "confirmed"}
                            for i, (a, b) in enumerate(pairs)]}


def link(a, b, status="confirmed", **fields):
    return {"id": f"link-{a[0]}-{b[0]}", "facility_id": "lab",
            "from": {"sheet_id": a[0], "component_id": a[1]},
            "to": {"sheet_id": b[0], "component_id": b[1]}, "status": status, **fields}


class NetworkTests(unittest.TestCase):
    def branched(self):
        return {"facilities": [{"id": "lab", "name": "Test lab"}], "sheets": [
            sheet("one", [component("pump", "pump"), component("tee"), valve("a"), valve("b"),
                          component("supply", "tank")],
                  [("pump", "tee"), ("tee", "a"), ("tee", "b"), ("b", "supply")])], "links": []}

    def test_both_branch_boundaries_stop_before_supply(self):
        state = self.branched()
        original = copy.deepcopy(state)
        result = scope_isolation(state, "lab", "one", "pump")
        self.assertEqual(result["status"], "candidate")
        self.assertEqual([v["tag"] for v in result["closures"]], ["A", "B"])
        self.assertEqual([v["index"] for v in result["closures"]], [1, 2])
        self.assertNotIn("supply", [node["component_id"] for node in result["nodes"]])
        self.assertEqual(state, original)
        traced = trace_network(state, "lab", "one", "pump")
        self.assertEqual(len(traced["nodes"]), 5)

    def test_confirmed_cross_sheet_continuation_and_facility_scope(self):
        state = {"sheets": [
            sheet("one", [component("pump", "pump"), component("off", "connector")], [("pump", "off")]),
            sheet("two", [component("off", "connector"), valve("stop")], [("off", "stop")]),
            sheet("other", [valve("stop")], facility="other-lab")],
            "links": [link(("one", "off"), ("two", "off"))]}
        result = scope_isolation(state, "lab", "one", "pump")
        self.assertEqual(result["status"], "candidate")
        self.assertEqual([(v["sheet_id"], v["component_id"]) for v in result["closures"]], [("two", "stop")])
        self.assertTrue(any(edge["cross_sheet"] for edge in result["edges"]))
        with self.assertRaises(ValueError):
            trace_network(state, "other-lab", "one", "pump")
        state["links"].append(link(("one", "off"), ("other", "stop")))
        result = scope_isolation(state, "lab", "one", "pump")
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any(item["code"] == "invalid_sheet_link" for item in result["open_ends"]))
        self.assertFalse(any(node["sheet_id"] == "other" for node in result["nodes"]))

    def test_suggested_link_is_explicit_gap_and_is_not_followed(self):
        state = {"sheets": [
            sheet("one", [component("pump", "pump"), component("off", "connector")], [("pump", "off")]),
            sheet("two", [component("off", "connector"), valve("stop")], [("off", "stop")])],
            "links": [link(("one", "off"), ("two", "off"), "suggested")]}
        result = scope_isolation(state, "lab", "one", "pump")
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["closures"])
        self.assertTrue(any(item["code"] == "unconfirmed_sheet_link" for item in result["open_ends"]))
        self.assertEqual({node["sheet_id"] for node in result["nodes"]}, {"one"})
        self.assertTrue(trace_network(state, "lab", "one", "pump")["warnings"])

    def test_invalid_confirmed_links_cannot_bypass_connector_semantics(self):
        state = {"sheets": [
            sheet("one", [component("pump", "pump"), component("off", "connector")], [("pump", "off")]),
            sheet("two", [component("off", "connector"), valve("stop")], [("off", "stop")])],
            "links": [link(("one", "off"), ("two", "stop"))]}
        for bad in (state["links"][0], link(("one", "off"), ("one", "pump")),
                    link(("one", "off"), ("two", "off"), kind="unknown")):
            with self.subTest(link=bad):
                state["links"] = [bad]
                result = scope_isolation(state, "lab", "one", "pump")
                self.assertEqual(result["status"], "incomplete")
                self.assertFalse(result["closures"])
                self.assertEqual({node["sheet_id"] for node in result["nodes"]}, {"one"})
                self.assertTrue(any(item["code"] == "invalid_sheet_link" for item in result["open_ends"]))

    def test_check_and_motor_valves_are_not_credited_and_signal_is_excluded(self):
        state = {"sheets": [sheet("one", [
            component("pump", "pump"), component("check", "check valve", isolation_credit="eligible"),
            component("motor", "motor-operated valve", isolation_credit="eligible"), valve("stop"),
            component("instrument", "instrument")], [("pump", "check"), ("check", "motor"), ("motor", "stop")])],
            "links": []}
        state["sheets"][0]["connections"].append({"id": "sig", "source": "pump", "target": "instrument", "kind": "signal"})
        result = scope_isolation(state, "lab", "one", "pump")
        self.assertEqual([item["tag"] for item in result["closures"]], ["STOP"])
        self.assertEqual({item["tag"] for item in result["not_credited"]}, {"CHECK", "MOTOR"})
        self.assertNotIn("instrument", {item["component_id"] for item in result["nodes"]})
        traced = trace_network(state, "lab", "one", "pump", include_signals=True)
        self.assertIn("instrument", {item["component_id"] for item in traced["nodes"]})

    def test_unreviewed_types_paths_and_unbounded_ends_remain_visible(self):
        state = {"sheets": [sheet("one", [component("pump", "pump"),
                component("v", "unknown", review_status="unreviewed", isolation_credit="eligible"),
                component("vent", "vent", role="vent")], [("pump", "v"), ("v", "vent")])], "links": []}
        state["sheets"][0]["connections"][0]["review_status"] = "unreviewed"
        result = scope_isolation(state, "lab", "one", "pump")
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(result["vents"])
        self.assertTrue(result["open_ends"])
        self.assertFalse(result["closures"])
        self.assertTrue(any("type" in value for value in result["warnings"]))
        self.assertTrue(any("Connection" in value for value in result["warnings"]))

    def test_cycle_terminates_and_unreviewed_manual_valve_does_not_stop_search(self):
        state = {"sheets": [sheet("one", [component("pump", "pump"),
                component("v", "manual valve", review_status="unreviewed", isolation_credit="eligible"),
                component("tee"), valve("stop")],
                [("pump", "v"), ("v", "tee"), ("tee", "pump"), ("tee", "stop")])], "links": []}
        result = scope_isolation(state, "lab", "one", "pump")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual([item["tag"] for item in result["closures"]], ["STOP"])
        self.assertEqual([item["tag"] for item in result["not_credited"]], ["V"])
        self.assertEqual(len(result["nodes"]), 4)
        self.assertEqual(len(result["edges"]), 4)

    def test_rejected_connections_are_gaps_at_both_ends_and_never_traversed(self):
        for kind in ("pipe", "signal"):
            with self.subTest(kind=kind):
                state = {"sheets": [sheet("one", [component("pump", "pump"), valve("stop")],
                                         [("pump", "stop")])], "links": []}
                state["sheets"][0]["connections"][0].update(kind=kind, review_status="rejected")
                for start in ("pump", "stop"):
                    traced = trace_network(state, "lab", "one", start, include_signals=True)
                    self.assertEqual([node["component_id"] for node in traced["nodes"]], [start])
                    self.assertEqual(traced["edges"], [])
                    self.assertTrue(any("rejected" in warning for warning in traced["warnings"]))
                if kind == "pipe":
                    result = scope_isolation(state, "lab", "one", "pump")
                    self.assertEqual(result["status"], "incomplete")
                    self.assertEqual(result["closures"], [])
                    self.assertTrue(any(item["code"] == "rejected_connection"
                                        and item["component_id"] == "pump" for item in result["open_ends"]))

    def test_connection_gaps_remain_visible_at_an_eligible_boundary(self):
        cases = [("unsupported_connection", {"kind": "unknown"}),
                 ("rejected_connection", {"review_status": "rejected"}),
                 ("missing_endpoint", {"target": "missing"})]
        for code, patch in cases:
            with self.subTest(code=code):
                state = {"sheets": [sheet("one", [component("pump", "pump"), valve("stop"),
                                                    component("other", "tank")],
                                         [("pump", "stop"), ("stop", "other")])], "links": []}
                state["sheets"][0]["connections"][1].update(patch)
                result = scope_isolation(state, "lab", "one", "pump")
                self.assertEqual([item["tag"] for item in result["closures"]], ["STOP"])
                self.assertEqual(result["status"], "incomplete")
                self.assertTrue(any(item["code"] == code and item["component_id"] == "stop"
                                    for item in result["open_ends"]))
                self.assertEqual({node["component_id"] for node in result["nodes"]}, {"pump", "stop"})
                self.assertEqual(len(result["edges"]), 1)

    def test_search_and_revision_filtering_and_missing_endpoint(self):
        current = sheet("one", [component("pump", "pump"), component("off", "connector")], [("pump", "off"), ("pump", "missing")])
        old = sheet("old", [component("pump", "pump")])
        old["active"] = False
        state = {"sheets": [current, old], "links": []}
        result = search(state, "lab", "pump")
        self.assertEqual([item["sheet_id"] for item in result["components"]], ["one"])
        self.assertEqual(len(search(state, "lab", "connector")["connectors"]), 1)
        self.assertEqual(len(search(state, "lab", "DWG-one")["sheets"]), 1)
        trace = trace_network(state, "lab", "one", "pump")
        self.assertEqual(len(trace["nodes"]), 2)
        self.assertTrue(any("endpoint" in message for message in trace["warnings"]))

    def test_explicit_manual_actuation_and_recorded_local_rule_override(self):
        state = {"sheets": [sheet("one", [component("pump", "pump"),
                 component("v", "valve", actuation="manual", isolation_credit="eligible", normal_position="closed")],
                 [("pump", "v")])], "links": [], "isolation_rules": {"manual_valve_types": []}}
        result = scope_isolation(state, "lab", "one", "pump")
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["rules"]["manual_valve_types"], [])
        self.assertIn("Verify", result["closures"][0]["reason"])

    def test_duplicate_segments_do_not_hide_terminal_and_source_is_explicit(self):
        state = {"sheets": [sheet("one", [component("pump", "pump"), component("source", "tank")],
                 [("pump", "source"), ("pump", "source")])], "links": []}
        result = scope_isolation(state, "lab", "one", "pump")
        self.assertTrue(any(item["code"] == "unbounded_terminal" for item in result["open_ends"]))
        state["sheets"][0]["components"][0]["role"] = "external source"
        result = scope_isolation(state, "lab", "one", "pump")
        self.assertTrue(any(item["code"] == "explicit_open_end" for item in result["open_ends"]))


if __name__ == "__main__":
    unittest.main()
