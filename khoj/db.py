"""FalkorDB connection helpers.

Khoj uses FalkorDB's multi-graph support:
  * one shared knowledge graph   -> settings.kg_graph
  * one isolated graph per buyer -> settings.buyer_graph_prefix + buyer_id
"""
from functools import lru_cache

from falkordb import FalkorDB

from .config import settings


@lru_cache(maxsize=1)
def get_db() -> FalkorDB:
    return FalkorDB(
        host=settings.falkordb_host,
        port=settings.falkordb_port,
        username=settings.falkordb_username,
        password=settings.falkordb_password,
        ssl=settings.falkordb_ssl,
    )


def kg():
    """The shared Bangalore property knowledge graph."""
    return get_db().select_graph(settings.kg_graph)


def buyer_graph(buyer_id: str):
    """An isolated memory graph for a single buyer."""
    safe = "".join(ch for ch in buyer_id.lower() if ch.isalnum() or ch in "-_")
    return get_db().select_graph(f"{settings.buyer_graph_prefix}{safe}")


def list_buyer_graphs() -> list[str]:
    prefix = settings.buyer_graph_prefix
    return [g[len(prefix):] for g in get_db().list_graphs() if g.startswith(prefix)]


def rows(result) -> list[dict]:
    """Turn a FalkorDB QueryResult into a list of dicts keyed by column name."""
    header = [h[1] if isinstance(h, (list, tuple)) else h for h in result.header]
    header = [h.decode() if isinstance(h, bytes) else h for h in header]
    return [dict(zip(header, r)) for r in result.result_set]


def query(graph, cypher: str, params: dict | None = None) -> list[dict]:
    return rows(graph.query(cypher, params or {}))


def ro_query(graph, cypher: str, params: dict | None = None) -> list[dict]:
    """Read-only query (FalkorDB rejects writes sent through GRAPH.RO_QUERY)."""
    return rows(graph.ro_query(cypher, params or {}))
