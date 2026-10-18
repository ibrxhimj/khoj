"""Record real agent runs for the app's "Recorded runs" mode.

Needs FalkorDB and ANTHROPIC_API_KEY (costs well under a dollar). Run from the repo root:

    python scripts/record_replays.py

It rebuilds the demo graph, runs each example question with fresh buyer memory,
records the memory scenario (shortlist, a market event, a new session), saves
everything to data/replays/, and rebuilds the graph again so no test data is
left behind. Commit data/replays/ so the deployed app can play them.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from khoj import events, memory, replay  # noqa: E402,F401
from khoj.agent import KhojSession  # noqa: E402
from khoj.config import settings  # noqa: E402
from khoj.loader import load  # noqa: E402

EXAMPLES = [
    "Is Skyvista Altura in Sarjapur Road a safe buy? I want a 3BHK under 1.7 Cr.",
    "Who is really behind Meridian Tranquil in Hebbal?",
    "Check the land title for Kaveri Crest Serene before I book.",
    "Hoysala is a 30-year brand. Is Hoysala Orchid Springs safe?",
]
MEMORY_Q = ("Shortlist Banyan Grove Whispering Woods and Greenfield Sarjapur Greens for me, and always check the "
            "land title before recommending anything.")


def pack(r) -> dict:
    return {"text": r.text, "events": r.events, "graph": r.graph, "usage": r.usage}


def log(e: dict) -> None:
    if e["type"] in {"tool_call", "handoff"}:
        print(f"   {e['agent']}: {e.get('name', 'handoff')}")


def main() -> None:
    meta = {"recorded_at": time.strftime("%Y-%m-%d %H:%M"), "models": {
        "concierge": settings.concierge_model, "investigator": settings.investigator_model}}
    load(verbose=False)
    for i, q in enumerate(EXAMPLES):
        buyer = f"replay-{i}"
        memory.reset_buyer(buyer)
        print(f"> {q}")
        r = KhojSession(buyer, "Ibz").ask(q, log)
        print(f"  saved {replay.save('ask', q, {'question': q, 'result': pack(r), **meta})}")
        memory.reset_buyer(buyer)

    print("> memory scenario")
    buyer = "replay-memory"
    memory.reset_buyer(buyer)
    s1 = KhojSession(buyer, "Ibz")
    r1 = s1.ask(MEMORY_Q, log)
    time.sleep(1.2)
    fired = events.fire("contractor_insolvency")
    s2 = KhojSession(buyer, "Ibz")
    w = s2.welcome()
    from khoj import tools
    alert_graph = tools._merge_graphs(*[tools.trace_risk(a["project_id"])["_graph"] for a in s2.watch["alerts"]]) \
        if s2.watch["alerts"] else {"nodes": [], "edges": []}
    replay.save("scenario", "memory", {
        **meta,
        "steps": [
            {"kind": "ask", "question": MEMORY_Q, "result": pack(r1)},
            {"kind": "event", "label": fired["fired"]},
            {"kind": "welcome", "alerts": s2.watch["alerts"],
             "result": {"text": w.text if w else "", "events": [], "graph": alert_graph}},
        ],
    })
    print("  saved memory scenario")
    memory.reset_buyer(buyer)
    load(verbose=False)
    print("Done. Commit data/replays/.")


if __name__ == "__main__":
    main()
