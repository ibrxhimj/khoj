"""Graph tools the agent calls. Every tool runs Cypher (or a FalkorDB graph
algorithm) against the knowledge graph.

The agent never writes Cypher. It picks a tool and fills in typed arguments;
every query below is human-written and parameterised, and runs read-only.
Project ids are resolved against the graph before any traversal runs.

Every result carries a "status", one of:
  ANSWERED_WITH_PATH   the graph held the relationships; the paths are returned
  ANSWERED_FROM_MEMORY a finding from the buyer's memory graph, with its date
  NOT_IN_GRAPH         the entity exists but nothing connects it as asked
                       ("no known connection", which is not the same as "safe")
  UNKNOWN_ENTITY       the name or id does not resolve; no traversal ran

Keys starting with "_" are for the UI only and are stripped before the result
is shown to the model:
  _cypher : the queries that ran (shown in the agent-trace panel)
  _graph  : nodes and edges to draw and highlight in the graph view
"""
from __future__ import annotations

import re

from .db import kg, ro_query
from .risk import risk_paths_cypher, score_project

ANSWERED_WITH_PATH = "ANSWERED_WITH_PATH"
ANSWERED_FROM_MEMORY = "ANSWERED_FROM_MEMORY"
NOT_IN_GRAPH = "NOT_IN_GRAPH"
UNKNOWN_ENTITY = "UNKNOWN_ENTITY"

RESOLVE = "MATCH (p:Project {id: $id}) RETURN p.name AS name"


def resolve_project(project_id: str) -> str | None:
    """Entity resolution: check an id against the graph before traversing from it."""
    r = ro_query(kg(), RESOLVE, {"id": project_id})
    return r[0]["name"] if r else None


def _unknown(project_id: str) -> dict:
    return {"status": UNKNOWN_ENTITY,
            "message": f"No project with id '{project_id}' in the graph. No traversal was run. "
                       "Use search_projects to find the right id.",
            "_cypher": [RESOLVE]}


def _requires_project(fn):
    """Decorator: resolve project_id first; return UNKNOWN_ENTITY without running the tool's queries."""
    import functools

    @functools.wraps(fn)
    def wrapper(project_id: str, *args, **kwargs):
        if not isinstance(project_id, str) or not resolve_project(project_id):
            return _unknown(str(project_id))
        return fn(project_id, *args, **kwargs)
    return wrapper

# --------------------------------------------------------------------------- graph-view helpers


def _graph_from_paths(paths: list[dict], flagged: set[str] | None = None) -> dict:
    """Build {nodes, edges} for the UI from a list of {"nodes": [...], "rels": [...]} paths."""
    nodes, edges, seen = {}, [], set()
    for p in paths:
        ns = p["nodes"]
        for n in ns:
            nodes.setdefault(n["id"], {**n, "flag": n["id"] in (flagged or set())})
        for a, b, rel in zip(ns, ns[1:], p["rels"]):
            key = (a["id"], b["id"], rel)
            if key not in seen:
                seen.add(key)
                edges.append({"src": a["id"], "dst": b["id"], "type": rel})
    return {"nodes": list(nodes.values()), "edges": edges}


def _merge_graphs(*graphs: dict) -> dict:
    nodes, edges, seen = {}, [], set()
    for g in graphs:
        for n in g.get("nodes", []):
            if n["id"] in nodes:
                nodes[n["id"]]["flag"] = nodes[n["id"]].get("flag") or n.get("flag")
            else:
                nodes[n["id"]] = dict(n)
        for e in g.get("edges", []):
            key = (e["src"], e["dst"], e["type"])
            if key not in seen:
                seen.add(key)
                edges.append(dict(e))
            elif e.get("hot"):
                for x in edges:
                    if (x["src"], x["dst"], x["type"]) == key:
                        x["hot"] = True
    return {"nodes": list(nodes.values()), "edges": edges}


# --------------------------------------------------------------------------- 1. search_projects

