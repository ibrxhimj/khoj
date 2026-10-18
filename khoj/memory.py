"""Buyer memory: one isolated FalkorDB graph per buyer.

FalkorDB holds many graphs in one instance, so each buyer's memory lives in its
own graph (khoj_buyer_<id>). Nothing about one buyer can leak into another's
queries, and deleting a buyer is deleting a graph.

Memory model
------------
(:Buyer {id, name, created_at})
  -[:HAS_PREF]->(:Preference {key, value, since, active, until})   semantic
       (:Preference)-[:SUPERSEDES]->(:Preference)                  ...with history
  -[:CONCERNED_ABOUT]->(:Concern {text, since})                    semantic
  -[:FOLLOWS_RULE]->(:Rule {text, since})                          procedural
  -[:HAD_SESSION]->(:Session {id, started_at})                     episodic
       (:Session)-[:NEXT]->(:Session)
       (:Session)-[:HAS_EVENT]->(:Event {ts, kind, text, project_ids})
  -[:SHORTLISTED {since, risk_at_add, last_risk, last_checked}]->(:ProjectRef {project_id, name})
  -[:REJECTED {at, reason}]->(:ProjectRef)                         decisions

ProjectRef.project_id points into the shared knowledge graph. At the start of
every session the agent re-scores each shortlisted project against the
knowledge graph and reports any risk that appeared since it last looked.
"""
from __future__ import annotations

import time
import uuid

from .db import buyer_graph, kg, query, ro_query
from .risk import score_project


def now() -> int:
    return int(time.time())


# --------------------------------------------------------------------------- setup


def ensure_buyer(buyer_id: str, name: str | None = None) -> None:
    g = buyer_graph(buyer_id)
    g.query("MERGE (b:Buyer {id: $id}) ON CREATE SET b.name = $name, b.created_at = $ts",
            {"id": buyer_id, "name": name or buyer_id, "ts": now()})


def start_session(buyer_id: str, name: str | None = None) -> str:
    ensure_buyer(buyer_id, name)
    g = buyer_graph(buyer_id)
    sid = f"S-{uuid.uuid4().hex[:8]}"
    g.query(
        """
        MATCH (b:Buyer {id: $bid})
        OPTIONAL MATCH (b)-[:HAD_SESSION]->(prev:Session) WHERE NOT (prev)-[:NEXT]->()
        CREATE (b)-[:HAD_SESSION]->(s:Session {id: $sid, started_at: $ts})
        WITH prev, s WHERE prev IS NOT NULL
        CREATE (prev)-[:NEXT]->(s)
        """,
        {"bid": buyer_id, "sid": sid, "ts": now()},
    )
    return sid


def log_event(buyer_id: str, session_id: str, kind: str, text: str, project_ids: list[str] | None = None) -> None:
    buyer_graph(buyer_id).query(
        "MATCH (s:Session {id: $sid}) CREATE (s)-[:HAS_EVENT]->(:Event {ts: $ts, kind: $kind, text: $text, project_ids: $pids})",
        {"sid": session_id, "ts": now(), "kind": kind, "text": text[:2000], "pids": project_ids or []},
    )


# --------------------------------------------------------------------------- writes (agent tools)


def remember(buyer_id: str, type: str, value: str, key: str | None = None) -> dict:
    """Store a preference (with history), a concern, or a rule."""
    g = buyer_graph(buyer_id)
    ensure_buyer(buyer_id)
    ts = now()
    if type == "preference":
        if not key:
            return {"error": "A preference needs a key, e.g. budget_max_lakh, localities, bhk, possession_by."}
        old = query(g, "MATCH (:Buyer {id: $bid})-[:HAS_PREF]->(p:Preference {key: $key, active: true}) "
                       "RETURN p.value AS value", {"bid": buyer_id, "key": key})
        if old and old[0]["value"] == value:
            return {"stored": False, "note": f"Already remembered {key} = {value}"}
        g.query(
            """
            MATCH (b:Buyer {id: $bid})
            OPTIONAL MATCH (b)-[:HAS_PREF]->(old:Preference {key: $key, active: true})
            CREATE (b)-[:HAS_PREF]->(new:Preference {key: $key, value: $value, since: $ts, active: true})
            WITH old, new WHERE old IS NOT NULL
            SET old.active = false, old.until = $ts
            CREATE (new)-[:SUPERSEDES]->(old)
            """,
            {"bid": buyer_id, "key": key, "value": value, "ts": ts},
        )
        return {"stored": True, "type": "preference", "key": key, "value": value,
                "replaced": old[0]["value"] if old else None}
    if type == "concern":
        g.query("MATCH (b:Buyer {id: $bid}) MERGE (b)-[:CONCERNED_ABOUT]->(c:Concern {text: $v}) ON CREATE SET c.since = $ts",
                {"bid": buyer_id, "v": value, "ts": ts})
        return {"stored": True, "type": "concern", "value": value}
    if type == "rule":
        g.query("MATCH (b:Buyer {id: $bid}) MERGE (b)-[:FOLLOWS_RULE]->(r:Rule {text: $v}) ON CREATE SET r.since = $ts",
                {"bid": buyer_id, "v": value, "ts": ts})
        return {"stored": True, "type": "rule", "value": value}
    return {"error": f"Unknown memory type '{type}'. Use preference, concern or rule."}


