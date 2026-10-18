import time

import pytest

from khoj import agent, events, memory, tools, verifier
from khoj.agent import KhojSession


# --------------------------------------------------------------------------- memory


def test_buyer_graphs_are_isolated(buyer):
    a, b = buyer("asha"), buyer("ravi")
    memory.remember(a, "preference", "150", "budget_max_lakh")
    assert memory.recall(b)["preferences"] == []
    assert memory.recall(a)["preferences"][0]["value"] == "150"


def test_preference_update_keeps_history(buyer):
    b = buyer()
    memory.remember(b, "preference", "150", "budget_max_lakh")
    r = memory.remember(b, "preference", "170", "budget_max_lakh")
    assert r["replaced"] == "150"
    prefs = memory.recall(b)["preferences"]
    assert len(prefs) == 1 and prefs[0]["value"] == "170" and prefs[0]["previously"] == ["150"]


def test_rules_concerns_and_rejections_persist_across_sessions(buyer):
    b = buyer()
    memory.start_session(b)
    memory.remember(b, "rule", "Always check land title")
    memory.remember(b, "concern", "Delays")
    memory.shortlist(b, "remove", "PRJ-ALTURA", "Directors ran Northstar")
    memory.start_session(b)
    r = memory.recall(b)
    assert r["sessions_so_far"] == 2 and r["rules"] == ["Always check land title"] and r["concerns"] == ["Delays"]
    assert r["rejected"][0]["reason"] == "Directors ran Northstar"


def test_unknown_project_cannot_be_shortlisted(buyer):
    assert "error" in memory.shortlist(buyer(), "add", "PRJ-NOPE")


def test_watchlist_catches_new_risk_with_its_path(buyer, fresh_kg):
    b = buyer()
    memory.start_session(b)
    memory.shortlist(b, "add", "PRJ-081")
    assert memory.watchlist_check(b)["alerts"] == []
    time.sleep(1.1)
    events.fire("contractor_insolvency")
    alerts = memory.watchlist_check(b)["alerts"]
    assert alerts and alerts[0]["risk_before"] == 0.0 and alerts[0]["risk_now"] > 20
    assert "Sterling EPC" in alerts[0]["new_flags"][0]["path"]
    assert memory.watchlist_check(b)["alerts"] == []  # reported once


def test_case_handoff_round_trip(buyer):
    b = buyer()
    sid = memory.start_session(b)
    cid = memory.open_case(b, sid, "Is Altura safe?", "Rules: check title")
    memory.record_finding(b, cid, "PRJ-ALTURA", "AVOID", "Directors ran Northstar", 84.6, ["a -> b"])
    memory.close_case(b, cid)
    case = memory.read_case(b, cid)
    assert case["status"] == "closed" and case["findings"][0]["verdict"] == "AVOID"
    assert memory.findings_for(b, "PRJ-ALTURA")[0]["risk_score"] == 84.6


# --------------------------------------------------------------------------- verifier


def test_verifier_rejects_wrong_verdict():
    r = verifier.verify_finding("PRJ-ALTURA", "SAFE", "Looks fine", 10, [])
    assert not r["ok"] and r["reason"] == "VERDICT_INCONSISTENT" and r["computed"]["expected_verdict"] == "AVOID"


def test_verifier_overwrites_model_score():
    r = verifier.verify_finding("PRJ-ALTURA", "AVOID", "x", 50,
                                ["Skyvista Altura -> Altura Habitat Pvt Ltd -> Meera Shetty -> Northstar Habitats Pvt Ltd"])
    assert r["ok"] and r["finding"]["risk_score"] == 84.6 and r["corrections"]


def test_verifier_rejects_entities_not_on_any_path():
    r = verifier.verify_finding("PRJ-ALTURA", "AVOID", "x", 85, ["Skyvista Altura -> Suresh Narayan"])
    assert not r["ok"] and r["reason"] == "EVIDENCE_NOT_ON_PATH"


def test_verifier_rejects_ungrounded_and_missing_evidence():
    assert verifier.verify_finding("PRJ-ALTURA", "AVOID", "x", 85, ["bad history"])["reason"] == "EVIDENCE_NOT_GROUNDED"
    assert verifier.verify_finding("PRJ-ALTURA", "AVOID", "x", 85, [])["reason"] == "EVIDENCE_MISSING"


def test_verifier_title_suit_forces_avoid():
    assert verifier.verify_finding("PRJ-SERENE", "CAUTION", "x", None,
                                   ["Kaveri Crest Serene -> O.S. 4521/2023"])["reason"] == "VERDICT_INCONSISTENT"
    assert verifier.verify_finding("PRJ-SERENE", "AVOID", "x", None,
                                   ["Kaveri Crest Serene -> Sy. No. 112/3, Bagalur -> O.S. 4521/2023"])["ok"]


