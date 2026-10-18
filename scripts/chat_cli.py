"""Talk to Khoj from the terminal (quick check that FalkorDB + the LLM key work).

    python scripts/chat_cli.py                       # interactive
    python scripts/chat_cli.py "Is Skyvista Altura safe?"
    python scripts/chat_cli.py --buyer ibz --name Ibz
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from khoj.agent import KhojSession  # noqa: E402

ICON = {"concierge": "[concierge]", "investigator": "  [investigator]", "khoj": "[khoj]"}


def show(e: dict) -> None:
    who = ICON.get(e["agent"], e["agent"])
    if e["type"] == "tool_call":
        print(f"{who} -> {e['name']}({json.dumps(e['input'])[:120]})")
    elif e["type"] == "handoff":
        print(f"{who} => opened case {e['case_id']}: {e['question'][:120]}")
    elif e["type"] == "case_closed":
        for f in e["findings"]:
            print(f"{who} <= finding: {f['project']} {f['verdict']} ({f['risk_score']})")
    elif e["type"] == "tool_result" and "error" in e["output"]:
        print(f"{who}    ! {e['output']['error'][:160]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="*")
    ap.add_argument("--buyer", default="cli-demo")
    ap.add_argument("--name", default="Ibz")
    ap.add_argument("--mode", default=None, choices=["team", "single"])
    a = ap.parse_args()
    s = KhojSession(a.buyer, a.name, a.mode)
    w = s.welcome(show)
    if w:
        print(f"\nKhoj: {w.text}\n")
    questions = [" ".join(a.question)] if a.question else None
    while True:
        q = questions.pop(0) if questions else (None if questions is not None else input("\nYou: ").strip())
        if not q:
            break
        r = s.ask(q, show)
        print(f"\nKhoj: {r.text}\n\n(tokens in/out: {r.usage['input']}/{r.usage['output']})")
        if questions is not None and not questions:
            break


if __name__ == "__main__":
    main()