SEARCH_BASE = """
MATCH (p:Project)<-[:DEVELOPS]-(c:Company)
OPTIONAL MATCH (g:Group)-[:CONTROLS]->(c)
WITH p, c, g
WHERE ($locality IS NULL OR toLower(p.locality) CONTAINS toLower($locality))
  AND ($budget IS NULL OR p.price_min_lakh <= $budget)
  AND ($bhk IS NULL OR $bhk IN p.bhk)
  AND ($ids IS NULL OR p.id IN $ids)
  AND ($include_stalled OR p.status <> 'stalled')
RETURN p.id AS id, p.name AS name, p.locality AS locality, p.status AS status, p.bhk AS bhk,
       p.price_min_lakh AS price_min_lakh, p.price_max_lakh AS price_max_lakh,
       p.launch_year AS launch_year, c.name AS company, g.name AS brand
ORDER BY p.price_min_lakh
LIMIT $limit
"""
FULLTEXT = "CALL db.idx.fulltext.queryNodes('Project', $q) YIELD node RETURN node.id AS id LIMIT 10"


def _fulltext_ids(text: str) -> tuple[list[str], list[str]]:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", text) if len(w) > 1]
    if not words:
        return [], []
    tried = []
    for q in (" ".join(f"{w}*" for w in words), " ".join(f"%{w}%" for w in words)):
        tried.append(q)
        ids = [r["id"] for r in ro_query(kg(), FULLTEXT, {"q": q})]
        if ids:
            return ids, tried
    return [], tried


def search_projects(name: str | None = None, locality: str | None = None, budget_max_lakh: float | None = None,
                    bhk: int | None = None, include_stalled: bool = False, limit: int = 12,
                    with_risk: bool = True) -> dict:
    cypher = []
    ids = None
    if name:
        ids, tried = _fulltext_ids(name)
        cypher += [FULLTEXT.replace("$q", repr(q)) for q in tried]
        if not ids:
            return {"status": UNKNOWN_ENTITY, "projects": [],
                    "message": f"No project name matches '{name}'. It is not in the graph; do not guess.",
                    "_cypher": cypher}
        include_stalled = True
    params = {"locality": locality, "budget": budget_max_lakh, "bhk": bhk, "ids": ids,
              "include_stalled": include_stalled, "limit": limit}
    rows = ro_query(kg(), SEARCH_BASE, params)
    cypher.append(SEARCH_BASE.strip())
    if with_risk:
        for r in rows:
            s = score_project(r["id"], top_k=1)
            r["risk_score"], r["risk_band"] = s["risk_score"], s["band"]
        cypher.append("-- risk scored per project with trace_risk's path query")
    return {"status": ANSWERED_WITH_PATH if rows else NOT_IN_GRAPH, "projects": rows, "count": len(rows),
            "_cypher": cypher}


# --------------------------------------------------------------------------- 2. project_profile

PROFILE = """
MATCH (p:Project {id: $id})<-[:DEVELOPS]-(c:Company)
OPTIONAL MATCH (g:Group)-[:CONTROLS]->(c)
OPTIONAL MATCH (d:Person)-[dir:DIRECTOR_OF]->(c)
OPTIONAL MATCH (p)-[:BUILT_ON]->(l:LandParcel)
OPTIONAL MATCH (con:Contractor)-[:BUILDS]->(p)
OPTIONAL MATCH (len:Lender)-[:FINANCES]->(p)
RETURN p {.*} AS project, c.name AS company, c.status AS company_status, c.incorporated AS incorporated,
       g.name AS brand, g.brand_since AS brand_since,
       collect(DISTINCT d.name) AS directors, l.name AS land, l.khata AS khata, l.acres AS acres,
       con.name AS contractor, len.name AS lender
"""
APPROVALS = """
MATCH (p:Project {id: $id})-[:HAS_APPROVAL]->(a:Approval)
RETURN a.type AS type, a.status AS status, a.year AS year, a.ref AS ref ORDER BY a.year
"""
COMPLAINTS = """
MATCH (p:Project {id: $id})<-[:AGAINST]-(c:Complaint)
RETURN c.status AS status, c.category AS category, count(c) AS n
"""


