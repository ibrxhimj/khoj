"""Khoj: graph-powered due diligence for Bangalore homebuyers.

Run locally:  streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

from khoj import events as live_events
from khoj import mcp_client, memory, replay, tools, ui
from khoj.agent import KhojSession
from khoj.config import llm_key_present, settings
from khoj.db import list_buyer_graphs
from khoj.loader import ensure_loaded, load

st.set_page_config(page_title="Khoj", page_icon="🔎", layout="wide")
st.markdown(ui.CSS, unsafe_allow_html=True)

USER_AVATAR = ":material/person:"
KHOJ_AVATAR = ":material/travel_explore:"

EXAMPLES = [
    "Is Skyvista Altura in Sarjapur Road a safe buy? I want a 3BHK under 1.7 Cr.",
    "Who is really behind Meridian Tranquil in Hebbal?",
    "Check the land title for Kaveri Crest Serene before I book.",
    "Hoysala is a 30-year brand. Is Hoysala Orchid Springs safe?",
]


# --------------------------------------------------------------------------- setup
@st.cache_resource(show_spinner="Preparing the knowledge graph…")
def _bootstrap() -> bool:
    return ensure_loaded()


try:
    _bootstrap()
except Exception as e:  # noqa: BLE001
    st.error(f"Can't reach FalkorDB at {settings.falkordb_host}:{settings.falkordb_port}. "
             f"Check FALKORDB_* settings in .env or Streamlit secrets.\n\n{e}")
    st.stop()

ss = st.session_state
ss.setdefault("buyer_id", "ibz")
ss.setdefault("buyer_name", "Ibz")
ss.setdefault("chat", [])        # [{role, text, events, graph, verdicts}]
ss.setdefault("ks", None)
ss.setdefault("pending", None)
ss.setdefault("mode", settings.agent_mode)

# Live agents need an API key; recorded runs need data/replays/ (scripts/record_replays.py).
LIVE_OK = llm_key_present() and settings.demo_mode != "replay"
RECORDINGS = replay.available()
MEMORY_SCENARIO = replay.scenario("memory")
REPLAY_OK = bool(RECORDINGS)
ss.setdefault("run", "live" if (LIVE_OK and settings.demo_mode != "replay") or not REPLAY_OK else "replay")
ss.setdefault("live_count", 0)
MEMORY_DEMO = "__memory_demo__"


def open_session() -> None:
    """Start a new session for the current buyer (this is what 'coming back tomorrow' looks like)."""
    if ss.run == "replay":
        ss.ks, ss.chat = None, []
        return
    try:
        ss.ks = KhojSession(ss.buyer_id, ss.buyer_name, ss.mode)
    except Exception as e:  # noqa: BLE001
        ss.ks = None
        st.error(str(e))
        return
    ss.chat = []
    alerts = ss.ks.watch["alerts"]
    try:
        w = ss.ks.welcome()
    except Exception as e:  # noqa: BLE001
        w = None
        st.warning(f"Welcome message failed: {e}")
    if w:
        verdicts = [{"verdict": ui.BAND_TO_VERDICT.get(a["band_now"], "CAUTION"), "score": a["risk_now"],
                     "project": a["project"]} for a in alerts]
        ss.chat.append({"role": "assistant", "text": w.text, "events": [], "graph": _alerts_graph(alerts),
                        "verdicts": verdicts, "risks": []})


def _alerts_graph(alerts: list[dict]) -> dict:
    graphs = [tools.trace_risk(a["project_id"])["_graph"] for a in alerts]
    return tools._merge_graphs(*graphs) if graphs else {"nodes": [], "edges": []}


def verdicts_from(events: list[dict]) -> list[dict]:
    out = []
    for e in events:
        if e["type"] == "case_closed":
            out += [{"verdict": f["verdict"], "score": f["risk_score"], "project": f["project"]} for f in e["findings"]]
    if not out:  # single mode: fall back to risk traces
        for r in risks_from(events):
            out.append({"verdict": ui.BAND_TO_VERDICT[r["band"]], "score": r["risk_score"], "project": r["project"]})
    seen, uniq = set(), []
    for v in out:
        if v["project"] not in seen:
            seen.add(v["project"])
            uniq.append(v)
    return uniq


def risks_from(events: list[dict]) -> list[dict]:
    seen, out = set(), []
    for e in events:
        if e["type"] == "tool_result" and e["name"] == "trace_risk" and "risk_score" in e["output"]:
            if e["output"]["project_id"] not in seen:
                seen.add(e["output"]["project_id"])
                out.append(e["output"])
    return out


# --------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("### Buyer")
    existing = sorted(set(list_buyer_graphs()) | {ss.buyer_id})
    choice = st.selectbox("Memory graph", existing + ["+ new buyer"], index=existing.index(ss.buyer_id),
                          help="Each buyer has an isolated FalkorDB graph for their memory.")
    if choice == "+ new buyer":
        new_name = st.text_input("Buyer name")
        if st.button("Create buyer") and new_name.strip():
            ss.buyer_name = new_name.strip()
            ss.buyer_id = "".join(c for c in new_name.lower() if c.isalnum()) or "buyer"
            open_session()
            st.rerun()
    elif choice != ss.buyer_id:
        ss.buyer_id, ss.buyer_name = choice, choice.title()
        open_session()
        st.rerun()

    if st.button("Start a new session", use_container_width=True,
                 help="Simulates the buyer coming back later. Khoj re-checks their shortlist against the graph."):
        open_session()
        st.rerun()

    st.markdown("### Something changes in the market")
    preset = st.selectbox("Event", list(live_events.PRESETS), format_func=lambda k: live_events.PRESETS[k]["label"],
                          label_visibility="collapsed")
    if st.button("Add this event to the graph", use_container_width=True):
        res = live_events.fire(preset)
        if "error" in res:
            st.error(res["error"])
        else:
            st.success("Added. Start a new session to see whether Khoj catches it on your shortlist.")

    st.markdown("### Agents")
    mode = st.radio("Mode", ["team", "single"], index=0 if ss.mode == "team" else 1, horizontal=True,
                    format_func=lambda m: {"team": "Concierge + Investigator", "single": "Single agent"}[m],
                    label_visibility="collapsed")
    if mode != ss.mode:
        ss.mode = mode
        open_session()
        st.rerun()

    if LIVE_OK and REPLAY_OK:
        run = st.radio("How Khoj answers", ["live", "replay"], index=0 if ss.run == "live" else 1,
                       format_func=lambda m: {"live": "Live agents", "replay": "Recorded runs"}[m],
                       help="Recorded runs replay real agent sessions saved earlier: no API key, no waiting.")
        if run != ss.run:
            ss.run = run
            open_session()
            st.rerun()
    elif ss.run == "replay":
        st.caption("Showing recorded runs: real agent sessions saved earlier and played back step by step.")

    with st.expander("Demo controls"):
        if st.button("Reset demo data", help="Reloads the knowledge graph and clears this buyer's memory."):
            load(verbose=False)
            memory.reset_buyer(ss.buyer_id)
            open_session()
            st.rerun()
        st.caption(f"Graph `{settings.kg_graph}`. Models: {settings.concierge_model} (Concierge), "
                   f"{settings.investigator_model} (Investigator). Graph tools via {mcp_client.transport()}.")

if ss.run == "live" and ss.ks is None:
    open_session()

# --------------------------------------------------------------------------- header
st.markdown(
    '<div class="khoj-head"><h1 class="khoj-title">Khoj</h1>'
    '<span class="khoj-sub">Checks a Bangalore launch the way a careful lawyer would: by following who is '
    'connected to whom, and remembering what you care about.</span></div>'
    '<div class="khoj-note">Demo data is synthetic. Every developer, company, person and case number is fictional; '
    'only locality names are real.</div>',
    unsafe_allow_html=True,
)

left, right = st.columns([11, 9], gap="large")

# --------------------------------------------------------------------------- chat
with left:
    for m in ss.chat:
        with st.chat_message(m["role"], avatar=USER_AVATAR if m["role"] == "user" else KHOJ_AVATAR):
            if m.get("verdicts"):
                st.markdown("".join(ui.stamp(v["verdict"], v["score"], v["project"]) for v in m["verdicts"]),
                            unsafe_allow_html=True)
            st.markdown(m["text"])
            lines = [l for l in (ui.describe_event(e) for e in m.get("events", [])) if l]
            if lines:
                with st.expander(f"How Khoj got here ({len(lines)} steps)"):
                    for l in lines:
                        st.markdown(f'<div class="trace-line">{l}</div>', unsafe_allow_html=True)

    if not ss.chat:
        recorded = [rec["question"] for rec in RECORDINGS.values()]
        examples = EXAMPLES if ss.run == "live" else (
            [q for q in EXAMPLES if replay.find(q)] + [q for q in recorded if q not in EXAMPLES])
        if ss.run == "replay":
            st.markdown("These are recorded runs of the real agents. Pick one to replay it step by step:")
        else:
            st.markdown(f"Ask about any project, {ss.buyer_name}. Try one of these:")
        cols = st.columns(2)
        for i, ex in enumerate(examples):
            if cols[i % 2].button(ex, key=f"ex{i}", use_container_width=True):
                ss.pending = ex
                st.rerun()
        if ss.run == "replay" and MEMORY_SCENARIO:
            if st.button("Replay the memory demo: shortlist, a market change, and the next session",
                         use_container_width=True):
                ss.pending = MEMORY_DEMO
                st.rerun()

prompt = st.chat_input("Ask about a project, a developer, or tell Khoj what you're looking for")
if ss.pending and not prompt:
    prompt, ss.pending = ss.pending, None

def run_turn(question: str | None, runner) -> None:
    """Show a turn live: the question, the agents' steps as they happen, then the answer."""
    with left:
        if question:
            ss.chat.append({"role": "user", "text": question})
            with st.chat_message("user", avatar=USER_AVATAR):
                st.markdown(question)
        with st.chat_message("assistant", avatar=KHOJ_AVATAR):
            status = st.status("Khoj is working on it…", expanded=True)

            def on_event(e: dict) -> None:
                line = ui.describe_event(e)
                if line:
                    status.markdown(f'<div class="trace-line">{line}</div>', unsafe_allow_html=True)

            try:
                result = runner(on_event)
                n_q = sum(len(e.get("cypher") or []) for e in result["events"] if e.get("type") == "tool_result")
                status.update(label=f"Done: {n_q} graph queries" if n_q else "Done", state="complete", expanded=False)
                ss.chat.append({"role": "assistant", "text": result["text"], "events": result["events"],
                                "graph": result["graph"], "verdicts": result.get("verdicts") or verdicts_from(result["events"]),
                                "risks": risks_from(result["events"])})
            except Exception as e:  # noqa: BLE001
                status.update(label="Something went wrong", state="error")
                hint = " Switch to *Recorded runs* in the sidebar to see the agents work." if REPLAY_OK else ""
                ss.chat.append({"role": "assistant", "text": f"The live agents hit an error and stopped: `{e}`.{hint}",
                                "events": []})


