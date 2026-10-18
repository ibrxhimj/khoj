import math
import time

import pytest

from khoj.db import query, ro_query
from khoj.risk import MAX_PATHS, decay_hops, score_all, score_project


# --------------------------------------------------------------------------- data


def test_graph_loaded_with_expected_shape(knowledge_graph):
    n = query(knowledge_graph, "MATCH (n) RETURN count(n) AS n")[0]["n"]
    e = query(knowledge_graph, "MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
    assert (n, e) == (847, 1256)


def test_every_node_has_id_kind_name(knowledge_graph):
    bad = query(knowledge_graph, "MATCH (n) WHERE n.id IS NULL OR n.kind IS NULL OR n.name IS NULL RETURN count(n) AS n")
    assert bad[0]["n"] == 0


def test_ids_are_unique(knowledge_graph):
    dup = query(knowledge_graph, "MATCH (n) WITH n.id AS id, count(*) AS c WHERE c > 1 RETURN count(id) AS n")
    assert dup[0]["n"] == 0


def test_red_flags_have_severity_and_reason(knowledge_graph):
    rows = query(knowledge_graph, "MATCH (f:RedFlag) RETURN f.severity AS s, f.reason AS r")
    assert rows and all(0 < r["s"] <= 1 and r["r"] for r in rows)


def test_reverse_directorship_edges_match(knowledge_graph):
    a = query(knowledge_graph, "MATCH ()-[r:DIRECTOR_OF]->() RETURN count(r) AS n")[0]["n"]
    b = query(knowledge_graph, "MATCH ()-[r:HAS_DIRECTOR]->() RETURN count(r) AS n")[0]["n"]
    assert a == b > 0


def test_fulltext_index_handles_typos(knowledge_graph):
    rows = ro_query(knowledge_graph, "CALL db.idx.fulltext.queryNodes('Project', '%skyvsta% %altra%') YIELD node "
                                     "RETURN node.id AS id")
    assert [r["id"] for r in rows] == ["PRJ-ALTURA"]


def test_read_only_queries_cannot_write(knowledge_graph):
    with pytest.raises(Exception):
        knowledge_graph.ro_query("CREATE (:Hack {x: 1})")


# --------------------------------------------------------------------------- planted stories


@pytest.mark.parametrize("pid,band,lo,hi", [
    ("PRJ-ALTURA", "HIGH", 80, 90),
    ("PRJ-SERENE", "HIGH", 60, 80),
    ("PRJ-ORCHID", "MEDIUM", 35, 50),
    ("PRJ-TRANQUIL", "MEDIUM", 40, 55),
])
def test_planted_stories_score_as_designed(pid, band, lo, hi):
    r = score_project(pid)
    assert r["band"] == band and lo <= r["risk_score"] <= hi


@pytest.mark.parametrize("pid", ["PRJ-081", "PRJ-082", "PRJ-083"])
def test_clean_comparators_score_zero(pid):
    assert score_project(pid)["risk_score"] == 0.0


def test_hero_project_looks_clean_on_the_surface():
    r = score_project("PRJ-ALTURA")
    assert r["direct_flags"] == []
    assert r["top_evidence"][0]["min_hops"] >= 3


def test_background_is_calibrated():
    scores = sorted(r["risk_score"] for r in score_all())
    median = scores[len(scores) // 2]
    assert 15 <= median <= 35


# --------------------------------------------------------------------------- model properties


def test_score_is_bounded_and_matches_katz_transform():
    for r in score_all():
        assert 0 <= r["risk_score"] <= 100
        assert abs(r["risk_score"] - round(100 * (1 - math.exp(-r["katz_K"])), 1)) <= 0.15


def test_evidence_shares_sum_to_100():
    r = score_project("PRJ-ALTURA", top_k=50)
    assert abs(sum(e["share_pct"] for e in r["top_evidence"]) - 100) < 1


def test_two_shared_directors_count_more_than_one():
    full = score_project("PRJ-ALTURA")
    strongest_only = score_project("PRJ-ALTURA", aggregate="max")
    assert full["katz_K"] > strongest_only["katz_K"]


def test_title_chain_hops_do_not_decay():
    assert decay_hops(["BUILT_ON", "PREVIOUSLY_OWNED_BY", "INVOLVES"]) == 1
    assert decay_hops(["DEVELOPS", "DIRECTOR_OF", "DIRECTOR_OF"]) == 2
    assert decay_hops(["AGAINST"]) == 0


def test_structural_rules_prevent_hub_flooding():
    with_rules = sorted(r["risk_score"] for r in score_all())
    from khoj.db import kg
    ids = [r["id"] for r in ro_query(kg(), "MATCH (p:Project) RETURN p.id AS id")]
    without = sorted(score_project(i, rules=False, top_k=1)["risk_score"] for i in ids)
    assert without[len(without) // 2] > with_rules[len(with_rules) // 2] + 20


def test_shared_locality_and_lender_carry_no_risk(fresh_kg):
    before = score_project("PRJ-081")["risk_score"]
    fresh_kg.query("""
        MATCH (p:Project {id: 'PRJ-081'})-[:LOCATED_IN]->(l)
        CREATE (s:Project:RedFlag {id: 'T-STALLED', kind: 'Project', name: 'Test Stalled', status: 'stalled',
                                   severity: 0.8, reason: 'Project stalled', created_at: 0})-[:LOCATED_IN]->(l)
        CREATE (len:Lender {id: 'T-LEN', kind: 'Lender', name: 'Test Lender'})
        CREATE (len)-[:FINANCES]->(s), (len)-[:FINANCES]->(p)""")
    assert score_project("PRJ-081")["risk_score"] == before


def test_new_case_on_contractor_raises_score(fresh_kg):
    from khoj import events
    before = score_project("PRJ-081")["risk_score"]
    events.fire("contractor_insolvency")
    after = score_project("PRJ-081")
    assert after["risk_score"] > before + 20
    assert any("insolvency" in e["reason"] for e in after["top_evidence"])


def test_truncation_sentinel_is_reported():
    r = score_project("PRJ-ALTURA")
    assert r["truncated"] is False and r["n_paths"] < MAX_PATHS


def test_risk_trace_is_fast():
    t = time.perf_counter()
    for pid in ["PRJ-ALTURA", "PRJ-SERENE", "PRJ-ORCHID", "PRJ-TRANQUIL"]:
        score_project(pid)
    assert (time.perf_counter() - t) / 4 < 0.25


def test_cypher_matches_python_reference(knowledge_graph):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from benchmark import python_reference
    from khoj.config import settings
    ids = [r["id"] for r in ro_query(knowledge_graph, "MATCH (p:Project) RETURN p.id AS id")]
    ref = python_reference(knowledge_graph, ids, settings.risk_beta, settings.risk_max_hops)
    for pid in ids:
        assert abs(round(ref[pid], 3) - score_project(pid, top_k=1)["katz_K"]) <= 0.0015, pid
