"""How the agents reach the graph tools: over MCP.

The agent discovers the tool catalogue with MCP list_tools and executes every
graph tool with MCP call_tool, against the server in khoj/mcp_server.py, using
the MCP client's in-memory transport (no subprocess, so it also runs on
Streamlit Community Cloud).

If the MCP SDK is not installed, it falls back to calling the same functions
directly, so the app still runs.
"""
from __future__ import annotations

import asyncio
import json
import threading
from functools import lru_cache

from . import tools

try:
    from mcp.client import Client

    from .mcp_server import server as _server

    MCP_AVAILABLE = True
except Exception:  # noqa: BLE001
    MCP_AVAILABLE = False

_DIRECT = {
    "search_projects": tools.search_projects,
    "project_profile": tools.project_profile,
    "trace_risk": tools.trace_risk,
    "developer_network": tools.developer_network,
    "key_people": tools.key_people,
    "land_title_check": tools.land_title_check,
    "contractor_record": tools.contractor_record,
    "find_alternatives": tools.find_alternatives,
}


def _run(coro):
    """Run a coroutine from sync code, even if an event loop is already running in this thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict = {}

    def target():
        box["v"] = asyncio.run(coro)
    t = threading.Thread(target=target)
    t.start()
    t.join()
    return box["v"]


async def _list():
    async with Client(_server) as c:
        return (await c.list_tools()).tools


async def _call(name: str, args: dict):
    async with Client(_server) as c:
        return await c.call_tool(name, args)


@lru_cache(maxsize=1)
def graph_tool_schemas() -> tuple[dict, ...]:
    """Tool definitions in Anthropic format, discovered from the MCP server."""
    if not MCP_AVAILABLE:
        from .agent import GRAPH_TOOLS_FALLBACK
        return tuple(GRAPH_TOOLS_FALLBACK)
    return tuple(
        {"name": t.name, "description": t.description or t.name, "input_schema": t.input_schema}
        for t in _run(_list())
    )


def call_graph_tool(name: str, args: dict) -> dict:
    if not MCP_AVAILABLE:
        return _DIRECT[name](**args)
    args = {k: v for k, v in (args or {}).items() if v is not None}
    r = _run(_call(name, args))
    if r.is_error:
        text = " ".join(getattr(c, "text", "") for c in r.content)
        return {"error": text or f"{name} failed"}
    if r.structured_content is not None:
        sc = r.structured_content
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    text = " ".join(getattr(c, "text", "") for c in r.content)
    try:
        return json.loads(text)
    except ValueError:
        return {"result": text}


def transport() -> str:
    return "MCP (in-memory transport)" if MCP_AVAILABLE else "direct calls (MCP SDK not installed)"
