"""Khoj agents.

Team mode (default)
-------------------
  Concierge (fast model)    talks to the buyer, owns the buyer's memory graph,
                            and delegates every property question.
  Investigator (strong model) works only on the knowledge graph, using Cypher
                            and FalkorDB graph algorithms as tools.

Handoff goes through the graph: the Concierge opens a :Case in the buyer's
memory graph (with a brief built from the buyer's stored preferences and
rules), the Investigator writes :Finding nodes into it, and the Concierge reads
the Case back. Findings persist, so later sessions know what was found.

The agents never write Cypher. Graph tools are discovered and called over MCP
(khoj/mcp_server.py); each is a human-written, parameterised, read-only query.

Single mode gives one agent every tool (useful if latency matters).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from . import mcp_client, memory, tools, verifier
from .config import settings
from .llm import LLM

Event = dict
OnEvent = Callable[[Event], None]

MAX_TOOL_CHARS = 12000

# --------------------------------------------------------------------------- tool schemas


def _schema(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties, "required": required or []}}


PID = {"type": "string", "description": "Project id, e.g. PRJ-ALTURA (get it from search_projects)."}

# Used only if the MCP SDK is missing; normally the catalogue comes from MCP list_tools.
GRAPH_TOOLS_FALLBACK = [
    _schema("search_projects",
            "Find projects by (fuzzy) name and/or filters. Returns ids, basics and each project's graph risk score.",
            {"name": {"type": "string", "description": "Project name or part of it; typos are fine."},
             "locality": {"type": "string"},
             "budget_max_lakh": {"type": "number", "description": "Max starting price in INR lakh (1 crore = 100)."},
             "bhk": {"type": "integer"},
             "include_stalled": {"type": "boolean"}}),
    _schema("project_profile",
            "The surface view a buyer sees: brochure facts, developer company, directors, RERA and approvals, land "
            "khata, contractor, complaint counts.", {"project_id": PID}, ["project_id"]),
    _schema("trace_risk",
            "Graph risk score (0-100) for a project and the evidence paths behind it: every red flag (stalled "
            "projects, insolvent companies, court cases, complaints, B-khata) reachable within 4 hops, weighted by "
            "distance and path count.", {"project_id": PID}, ["project_id"]),
    _schema("developer_network",
            "Reveal the hidden group behind a project's developer: all companies linked through shared directors "
            "(Weakly Connected Components), their brands, failures, stalled projects and court cases.",
            {"project_id": PID}, ["project_id"]),
    _schema("key_people",
            "Rank the project's directors by betweenness centrality across the whole director-company network, "
            "with every board they sit on. High betweenness = a bridge between otherwise separate companies.",
            {"project_id": PID}, ["project_id"]),
    _schema("land_title_check",
            "Land parcel, khata type, previous owner, and any court case on the land or its previous owner.",
            {"project_id": PID}, ["project_id"]),
    _schema("find_alternatives",
            "Lower-risk projects matching the buyer's criteria, ranked by graph risk then price.",
            {"locality": {"type": "string"}, "budget_max_lakh": {"type": "number"}, "bhk": {"type": "integer"},
             "max_risk": {"type": "number", "description": "Default 30."},
             "exclude_project_id": {"type": "string"}}, ["locality"]),
    _schema("contractor_record",
            "Track record of the contractor building this project: its other late or stalled projects and court "
            "cases.", {"project_id": PID}, ["project_id"]),
]

RECORD_FINDING = _schema(
    "record_finding",
    "Record your conclusion about one project into the shared case file. Call once per project you analysed.",
    {"project_id": PID,
     "verdict": {"type": "string", "enum": ["SAFE", "CAUTION", "AVOID"]},
     "risk_score": {"type": "number"},
     "summary": {"type": "string", "description": "1-2 sentences."},
     "evidence": {"type": "array", "items": {"type": "string"},
                  "description": "1-3 key evidence chains in plain words, e.g. 'Skyvista Altura -> Altura Habitat -> "
                                 "director Meera Shetty -> Northstar Habitats (insolvent)'."}},
    ["project_id", "verdict", "summary"])

RECALL_FINDINGS = _schema(
    "recall_findings",
    "Read earlier findings about a project from this buyer's memory graph, with the date of each. Call this "
    "before investigating a project.", {"project_id": PID}, ["project_id"])

MEMORY_TOOLS = [
    _schema("remember",
            "Save a durable fact about the buyer to their memory graph. type=preference needs a key "
            "(budget_max_lakh, localities, bhk, possession_by, purpose); type=concern for worries; type=rule for "
            "standing instructions (e.g. 'always check land title').",
            {"type": {"type": "string", "enum": ["preference", "concern", "rule"]},
             "key": {"type": "string"}, "value": {"type": "string"}}, ["type", "value"]),
    _schema("shortlist",
            "Track a project for the buyer (add) or record that they decided against it (remove, with reason).",
            {"action": {"type": "string", "enum": ["add", "remove"]}, "project_id": PID,
             "reason": {"type": "string"}}, ["action", "project_id"]),
]

ASK_INVESTIGATOR = _schema(
    "ask_investigator",
    "Delegate research to the Investigator, who works on the property knowledge graph. Use it for ANY question "
    "about projects, developers, directors, land, contractors, risk or alternatives. Write a precise question and "
    "include the buyer's relevant criteria.",
    {"question": {"type": "string"}}, ["question"])

# --------------------------------------------------------------------------- prompts

INVESTIGATOR_PROMPT = f"""You are the Investigator inside Khoj, a due-diligence system for Bangalore residential \
property. You work only from the FalkorDB knowledge graph through your tools. You never write Cypher: you choose a \
tool and fill in its arguments. Never state a fact you did not get from a tool.

