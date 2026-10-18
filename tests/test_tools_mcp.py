import inspect

import pytest

from khoj import mcp_client, tools


def test_unknown_project_runs_no_traversal():
    for fn in [tools.project_profile, tools.trace_risk, tools.developer_network, tools.key_people,
               tools.land_title_check, tools.contractor_record]:
        r = fn("PRJ-DOES-NOT-EXIST")
        assert r["status"] == tools.UNKNOWN_ENTITY
        assert r["_cypher"] == [tools.RESOLVE]


def test_unknown_name_is_reported_not_guessed():
    r = tools.search_projects(name="Sunshine Towers Koramangala")
    assert r["status"] == tools.UNKNOWN_ENTITY and r["projects"] == []


def test_typo_tolerant_search():
    r = tools.search_projects(name="skyvista altra")
    assert [p["id"] for p in r["projects"]] == ["PRJ-ALTURA"]


def test_search_filters():
    r = tools.search_projects(locality="sarjapur", budget_max_lakh=120, bhk=3)
    assert r["projects"] and all("Sarjapur" in p["locality"] and p["price_min_lakh"] <= 120 and 3 in p["bhk"]
                                 for p in r["projects"])


def test_clean_project_is_no_known_connection_not_safe():
    r = tools.trace_risk("PRJ-081")
    assert r["status"] == tools.NOT_IN_GRAPH
    assert "not a guarantee" in r["note"]


def test_trace_risk_returns_paths_and_highlights_top_path():
    r = tools.trace_risk("PRJ-ALTURA")
    assert r["status"] == tools.ANSWERED_WITH_PATH
    assert any(e.get("hot") for e in r["_graph"]["edges"])
    assert "Northstar Habitats Pvt Ltd" in r["top_evidence"][0]["path_text"]


def test_developer_network_reveals_hidden_group():
    r = tools.developer_network("PRJ-ALTURA")
    assert set(r["brands_in_group"]) == {"Skyvista Builders", "Northstar Homes"}
    assert {"Rohan Kulkarni", "Meera Shetty"} <= set(r["bridging_directors"])


def test_key_people_ranks_the_bridge_director_first():
    r = tools.key_people("PRJ-TRANQUIL")
    top = r["directors"][0]
    assert top["name"] == "Suresh Narayan" and top["rank_among_all_directors"] == 1
    assert len(top["failed_companies"]) == 2


def test_land_title_check_finds_pending_suit():
    r = tools.land_title_check("PRJ-SERENE")
    assert r["status"] == tools.ANSWERED_WITH_PATH and r["verdict"].startswith("PENDING TITLE DISPUTE")
    assert len(r["court_cases"]) == 1


def test_contractor_record_finds_late_projects():
    r = tools.contractor_record("PRJ-ORCHID")
    assert r["contractor"] == "Monsoon Infra Contractors"
    assert len(r["late_or_stalled"]) == 3 and r["court_cases"]


def test_find_alternatives_excludes_risky_and_unknown_locality():
    r = tools.find_alternatives("Sarjapur Road", 170, 3, exclude_project_id="PRJ-ALTURA")
    assert r["alternatives"] and all(p["risk_score"] <= 30 for p in r["alternatives"])
    assert tools.find_alternatives("Atlantis")["status"] == tools.UNKNOWN_ENTITY


# --------------------------------------------------------------------------- MCP


def test_mcp_catalogue_is_fixed_and_has_no_free_cypher():
    schemas = mcp_client.graph_tool_schemas()
    names = [s["name"] for s in schemas]
    assert names == ["search_projects", "project_profile", "trace_risk", "developer_network", "key_people",
                     "land_title_check", "contractor_record", "find_alternatives"]
    for s in schemas:
        assert not {"query", "cypher"} & set(s["input_schema"].get("properties", {}))


def test_mcp_transport_is_used():
    assert mcp_client.MCP_AVAILABLE and mcp_client.transport().startswith("MCP")


@pytest.mark.parametrize("name,args,status", [
    ("trace_risk", {"project_id": "PRJ-ALTURA"}, tools.ANSWERED_WITH_PATH),
    ("trace_risk", {"project_id": "PRJ-081"}, tools.NOT_IN_GRAPH),
    ("trace_risk", {"project_id": "NOPE"}, tools.UNKNOWN_ENTITY),
    ("search_projects", {"name": "hoysala orchid"}, tools.ANSWERED_WITH_PATH),
])
def test_mcp_call_returns_structured_status(name, args, status):
    assert mcp_client.call_graph_tool(name, args)["status"] == status


def test_mcp_result_keeps_graph_for_ui():
    r = mcp_client.call_graph_tool("trace_risk", {"project_id": "PRJ-ALTURA"})
    assert r["_graph"]["nodes"] and r["_cypher"]


def test_no_tool_function_accepts_raw_cypher():
    for name in ["search_projects", "project_profile", "trace_risk", "developer_network", "key_people",
                 "land_title_check", "contractor_record", "find_alternatives"]:
        params = inspect.signature(getattr(tools, name)).parameters
        assert "query" not in params and "cypher" not in params