def shortlist(buyer_id: str, action: str, project_id: str, reason: str | None = None) -> dict:
    g = buyer_graph(buyer_id)
    ensure_buyer(buyer_id)
    proj = ro_query(kg(), "MATCH (p:Project {id: $id}) RETURN p.name AS name", {"id": project_id})
    if not proj:
        return {"error": f"No project with id {project_id}"}
    name, ts = proj[0]["name"], now()
    if action == "add":
        risk = score_project(project_id, top_k=1)["risk_score"]
        g.query(
            """
            MATCH (b:Buyer {id: $bid})
            MERGE (w:ProjectRef {project_id: $pid}) ON CREATE SET w.name = $name
            MERGE (b)-[s:SHORTLISTED]->(w)
            ON CREATE SET s.since = $ts, s.risk_at_add = $risk
            SET s.last_risk = $risk, s.last_checked = $ts
            WITH b, w
            OPTIONAL MATCH (b)-[r:REJECTED]->(w) DELETE r
            """,
            {"bid": buyer_id, "pid": project_id, "name": name, "ts": ts, "risk": risk},
        )
        return {"shortlisted": name, "risk_score_now": risk}
    if action == "remove":
        g.query(
            """
            MATCH (b:Buyer {id: $bid})
            MERGE (w:ProjectRef {project_id: $pid}) ON CREATE SET w.name = $name
            WITH b, w
            OPTIONAL MATCH (b)-[s:SHORTLISTED]->(w) DELETE s
            WITH b, w
            MERGE (b)-[r:REJECTED]->(w) SET r.at = $ts, r.reason = $reason
            """,
            {"bid": buyer_id, "pid": project_id, "name": name, "ts": ts, "reason": reason or ""},
        )
        return {"rejected": name, "reason": reason}
    return {"error": "action must be add or remove"}


# --------------------------------------------------------------------------- reads


def recall(buyer_id: str, events: int = 12) -> dict:
    """Everything the agent knows about this buyer, assembled from their graph."""
    g = buyer_graph(buyer_id)
    ensure_buyer(buyer_id)
    p = {"bid": buyer_id}
    buyer = query(g, "MATCH (b:Buyer {id: $bid}) RETURN b.name AS name, b.created_at AS since", p)[0]
    prefs = query(g, "MATCH (:Buyer {id: $bid})-[:HAS_PREF]->(x:Preference {active: true}) "
                     "OPTIONAL MATCH (x)-[:SUPERSEDES*]->(old:Preference) "
                     "RETURN x.key AS key, x.value AS value, x.since AS since, collect(old.value) AS previously", p)
    concerns = query(g, "MATCH (:Buyer {id: $bid})-[:CONCERNED_ABOUT]->(c) RETURN c.text AS text ORDER BY c.since", p)
    rules = query(g, "MATCH (:Buyer {id: $bid})-[:FOLLOWS_RULE]->(r) RETURN r.text AS text ORDER BY r.since", p)
    short = query(g, "MATCH (:Buyer {id: $bid})-[s:SHORTLISTED]->(w) RETURN w.project_id AS project_id, w.name AS name, "
                     "s.since AS since, s.risk_at_add AS risk_at_add, s.last_risk AS last_risk ORDER BY s.since", p)
    rejected = query(g, "MATCH (:Buyer {id: $bid})-[r:REJECTED]->(w) RETURN w.project_id AS project_id, w.name AS name, "
                        "r.reason AS reason", p)
    sessions = query(g, "MATCH (:Buyer {id: $bid})-[:HAD_SESSION]->(s) RETURN count(s) AS n", p)[0]["n"]
    recent = query(g, "MATCH (:Buyer {id: $bid})-[:HAD_SESSION]->(s)-[:HAS_EVENT]->(e) "
                      "RETURN s.id AS session, e.ts AS ts, e.kind AS kind, e.text AS text "
                      "ORDER BY e.ts DESC, id(e) DESC LIMIT $n", {**p, "n": events})
    return {
        "buyer": buyer["name"], "sessions_so_far": sessions,
        "preferences": prefs, "concerns": [c["text"] for c in concerns], "rules": [r["text"] for r in rules],
        "shortlist": short, "rejected": rejected, "recent_events": list(reversed(recent)),
    }


