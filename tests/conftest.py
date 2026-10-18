"""Tests run against a live FalkorDB (FALKORDB_HOST/PORT, default localhost:6379) in their
own graphs, so the demo data is never touched. No LLM calls are made."""
import os
import sys
from pathlib import Path

os.environ["KG_GRAPH"] = "khoj_test_kg"
os.environ["BUYER_GRAPH_PREFIX"] = "khoj_test_buyer_"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from khoj.config import settings  # noqa: E402
from khoj.db import get_db, kg  # noqa: E402
from khoj.loader import load  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def knowledge_graph():
    load(verbose=False)
    yield kg()
    db = get_db()
    for name in db.list_graphs():
        if name == settings.kg_graph or name.startswith(settings.buyer_graph_prefix):
            db.select_graph(name).delete()


@pytest.fixture
def fresh_kg():
    """For tests that write to the knowledge graph: rebuild it afterwards."""
    yield kg()
    load(verbose=False)


@pytest.fixture
def buyer():
    from khoj import memory
    ids = []

    def make(name="tester"):
        memory.reset_buyer(name)
        ids.append(name)
        return name
    yield make
    for b in ids:
        memory.reset_buyer(b)
