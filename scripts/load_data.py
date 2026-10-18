"""Load data/seed.json into FalkorDB as the Khoj knowledge graph.

Run from the repo root:
    python scripts/load_data.py            # (re)builds the graph
    python scripts/load_data.py --check    # also prints the planted stories
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from khoj.db import kg, query  # noqa: E402
from khoj.loader import load  # noqa: E402


def check() -> None:
    g = kg()
    print("\nPlanted stories:")
    for pid in ["PRJ-ALTURA", "PRJ-SERENE", "PRJ-ORCHID", "PRJ-TRANQUIL"]:
        r = query(g, "MATCH (p:Project {id: $id})<-[:DEVELOPS]-(c:Company) RETURN p.name AS project, c.name AS company, "
                     "p.locality AS locality, p.status AS status", {"id": pid})
        print(" ", r[0] if r else f"{pid} MISSING")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    load()
    if args.check:
        check()
