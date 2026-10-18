"""Minimal reproductions of FalkorDB behaviours we hit while building Khoj.

    python scripts/falkordb_repros.py

Runs against FALKORDB_HOST/PORT in a scratch graph and prints what each case
returns. See docs/falkordb-notes.md for context and the workarounds we used.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from khoj.db import get_db  # noqa: E402

G = "khoj_repro"


def run(g, label, q):
    try:
        print(f"{label}\n  -> {g.query(q).result_set}")
    except Exception as e:  # noqa: BLE001
        print(f"{label}\n  -> ERROR: {e}")


def main():
    db = get_db()
    g = db.select_graph(G)
    g.query("MATCH (n) DETACH DELETE n")
    # Two companies joined only through one person: Person -> Company edges, one direction.
    g.query("CREATE (:Company {n:'A'})<-[:DIRECTOR_OF]-(:Person {n:'p'})-[:DIRECTOR_OF]->(:Company {n:'B'})")

    run(g, "0. variable-length paths never reuse an edge: A-p-A is impossible with one A-p edge",
        "MATCH p = (a {n:'A'})-[*1..4]-(x) RETURN length(p), [n IN nodes(p) | n.n] ORDER BY length(p)")
    run(g, "1. algo.betweenness on one-directional edges: the bridging person scores 0",
        "CALL algo.betweenness({nodeLabels:['Person','Company'], relationshipTypes:['DIRECTOR_OF']}) "
        "YIELD node, score RETURN node.n, score")
    g.query("MATCH (p:Person)-[r:DIRECTOR_OF]->(c:Company) CREATE (c)-[:HAS_DIRECTOR]->(p)")
    run(g, "   workaround: add reverse edges and include both types",
        "CALL algo.betweenness({nodeLabels:['Person','Company'], relationshipTypes:['DIRECTOR_OF','HAS_DIRECTOR']}) "
        "YIELD node, score RETURN node.n, score")
    run(g, "2. undirected shortestPath",
        "MATCH (a {n:'A'}), (b {n:'B'}) RETURN length(shortestPath((a)-[*]-(b)))")
    run(g, "3. shortestPath in a MATCH clause",
        "MATCH p = shortestPath((a {n:'A'})-[*]->(b {n:'B'})) RETURN length(p)")
    g.delete()


if __name__ == "__main__":
    main()
