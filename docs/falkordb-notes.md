# FalkorDB notes

Behaviours we hit while building Khoj, with minimal reproductions in [`scripts/falkordb_repros.py`](../scripts/falkordb_repros.py) (CI runs it on every push). These are limitations and semantics worth knowing, not crashes. Tested on FalkorDB 4.18.

## 1. `algo.betweenness` follows edge direction

A person who directs two companies is the only bridge between them, yet scores 0, because every `DIRECTOR_OF` edge points away from the person and no directed shortest path passes through them.

```
CALL algo.betweenness({nodeLabels:['Person','Company'], relationshipTypes:['DIRECTOR_OF']}) YIELD node, score
-> A 0.0, p 0.0, B 0.0
```

**Workaround used in Khoj:** add a reverse `HAS_DIRECTOR` edge for every directorship and pass both types. The bridging person then scores 2.0. In the demo graph this ranks Suresh Narayan first of 34 directors. A `direction: 'both'` option would avoid duplicating edges.

## 2. Undirected `shortestPath` is not supported

```
MATCH (a {n:'A'}), (b {n:'B'}) RETURN length(shortestPath((a)-[*]-(b)))
-> FalkorDB does not currently support undirected shortestPath traversals
```

Investigation graphs are naturally undirected, so Khoj builds explanations from bounded variable-length paths instead (see the risk query in the README).

## 3. `shortestPath` only in `WITH` or `RETURN`

```
MATCH p = shortestPath((a)-[*]->(b)) RETURN length(p)
-> FalkorDB currently only supports shortestPaths in WITH or RETURN clauses
```

## 4. Variable-length paths are relationship-unique

A path may revisit a node but never reuses an edge. Khoj's risk score counts paths, so this semantics matters. We verified it independently: the benchmark recomputes every score by enumerating relationship-unique walks in pure Python, with **0 mismatches** across all 91 demo projects and 200 held-out ones.

## What worked well

- Multi-graph: one instance holds the market graph plus an isolated memory graph per buyer.
- `GRAPH.RO_QUERY` gives the agents a database-enforced read-only boundary.
- Full-text indexes with `%term%` fuzzy matching resolve misspelled project names.
- Speed: a full 4-hop risk trace takes about 3 ms (p50) on a 3,700-node graph.
