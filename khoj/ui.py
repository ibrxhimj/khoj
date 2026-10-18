"""Presentation helpers for the Streamlit app: styles, the graph view and the
verdict stamp. Kept separate from app.py so the app reads as plain flow."""
from __future__ import annotations

import html
import json

PAPER = "#EEF1F4"
PAPER_2 = "#E2E7EE"
INK = "#1D2B4F"
INK_SOFT = "#4A5878"
RULE = "#C8D0DA"
SEAL = "#B3261E"
CAUTION = "#A8650F"
SAFE = "#2E7D5B"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700&family=Source+Serif+4:opsz,wght@8..60,500;8..60,650&display=swap');
html, body, [class*="css"], .stMarkdown, .stChatMessage, button, input, textarea {{
  font-family: 'Public Sans', system-ui, sans-serif;
}}
.stApp {{ background: {PAPER}; color: {INK}; }}
h1, h2, h3, .khoj-title {{ font-family: 'Source Serif 4', Georgia, serif; color: {INK}; letter-spacing: -0.01em; }}
.block-container {{ padding-top: 1.6rem; max-width: 1500px; }}
section[data-testid="stSidebar"] {{ background: {PAPER_2}; border-right: 1px solid {RULE}; }}
.khoj-head {{ display: flex; align-items: baseline; gap: 1rem; flex-wrap: wrap;
  border-bottom: 2px solid {INK}; padding-bottom: .5rem; margin-bottom: .9rem; }}
.khoj-title {{ font-size: 2.4rem; font-weight: 650; line-height: 1; margin: 0; }}
.khoj-sub {{ color: {INK_SOFT}; font-size: 1rem; max-width: 70ch; }}
.khoj-note {{ font-size: .82rem; color: {INK_SOFT}; border-left: 3px solid {RULE}; padding: .1rem .6rem; margin: .2rem 0 1rem; }}
.stamp {{ display: inline-flex; align-items: baseline; gap: .6rem; padding: .35rem .8rem .3rem;
  border: 3px double currentColor; border-radius: 6px; transform: rotate(-2.5deg);
  font-family: 'Source Serif 4', Georgia, serif; margin: .2rem .6rem .6rem 0; background: rgba(255,255,255,.35); }}