{tools.SCHEMA_TEXT}

Every tool result has a status. Respect it:
- ANSWERED_WITH_PATH: the graph holds the relationships. Cite the path.
- ANSWERED_FROM_MEMORY: an earlier finding from the buyer's memory. Say when it was made.
- NOT_IN_GRAPH: the entity exists but nothing connects it as asked. Say "no known connection in the graph", \
never "safe" or "nothing wrong".
- UNKNOWN_ENTITY: the name does not resolve. Say it is not in the graph and do not guess or investigate further.

How to investigate a project:
1. Resolve its id with search_projects unless you were given one.
2. recall_findings: check the buyer's memory for earlier findings on it.
3. project_profile: the surface view a buyer would see.
4. trace_risk: the graph risk score and the evidence paths. This is the core of your work.
5. Follow the evidence. Paths through directors: developer_network and key_people. Paths through land or court \
cases: land_title_check. Paths through a contractor: contractor_record.
6. Follow every rule in the buyer brief (for example, always checking land title).
7. If a project is MEDIUM or HIGH risk and you know the buyer's criteria, call find_alternatives.
8. Call record_finding once for every project you analysed. A deterministic verifier checks it against the graph \
before it is saved. Verdict rules: AVOID if risk > 60 or there is a pending title suit on the plot or its previous \
owner; SAFE only if risk < 30 with no red flag within one hop; otherwise CAUTION. Evidence chains may only name \
entities your tools returned for that project. If the verifier rejects the finding, fix it and record it again.

Accuracy rules: respect dates. A directorship with an end year is a former directorship, so say "former director \
(until 2024)". State only what the tools returned; do not add speculation such as fund diversion or fraud unless a \
tool returned it.

Then reply with a compact report (under 250 words): for each project, the verdict and risk score, the surface view \
in one line, the hidden evidence as chains in plain words (A -> B -> C), and what the buyer should verify offline. \
Do not mention tool names."""

CONCIERGE_PROMPT = """You are Khoj, a homebuyer's due-diligence concierge for new residential launches in Bangalore. \
You talk to the buyer, keep their memory, and delegate all research to the Investigator.

