"""Settings, read from environment variables (or a local .env file).

On Streamlit Community Cloud, root-level secrets are exposed as environment
variables, so the same names work there.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # python-dotenv is optional
    pass


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    # FalkorDB connection
    falkordb_host: str = os.getenv("FALKORDB_HOST", "localhost")
    falkordb_port: int = int(os.getenv("FALKORDB_PORT", "6379"))
    falkordb_username: str | None = os.getenv("FALKORDB_USERNAME") or None
    falkordb_password: str | None = os.getenv("FALKORDB_PASSWORD") or None
    falkordb_ssl: bool = _bool(os.getenv("FALKORDB_SSL"), False)

    # Graph names
    kg_graph: str = os.getenv("KG_GRAPH", "khoj_bangalore")
    buyer_graph_prefix: str = os.getenv("BUYER_GRAPH_PREFIX", "khoj_buyer_")

    # LLM
    llm_provider: str = os.getenv("LLM_PROVIDER", "anthropic")  # anthropic | gemini | openai
    llm_model: str = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY") or None
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY") or None
    # Any OpenAI-compatible endpoint works (e.g. Gemini, Groq)
    openai_base_url: str | None = os.getenv("OPENAI_BASE_URL") or None

    # Google Gemini through its OpenAI-compatible endpoint (free tier available).
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY") or None
    gemini_base_url: str = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
    # Optional: low | medium | high. Lower is faster. Leave empty for the provider default.
    reasoning_effort: str | None = os.getenv("LLM_REASONING_EFFORT") or None

    # Agents. "team" = Concierge (memory, talks to buyer) + Investigator (graph
    # research), handing off through a Case node in the buyer's graph.
    # "single" = one agent with every tool.
    agent_mode: str = os.getenv("AGENT_MODE", "team")
    _gemini = os.getenv("LLM_PROVIDER", "anthropic") == "gemini"
    concierge_model: str = os.getenv("CONCIERGE_MODEL", "gemini-3.8-flash" if _gemini else "claude-haiku-5-5")
    _llm_model = os.getenv("LLM_MODEL")
    if _gemini and _llm_model and _llm_model.startswith("claude"):
        _llm_model = None  # a Claude model name left in .env does not apply to Gemini
    investigator_model: str = os.getenv("INVESTIGATOR_MODEL", _llm_model or (
        "gemini-3.8-flash" if _gemini else "claude-sonnet-5-5"))
    investigator_thinking: bool = _bool(os.getenv("INVESTIGATOR_THINKING"), False)

    # Demo mode for the public app: "live" (agents call the LLM), "replay" (play
    # runs recorded with scripts/record_replays.py, no API key needed) or "auto"
    # (live when an API key is set, otherwise replay).
    demo_mode: str = os.getenv("DEMO_MODE", "auto")
    max_live_questions: int = int(os.getenv("MAX_LIVE_QUESTIONS", "25"))

    # Risk model
    risk_max_hops: int = int(os.getenv("RISK_MAX_HOPS", "4"))
    risk_beta: float = float(os.getenv("RISK_BETA", "0.6"))


def llm_key_present() -> bool:
    return bool({"anthropic": Settings.anthropic_api_key, "gemini": Settings.gemini_api_key,
                 "openai": Settings.openai_api_key}.get(Settings.llm_provider))


settings = Settings()