def watchlist_check(buyer_id: str) -> dict:
    """Re-score every shortlisted project; report risk that appeared since the last check."""
    g = buyer_graph(buyer_id)
    ensure_buyer(buyer_id)
    items = query(g, "MATCH (:Buyer {id: $bid})-[s:SHORTLISTED]->(w) RETURN w.project_id AS pid, w.name AS name, "
                     "s.last_risk AS last_risk, s.last_checked AS last_checked", {"bid": buyer_id})
    alerts, ts = [], now()
    for it in items:
        s = score_project(it["pid"], top_k=20)
        new_flags = [
            {"reason": e["reason"], "flag": e["flag_name"], "path": e["path_text"], "share_pct": e["share_pct"]}
            for e in s["top_evidence"] if e.get("created_at") and e["created_at"] > (it["last_checked"] or 0)
        ]
        delta = round(s["risk_score"] - (it["last_risk"] or 0), 1)
        if new_flags or abs(delta) >= 5:
            alerts.append({"project_id": it["pid"], "project": it["name"], "risk_before": it["last_risk"],
                           "risk_now": s["risk_score"], "band_now": s["band"], "change": delta, "new_flags": new_flags})
        g.query("MATCH (:Buyer {id: $bid})-[s:SHORTLISTED]->(:ProjectRef {project_id: $pid}) "
                "SET s.last_risk = $risk, s.last_checked = $ts",
                {"bid": buyer_id, "pid": it["pid"], "risk": s["risk_score"], "ts": ts})
    return {"checked": len(items), "alerts": alerts}


# --------------------------------------------------------------------------- agent handoff (shared state)
#
# (:Session)-[:OPENED]->(:Case {id, question, brief, status, opened_at, closed_at})
#   (:Case)-[:HAS_FINDING]->(:Finding {project_id, project, verdict, risk_score, summary, evidence, ts})
#   (:Finding)-[:ABOUT]->(:ProjectRef)
#
# The Concierge opens a Case, the Investigator writes Findings into it, and the
# Concierge reads the Case back from the graph. Findings stay in the buyer's
# graph, so the next session remembers what was already investigated.


def open_case(buyer_id: str, session_id: str, question: str, brief: str) -> str:
    cid = f"C-{uuid.uuid4().hex[:8]}"
    buyer_graph(buyer_id).query(
        """
        MATCH (s:Session {id: $sid})
        CREATE (s)-[:OPENED]->(:Case {id: $cid, question: $q, brief: $brief, status: 'open', opened_at: $ts})
        """,
        {"sid": session_id, "cid": cid, "q": question, "brief": brief, "ts": now()},
    )
    return cid


def record_finding(buyer_id: str, case_id: str, project_id: str, verdict: str, summary: str,
                   risk_score: float | None = None, evidence: list[str] | None = None) -> dict:
    proj = ro_query(kg(), "MATCH (p:Project {id: $id}) RETURN p.name AS name", {"id": project_id})
    name = proj[0]["name"] if proj else project_id
    buyer_graph(buyer_id).query(
        """
        MATCH (c:Case {id: $cid})
        MERGE (w:ProjectRef {project_id: $pid}) ON CREATE SET w.name = $name
        CREATE (c)-[:HAS_FINDING]->(f:Finding {project_id: $pid, project: $name, verdict: $verdict,
                summary: $summary, risk_score: $risk, evidence: $evidence, ts: $ts})
        CREATE (f)-[:ABOUT]->(w)
        """,
        {"cid": case_id, "pid": project_id, "name": name, "verdict": verdict, "summary": summary,
         "risk": risk_score if risk_score is not None else -1, "evidence": evidence or [], "ts": now()},
    )
    return {"recorded": True, "project": name, "verdict": verdict}