.stamp .v {{ font-size: 1.25rem; font-weight: 650; letter-spacing: .08em; text-transform: uppercase; }}
.stamp .s {{ font-size: .95rem; font-weight: 500; }}
.stamp .p {{ font-family: 'Public Sans', sans-serif; font-size: .78rem; opacity: .85; }}
.stamp.AVOID {{ color: {SEAL}; }} .stamp.CAUTION {{ color: {CAUTION}; }} .stamp.SAFE {{ color: {SAFE}; }}
.riskcard {{ border: 1px solid {RULE}; border-radius: 4px; padding: .7rem .9rem; margin-bottom: .8rem; background: #F7F9FB; }}
.riskcard h4 {{ margin: 0 0 .3rem; font-family: 'Source Serif 4', serif; font-size: 1.1rem; }}
.bar {{ height: 8px; background: {PAPER_2}; border-radius: 4px; overflow: hidden; margin: .3rem 0 .5rem; }}
.bar > div {{ height: 100%; }}
.ev {{ font-size: .84rem; padding: .3rem 0; border-top: 1px dashed {RULE}; }}
.ev b {{ color: {INK}; }}
.ev .path {{ color: {INK_SOFT}; font-size: .78rem; }}
.trace-line {{ font-size: .85rem; padding: .15rem 0; }}
.legend span {{ display: inline-flex; align-items: center; gap: .3rem; margin-right: .9rem; font-size: .78rem; color: {INK_SOFT}; }}
.legend i {{ width: 10px; height: 10px; display: inline-block; border-radius: 2px; }}
div[data-testid="stChatMessage"] {{ background: #F7F9FB; border: 1px solid {RULE}; }}
.stButton > button {{ border: 1px solid {INK}; color: {INK}; background: transparent; border-radius: 4px; text-align: left; }}
.stButton > button:hover {{ background: {INK}; color: {PAPER}; border-color: {INK}; }}
.stButton > button:focus-visible {{ outline: 2px solid {SEAL}; outline-offset: 2px; }}
</style>
"""

BAND_TO_VERDICT = {"HIGH": "AVOID", "MEDIUM": "CAUTION", "LOW": "SAFE"}
VERDICT_COLOR = {"AVOID": SEAL, "CAUTION": CAUTION, "SAFE": SAFE}


def stamp(verdict: str, score: float | None, project: str) -> str:
    s = f"{score:.0f} / 100" if isinstance(score, (int, float)) and score >= 0 else ""
    return (f'<span class="stamp {verdict}"><span class="v">{html.escape(verdict)}</span>'
            f'<span class="s">{s}</span><span class="p">{html.escape(project)}</span></span>')


def risk_card(r: dict) -> str:
    verdict = BAND_TO_VERDICT.get(r["band"], "CAUTION")
    color = VERDICT_COLOR[verdict]
    rows = "".join(
        f'<div class="ev"><b>{e["share_pct"]:.0f}%</b> &nbsp;{html.escape(e["reason"] or "")}'
        f'{" &nbsp;(" + str(e["n_paths"]) + " paths)" if e["n_paths"] > 1 else ""}'
        f'<div class="path">{html.escape(e["path_text"])}</div></div>'
        for e in r.get("top_evidence", [])[:6]
    )
    return (f'<div class="riskcard"><h4>{html.escape(r["project"])}</h4>'
            f'<div style="display:flex;justify-content:space-between;font-size:.9rem">'
            f'<span style="color:{color};font-weight:600">{r["risk_score"]:.0f}/100, {r["band"].lower()} risk</span>'
            f'<span style="color:{INK_SOFT}">{r["n_flags"]} red flags, {r["n_paths"]} paths</span></div>'
            + (f'<div style="color:{SEAL};font-size:.8rem;font-weight:600">Search truncated at the path limit: '
               f'this score is a lower bound and the project cannot be called safe.</div>' if r.get("truncated") else
               f'<div style="color:{INK_SOFT};font-size:.78rem">Complete search: every path within 4 hops counted.</div>')
            + (f'<div style="color:{CAUTION};font-size:.8rem">No known connection to a red flag in the graph. '
               f'That is not the same as safe.</div>' if not r["n_flags"] else '')
            + ''
            f'<div class="bar"><div style="width:{max(2, r["risk_score"])}%;background:{color}"></div></div>'
            f'{rows or "<div class=ev>No red flags within 4 hops.</div>"}</div>')


KIND_STYLE = {
    # kind: (shape, background, border, font color)
    "Project": ("box", INK, INK, "#FFFFFF"),
    "Company": ("ellipse", "#FFFFFF", INK, INK),
    "Group": ("ellipse", PAPER_2, INK_SOFT, INK),
    "Person": ("dot", "#7C8DB0", "#5A6B8F", INK),
    "LandParcel": ("square", "#C9B98F", "#8E7D52", INK),
    "CourtCase": ("diamond", "#FFFFFF", SEAL, INK),
    "Complaint": ("dot", "#E9C9C6", SEAL, INK),
    "Contractor": ("hexagon", "#D9DEE6", INK_SOFT, INK),
    "Approval": ("dot", "#D7E8DF", SAFE, INK),
    "Lender": ("dot", "#D9DEE6", INK_SOFT, INK),
    "Locality": ("dot", "#D9DEE6", INK_SOFT, INK),
    # memory graph
    "Buyer": ("star", INK, INK, INK),
    "Session": ("dot", "#AEB9CC", "#7C8DB0", INK),
    "Preference": ("box", "#FFFFFF", SAFE, INK),
    "Concern": ("box", "#FFFFFF", CAUTION, INK),
    "Rule": ("box", "#FFFFFF", INK, INK),
    "ProjectRef": ("box", INK, INK, "#FFFFFF"),
    "Case": ("diamond", "#FFFFFF", INK, INK),
    "Finding": ("box", "#F7F9FB", INK_SOFT, INK),
}


def graph_html(graph: dict, height: int = 540, directed: bool = False, focus: str | None = None,
               layout: str = "force") -> str:
    """Render {nodes, edges} with vis-network. Red-flag nodes are drawn in seal red."""
    vis_nodes, vis_edges = [], []
    for n in graph.get("nodes", []):
        shape, bg, border, font = KIND_STYLE.get(n.get("kind"), ("dot", "#D9DEE6", INK_SOFT, INK))
        label = n.get("name") or n["id"]
        label = label if len(label) <= 30 else label[:28] + "…"
        node = {"id": n["id"], "label": label, "shape": shape, "title": f"{n.get('kind')}: {n.get('name')}",
                "color": {"background": bg, "border": border},
                "font": {"color": font, "face": "Public Sans, system-ui, sans-serif", "size": 15},
                "borderWidth": 1.5, "size": 14, "margin": 8}
        if n.get("flag"):
            node["color"] = {"background": SEAL, "border": "#7A1712"}
            node["font"]["color"] = "#FFFFFF" if shape in {"box", "ellipse", "square"} else SEAL
            node["borderWidth"] = 3
        if n.get("inactive"):
            node["color"] = {"background": PAPER_2, "border": RULE}
            node["font"]["color"] = "#8A94A8"
            node["shapeProperties"] = {"borderDashes": [4, 3]}
        if shape in {"dot", "diamond", "square", "hexagon", "star"} and node["font"]["color"] == "#FFFFFF":
            node["font"]["color"] = INK
        if focus and n["id"] == focus:
            node["borderWidth"] = 4
        vis_nodes.append(node)
    for e in graph.get("edges", []):
        hot = bool(e.get("hot"))
        vis_edges.append({"from": e["src"], "to": e["dst"], "label": e["type"].lower().replace("_", " "),
                          "font": {"size": 11, "color": SEAL if hot else INK_SOFT, "strokeWidth": 4,
                                   "strokeColor": PAPER, "face": "Public Sans, system-ui, sans-serif"},
                          "color": {"color": SEAL if hot else "#8A94A8"}, "width": 4 if hot else 1,
                          "arrows": {"to": {"enabled": directed, "scaleFactor": .5}},
                          "smooth": {"type": "continuous"}})
    # Escape "<" so text typed by a buyer can never close the <script> tag.
    data = json.dumps({"nodes": vis_nodes, "edges": vis_edges}).replace("<", "\\u003c")
    if layout == "tree":
        opts = ("{ layout: { hierarchical: { direction: 'UD', sortMethod: 'directed', levelSeparation: 95, "
                "nodeSpacing: 150, shakeTowards: 'roots' } }, physics: false, "
                "interaction: { hover: true, tooltipDelay: 120 } }")
    else:
        opts = ("{ physics: { solver: 'forceAtlas2Based', forceAtlas2Based: { gravitationalConstant: -70, "
                "springLength: 90 }, stabilization: { iterations: 250 } }, "
                "interaction: { hover: true, tooltipDelay: 120 }, layout: { improvedLayout: true } }")
    return f"""
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;600&display=swap" rel="stylesheet">
<style>html,body{{margin:0;padding:0;background:{PAPER};font-family:'Public Sans',system-ui,sans-serif}}</style>
<div id="g" style="height:{height - 4}px;background:{PAPER};border:1px solid {RULE};border-radius:4px"></div>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<script>
const d = {data};
const el = document.getElementById('g');
let net = null;
// Build only once the frame is visible: graphs inside hidden tabs start at zero width.
function build() {{
  if (net || el.clientWidth === 0) return;
  net = new vis.Network(el, {{nodes: new vis.DataSet(d.nodes), edges: new vis.DataSet(d.edges)}}, {opts});
  net.once('stabilizationIterationsDone', () => {{ net.setOptions({{ physics: false }}); net.fit(); }});
  setTimeout(() => net.fit(), 120);
}}
new ResizeObserver(() => {{ if (!net) build(); else if (el.clientWidth > 0) {{ net.redraw(); net.fit(); }} }}).observe(el);
build();
</script>"""


LEGEND = (
    '<div class="legend">'
    f'<span><i style="background:{INK}"></i>Project</span>'
    f'<span><i style="background:#FFFFFF;border:1px solid {INK}"></i>Company</span>'
    f'<span><i style="background:#7C8DB0;border-radius:50%"></i>Person</span>'
    f'<span><i style="background:#C9B98F"></i>Land</span>'
    f'<span><i style="background:#D9DEE6"></i>Contractor</span>'
    f'<span><i style="background:{SEAL}"></i>Red flag</span>'
    f'<span><i style="background:{SEAL};height:3px;width:16px;border-radius:0"></i>Strongest risk path</span>'
    '</div>'
)

TOOL_LABEL = {
    "search_projects": "Searched projects",
    "project_profile": "Read the project profile",
    "trace_risk": "Traced risk paths",
    "developer_network": "Mapped the developer's hidden group (WCC)",
    "key_people": "Ranked directors by betweenness",
    "land_title_check": "Checked the land title chain",
    "find_alternatives": "Looked for safer alternatives",
    "contractor_record": "Checked the contractor's track record",
    "recall_findings": "Checked earlier findings in buyer memory",
    "record_finding": "Wrote a finding to the case file",
    "remember": "Saved to buyer memory",
    "shortlist": "Updated the shortlist",
    "ask_investigator": "Handed the question to the Investigator",
}


STATUS_BADGE = {
    "ANSWERED_WITH_PATH": ("answered with path", INK),
    "ANSWERED_FROM_MEMORY": ("answered from memory", SAFE),
    "NOT_IN_GRAPH": ("no known connection", CAUTION),
    "UNKNOWN_ENTITY": ("unknown entity, nothing run", SEAL),
}


def badge(status: str | None) -> str:
    if status not in STATUS_BADGE:
        return ""
    label, color = STATUS_BADGE[status]
    return (f'<span style="border:1px solid {color};color:{color};border-radius:3px;padding:0 .35rem;'
            f'font-size:.72rem;margin-left:.4rem;white-space:nowrap">{label}</span>')


def describe_event(e: dict) -> str | None:
    who = {"concierge": "Concierge", "investigator": "Investigator", "khoj": "Khoj"}.get(e["agent"], e["agent"])
    if e["type"] == "tool_call":
        inp = e["input"]
        arg = inp.get("project_id") or inp.get("name") or inp.get("locality") or inp.get("value") or ""
        if e["name"] == "ask_investigator":
            return None
        arg_html = f" (<code>{html.escape(str(arg))}</code>)" if arg else ""
        return f"<b>{who}</b>: {TOOL_LABEL.get(e['name'], e['name'])}{arg_html}"
    if e["type"] == "handoff":
        return f"<b>Concierge → Investigator</b>: opened case <code>{e['case_id']}</code> in the buyer's graph"
    if e["type"] == "case_closed":
        n = len(e["findings"])
        r = len(e.get("rejected_drafts") or [])
        rej = f", {r} draft{'s' if r != 1 else ''} rejected by the verifier" if r else ""
        return (f"<b>Investigator → Concierge</b>: {n} verified finding{'s' if n != 1 else ''} in case "
                f"<code>{e['case_id']}</code>{rej}")
    if e["type"] == "tool_result" and isinstance(e.get("output"), dict):
        out = e["output"]
        if out.get("error"):
            return f"&nbsp;&nbsp;↳ error: {html.escape(out['error'][:140])}"
        if e["name"] == "record_finding":
            if out.get("status") == "REJECTED":
                return (f"&nbsp;&nbsp;↳ <b style='color:{SEAL}'>Verifier rejected the draft</b>: "
                        f"{html.escape(str(out.get('detail') or out.get('reason')))}")
            if out.get("status") == "VERIFIED":
                fix = f" ({html.escape('; '.join(out['corrections']))})" if out.get("corrections") else ""
                return f"&nbsp;&nbsp;↳ <b style='color:{SAFE}'>Verified against the graph</b>{fix}"
        if out.get("status") and e["name"] not in {"ask_investigator", "remember", "shortlist", "record_finding"}:
            return f"&nbsp;&nbsp;↳ {badge(out['status'])}"
    if e["type"] == "grounding_warning":
        bits = []
        if e.get("names"):
            bits.append("unverified names: " + ", ".join(e["names"]))
        if e.get("scores"):
            bits.append("unmatched scores: " + ", ".join(map(str, e["scores"])))
        return f"<b>Grounding check</b>: {html.escape('; '.join(bits))}"
    return None
