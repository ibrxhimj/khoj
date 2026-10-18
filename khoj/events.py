"""Live changes to the knowledge graph, used to show that memory + graph
together catch new risk on a buyer's shortlist.

Each event writes new nodes with created_at = now and re-runs the red-flag
rules, exactly as an ingestion job would when new K-RERA or court data lands.
"""
from __future__ import annotations

import random
import time

from .db import kg, query
from .flags import apply_flag_rules

PRESETS = {
    "contractor_insolvency": {
        "label": "Sterling EPC (contractor) faces an insolvency petition",
        "kind": "court_case", "target": "Contractor", "target_name": "Sterling EPC",
        "nature": "insolvency", "court": "NCLT Bengaluru",
        "parties": "Operational creditor vs Sterling EPC",
    },
    "title_suit_greens": {
        "label": "Title suit filed over the land of Greenfield Sarjapur Greens",
        "kind": "court_case", "target": "ProjectLand", "target_name": "Greenfield Sarjapur Greens",
        "nature": "title_dispute", "court": "City Civil Court, Bengaluru",
        "parties": "Co-owner's heirs vs previous landowner",
    },
    "complaints_woods": {
        "label": "4 new K-RERA delay complaints against Banyan Grove Whispering Woods",
        "kind": "complaints", "target_name": "Banyan Grove Whispering Woods", "n": 4, "category": "delay",
    },
}


def _resolve_target(preset: dict) -> str | None:
    g = kg()
    if preset["target"] == "Contractor":
        r = query(g, "MATCH (c:Contractor {name: $n}) RETURN c.id AS id", {"n": preset["target_name"]})
    elif preset["target"] == "ProjectLand":
        r = query(g, "MATCH (:Project {name: $n})-[:BUILT_ON]->(l) RETURN l.id AS id", {"n": preset["target_name"]})
    else:
        r = query(g, "MATCH (x {name: $n}) RETURN x.id AS id", {"n": preset["target_name"]})
    return r[0]["id"] if r else None


def fire(preset_key: str) -> dict:
    preset = PRESETS[preset_key]
    g = kg()
    ts = int(time.time())
    if preset["kind"] == "court_case":
        target = _resolve_target(preset)
        if not target:
            return {"error": f"Target not found for {preset_key}"}
        case_no = f"{'CP (IB)' if preset['nature'] == 'insolvency' else 'O.S.'} {random.randint(100, 9999)}/2026"
        g.query(
            """
            MATCH (t {id: $target})
            CREATE (k:CourtCase {id: $id, kind: 'CourtCase', name: $name, case_no: $case_no, court: $court,
                    nature: $nature, status: 'pending', filed: 2026, parties: $parties, created_at: $ts})
            CREATE (k)-[:INVOLVES]->(t)
            """,
            {"target": target, "id": f"CRT-LIVE-{ts}", "name": f"{case_no} ({preset['nature'].replace('_', ' ')})",
             "case_no": case_no, "court": preset["court"], "nature": preset["nature"],
             "parties": preset["parties"], "ts": ts},
        )
    elif preset["kind"] == "complaints":
        pid = query(g, "MATCH (p:Project {name: $n}) RETURN p.id AS id", {"n": preset["target_name"]})
        if not pid:
            return {"error": "Project not found"}
        rows = [{"id": f"CPL-LIVE-{ts}-{i}", "no": f"CMP/2610{random.randint(10, 28)}/{random.randint(10000, 99999):07d}"}
                for i in range(preset["n"])]
        g.query(
            """
            MATCH (p:Project {id: $pid})
            UNWIND $rows AS r
            CREATE (c:Complaint {id: r.id, kind: 'Complaint', name: 'K-RERA ' + r.no, complaint_no: r.no,
                    forum: 'K-RERA', category: $cat, status: 'open', filed: 2026, about: p.name, created_at: $ts})
            CREATE (c)-[:AGAINST]->(p)
            """,
            {"pid": pid[0]["id"], "rows": rows, "cat": preset["category"], "ts": ts},
        )
    apply_flag_rules(g)
    return {"fired": preset["label"], "at": ts}