@_requires_project
def project_profile(project_id: str) -> dict:
    """What a buyer sees on the surface: brochure facts, RERA, approvals, complaints."""
    g = kg()
    rows = ro_query(g, PROFILE, {"id": project_id})
    if not rows:
        return {"error": f"No project with id {project_id}", "_cypher": [PROFILE.strip()]}
    r = rows[0]
    proj = {k: v for k, v in r.pop("project").items() if k not in {"kind", "created_at", "severity"}}
    complaints = ro_query(g, COMPLAINTS, {"id": project_id})
    return {
        "status": ANSWERED_WITH_PATH,
        "project": proj,
        **r,
        "approvals": ro_query(g, APPROVALS, {"id": project_id}),
        "complaints": {
            "open": sum(c["n"] for c in complaints if c["status"] == "open"),
            "resolved": sum(c["n"] for c in complaints if c["status"] == "resolved"),
            "by_category": complaints,
        },
        "_cypher": [PROFILE.strip(), APPROVALS.strip(), COMPLAINTS.strip()],
    }


# --------------------------------------------------------------------------- 3. trace_risk


@_requires_project
def trace_risk(project_id: str) -> dict:
    """Katz-style risk score plus the evidence paths behind it."""
    s = score_project(project_id, top_k=8)
    if "error" in s:
        return _unknown(project_id)
    flagged = {e["flag_id"] for e in s["top_evidence"]}
    graph = _graph_from_paths([e["best_path"] for e in s["top_evidence"] if e["best_path"]], flagged)
    if s["top_evidence"] and s["top_evidence"][0]["best_path"]:
        top = s["top_evidence"][0]["best_path"]
        hot = {(a["id"], b["id"]) for a, b in zip(top["nodes"], top["nodes"][1:])}
        for e in graph["edges"]:
            e["hot"] = (e["src"], e["dst"]) in hot or (e["dst"], e["src"]) in hot
    for e in s["top_evidence"]:
        e.pop("best_path", None)
    s["status"] = ANSWERED_WITH_PATH if s["n_flags"] else NOT_IN_GRAPH
    if not s["n_flags"]:
        s["note"] = ("No red flag is connected to this project within 4 hops. That means no known connection in "
                     "the graph, not a guarantee that the project is safe.")
    s["how_to_read"] = ("risk_score 0-100 = 100*(1-exp(-K)); K sums severity * 0.6^(hops-1) * edge weights "
                        "over every path to a red flag within 4 hops. share_pct = share of K from that flag; "
                        "n_paths = how many distinct paths reach it.")
    s["_cypher"] = [risk_paths_cypher().strip()]
    s["_graph"] = graph
    return s


# --------------------------------------------------------------------------- 4. developer_network (WCC)

WCC_SIMPLE = """
CALL algo.WCC({nodeLabels: ['Person', 'Company'], relationshipTypes: ['DIRECTOR_OF']})
YIELD node, componentId
RETURN node.id AS id, componentId AS component
"""
NETWORK_DETAIL = """
MATCH (c:Company) WHERE c.id IN $company_ids
OPTIONAL MATCH (g:Group)-[:CONTROLS]->(c)
OPTIONAL MATCH (c)-[:DEVELOPS]->(p:Project)
OPTIONAL MATCH (p)<-[:AGAINST]-(cp:Complaint {status: 'open'})
WITH c, g, p, count(cp) AS open_complaints
RETURN c.id AS company_id, c.name AS company, c.status AS company_status, g.name AS brand,
       collect(CASE WHEN p IS NULL THEN NULL ELSE {id: p.id, name: p.name, status: p.status,
               delay_months: p.delay_months, open_complaints: open_complaints} END) AS projects
"""
DIRECTOR_LINKS = """
MATCH (d:Person)-[r:DIRECTOR_OF]->(c:Company)
WHERE c.id IN $company_ids
RETURN d.id AS person_id, d.name AS person, c.id AS company_id, c.name AS company,
       r.since AS since, r.until AS until
"""
CASES = """
MATCH (k:CourtCase)-[:INVOLVES]->(c:Company) WHERE c.id IN $company_ids
RETURN c.name AS company, k.case_no AS case_no, k.court AS court, k.nature AS nature, k.status AS status
"""


