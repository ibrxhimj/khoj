"""Recorded runs, so the public demo works without an API key.

scripts/record_replays.py runs the real agents (real Claude calls, real
FalkorDB queries) and saves every event, tool result, graph and answer to
data/replays/. The app plays them back step by step in "Recorded runs" mode.
Nothing in a recording is written by hand.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

REPLAY_DIR = Path(__file__).resolve().parent.parent / "data" / "replays"


def _norm(q: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", q.lower()).strip()


def slug(q: str) -> str:
    return _norm(q).replace(" ", "-")[:60]


def available() -> dict[str, dict]:
    """{normalised question: recording} for every saved single-question run."""
    out = {}
    if REPLAY_DIR.exists():
        for f in sorted(REPLAY_DIR.glob("*.json")):
            try:
                rec = json.loads(f.read_text())
            except ValueError:
                continue
            if rec.get("kind") == "ask":
                out[_norm(rec["question"])] = rec
    return out


def scenario(name: str) -> dict | None:
    f = REPLAY_DIR / f"scenario-{name}.json"
    return json.loads(f.read_text()) if f.exists() else None


def find(question: str) -> dict | None:
    return available().get(_norm(question))


def play(rec: dict, on_event, delay: float = 0.12) -> dict:
    """Stream a recording's events through on_event, then return its result."""
    for e in rec["result"]["events"]:
        on_event(e)
        if e.get("type") in {"tool_call", "handoff", "case_closed"}:
            time.sleep(delay)
    return rec["result"]


def save(kind: str, name: str, payload: dict) -> Path:
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    path = REPLAY_DIR / (f"scenario-{name}.json" if kind == "scenario" else f"{slug(name)}.json")
    path.write_text(json.dumps({"kind": kind, **payload}, default=str, indent=1))
    return path
