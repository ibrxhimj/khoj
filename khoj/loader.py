"""Build the Khoj knowledge graph in FalkorDB from data/seed.json.

Drops and recreates the graph, creates exact and full-text indexes, loads nodes
and edges in batches with UNWIND, adds reverse directorship edges (for the
betweenness algorithm), then applies the red-flag rules.
"""
import json
import time
from collections import defaultdict
from pathlib import Path

from .config import settings
from .db import get_db, query
from .flags import apply_flag_rules

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "seed.json"
BATCH = 500
FULLTEXT = ["Project", "Company", "Person", "Contractor", "Group", "Locality"]


def chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def load(seed_path: Path = SEED, verbose: bool = True, graph_name: str | None = None) -> dict:
    """Build the knowledge graph. graph_name defaults to settings.kg_graph (the benchmark and tests use their own)."""
    data = json.loads(seed_path.read_text())
    db = get_db()
    name = graph_name or settings.kg_graph
    if name in db.list_graphs():
        db.select_graph(name).delete()
    g = db.select_graph(name)
    t0 = time.time()

    by_kind = defaultdict(list)
    for n in data["nodes"]:
        props = {k: v for k, v in n.items() if v is not None}
        by_kind[n["kind"]].append(props)

    # Create one node first so the graph exists, then indexes, then the rest.
    for kind, items in by_kind.items():
        for batch in chunks(items, BATCH):
            g.query(f"UNWIND $rows AS r CREATE (n:{kind}) SET n = r", {"rows": batch})
        g.query(f"CREATE INDEX FOR (n:{kind}) ON (n.id)")
    for kind in FULLTEXT:
        g.query(f"CALL db.idx.fulltext.createNodeIndex('{kind}', 'name')")
    g.query("CREATE INDEX FOR (n:Project) ON (n.locality, n.status)")

    kind_of = {n["id"]: n["kind"] for n in data["nodes"]}
    grouped = defaultdict(list)
    for e in data["edges"]:
        props = {k: v for k, v in e.items() if k not in {"type", "src", "dst"} and v is not None}
        grouped[(e["type"], kind_of[e["src"]], kind_of[e["dst"]])].append(
            {"src": e["src"], "dst": e["dst"], "props": props})
    for (rel, sk, dk), items in grouped.items():
        for batch in chunks(items, BATCH):
            g.query(
                f"UNWIND $rows AS r "
                f"MATCH (a:{sk} {{id: r.src}}), (b:{dk} {{id: r.dst}}) "
                f"CREATE (a)-[e:{rel}]->(b) SET e = r.props",
                {"rows": batch},
            )

    # Reverse edge for every directorship. FalkorDB's betweenness algorithm
    # follows edge direction; with both directions present it measures the
    # undirected Person-Company network.
    g.query("MATCH (p:Person)-[r:DIRECTOR_OF]->(c:Company) CREATE (c)-[:HAS_DIRECTOR {since: r.since}]->(p)")

    flags = apply_flag_rules(g)
    n_nodes = query(g, "MATCH (n) RETURN count(n) AS n")[0]["n"]
    n_edges = query(g, "MATCH ()-[e]->() RETURN count(e) AS n")[0]["n"]
    if verbose:
        print(f"Loaded graph '{name}': {n_nodes} nodes, {n_edges} edges in {time.time() - t0:.1f}s")
        print("Red flags:", ", ".join(f"{k} {v}" for k, v in flags.items()))
    return {"nodes": n_nodes, "edges": n_edges, "red_flags": flags}


def ensure_loaded() -> bool:
    """Load the seed data if the knowledge graph does not exist yet. Returns True if it loaded."""
    if settings.kg_graph in get_db().list_graphs():
        return False
    load(verbose=False)
    return True


