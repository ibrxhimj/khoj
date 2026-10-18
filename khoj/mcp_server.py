"""Khoj's graph tools as an MCP server.

The Investigator agent discovers and calls these tools over MCP (in-process,
through the MCP client's in-memory transport). Any other MCP client can use the
same tools by launching this module over stdio:

    python -m khoj.mcp_server

Every tool is a human-written, parameterised, read-only Cypher query or a
FalkorDB graph algorithm. The model chooses a tool and fills in arguments; it
never writes Cypher.
"""
from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import tools

server = MCPServer(
    "khoj-graph",
    instructions="Due-diligence tools over a FalkorDB knowledge graph of Bangalore residential projects. "
                 "Resolve a project id with search_projects first. Every result has a status: "
                 "ANSWERED_WITH_PATH, NOT_IN_GRAPH (no known connection, not proof of safety) or UNKNOWN_ENTITY.",
)


@server.tool()
def search_projects(name: str | None = None, locality: str | None = None, budget_max_lakh: float | None = None,
                    bhk: int | None = None, include_stalled: bool = False) -> dict[str, Any]:
    """Find projects by name (typos are fine) and/or filters. Returns ids, basics and each project's
    graph risk score. budget_max_lakh is the max starting price in INR lakh (1 crore = 100)."""
    return tools.search_projects(name=name, locality=locality, budget_max_lakh=budget_max_lakh, bhk=bhk,
                                 include_stalled=include_stalled)


@server.tool()
def project_profile(project_id: str) -> dict[str, Any]:
    """The surface view a buyer sees: brochure facts, developer company, directors, RERA and approvals,
    land khata, contractor and complaint counts."""
    return tools.project_profile(project_id)


@server.tool()
def trace_risk(project_id: str) -> dict[str, Any]:
    """Graph risk score (0-100) and the evidence paths behind it: every red flag (stalled projects,
    insolvent companies, court cases, complaints, B-khata land) within 4 hops, weighted by distance,
    edge type and number of paths."""
    return tools.trace_risk(project_id)


@server.tool()
def developer_network(project_id: str) -> dict[str, Any]:
    """The hidden group behind a project's developer: every company linked through shared directors
    (FalkorDB algo.WCC), with their brands, failures, stalled projects and court cases."""
    return tools.developer_network(project_id)


@server.tool()
def key_people(project_id: str) -> dict[str, Any]:
    """The project's directors ranked by betweenness centrality (FalkorDB algo.betweenness) across the
    whole director-company network, with every board they sit on."""
    return tools.key_people(project_id)


@server.tool()
def land_title_check(project_id: str) -> dict[str, Any]:
    """Land parcel, khata type, previous owner, and any court case on the land or its previous owner."""
    return tools.land_title_check(project_id)


@server.tool()
def contractor_record(project_id: str) -> dict[str, Any]:
    """Track record of the contractor building this project: its other projects that are late or
    stalled, and any court cases against it."""
    return tools.contractor_record(project_id)


@server.tool()
def find_alternatives(locality: str, budget_max_lakh: float | None = None, bhk: int | None = None,
                      max_risk: float = 30, exclude_project_id: str | None = None) -> dict[str, Any]:
    """Lower-risk projects matching the buyer's criteria, ranked by graph risk then price."""
    return tools.find_alternatives(locality=locality, budget_max_lakh=budget_max_lakh, bhk=bhk,
                                   max_risk=max_risk, exclude_project_id=exclude_project_id)


if __name__ == "__main__":
    server.run()