@_requires_project
def developer_network(project_id: str) -> dict:
    """Find every company tied to this project's developer through shared directors.

    Uses FalkorDB's Weakly Connected Components algorithm on the Person-Company
    directorship graph: companies in the same component are one hidden group,
    whatever brand they trade under.
    """
    g = kg()
    dev = ro_query(g, "MATCH (p:Project {id: $id})<-[:DEVELOPS]-(c:Company) RETURN c.id AS id, c.name AS name",
                   {"id": project_id})
    if not dev:
        return {"error": f"No project with id {project_id}"}
    comp = {r["id"]: r["component"] for r in ro_query(g, WCC_SIMPLE)}
    target = comp.get(dev[0]["id"])
    members = [nid for nid, c in comp.items() if c == target]
    company_ids = [m for m in members if m.startswith("CMP")]
    detail = ro_query(g, NETWORK_DETAIL, {"company_ids": company_ids})
    links = ro_query(g, DIRECTOR_LINKS, {"company_ids": company_ids})
    cases = ro_query(g, CASES, {"company_ids": company_ids})

    # Directors who sit on 2+ companies in the group are the bridges.
    per_person: dict[str, list] = {}
    for l in links:
        per_person.setdefault(l["person"], []).append(
            f"{l['company']} ({l['since']}-{l['until'] or 'now'})")
    bridges = {p: cs for p, cs in per_person.items() if len(cs) >= 2}

    brands = sorted({d["brand"] or "(no brand)" for d in detail})
    failed = [d["company"] for d in detail if d["company_status"] != "active"]

    # graph view: directors <-> companies, plus troubled projects
    nodes, edges = {}, []
    for d in detail:
        nodes[d["company_id"]] = {"id": d["company_id"], "kind": "Company", "name": d["company"],
                                  "flag": d["company_status"] != "active"}
        for p in d["projects"]:
            if p and (p["status"] == "stalled" or (p["delay_months"] or 0) >= 12 or d["company_id"] == dev[0]["id"]):
                nodes[p["id"]] = {"id": p["id"], "kind": "Project", "name": p["name"],
                                  "flag": p["status"] == "stalled" or (p["delay_months"] or 0) >= 12}
                edges.append({"src": d["company_id"], "dst": p["id"], "type": "DEVELOPS"})
    for l in links:
        if l["person"] in bridges or l["company_id"] == dev[0]["id"]:
            nodes[l["person_id"]] = {"id": l["person_id"], "kind": "Person", "name": l["person"], "flag": False}
            edges.append({"src": l["person_id"], "dst": l["company_id"], "type": "DIRECTOR_OF"})

    linked = len(company_ids) > 1
    return {
        "status": ANSWERED_WITH_PATH if linked else NOT_IN_GRAPH,
        "developer": dev[0]["name"],
        "group_size": {"companies": len(company_ids), "people": len(members) - len(company_ids)},
        "brands_in_group": brands,
        "failed_companies": failed,
        "bridging_directors": bridges,
        "companies": detail,
        "court_cases": cases,
        "algorithm": "algo.WCC on (:Person)-[:DIRECTOR_OF]->(:Company)",
        "_cypher": [WCC_SIMPLE.strip(), NETWORK_DETAIL.strip(), DIRECTOR_LINKS.strip(), CASES.strip()],
        "_graph": {"nodes": list(nodes.values()), "edges": edges},
    }


# --------------------------------------------------------------------------- 5. key_people (betweenness)

