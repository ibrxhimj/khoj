"""Red-flag rules.

A red flag is any node that is, on its own, a warning sign: a stalled project,
an open complaint, a pending court case, an insolvent company, a B-khata plot,
a rejected approval. Each rule labels the node :RedFlag and gives it a
severity in (0, 1] plus a human-readable reason.

The rules are plain Cypher so they can be re-run whenever the graph changes
(for example when a new complaint is filed), and so anyone reviewing the model can read them.
"""
from .db import query

# Severity for pending court cases, by the nature of the case.
CASE_SEVERITY = {
    "title_dispute": 0.95,  # a title defect can void the purchase outright
    "fraud": 0.80,
    "insolvency": 0.75,
    "cheque_bounce": 0.30,
}

FLAG_RULES: list[tuple[str, str]] = [
    (
        "Stalled projects",
        """
        MATCH (p:Project) WHERE p.status = 'stalled'
        SET p:RedFlag, p.severity = 0.8,
            p.reason = 'Project stalled'
        """,
    ),
    (
        "Severely delayed projects",
        """
        MATCH (p:Project) WHERE p.status <> 'stalled' AND p.delay_months >= 24
        SET p:RedFlag, p.severity = 0.55,
            p.reason = 'Delivered/running ' + toString(p.delay_months) + ' months late'
        """,
    ),
    (
        "Delayed projects",
        """
        MATCH (p:Project) WHERE p.status <> 'stalled'
          AND p.delay_months >= 12 AND p.delay_months < 24
        SET p:RedFlag, p.severity = 0.35,
            p.reason = 'Running ' + toString(p.delay_months) + ' months late'
        """,
    ),
    (
        "Open complaints",
        """
        MATCH (c:Complaint) WHERE c.status = 'open'
        SET c:RedFlag, c.severity = 0.1,
            c.reason = 'Open ' + c.forum + ' complaint (' + c.category + ')'
        """,
    ),
    (
        "Pending court cases",
        """
        MATCH (k:CourtCase) WHERE k.status = 'pending'
        SET k:RedFlag,
            k.severity = CASE k.nature
                WHEN 'title_dispute' THEN $sev.title_dispute
                WHEN 'fraud' THEN $sev.fraud
                WHEN 'insolvency' THEN $sev.insolvency
                ELSE $sev.cheque_bounce END,
            k.reason = 'Pending ' + replace(k.nature, '_', ' ') + ' case in ' + k.court
        """,
    ),
    (
        "Insolvent or struck-off companies",
        """
        MATCH (c:Company) WHERE c.status IN ['insolvency', 'struck_off']
        SET c:RedFlag,
            c.severity = CASE c.status WHEN 'insolvency' THEN 0.8 ELSE 0.6 END,
            c.reason = 'Company ' + replace(c.status, '_', ' ')
        """,
    ),
    (
        "B-khata land",
        """
        MATCH (l:LandParcel) WHERE l.khata = 'B'
        SET l:RedFlag, l.severity = 0.45,
            l.reason = 'Land is B-khata (irregular, limits loans and resale)'
        """,
    ),
    (
        "Rejected approvals",
        """
        MATCH (a:Approval) WHERE a.status = 'rejected'
        SET a:RedFlag, a.severity = 0.5,
            a.reason = a.type + ' approval rejected'
        """,
    ),
]


def apply_flag_rules(graph) -> dict[str, int]:
    """(Re)apply every rule. Returns the number of red flags by node kind."""
    for _name, cypher in FLAG_RULES:
        graph.query(cypher, {"sev": CASE_SEVERITY})
    counts = query(graph, "MATCH (f:RedFlag) RETURN f.kind AS kind, count(f) AS n ORDER BY n DESC")
    return {r["kind"]: r["n"] for r in counts}
