import unittest

from aip.v3.review import review_queue


class ReviewQueueTests(unittest.TestCase):
    def test_unread_unknown_duplicate_and_low_confidence_are_actionable(self):
        trace = {"components": [
            {"id": "confirmed", "type": "valve", "tag": "V-1", "review_status": "confirmed"},
            {"id": "duplicate", "type": "valve", "tag": "v – 1"},
            {"id": "missed-label-and-type", "type": "unknown", "tag": "unreadable"},
            {"id": "weak-shape", "type": "valve", "tag": "V-2", "confidence": 0.4},
            {"id": "recognized", "type": "pump", "tag": "P-3", "review_status": "unreviewed",
             "uncertainty": "The outline alone does not establish operating state."},
            {"id": "junction", "type": "pipe junction", "tag": ""},
            {"id": "removed", "type": "unknown", "review_status": "rejected"},
        ]}
        self.assertEqual(review_queue(trace), [
            {"kind": "component", "id": "duplicate", "reason": "Equipment name is repeated on this drawing"},
            {"kind": "component", "id": "missed-label-and-type",
             "reason": "Equipment name unread · Equipment type unknown"},
            {"kind": "component", "id": "weak-shape", "reason": "Model reported low confidence"},
        ])
        trace["components"][2].update(tag="T-1", type="tank", review_status="confirmed")
        self.assertNotIn("missed-label-and-type", [item["id"] for item in review_queue(trace)])

    def test_direction_review_uses_conflict_or_failed_evidence_instead_of_every_unknown(self):
        trace = {
            "connections": [
                {"id": "direct", "kind": "pipe", "direction": "source_to_target",
                 "direction_evidence": {"basis": "on_edge", "arrow_ids": ["A"]}},
                {"id": "run", "kind": "direct_undirected", "direction": "target_to_source",
                 "direction_evidence": {"basis": "run", "arrow_ids": ["B"]}},
                {"id": "unknown", "kind": "pipe", "direction": "unknown"},
                {"id": "signal", "kind": "signal", "direction": "unknown"},
                {"id": "collision", "kind": "pipe", "direction": "unknown",
                 "direction_evidence": {"basis": "conflict", "arrow_ids": ["A", "B"]}},
                {"id": "shared-arrow", "kind": "pipe", "direction": "unknown",
                 "direction_evidence": {"basis": "ambiguous", "arrow_ids": ["C"]}},
                {"id": "claim", "kind": "pipe", "direction": "unknown",
                 "direction_evidence": {"basis": "unsupported", "proposed_direction": "source_to_target"}},
                {"id": "legacy", "kind": "pipe", "direction": "unknown"},
                {"id": "unaligned", "kind": "pipe", "direction": "unknown"},
                {"id": "unclassified", "kind": "unknown", "direction": "unknown"},
                {"id": "reviewer-checked", "kind": "pipe", "direction": "unknown", "review_status": "confirmed",
                 "direction_evidence": {"basis": "conflict", "arrow_ids": ["A", "B"]}},
            ],
            "arrows": [{"id": "D", "proposed_connection_id": "unaligned", "verified": False}],
            "uncertainties": ["legacy: The proposed direction lacked matching arrow evidence."],
        }
        self.assertEqual(review_queue(trace), [
            {"kind": "connection", "id": "collision", "reason": "Visible arrows disagree"},
            {"kind": "connection", "id": "shared-arrow", "reason": "Arrow cannot be assigned to one connection"},
            {"kind": "connection", "id": "claim", "reason": "Proposed direction lacks matching arrow evidence"},
            {"kind": "connection", "id": "legacy", "reason": "Proposed direction lacks matching arrow evidence"},
            {"kind": "connection", "id": "unaligned", "reason": "Proposed arrow could not be aligned with the connection"},
            {"kind": "connection", "id": "unclassified", "reason": "Connection type unknown"},
        ])
