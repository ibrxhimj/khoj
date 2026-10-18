"""Thin LLM layer. Internally everything uses the Anthropic Messages format
(content blocks: text, tool_use, tool_result). The OpenAI-compatible adapter
converts to and from that format, so the agent code never changes.

LLM_PROVIDER=anthropic (default), gemini (Google's OpenAI-compatible endpoint,
GEMINI_API_KEY) or openai (any OpenAI-compatible endpoint via OPENAI_BASE_URL).
"""
from __future__ import annotations

import json
import time
import uuid

from .config import settings


class LLM:
    def __init__(self, model: str, thinking: bool = False):
        self.model = model
        self.thinking = thinking
        self.provider = settings.llm_provider
        if self.provider == "anthropic":
            import anthropic

            if not settings.anthropic_api_key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set (add it to .env or Streamlit secrets).")
            self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        elif self.provider == "gemini":
            from openai import OpenAI

            if not settings.gemini_api_key:
                raise RuntimeError("GEMINI_API_KEY is not set (add it to .env or Streamlit secrets).")
            # max_retries=0: our own backoff (_with_retries) handles rate limits with longer waits.
            self.client = OpenAI(api_key=settings.gemini_api_key, base_url=settings.gemini_base_url, max_retries=0)
        elif self.provider == "openai":
            from openai import OpenAI

            if not settings.openai_api_key:
                raise RuntimeError("OPENAI_API_KEY is not set (add it to .env or Streamlit secrets).")
            self.client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, max_retries=0)
        else:
            raise RuntimeError(f"Unknown LLM_PROVIDER {self.provider}")

    # ------------------------------------------------------------------ public
    def create(self, system: str, messages: list[dict], tools: list[dict], max_tokens: int = 2048,
               allow_tools: bool = True) -> dict:
        if self.provider == "anthropic":
            return self._anthropic(system, messages, tools, max_tokens, allow_tools)
        return self._openai(system, messages, tools, max_tokens, allow_tools)

    # ------------------------------------------------------------------ anthropic
    def _anthropic(self, system, messages, tools, max_tokens, allow_tools):
        import anthropic

        tools = [dict(t) for t in tools]
        if tools:
            tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            tools=tools,
        )
        if not allow_tools and tools:
            kwargs["tool_choice"] = {"type": "none"}
        if not self.thinking:
            kwargs["thinking"] = {"type": "disabled"}
        try:
            resp = self.client.messages.create(**kwargs)
        except anthropic.BadRequestError as e:
            # Some models reject disabling thinking; retry with the model default.
            if "thinking" in kwargs and "thinking" in str(e).lower():
                kwargs.pop("thinking")
                resp = self.client.messages.create(**kwargs)
            else:
                raise
        return {
            "content": [b.model_dump(exclude_none=True) for b in resp.content],
            "stop_reason": resp.stop_reason,
            "usage": {"input": resp.usage.input_tokens, "output": resp.usage.output_tokens},
        }

    # ------------------------------------------------------------------ openai-compatible
    def _openai(self, system, messages, tools, max_tokens, allow_tools):
        oa_msgs = [{"role": "system", "content": system}]
        for m in messages:
            if isinstance(m["content"], str):
                oa_msgs.append({"role": m["role"], "content": m["content"]})
                continue
            if m["role"] == "assistant":
                text = "".join(b.get("text", "") for b in m["content"] if b["type"] == "text")
                # Send each tool call back exactly as the provider returned it. Gemini 3 attaches
                # thought signatures to tool calls and needs them back on the next turn.
                calls = [b.get("_raw") or {"id": b["id"], "type": "function",
                                           "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                         for b in m["content"] if b["type"] == "tool_use"]
                msg = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                oa_msgs.append(msg)
            else:
                for b in m["content"]:
                    if b["type"] == "tool_result":
                        oa_msgs.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
                    elif b["type"] == "text":
                        oa_msgs.append({"role": "user", "content": b["text"]})
        oa_tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                      "parameters": _clean_schema(t["input_schema"])}}
                    for t in tools]
        kwargs = dict(model=self.model, messages=oa_msgs, max_tokens=max_tokens)
        if oa_tools:
            kwargs.update(tools=oa_tools, tool_choice="auto" if allow_tools else "none")
        if settings.reasoning_effort or self.provider == "gemini":
            kwargs["reasoning_effort"] = settings.reasoning_effort or "low"
        resp = self._with_retries(kwargs)
        choice = resp.choices[0]
        content = []
        if choice.message.content:
            content.append({"type": "text", "text": choice.message.content})
        for tc in choice.message.tool_calls or []:
            raw = tc.model_dump(exclude_none=True)
            raw["id"] = raw.get("id") or f"call_{uuid.uuid4().hex[:12]}"
            args = tc.function.arguments
            try:
                parsed = json.loads(args) if isinstance(args, str) else (args or {})
            except ValueError:
                parsed = {}
            content.append({"type": "tool_use", "id": raw["id"], "name": tc.function.name,
                            "input": parsed or {}, "_raw": raw})
        usage = getattr(resp, "usage", None)
        return {
            "content": content,
            "stop_reason": "tool_use" if choice.message.tool_calls else "end_turn",
            "usage": {"input": getattr(usage, "prompt_tokens", 0) or 0,
                      "output": getattr(usage, "completion_tokens", 0) or 0},
        }

    def _with_retries(self, kwargs, attempts: int = 8):
        """Free tiers rate-limit hard (requests per minute); back off and retry instead of failing the turn."""
        import openai

        delay = 5.0
        for i in range(attempts):
            try:
                return self.client.chat.completions.create(**kwargs)
            except openai.BadRequestError as e:
                if "reasoning" in str(e).lower() and "reasoning_effort" in kwargs:
                    kwargs.pop("reasoning_effort")
                    continue
                raise
            except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError):
                if i == attempts - 1:
                    raise
                print(f"Rate limited by the LLM provider; waiting {delay:.0f}s before retrying...", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 60)


def _clean_schema(schema):
    """Reduce a JSON Schema to the subset every OpenAI-compatible provider accepts:
    drop titles and defaults, and turn 'anyOf [X, null]' (optional arguments) into X."""
    if isinstance(schema, list):
        return [_clean_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    if "anyOf" in schema:
        options = [o for o in schema["anyOf"] if o.get("type") != "null"]
        if len(options) == 1:
            merged = {**{k: v for k, v in schema.items() if k != "anyOf"}, **options[0]}
            return _clean_schema(merged)
    return {k: _clean_schema(v) for k, v in schema.items() if k not in {"title", "default"}}