BETWEENNESS = """
CALL algo.betweenness({nodeLabels: ['Person', 'Company'], relationshipTypes: ['DIRECTOR_OF', 'HAS_DIRECTOR']})
YIELD node, score
WITH node, score WHERE node.kind = 'Person' AND score > 0
RETURN node.id AS id, node.name AS name, score
ORDER BY score DESC
"""
PERSON_BOARDS = """
MATCH (d:Person)-[r:DIRECTOR_OF]->(c:Company) WHERE d.id IN $ids
OPTIONAL MATCH (g:Group)-[:CONTROLS]->(c)
RETURN d.id AS id, collect({company_id: c.id, company: c.name, status: c.status, brand: g.name,
                             since: r.since, until: r.until}) AS boards
"""


@_requires_project
def key_people(project_id: str) -> dict:
    """Rank the directors behind this project by betweenness centrality.

    A high score means the person is a bridge between otherwise separate
    companies, the classic signature of a nominee director or a promoter who
    moves between entities.
    """
    g = kg()
    dirs = ro_query(g, "MATCH (p:Project {id: $id})<-[:DEVELOPS]-(:Company)<-[:DIRECTOR_OF]-(d:Person) "
                       "RETURN d.id AS id, d.name AS name", {"id": project_id})
    if not dirs:
        return {"error": f"No directors found for {project_id}"}
    ranking = ro_query(g, BETWEENNESS)
    rank_of = {r["id"]: (i + 1, r["score"]) for i, r in enumerate(ranking)}
    ids = [d["id"] for d in dirs]
    boards = {r["id"]: r["boards"] for r in ro_query(g, PERSON_BOARDS, {"ids": ids})}
    people = []
    for d in dirs:
        rank, score = rank_of.get(d["id"], (None, 0.0))
        b = boards.get(d["id"], [])
        people.append({
            "name": d["name"],
            "betweenness": round(score, 1),
            "rank_among_all_directors": rank,
            "total_ranked_directors": len(ranking),
            "companies": len(b),
            "brands": sorted({x["brand"] or "(no brand)" for x in b}),
            "failed_companies": [x["company"] for x in b if x["status"] != "active"],
            "boards": b,
        })
    people.sort(key=lambda p: -p["betweenness"])
    nodes, edges = {}, []
    for d in dirs:
        nodes[d["id"]] = {"id": d["id"], "kind": "Person", "name": d["name"], "flag": False}
        for x in boards.get(d["id"], []):
            nodes[x["company_id"]] = {"id": x["company_id"], "kind": "Company", "name": x["company"],
                                      "flag": x["status"] != "active"}
            edges.append({"src": d["id"], "dst": x["company_id"], "type": "DIRECTOR_OF"})
    for p in people:
        for x in p["boards"]:
            x.pop("company_id", None)
    return {
        "status": ANSWERED_WITH_PATH if any(p["betweenness"] > 0 for p in people) else NOT_IN_GRAPH,
        "directors": people,
        "algorithm": "algo.betweenness on the Person-Company directorship network (both edge directions)",
        "_cypher": [BETWEENNESS.strip(), PERSON_BOARDS.strip()],
        "_graph": {"nodes": list(nodes.values()), "edges": edges},
    }


# --------------------------------------------------------------------------- 6. land_title_check

LAND = """
MATCH (p:Project {id: $id})-[b:BUILT_ON]->(l:LandParcel)
OPTIONAL MATCH (l)-[po:PREVIOUSLY_OWNED_BY]->(o)
OPTIONAL MATCH (k:CourtCase)-[:INVOLVES]->(x) WHERE x = l OR x = o
RETURN p.name AS project, l.id AS land_id, l.name AS land, l.khata AS khata, l.acres AS acres,
       o.id AS owner_id, o.name AS previous_owner, o.kind AS owner_kind, po.until AS owned_until,
       collect(DISTINCT CASE WHEN k IS NULL THEN NULL ELSE
           {id: k.id, case_no: k.case_no, court: k.court, nature: k.nature, status: k.status,
            filed: k.filed, parties: k.parties, involves: x.name} END) AS cases
"""