def test_verifier_accepts_safe_clean_project():
    assert verifier.verify_finding("PRJ-081", "SAFE", "No known connections", 0, [])["ok"]


# --------------------------------------------------------------------------- agent loop (scripted model)


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)

    def create(self, system, messages, tools_, max_tokens=2048, allow_tools=True):
        step = self.script.pop(0)
        content = [{"type": "text", "text": step["text"]}] if step.get("text") else []
        content += [{"type": "tool_use", "id": f"t{len(self.script)}{i}", "name": n, "input": a}
                    for i, (n, a) in enumerate(step.get("tools", []))]
        return {"content": content, "stop_reason": "tool_use" if step.get("tools") else "end_turn",
                "usage": {"input": 1, "output": 1}}


def session(buyer_id, concierge, investigator):
    s = KhojSession.__new__(KhojSession)
    s.buyer_id, s.buyer_name, s.mode = buyer_id, "Ibz", "team"
    s.session_id = memory.start_session(buyer_id, "Ibz")
    s.watch, s.history = memory.watchlist_check(buyer_id), []
    s.concierge, s.investigator = FakeLLM(concierge), FakeLLM(investigator)
    return s


GOOD_EVIDENCE = ["Skyvista Altura -> Altura Habitat Pvt Ltd -> Meera Shetty -> Northstar Habitats Pvt Ltd"]


def test_team_handoff_verifier_retry_and_memory(buyer):
    b = buyer()
    memory.remember(b, "rule", "Always check land title")
    s = session(b,
                [{"tools": [("ask_investigator", {"question": "Is Skyvista Altura safe?"})]},
                 {"text": "**Avoid for now: risk 85/100 (HIGH)**"}],
                [{"tools": [("search_projects", {"name": "skyvista altura"})]},
                 {"tools": [("trace_risk", {"project_id": "PRJ-ALTURA"})]},
                 {"tools": [("record_finding", {"project_id": "PRJ-ALTURA", "verdict": "SAFE", "summary": "fine"})]},
                 {"tools": [("record_finding", {"project_id": "PRJ-ALTURA", "verdict": "AVOID", "summary": "x",
                                                "risk_score": 70, "evidence": GOOD_EVIDENCE})]},
                 {"text": "report"}])
    r = s.ask("Is Skyvista Altura safe?")
    handoff = next(e for e in r.events if e["type"] == "handoff")
    assert "Always check land title" in handoff["brief"]
    closed = next(e for e in r.events if e["type"] == "case_closed")
    assert [f["verdict"] for f in closed["findings"]] == ["AVOID"]
    assert closed["findings"][0]["risk_score"] == 84.6
    assert [d["reason"] for d in closed["rejected_drafts"]] == ["VERDICT_INCONSISTENT"]
    assert not any(e["type"] == "grounding_warning" for e in r.events)
    assert "Skyvista Altura: AVOID" in agent.memory_brief(b)


def test_grounding_check_flags_unverified_names_and_scores(buyer):
    b = buyer()
    s = session(b, [{"text": "Skyvista Altura scores 12/100 and Hoysala Orchid Springs is great."}], [])
    r = s.ask("hi")
    warn = next(e for e in r.events if e["type"] == "grounding_warning")
    assert {"Skyvista Altura", "Hoysala Orchid Springs"} <= set(warn["names"]) and warn["scores"] == ["12"]
    assert "unchecked" in r.text


def test_recall_findings_reports_memory_with_date(buyer):
    b = buyer()
    s = session(b, [], [])
    assert s._recall_findings("PRJ-ALTURA")["status"] == tools.NOT_IN_GRAPH
    cid = memory.open_case(b, s.session_id, "q", "brief")
    memory.record_finding(b, cid, "PRJ-ALTURA", "AVOID", "x", 84.6, [])
    r = s._recall_findings("PRJ-ALTURA")
    assert r["status"] == tools.ANSWERED_FROM_MEMORY and r["findings"][0]["recorded_on"]


def test_grounding_check_reads_decimal_scores(buyer):
    """84.6/100 must be read as 84.6, not as 6/100."""
    s = session(buyer(), [{"tools": [("ask_investigator", {"question": "Is Skyvista Altura safe?"})]},
                          {"text": "**Avoid for now: risk 84.6/100 (HIGH)**"}],
                [{"tools": [("trace_risk", {"project_id": "PRJ-ALTURA"})]}, {"text": "report"}])
    out = s.ask("Is Skyvista Altura safe?")
    assert not any(e["type"] == "grounding_warning" for e in out.events)
    assert "unchecked" not in out.text
