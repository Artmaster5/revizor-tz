"""What an answer changes: for every question, code recomputes the verdict and the readiness index on the findings
without the ones the question is linked to. Nothing is asked from the model."""
from __future__ import annotations

from . import score, verdict
from .model import Finding, Question, Requirement

PRIO_RANK = {"blocker": 0, "important": 1, "nice": 2}


def _index(requirements: list[Requirement], findings: list[Finding]) -> int | None:
    return score.readiness(score.clarities(requirements, findings), findings)[0]


def compute(q: Question, counted_open: list[Finding], requirements: list[Requirement], *,
            base_index: int | None, offline: bool = False) -> dict:
    open_ids = {f.fid for f in counted_open}
    closes = [fid for fid in q.finding_ids if fid in open_ids]
    rest = [f for f in counted_open if f.fid not in closes]
    by_fid = {f.fid: f for f in counted_open}
    after = _index(requirements, rest)
    delta = after - base_index if after is not None and base_index is not None else 0
    return {"closes": closes,
            "blockers_closed": sum(1 for fid in closes if by_fid[fid].severity == "CRITICAL"),
            "verdict_after": verdict.decide(rest, offline=offline)[0],
            "index_after": after,
            "index_delta": delta}


def apply(questions: list[Question], counted_open: list[Finding], requirements: list[Requirement], *,
          offline: bool = False, reorder: bool = False, findings: list[Finding] | None = None) -> None:
    """Fill ``Question.impact``. With ``reorder``, questions inside each priority group are ordered by impact (more
    blockers closed first, then a bigger index gain; ties keep the order) and renumbered В-01…; the findings'
    ``question_id`` follow the new numbers. Only questions without an impact are computed when ``reorder`` is False."""
    live = [f for f in counted_open if f.status == "open" and not f.advisory]
    base = _index(requirements, live)
    for q in questions:
        if reorder or not q.impact:
            q.impact = compute(q, live, requirements, base_index=base, offline=offline)
    if not reorder or not questions:
        return
    pos = {id(q): k for k, q in enumerate(questions)}
    questions.sort(key=lambda q: (PRIO_RANK.get(q.priority, 1), -q.impact.get("blockers_closed", 0),
                                  -(q.impact.get("index_delta") or 0), pos[id(q)]))
    moved: dict[str, str] = {}
    for k, q in enumerate(questions, 1):
        new = f"В-{k:02d}"
        if q.qid and q.qid != new:
            moved[q.qid] = new
        q.qid = new
    if moved:
        for f in findings if findings is not None else counted_open:
            if f.question_id in moved:
                f.question_id = moved[f.question_id]


def blocker_questions(questions: list[Question], counted_open: list[Finding]) -> tuple[list[str], list[str]]:
    """(ids of the questions linked to open CRITICAL findings, ids of open CRITICAL findings no question is linked to)."""
    crit = [f.fid for f in counted_open if f.severity == "CRITICAL"]
    qids = [q.qid for q in questions if any(fid in crit for fid in q.finding_ids)]
    asked = {fid for q in questions for fid in q.finding_ids}
    return qids, [fid for fid in crit if fid not in asked]