@_requires_project
def land_title_check(project_id: str) -> dict:
    g = kg()
    rows = ro_query(g, LAND, {"id": project_id})
    if not rows:
        return {"error": f"No land parcel found for {project_id}", "_cypher": [LAND.strip()]}
    r = rows[0]
    by_id: dict[str, dict] = {}
    for c in r["cases"]:
        if not c:
            continue
        if c["id"] in by_id:
            by_id[c["id"]]["involves"] += f", {c['involves']}"
        else:
            by_id[c["id"]] = dict(c)
    cases = list(by_id.values())
    pending_title = [c for c in cases if c["status"] == "pending" and c["nature"] == "title_dispute"]
    nodes = {
        project_id: {"id": project_id, "kind": "Project", "name": r["project"], "flag": False},
        r["land_id"]: {"id": r["land_id"], "kind": "LandParcel", "name": r["land"], "flag": r["khata"] == "B"},
    }
    edges = [{"src": project_id, "dst": r["land_id"], "type": "BUILT_ON"}]
    if r["owner_id"]:
        nodes[r["owner_id"]] = {"id": r["owner_id"], "kind": r["owner_kind"], "name": r["previous_owner"], "flag": False}
        edges.append({"src": r["land_id"], "dst": r["owner_id"], "type": "PREVIOUSLY_OWNED_BY"})
    for c in cases:
        nodes[c["id"]] = {"id": c["id"], "kind": "CourtCase", "name": c["case_no"], "flag": c["status"] == "pending"}
        if r["land"] in c["involves"]:
            edges.append({"src": c["id"], "dst": r["land_id"], "type": "INVOLVES"})
        if r["previous_owner"] and r["previous_owner"] in c["involves"]:
            edges.append({"src": c["id"], "dst": r["owner_id"], "type": "INVOLVES"})
    return {
        "status": ANSWERED_WITH_PATH if cases or r["khata"] == "B" else NOT_IN_GRAPH,
        **{k: v for k, v in r.items() if k not in {"cases", "land_id", "owner_id"}},
        "court_cases": cases,
        "verdict": ("PENDING TITLE DISPUTE - do not pay a booking amount until resolved"
                    if pending_title else ("B-khata - check loan eligibility" if r["khata"] == "B" else "No title issues found")),
        "_cypher": [LAND.strip()],
        "_graph": {"nodes": list(nodes.values()), "edges": edges},
    }


# --------------------------------------------------------------------------- 7. find_alternatives


def find_alternatives(locality: str, budget_max_lakh: float | None = None, bhk: int | None = None,
                      max_risk: float = 30, exclude_project_id: str | None = None, limit: int = 5) -> dict:
    known = ro_query(kg(), "MATCH (l:Locality) WHERE toLower(l.name) CONTAINS toLower($l) RETURN l.name AS name",
                     {"l": locality})
    if not known:
        return {"status": UNKNOWN_ENTITY, "message": f"No locality matching '{locality}' in the graph.",
                "_cypher": ["MATCH (l:Locality) WHERE toLower(l.name) CONTAINS toLower($l) RETURN l.name"]}
    res = search_projects(locality=locality, budget_max_lakh=budget_max_lakh, bhk=bhk, limit=40)
    cands = [p for p in res["projects"] if p["id"] != exclude_project_id and p["status"] != "delivered"]
    safe = sorted([p for p in cands if p["risk_score"] <= max_risk], key=lambda p: (p["risk_score"], p["price_min_lakh"]))
    return {
        "status": ANSWERED_WITH_PATH if safe else NOT_IN_GRAPH,
        "criteria": {"locality": locality, "budget_max_lakh": budget_max_lakh, "bhk": bhk, "max_risk": max_risk},
        "alternatives": safe[:limit],
        "considered": len(cands),
        "rejected_as_risky": [{"name": p["name"], "risk_score": p["risk_score"]} for p in cands if p["risk_score"] > max_risk],
        "_cypher": res["_cypher"],
    }