def live_runner(question: str):
    def run(on_event):
        r = ss.ks.ask(question, on_event)
        return {"text": r.text, "events": r.events, "graph": r.graph}
    return run


if prompt == MEMORY_DEMO and MEMORY_SCENARIO:
    for step in MEMORY_SCENARIO["steps"]:
        if step["kind"] == "ask":
            run_turn(step["question"], lambda on_event, st_=step: replay.play(st_, on_event))
        elif step["kind"] == "event":
            ss.chat.append({"role": "assistant", "events": [],
                            "text": f"*A week later, the market changes:* {step['label']}. The buyer opens a new session."})
        elif step["kind"] == "welcome":
            verdicts = [{"verdict": ui.BAND_TO_VERDICT.get(a["band_now"], "CAUTION"), "score": a["risk_now"],
                         "project": a["project"]} for a in step["alerts"]]
            ss.chat.append({"role": "assistant", "text": step["result"]["text"], "events": [],
                            "graph": step["result"]["graph"], "verdicts": verdicts, "risks": []})
    st.rerun()
elif prompt and ss.run == "replay":
    rec = replay.find(prompt)
    if rec:
        run_turn(prompt, lambda on_event: replay.play(rec, on_event))
    else:
        ss.chat += [{"role": "user", "text": prompt},
                    {"role": "assistant", "events": [], "text": "This public demo is playing recorded runs, so it can "
                     "only replay the example questions. Pick one of them, or run Khoj locally with an API key to ask "
                     "anything."}]
    st.rerun()
