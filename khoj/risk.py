"""Graph risk model: Katz-style risk propagation from red flags.

For a project p, every red flag f reachable within H hops contributes along
every path between them, attenuated by a factor beta per extra hop:

    K(p) = sum over paths P from p to a red flag f:  severity(f) * beta^(len(P) - 1)

Each relationship type also carries a weight w(r) (a contractor link is weaker
evidence than a directorship), so the full contribution of a path is

    severity(f) * beta^d(P) * product of w(r) over its relationships

where d(P) is the path length minus one, not counting title-chain hops
(PREVIOUSLY_OWNED_BY): a buyer inherits the vendor's title, so a defect in the
previous owner's title transfers without distance decay.

This is Katz centrality restricted to red-flag targets and bounded paths.
Counting paths, not just distance, is deliberate: two shared directors are
stronger evidence than one, and a contractor behind on three projects is
worse than one behind on one.

K is unbounded, so we map it to a 0-100 score with a saturating transform:

    score(p) = 100 * (1 - exp(-K(p)))

A red flag on the project itself counts with full severity (path length 0).

Only relationships that carry legal, financial or execution risk are followed.
LOCATED_IN and FINANCES are excluded: sharing a locality or a lender with a
troubled project says nothing about this one.

Two structural rules stop hubs from flooding the score:
  * a path may not pass *through* another project. A troubled project is
    evidence in itself (it is a red flag); its own complaints are not counted
    again for its neighbours.
  * a path may use at most one PREVIOUSLY_OWNED_BY edge, so a land aggregator
    that once owned many plots does not connect unrelated projects.
"""
from __future__ import annotations

import math
from collections import defaultdict

from .config import settings
from .db import kg, ro_query

# Relationship types the risk walk may follow, with their evidence weight.
REL_WEIGHTS = {
    "DEVELOPS": 1.0,
    "DIRECTOR_OF": 1.0,
    "CONTROLS": 0.7,             # same brand, different company
    "BUILT_ON": 1.0,
    "PREVIOUSLY_OWNED_BY": 0.9,
    "INVOLVES": 1.0,
    "AGAINST": 1.0,
    "HAS_APPROVAL": 1.0,
    "BUILDS": 0.75,              # shared contractor
}
RISK_RELS = list(REL_WEIGHTS)

# Upper bound on paths per project. The query asks for one more row than this;
# if that sentinel row comes back, the result is marked truncated (the score is
# then a lower bound) instead of being silently presented as complete.
MAX_PATHS = 2000

STRUCTURAL_RULES = """
  AND none(n IN nodes(path)[1..-1] WHERE n.kind = 'Project')
  AND size([r IN relationships(path) WHERE type(r) = 'PREVIOUSLY_OWNED_BY']) <= 1"""

# Returns every bounded path from the project to a red flag.
RISK_PATHS_CYPHER = """
MATCH path = (p:Project {{id: $id}})-[:{rels}*1..{hops}]-(f:RedFlag)
WHERE f <> p{rules}
RETURN f.id AS flag_id, f.kind AS flag_kind, f.name AS flag_name, f.reason AS reason,
       f.severity AS severity, f.created_at AS created_at, length(path) AS hops,
       [n IN nodes(path) | {{id: n.id, kind: n.kind, name: n.name}}] AS nodes,
       [r IN relationships(path) | type(r)] AS rels
LIMIT {limit}
"""

SELF_FLAG_CYPHER = """
MATCH (p:Project {id: $id})
RETURN p.name AS name, p:RedFlag AS flagged, p.severity AS severity, p.reason AS reason
"""


def decay_hops(rels: list[str]) -> int:
    """Hops that attenuate risk: every hop after the first, except title-chain hops.
    A buyer inherits the vendor's title, so a defect in the previous owner's title
    is a defect in the plot's title: PREVIOUSLY_OWNED_BY carries its edge weight
    but no distance decay."""
    return max(0, len(rels) - 1 - sum(r == "PREVIOUSLY_OWNED_BY" for r in rels))


def band(score: float) -> str:
    if score >= 60:
        return "HIGH"
    if score >= 30:
        return "MEDIUM"
    return "LOW"


def risk_paths_cypher(hops: int | None = None, rels: list[str] | None = None, rules: bool = True) -> str:
    return RISK_PATHS_CYPHER.format(rels="|".join(rels or RISK_RELS), hops=hops or settings.risk_max_hops,
                                    rules=STRUCTURAL_RULES if rules else "", limit=MAX_PATHS + 1)


