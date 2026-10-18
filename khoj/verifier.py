"""Agents propose, code disposes.

Before a finding from the Investigator is written to the buyer's graph, this
module checks it against the graph deterministically:

1. The project resolves (otherwise UNKNOWN_ENTITY).
2. The verdict follows from the computed evidence, by fixed rules:
     AVOID    risk > 60, or a pending title suit on the plot or its previous owner
     SAFE     risk < 30, no red flag within one hop, result not truncated
     CAUTION  everything else
3. Every evidence chain names at least one entity, and every entity it names
   was returned by a graph tool for this project (risk paths, profile,
   developer network, boards, land chain, contractor record).
4. The risk score is the computed one; whatever number the model wrote is
   replaced.

A rejected finding is not written. The agent gets the reason and the computed
facts and must try again; the rejected draft is kept in the case for audit.
"""
from __future__ import annotations

from functools import lru_cache

from . import tools
from .db import kg, ro_query
from .risk import score_project

VERDICTS = {"SAFE", "CAUTION", "AVOID"}


@lru_cache(maxsize=1)
def _entity_names() -> frozenset[str]:
    rows = ro_query(kg(), """
        MATCH (n) WHERE n.kind IN ['Project', 'Company', 'Person', 'Contractor', 'Group', 'LandParcel', 'CourtCase']
        RETURN n.name AS name, n.case_no AS case_no, n.survey_no AS survey_no""")
    names = set()
    for r in rows:
        for v in (r["name"], r["case_no"]):
            if v and len(v) >= 5:
                names.add(v)
    return frozenset(names)


def refresh_names() -> None:
    _entity_names.cache_clear()


def expected_verdict(score: dict, title_suit: bool) -> tuple[str, str]:
    s = score["risk_score"]
    if s > 60:
        return "AVOID", f"computed risk {s} is above 60"
    if title_suit:
        return "AVOID", "there is a pending title suit on the plot or its previous owner"
    if s < 30 and not score["direct_flags"] and not score["truncated"]:
        return "SAFE", f"computed risk {s} is below 30 with no red flag within one hop"
    why = [f"computed risk {s} is between 30 and 60" if s >= 30 else f"computed risk {s}"]
    if score["direct_flags"]:
        why.append("there is a red flag within one hop: " + "; ".join(score["direct_flags"][:3]))
    if score["truncated"]:
        why.append("the path search was truncated, so the project cannot be called safe")
    return "CAUTION", ", ".join(why)


def grounded_names(project_id: str) -> set[str]:
    """Every entity name a graph tool returns for this project."""
    names: set[str] = set(score_project(project_id, top_k=1)["_path_names"])
    prof = tools.project_profile(project_id)
    names.update(x for x in [prof.get("company"), prof.get("brand"), prof.get("land"), prof.get("contractor"),
                             prof.get("lender"), (prof.get("project") or {}).get("name")] if x)
    names.update(prof.get("directors") or [])
    net = tools.developer_network(project_id)
    for c in net.get("companies", []):
        names.update(x for x in [c["company"], c["brand"]] if x)
        names.update(p["name"] for p in c["projects"] if p)
    names.update(net.get("bridging_directors", {}).keys())
    names.update(c["case_no"] for c in net.get("court_cases", []))
    kp = tools.key_people(project_id)
    for d in kp.get("directors", []):
        names.add(d["name"])
        names.update(b["company"] for b in d["boards"])
    land = tools.land_title_check(project_id)
    names.update(x for x in [land.get("land"), land.get("previous_owner")] if x)
    names.update(c["case_no"] for c in land.get("court_cases", []))
    con = tools.contractor_record(project_id)
    names.update(x for x in [con.get("contractor")] if x)
    names.update(o["name"] for o in con.get("late_or_stalled", []))
    names.update(c["case_no"] for c in con.get("court_cases", []))
    return {n for n in names if n}


def verify_finding(project_id: str, verdict: str, summary: str, risk_score: float | None = None,
                   evidence: list[str] | None = None) -> dict:
    """Returns {"ok": True, "finding": {...}} or {"ok": False, "reason": ..., ...}."""
    if not tools.resolve_project(project_id):
        return {"ok": False, "reason": "UNKNOWN_ENTITY", "detail": f"No project with id {project_id}."}
    verdict = (verdict or "").upper()
    score = score_project(project_id, top_k=1)
    land = tools.land_title_check(project_id)
    title_suit = any(c["status"] == "pending" and c["nature"] == "title_dispute" for c in land.get("court_cases", []))
    expected, why = expected_verdict(score, title_suit)
    computed = {"risk_score": score["risk_score"], "band": score["band"], "direct_flags": score["direct_flags"],
                "pending_title_suit": title_suit, "truncated": score["truncated"], "expected_verdict": expected}

    if verdict not in VERDICTS:
        return {"ok": False, "reason": "INVALID_VERDICT", "detail": "Verdict must be SAFE, CAUTION or AVOID.",
                "computed": computed}
    if verdict != expected:
        return {"ok": False, "reason": "VERDICT_INCONSISTENT",
                "detail": f"Verdict {verdict} does not follow from the graph: expected {expected} because {why}.",
                "computed": computed}

    evidence = [e for e in (evidence or []) if e and e.strip()]
    if verdict != "SAFE" and not evidence:
        return {"ok": False, "reason": "EVIDENCE_MISSING",
                "detail": "A CAUTION or AVOID finding needs at least one evidence chain from the graph.",
                "computed": computed}
    allowed = grounded_names(project_id)
    known = _entity_names()
    for chain in evidence:
        mentioned = {n for n in known if n in chain}
        stray = sorted(n for n in mentioned if n not in allowed)
        if stray:
            return {"ok": False, "reason": "EVIDENCE_NOT_ON_PATH",
                    "detail": f"The evidence names {', '.join(stray)}, which no graph tool returned for this project.",
                    "computed": computed}
        if not (mentioned & allowed):
            return {"ok": False, "reason": "EVIDENCE_NOT_GROUNDED",
                    "detail": f"The evidence chain '{chain[:80]}' names no entity from the graph.",
                    "computed": computed}

    corrections = []
    if risk_score is not None and abs(float(risk_score) - score["risk_score"]) > 1:
        corrections.append(f"risk score corrected from {risk_score} to the computed {score['risk_score']}")
    return {"ok": True, "finding": {"project_id": project_id, "verdict": verdict, "summary": summary,
                                    "risk_score": score["risk_score"], "evidence": evidence},
            "corrections": corrections, "computed": computed}