def record_rejected_draft(buyer_id: str, case_id: str, project_id: str, verdict: str, reason: str,
                          detail: str) -> None:
    """Keep drafts the verifier rejected, for audit."""
    buyer_graph(buyer_id).query(
        "MATCH (c:Case {id: $cid}) CREATE (c)-[:REJECTED_DRAFT]->(:Draft {project_id: $pid, verdict: $v, "
        "reason: $r, detail: $d, ts: $ts})",
        {"cid": case_id, "pid": project_id, "v": verdict, "r": reason, "d": detail[:500], "ts": now()})


def close_case(buyer_id: str, case_id: str) -> None:
    buyer_graph(buyer_id).query("MATCH (c:Case {id: $cid}) SET c.status = 'closed', c.closed_at = $ts",
                                {"cid": case_id, "ts": now()})


def read_case(buyer_id: str, case_id: str) -> dict:
    g = buyer_graph(buyer_id)
    case = query(g, "MATCH (c:Case {id: $cid}) RETURN c.question AS question, c.status AS status", {"cid": case_id})
    findings = query(g, "MATCH (:Case {id: $cid})-[:HAS_FINDING]->(f) RETURN f.project_id AS project_id, "
                        "f.project AS project, f.verdict AS verdict, f.risk_score AS risk_score, "
                        "f.summary AS summary, f.evidence AS evidence ORDER BY f.ts", {"cid": case_id})
    drafts = query(g, "MATCH (:Case {id: $cid})-[:REJECTED_DRAFT]->(d) RETURN d.project_id AS project_id, "
                      "d.verdict AS verdict, d.reason AS reason, d.detail AS detail ORDER BY d.ts", {"cid": case_id})
    return {"case_id": case_id, **(case[0] if case else {}), "findings": findings, "rejected_drafts": drafts}


def past_findings(buyer_id: str, limit: int = 8) -> list[dict]:
    return query(buyer_graph(buyer_id),
                 "MATCH (:Buyer {id: $bid})-[:HAD_SESSION]->(:Session)-[:OPENED]->(:Case)-[:HAS_FINDING]->(f) "
                 "RETURN f.project AS project, f.verdict AS verdict, f.risk_score AS risk_score, f.summary AS summary "
                 "ORDER BY f.ts DESC LIMIT $n", {"bid": buyer_id, "n": limit})


def findings_for(buyer_id: str, project_id: str, limit: int = 3) -> list[dict]:
    """Earlier findings about one project, newest first, with when they were made."""
    return query(buyer_graph(buyer_id),
                 "MATCH (c:Case)-[:HAS_FINDING]->(f:Finding {project_id: $pid}) "
                 "RETURN f.verdict AS verdict, f.risk_score AS risk_score, f.summary AS summary, "
                 "f.evidence AS evidence, f.ts AS ts, c.question AS question ORDER BY f.ts DESC LIMIT $n",
                 {"pid": project_id, "n": limit})


def memory_graph(buyer_id: str) -> dict:
    """Nodes and edges of the buyer's memory graph, for the UI."""
    g = buyer_graph(buyer_id)
    ensure_buyer(buyer_id)
    rows = query(g, """
        MATCH (a)-[r]->(b) WHERE NOT b:Event AND NOT a:Event
        RETURN id(a) AS a, labels(a)[0] AS al,
               coalesce(a.name, a.key + ': ' + a.value, a.text, a.question, a.project + ': ' + a.verdict, a.id) AS an,
               coalesce(a.active, true) AS aact,
               type(r) AS t,
               id(b) AS b, labels(b)[0] AS bl,
               coalesce(b.name, b.key + ': ' + b.value, b.text, b.question, b.project + ': ' + b.verdict, b.id) AS bn,
               coalesce(b.active, true) AS bact
        LIMIT 200""")
    nodes, edges = {}, []
    for r in rows:
        for i, l, n, act in [(r["a"], r["al"], r["an"], r["aact"]), (r["b"], r["bl"], r["bn"], r["bact"])]:
            nodes[f"m{i}"] = {"id": f"m{i}", "kind": l, "name": str(n), "flag": False, "inactive": not act}
        edges.append({"src": f"m{r['a']}", "dst": f"m{r['b']}", "type": r["t"]})
    return {"nodes": list(nodes.values()), "edges": edges}


def reset_buyer(buyer_id: str) -> None:
    from .db import get_db
    from .config import settings
    name = f"{settings.buyer_graph_prefix}{buyer_id}"
    if name in get_db().list_graphs():
        buyer_graph(buyer_id).delete()