def score_project(project_id: str, graph=None, hops: int | None = None, beta: float | None = None,
                  top_k: int = 8, rels: list[str] | None = None, rules: bool = True,
                  aggregate: str = "sum") -> dict:
    """Compute the risk score of one project and the evidence behind it.

    The keyword options exist for the ablation benchmark (scripts/benchmark.py):
      rels      restrict which relationship types the walk may follow
      rules     False drops the two structural rules (no pass-through projects,
                at most one ownership edge)
      aggregate "sum" (Katz, the product default) or "max" (each red flag counts
                once, through its single strongest path)
    """
    graph = graph or kg()
    beta = settings.risk_beta if beta is None else beta
    me = ro_query(graph, SELF_FLAG_CYPHER, {"id": project_id})
    if not me:
        return {"error": f"No project with id {project_id}"}
    me = me[0]

    paths = ro_query(graph, risk_paths_cypher(hops, rels, rules), {"id": project_id})
    truncated = len(paths) > MAX_PATHS
    paths = paths[:MAX_PATHS]

    K = 0.0
    per_flag: dict[str, dict] = {}
    by_kind: dict[str, float] = defaultdict(float)
    if me["flagged"]:
        K += me["severity"]
        per_flag[project_id] = {
            "flag_id": project_id, "flag_kind": "Project", "flag_name": me["name"], "reason": me["reason"],
            "severity": me["severity"], "created_at": None,
            "contribution": me["severity"], "n_paths": 1, "min_hops": 0,
            "best_path": {"nodes": [{"id": project_id, "kind": "Project", "name": me["name"]}], "rels": []},
        }
        by_kind["Project"] += me["severity"]

    path_names: set[str] = {me["name"]}
    for row in paths:
        w = math.prod(REL_WEIGHTS.get(r, 1.0) for r in row["rels"])
        c = row["severity"] * beta ** decay_hops(row["rels"]) * w
        path_names.update(n["name"] for n in row["nodes"] if n.get("name"))
        prev = per_flag.get(row["flag_id"])
        if aggregate == "max" and prev is not None:
            if c > prev["contribution"]:
                K += c - prev["contribution"]
                by_kind[row["flag_kind"]] += c - prev["contribution"]
                prev["contribution"] = c
            prev["n_paths"] += 1
            continue
        K += c
        by_kind[row["flag_kind"]] += c
        f = per_flag.get(row["flag_id"])
        if f is None:
            f = per_flag[row["flag_id"]] = {
                "flag_id": row["flag_id"], "flag_kind": row["flag_kind"], "flag_name": row["flag_name"],
                "reason": row["reason"], "severity": row["severity"], "created_at": row["created_at"],
                "contribution": 0.0,
                "n_paths": 0, "min_hops": row["hops"], "best_path": None,
            }
        f["contribution"] += c
        f["n_paths"] += 1
        if f["best_path"] is None or row["hops"] < f["min_hops"]:
            f["min_hops"] = row["hops"]
            f["best_path"] = {"nodes": row["nodes"], "rels": row["rels"]}

    score = round(100 * (1 - math.exp(-K)), 1)
    evidence = sorted(per_flag.values(), key=lambda x: -x["contribution"])
    for e in evidence:
        e["contribution"] = round(e["contribution"], 3)
        e["share_pct"] = round(100 * e["contribution"] / K, 1) if K else 0.0
        e["path_text"] = path_text(e["best_path"])
    return {
        "project_id": project_id,
        "project": me["name"],
        "truncated": truncated,
        "direct_flags": [e["reason"] for e in evidence if e["min_hops"] <= 1],
        "_path_names": sorted(path_names),
        "risk_score": score,
        "band": band(score),
        "katz_K": round(K, 3),
        "n_flags": len(per_flag),
        "n_paths": len(paths) + (1 if me["flagged"] else 0),
        "by_flag_kind": {k: round(v, 3) for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1])},
        "top_evidence": evidence[:top_k],
        "model": {"beta": beta, "max_hops": hops or settings.risk_max_hops},
    }


def path_text(path: dict | None) -> str:
    """Readable form of a path, e.g. 'Skyvista Altura <-DEVELOPS- Altura Habitat ...'."""
    if not path:
        return ""
    names = [n["name"] for n in path["nodes"]]
    out = names[0]
    for rel, name in zip(path["rels"], names[1:]):
        out += f" -[{rel}]- {name}"
    return out


def score_all(graph=None) -> list[dict]:
    graph = graph or kg()
    ids = [r["id"] for r in ro_query(graph, "MATCH (p:Project) RETURN p.id AS id")]
    return sorted((score_project(i, graph, top_k=3) for i in ids), key=lambda r: -r["risk_score"])