# --------------------------------------------------------------------------- 8. contractor_record

CONTRACTOR_RECORD = """
MATCH (p:Project {id: $id})<-[:BUILDS]-(con:Contractor)
OPTIONAL MATCH (con)-[:BUILDS]->(o:Project) WHERE o <> p
OPTIONAL MATCH (k:CourtCase)-[:INVOLVES]->(con)
WITH con, collect(DISTINCT o {.id, .name, .status, .delay_months, .locality}) AS others,
     collect(DISTINCT k {.case_no, .court, .nature, .status, .filed}) AS cases
RETURN con.id AS contractor_id, con.name AS contractor, others, cases
"""


@_requires_project
def contractor_record(project_id: str) -> dict:
    """The track record of the contractor building this project: its other projects and court cases."""
    rows = ro_query(kg(), CONTRACTOR_RECORD, {"id": project_id})
    if not rows:
        return {"status": NOT_IN_GRAPH, "message": "No contractor recorded for this project.",
                "_cypher": [CONTRACTOR_RECORD.strip()]}
    r = rows[0]
    others = [o for o in r["others"] if o]
    cases = [c for c in r["cases"] if c]
    late = [o for o in others if o["status"] == "stalled" or (o["delay_months"] or 0) >= 12]
    pending = [c for c in cases if c["status"] == "pending"]
    nodes = {r["contractor_id"]: {"id": r["contractor_id"], "kind": "Contractor", "name": r["contractor"],
                                  "flag": bool(pending)},
             project_id: {"id": project_id, "kind": "Project", "name": resolve_project(project_id), "flag": False}}
    edges = [{"src": r["contractor_id"], "dst": project_id, "type": "BUILDS"}]
    for o in late:
        nodes[o["id"]] = {"id": o["id"], "kind": "Project", "name": o["name"], "flag": True}
        edges.append({"src": r["contractor_id"], "dst": o["id"], "type": "BUILDS"})
    return {
        "status": ANSWERED_WITH_PATH if (late or pending) else NOT_IN_GRAPH,
        "contractor": r["contractor"],
        "other_projects": len(others),
        "late_or_stalled": [{"name": o["name"], "status": o["status"], "delay_months": o["delay_months"],
                             "locality": o["locality"]} for o in late],
        "on_time_share_pct": round(100 * (1 - len(late) / len(others)), 0) if others else None,
        "court_cases": cases,
        "_cypher": [CONTRACTOR_RECORD.strip()],
        "_graph": {"nodes": list(nodes.values()), "edges": edges},
    }


SCHEMA_TEXT = """Knowledge graph schema (every node has id, kind, name):
(:Group {brand_since, status})-[:CONTROLS]->(:Company {status: active|insolvency|struck_off, incorporated})
(:Person {role: director|landowner})-[:DIRECTOR_OF {since, until}]->(:Company)  (reverse edge: (:Company)-[:HAS_DIRECTOR]->(:Person))
(:Company)-[:DEVELOPS]->(:Project {status: launch|under_construction|delivered|stalled, launch_year,
    promised_possession, delay_months, bhk (list), price_min_lakh, price_max_lakh, units, locality, rera_id})
(:Project)-[:LOCATED_IN]->(:Locality {zone, authority})
(:Project)-[:BUILT_ON]->(:LandParcel {survey_no, village, khata: A|B, acres})-[:PREVIOUSLY_OWNED_BY {until}]->(:Person|:Company)
(:Project)-[:HAS_APPROVAL]->(:Approval {type, status, year})
(:Complaint {forum, category, status: open|resolved, filed})-[:AGAINST]->(:Project)
(:CourtCase {case_no, court, nature, status: pending|disposed, filed})-[:INVOLVES]->(:LandParcel|:Company|:Person|:Contractor)
(:Contractor)-[:BUILDS]->(:Project)
(:Lender)-[:FINANCES]->(:Project)
Red-flag nodes also carry the :RedFlag label with severity (0-1) and reason."""