Rules:
- For any question about a project, developer, land, risk or alternatives, call ask_investigator with a precise \
question. Include the buyer's budget, BHK and localities from memory when relevant. Never answer property facts \
from your own knowledge.
- When the buyer states a durable fact, save it with remember: preferences (keys: budget_max_lakh, localities, bhk, \
possession_by, purpose), concerns, and rules (standing instructions). Update a preference when it changes.
- When the buyer wants to track a project, call shortlist with action add. When they decide against one, call \
shortlist with action remove and the reason.
- Use the memory below. Do not ask for things you already know.

Report only what the Investigator found, keeping its dates and qualifiers (for example "former director"). Do not \
add risks it did not report.

Answer style: start with a one-line verdict in bold (for example "**Avoid for now: risk 85/100 (HIGH)**"), then 2-4 \
reasons written as evidence chains in plain words, then one concrete next step. Under 200 words unless the buyer \
asks for more. The dataset is a synthetic demo; never present it as real records.

MEMORY (read from the buyer's graph):
{brief}"""

SINGLE_PROMPT = CONCIERGE_PROMPT.replace(
    "and delegate all research to the Investigator.", "and research properties on the knowledge graph yourself.").replace(
    "For any question about a project, developer, land, risk or alternatives, call ask_investigator with a precise "
    "question. Include the buyer's budget, BHK and localities from memory when relevant.",
    "For any question about a project, developer, land, risk or alternatives, use the graph tools: search_projects, "
    "project_profile, trace_risk, then follow the evidence with developer_network, key_people, land_title_check or "
    "contractor_record. Never call a project safe because a tool returned NOT_IN_GRAPH; say there is no known "
    "connection. If a tool returns UNKNOWN_ENTITY, say the project is not in the graph.")

WELCOME_PROMPT = """The buyer has just opened a new session. Write a short welcome-back message (2-4 sentences): \
greet them by name; if anything changed on their shortlist since last time (see ALERTS), say what changed and the \
specific new evidence chain; otherwise recap where they left off in one sentence. End with one suggested next step. \
Do not call any tools."""

# --------------------------------------------------------------------------- helpers


def _strip(out: dict) -> tuple[dict, dict]:
    model = {k: v for k, v in out.items() if not k.startswith("_")}
    ui = {k: v for k, v in out.items() if k.startswith("_")}
    return model, ui


def memory_brief(buyer_id: str, alerts: list[dict] | None = None) -> str:
    r = memory.recall(buyer_id, events=8)
    lines = [f"Buyer: {r['buyer']} | this is session #{r['sessions_so_far']}"]
    if r["preferences"]:
        prefs = []
        for p in r["preferences"]:
            s = f"{p['key']}={p['value']}"
            if p["previously"]:
                s += f" (previously {', '.join(p['previously'])})"
            prefs.append(s)
        lines.append("Preferences: " + "; ".join(prefs))
    if r["concerns"]:
        lines.append("Concerns: " + "; ".join(r["concerns"]))
    if r["rules"]:
        lines.append("Standing rules: " + "; ".join(r["rules"]))
    if r["shortlist"]:
        lines.append("Shortlist: " + "; ".join(
            f"{s['name']} [{s['project_id']}] risk {s['last_risk']} (was {s['risk_at_add']} when added)"
            for s in r["shortlist"]))
    if r["rejected"]:
        lines.append("Decided against: " + "; ".join(
            f"{x['name']} [{x['project_id']}]: {x['reason']}" for x in r["rejected"]))
    past = memory.past_findings(buyer_id, limit=6)
    if past:
        lines.append("Past findings: " + " | ".join(
            f"{f['project']}: {f['verdict']} ({f['risk_score']}) {f['summary']}" for f in past))
    if r["recent_events"]:
        lines.append("Recent conversation:")
        for e in r["recent_events"]:
            lines.append(f"  - {e['kind']}: {e['text'][:220]}")
    if alerts:
        lines.append("ALERTS since the last session:")
        for a in alerts:
            chains = "; ".join(f"{f['reason']} via {f['path']}" for f in a["new_flags"][:3])
            lines.append(f"  - {a['project']}: risk {a['risk_before']} -> {a['risk_now']} ({a['band_now']}). {chains}")
    if len(lines) == 1:
        lines.append("New buyer: nothing stored yet. Learn their budget, localities, BHK and timeline as you go.")
    return "\n".join(lines)


@dataclass
class TurnResult:
    text: str
    graph: dict = field(default_factory=lambda: {"nodes": [], "edges": []})
    cypher: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0})


# --------------------------------------------------------------------------- the loop


def run_loop(llm: LLM, agent: str, system: str, messages: list[dict], schemas: list[dict],
             handlers: dict[str, Callable], turn: TurnResult, on_event: OnEvent, max_steps: int = 10) -> str:
    def emit(e: Event):
        turn.events.append(e)
        on_event(e)

    for _ in range(max_steps):
        resp = llm.create(system, messages, schemas)
        turn.usage["input"] += resp["usage"]["input"]
        turn.usage["output"] += resp["usage"]["output"]
        messages.append({"role": "assistant", "content": resp["content"]})
        texts = [b["text"] for b in resp["content"] if b["type"] == "text" and b.get("text")]
        uses = [b for b in resp["content"] if b["type"] == "tool_use"]
        if not uses:
            return "\n".join(texts).strip()
        if texts:
            emit({"agent": agent, "type": "thought", "text": "\n".join(texts)})
        results = []
        for u in uses:
            emit({"agent": agent, "type": "tool_call", "name": u["name"], "input": u["input"]})
            try:
                out = handlers[u["name"]](**u["input"])
            except Exception as e:  # report errors to the model so it can recover
                out = {"error": f"{type(e).__name__}: {e}"}
            model_out, ui = _strip(out if isinstance(out, dict) else {"result": out})
            if ui.get("_graph"):
                turn.graph = tools._merge_graphs(turn.graph, ui["_graph"])
            turn.cypher += ui.get("_cypher", [])
            emit({"agent": agent, "type": "tool_result", "name": u["name"], "output": model_out,
                  "cypher": ui.get("_cypher", []), "graph": ui.get("_graph")})
            content = json.dumps(model_out, default=str)
            if len(content) > MAX_TOOL_CHARS:
                content = content[:MAX_TOOL_CHARS] + ' ... (truncated)'
            results.append({"type": "tool_result", "tool_use_id": u["id"], "content": content})
        messages.append({"role": "user", "content": results})

    # Out of steps: ask for a final answer with tools disabled.
    messages[-1]["content"].append({"type": "text", "text": "Stop researching and give your final answer now."})
    resp = llm.create(system, messages, schemas, allow_tools=False)
    return "\n".join(b["text"] for b in resp["content"] if b["type"] == "text").strip()


# --------------------------------------------------------------------------- session



class KhojSession:
    """One chat session for one buyer."""

    def __init__(self, buyer_id: str, buyer_name: str | None = None, mode: str | None = None):
        self.buyer_id = buyer_id
        self.buyer_name = buyer_name or buyer_id
        self.mode = mode or settings.agent_mode
        self.session_id = memory.start_session(buyer_id, self.buyer_name)
        self.watch = memory.watchlist_check(buyer_id)
        self.history: list[dict] = []  # plain user/assistant text turns of this session
        self.concierge = LLM(settings.concierge_model if self.mode == "team" else settings.investigator_model)
        self.investigator = LLM(settings.investigator_model, thinking=settings.investigator_thinking)

    # ---------------------------------------------------------------- handlers
    def _graph_schemas(self) -> list[dict]:
        return list(mcp_client.graph_tool_schemas())

    def _graph_handlers(self) -> dict[str, Callable]:
        return {t["name"]: (lambda _n=t["name"], **k: mcp_client.call_graph_tool(_n, k))
                for t in self._graph_schemas()}

    def _recall_findings(self, project_id: str) -> dict:
        found = memory.findings_for(self.buyer_id, project_id)
        if not found:
            return {"status": tools.NOT_IN_GRAPH, "message": "No earlier finding about this project in the buyer's memory."}
        for f in found:
            f["recorded_on"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(f.pop("ts")))
        return {"status": tools.ANSWERED_FROM_MEMORY, "findings": found}

    def _memory_handlers(self) -> dict[str, Callable]:
        return {
            "remember": lambda type, value, key=None: memory.remember(self.buyer_id, type, value, key),
            "shortlist": lambda action, project_id, reason=None: memory.shortlist(self.buyer_id, action, project_id, reason),
        }

    def _investigate(self, question: str, turn: TurnResult, on_event: OnEvent) -> dict:
        r = memory.recall(self.buyer_id, events=0)
        brief_parts = []
        if r["preferences"]:
            brief_parts.append("Preferences: " + "; ".join(f"{p['key']}={p['value']}" for p in r["preferences"]))
        if r["concerns"]:
            brief_parts.append("Concerns: " + "; ".join(r["concerns"]))
        if r["rules"]:
            brief_parts.append("Rules you must follow: " + "; ".join(r["rules"]))
        if r["rejected"]:
            brief_parts.append("Already rejected by the buyer: " + "; ".join(x["name"] for x in r["rejected"]))
        brief = "\n".join(brief_parts) or "No stored preferences yet."
        case_id = memory.open_case(self.buyer_id, self.session_id, question, brief)
        ev = {"agent": "concierge", "type": "handoff", "case_id": case_id, "question": question, "brief": brief}
        turn.events.append(ev)
        on_event(ev)

        handlers = self._graph_handlers()
        def record_finding(project_id, verdict, summary, risk_score=None, evidence=None):
            v = verifier.verify_finding(project_id, verdict, summary, risk_score, evidence)
            if not v["ok"]:
                memory.record_rejected_draft(self.buyer_id, case_id, project_id, str(verdict), v["reason"],
                                             v.get("detail", ""))
                return {"status": "REJECTED", "reason": v["reason"], "detail": v.get("detail"),
                        "computed": v.get("computed"),
                        "instruction": "The finding was not recorded. Fix it using the computed facts and call "
                                       "record_finding again."}
            f = v["finding"]
            memory.record_finding(self.buyer_id, case_id, f["project_id"], f["verdict"], f["summary"],
                                  f["risk_score"], f["evidence"])
            return {"status": "VERIFIED", "recorded": True, "verdict": f["verdict"], "risk_score": f["risk_score"],
                    "corrections": v["corrections"]}

        handlers["record_finding"] = record_finding
        handlers["recall_findings"] = self._recall_findings
        system = f"{INVESTIGATOR_PROMPT}\n\nCase {case_id}. Buyer brief (from the buyer's memory graph):\n{brief}"
        notes = run_loop(self.investigator, "investigator", system, [{"role": "user", "content": question}],
                         self._graph_schemas() + [RECALL_FINDINGS, RECORD_FINDING], handlers, turn, on_event,
                         max_steps=14)
        memory.close_case(self.buyer_id, case_id)
        case = memory.read_case(self.buyer_id, case_id)  # the handoff: read back from the graph
        ev = {"agent": "investigator", "type": "case_closed", "case_id": case_id, "findings": case["findings"],
              "rejected_drafts": case["rejected_drafts"]}
        turn.events.append(ev)
        on_event(ev)
        return {"case_id": case_id, "findings": case["findings"], "investigator_report": notes}

    def _ground(self, text: str, brief: str, turn: TurnResult, on_event: OnEvent) -> str:
        """Check that every project named in the answer came from this turn's tool results or the buyer's
        memory. If one did not, say so instead of letting an unverified name stand."""
        evidence = brief + " " + " ".join(json.dumps(e.get("output", e.get("findings", "")), default=str)
                                          for e in turn.events)
        names = verifier._entity_names()
        ungrounded = sorted(n for n in names if n in text and n not in evidence)
        scores = {float(x) for x in re.findall(r'"risk_score": ([0-9.]+)', evidence)}
        scores |= {float(x) for x in re.findall(r"risk[ _]?(?:score)?\s*[:=]?\s*([0-9.]+)", brief)}
        bad_scores = sorted({m for m in re.findall(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*/\s*100\b", text)
                             if not any(abs(float(m) - sc) <= 1 for sc in scores)}, key=float)
        if ungrounded or bad_scores:
            ev = {"agent": "khoj", "type": "grounding_warning", "names": ungrounded, "scores": bad_scores}
            turn.events.append(ev)
            on_event(ev)
            notes = []
            if ungrounded:
                notes.append("I did not verify " + ", ".join(ungrounded) + " against the graph in this answer")
            if bad_scores:
                notes.append("the score(s) " + ", ".join(f"{b}/100" for b in bad_scores) +
                             " do not match any computed score")
            text += "\n\n_Note: " + "; ".join(notes) + ", so treat those as unchecked._"
        return text

    # ---------------------------------------------------------------- public
    def welcome(self, on_event: OnEvent = lambda e: None) -> TurnResult | None:
        r = memory.recall(self.buyer_id, events=0)
        if r["sessions_so_far"] <= 1 and not self.watch["alerts"]:
            return None
        turn = TurnResult(text="")
        system = CONCIERGE_PROMPT.format(brief=memory_brief(self.buyer_id, self.watch["alerts"]))
        resp = self.concierge.create(system, [{"role": "user", "content": WELCOME_PROMPT}], [MEMORY_TOOLS[0]],
                                     max_tokens=500, allow_tools=False)
        turn.text = "\n".join(b["text"] for b in resp["content"] if b["type"] == "text").strip()
        memory.log_event(self.buyer_id, self.session_id, "khoj", turn.text)
        self.history.append({"role": "assistant", "content": turn.text})
        return turn

    def ask(self, user_text: str, on_event: OnEvent = lambda e: None) -> TurnResult:
        turn = TurnResult(text="")
        memory.log_event(self.buyer_id, self.session_id, "buyer", user_text)
        brief = memory_brief(self.buyer_id, self.watch["alerts"])
        messages = [*self.history, {"role": "user", "content": user_text}]

        if self.mode == "team":
            system = CONCIERGE_PROMPT.format(brief=brief)
            search = next(t for t in self._graph_schemas() if t["name"] == "search_projects")
            schemas = [ASK_INVESTIGATOR, search, *MEMORY_TOOLS]
            handlers = {**self._memory_handlers(),
                        "search_projects": self._graph_handlers()["search_projects"],
                        "ask_investigator": lambda question: self._investigate(question, turn, on_event)}
            text = run_loop(self.concierge, "concierge", system, messages, schemas, handlers, turn, on_event, max_steps=6)
        else:
            system = SINGLE_PROMPT.format(brief=brief)
            schemas = self._graph_schemas() + [RECALL_FINDINGS] + MEMORY_TOOLS
            handlers = {**self._graph_handlers(), **self._memory_handlers(), "recall_findings": self._recall_findings}
            text = run_loop(self.concierge, "khoj", system, messages, schemas, handlers, turn, on_event, max_steps=12)

        text = self._ground(text, brief, turn, on_event)
        turn.text = text
        self.history += [{"role": "user", "content": user_text}, {"role": "assistant", "content": text}]
        memory.log_event(self.buyer_id, self.session_id, "khoj", text[:1500])
        return turn