elif prompt:
    if ss.live_count >= settings.max_live_questions:
        ss.chat += [{"role": "user", "text": prompt},
                    {"role": "assistant", "events": [], "text": "This demo session has reached its limit of live "
                     "questions." + (" Switch to *Recorded runs* in the sidebar to keep exploring." if REPLAY_OK else "")}]
    elif ss.ks is None:
        ss.chat += [{"role": "user", "text": prompt},
                    {"role": "assistant", "events": [], "text": "Live agents aren't available: no API key is set."}]
    else:
        ss.live_count += 1
        run_turn(prompt, live_runner(prompt))
    st.rerun()

# --------------------------------------------------------------------------- right panel
last = next((m for m in reversed(ss.chat) if m["role"] == "assistant" and m.get("graph", {}).get("nodes")), None)
last_any = next((m for m in reversed(ss.chat) if m["role"] == "assistant"), None)

with right:
    t_graph, t_risk, t_trace, t_mem = st.tabs(["Evidence graph", "Risk score", "Agent trace", "Buyer memory"])

    with t_graph:
        if last:
            st.iframe(ui.graph_html(last["graph"], height=520), height=530)
            st.markdown(ui.LEGEND, unsafe_allow_html=True)
            st.caption("Every red node is a red flag the agent reached by following relationships in FalkorDB. "
                       "Drag nodes to rearrange; hover for details.")
        else:
            st.info("Ask about a project and the evidence the agent followed will appear here.")

    with t_risk:
        risks = [r for m in reversed(ss.chat) if m["role"] == "assistant" for r in m.get("risks", [])]
        if risks:
            shown = set()
            for r in risks:
                if r["project_id"] not in shown:
                    shown.add(r["project_id"])
                    st.markdown(ui.risk_card(r), unsafe_allow_html=True)
            with st.expander("How the score works"):
                st.markdown(
                    "Every red flag within 4 hops pushes risk onto the project along **every** path between them:\n\n"
                    "$K = \\sum_{\\text{paths } P} \\text{severity}(f)\\cdot\\beta^{d(P)}\\cdot\\prod_{r\\in P} w(r)$, "
                    "with $\\beta = 0.6$, then $\\text{score} = 100\\,(1-e^{-K})$. Here $d(P)$ is the path length minus one, "
                    "not counting title-chain hops: a buyer inherits the vendor's title.\n\n"
                    "This is Katz centrality restricted to red-flag targets. Summing over paths means two shared "
                    "directors count more than one. Paths may not pass through another project (its trouble is "
                    "already its own flag), and locality or lender links are ignored.")
        else:
            st.info("Risk scores appear here once a project has been traced.")

    with t_trace:
        if last_any and last_any.get("events"):
            for e in last_any["events"]:
                if e["type"] == "handoff":
                    st.markdown(f"**Concierge → Investigator**, case `{e['case_id']}`")
                    st.caption(f"Question: {e['question']}")
                    st.code(e["brief"], language="text")
                elif e["type"] == "tool_call":
                    st.markdown(f"**{e['agent'].title()}** called `{e['name']}`")
                    st.json(e["input"], expanded=False)
                elif e["type"] == "tool_result":
                    status = e["output"].get("status") if isinstance(e.get("output"), dict) else None
                    if status:
                        st.markdown(f'<div class="trace-line">↳ result {ui.badge(status)}</div>',
                                    unsafe_allow_html=True)
                    if e.get("cypher"):
                        with st.expander(f"Cypher behind {e['name']}"):
                            for q in e["cypher"]:
                                st.code(q, language="cypher")
                elif e["type"] == "grounding_warning":
                    st.warning(f"Grounding check: unverified project name(s) in the answer: {', '.join(e['names'])}")
                elif e["type"] == "case_closed":
                    st.markdown(f"**Investigator → Concierge**, findings read back from the graph")
                    st.json(e["findings"], expanded=False)
        else:
            st.info("Each tool call, the Cypher behind it, and the handoff between agents show up here.")

    with t_mem:
        r = memory.recall(ss.buyer_id, events=0)
        mg = memory.memory_graph(ss.buyer_id)
        if len(mg["nodes"]) > 1:
            st.iframe(ui.graph_html(mg, height=380, directed=True, layout="tree"), height=390)
            st.caption(f"Graph `{settings.buyer_graph_prefix}{ss.buyer_id}`, isolated from every other buyer. "
                       "Dashed nodes are superseded preferences, kept as history.")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Preferences**")
            for p in r["preferences"]:
                prev = f" (was {', '.join(p['previously'])})" if p["previously"] else ""
                st.markdown(f"- {p['key']}: {p['value']}{prev}")
            if not r["preferences"]:
                st.caption("None yet. Tell Khoj your budget, BHK and localities.")
            st.markdown("**Rules and concerns**")
            for x in r["rules"] + r["concerns"]:
                st.markdown(f"- {x}")
        with c2:
            st.markdown("**Shortlist**")
            for s in r["shortlist"]:
                st.markdown(f"- {s['name']}: risk {s['last_risk']:.0f} (was {s['risk_at_add']:.0f} when added)")
            if not r["shortlist"]:
                st.caption("Empty. Say “shortlist it” after a check.")
            st.markdown("**Decided against**")
            for x in r["rejected"]:
                st.markdown(f"- {x['name']}: {x['reason']}")
        st.caption(f"Session {r['sessions_so_far']} for {r['buyer']}.")
