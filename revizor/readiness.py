"""Two kinds of readiness, decided by code: «можно отдавать в разработку» and «можно отдавать в тестирование».

Development: no open counted blocker. Testing: every requirement that can be checked is named by an acceptance
criterion of the document, and no blocker is open. The share of covered requirements is counted from what the pipeline
already knows: requirement ids, the requirements of kind «критерий приёмки» (and what sits in an acceptance section),
the model's coverage table, and the open findings of the rule «requirement-without-criterion». The existing verdict
(``verdict.py``) is not touched.

``compute`` returns {"dev": {"ok", "text", "blockers": [fids]},
                     "test": {"ok", "text", "coverage_pct": int | None, "uncovered": [requirement ids]}}.
"""
from __future__ import annotations

import re
from typing import Any

from .model import Question, Requirement, Result
from .textfmt import count

# Kinds of requirement that an acceptance criterion is expected to check.
COVERABLE = ("functional", "nonfunctional", "data", "integration", "api", "ui", "process")
_CRIT_SECTION = re.compile(r"при[её]мк|acceptance", re.I)
RULE_NO_CRITERION = "requirement-without-criterion"
QUESTION_CAP = 20  # «как проверим?» questions added in one run
_ASKED = re.compile(r"как проверим|критери\w* при[её]мк|чем подтвердим", re.I)
_RANGE_MAX = 60


def _is_criterion(r: Requirement) -> bool:
    return r.kind == "acceptance" or bool(_CRIT_SECTION.search(r.section or ""))


def _split_id(doc_id: str) -> tuple[str, int] | None:
    m = re.fullmatch(r"(\D+?)(\d+)", doc_id.strip().rstrip("."))
    return (m.group(1), int(m.group(2))) if m else None


def _named_in(text: str, targets: list[Requirement]) -> set[str]:
    """Requirement ids (REQ-…) of ``targets`` whose own number (doc_id) or REQ-number the text names; «Ф-1–Ф-5» too."""
    out: set[str] = set()
    t = text or ""
    for r in targets:
        for token in {r.rid, (r.doc_id or "").strip().rstrip(".")}:
            if token and re.search(r"(?<![\w\-])" + re.escape(token) + r"(?![\w]|\.\d)", t):
                out.add(r.rid)
    by_prefix: dict[str, dict[int, str]] = {}
    for r in targets:
        sp = _split_id(r.doc_id or "")
        if sp:
            by_prefix.setdefault(sp[0], {})[sp[1]] = r.rid
    for pre, nums in by_prefix.items():
        rx = re.compile(re.escape(pre) + r"(\d+)\s*[–—-]\s*(?:" + re.escape(pre) + r")?(\d+)")
        for m in rx.finditer(t):
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo < hi and hi - lo < _RANGE_MAX:
                out.update(nums[k] for k in range(lo, hi + 1) if k in nums)
    return out


def coverage(result: Result) -> dict[str, Any]:
    """{"state": "none" | "unlinked" | "none_to_check" | "ok", "pct": int | None, "covered": [rids],
    "uncovered": [rids], "eligible": n}.  «none»: the document has no acceptance criteria at all; «unlinked»: it has
    them, but they do not say which requirements they check, so no share can be counted."""
    reqs = result.requirements
    crit = [r for r in reqs if _is_criterion(r)]
    eligible = [r for r in reqs if r.kind in COVERABLE and not _is_criterion(r)]
    rule_missing = {rid for f in result.findings if RULE_NO_CRITERION in f.rule_ids and f.status == "open"
                    for rid in f.req_ids}
    out: dict[str, Any] = {"state": "ok", "pct": None, "covered": [], "uncovered": [], "eligible": len(eligible)}
    if not crit and not result.coverage:
        out["state"] = "none"
        return out
    if not eligible:
        out["state"] = "none_to_check"
        return out
    ids = {r.rid for r in eligible}
    covered: set[str] = set()
    for c in crit:
        covered |= _named_in(" ".join(x for x in (c.quote, c.summary, c.doc_id) if x), eligible)
    for row in result.coverage:
        covered |= {x for x in row.covers if x in ids}
    missing = rule_missing & ids
    if missing:
        # the rule only speaks when the criteria do link to numbers: everything it does not list is linked
        covered = ids - missing
    n = len(covered)
    if n < 3 and n / len(eligible) < 0.3 and not missing:
        out["state"] = "unlinked"
        return out
    out["covered"] = [r.rid for r in eligible if r.rid in covered]
    out["uncovered"] = [r.rid for r in eligible if r.rid not in covered]
    out["pct"] = round(100 * n / len(eligible))
    return out


def _ids(fids: list[str], cap: int = 6) -> str:
    return ", ".join(fids[:cap]) + (f" и ещё {len(fids) - cap}" if len(fids) > cap else "")


def compute(result: Result) -> dict[str, Any]:
    """Both kinds of readiness for a checked document; {} for a result with a diagnostic (nothing was checked)."""
    if result.diagnostic is not None:
        return {}
    from . import consistency  # local: consistency imports this module's neighbours
    blockers = [f.fid for f in consistency.blockers(result.findings)]
    t = consistency.tally(result.findings)
    prelim = " (предварительно: модель не использовалась)" if result.run.mode == "offline" else ""
    if blockers:
        dev_text = (f"Нельзя отдавать в разработку: {count(len(blockers), ('блокер', 'блокера', 'блокеров'))} — "
                    f"{_ids(blockers)}")
    elif t["MAJOR"]:
        dev_text = (f"Можно отдавать в разработку: блокеров нет; существенных замечаний {t['MAJOR']} — "
                    "их лучше закрыть до начала работы")
    else:
        dev_text = "Можно отдавать в разработку: блокеров нет"
    dev = {"ok": not blockers, "text": dev_text + prelim, "blockers": blockers}

    cov = coverage(result)
    pct, unc = cov["pct"], cov["uncovered"]
    blocked_tail = (f"; но в документе {count(len(blockers), ('блокер', 'блокера', 'блокеров'))} — сначала их устранить"
                    if blockers else "")
    if cov["state"] == "none":
        ok, text = False, ("Нельзя отдавать в тестирование: в документе нет критериев приёмки, "
                           "поэтому нечем подтвердить, что требования выполнены")
    elif cov["state"] == "none_to_check":
        ok, text = False, "Пока нельзя отдавать в тестирование: не найдено требований, для которых нужны критерии приёмки"
    elif cov["state"] == "unlinked":
        ok, text = False, ("Пока нельзя отдавать в тестирование: критерии приёмки есть, но в них не названо, какие "
                           "требования они проверяют, поэтому долю покрытия посчитать нельзя")
    elif unc:
        ok = False
        text = (f"Нельзя отдавать в тестирование: критериями приёмки покрыто {pct}% требований "
                f"({len(cov['covered'])} из {cov['eligible']}); без критерия остались {_ids(unc, 8)}")
    else:
        ok = not blockers
        text = (f"Можно отдавать в тестирование: критерии приёмки есть для всех требований "
                f"({cov['eligible']} из {cov['eligible']})" if ok else
                f"Пока нельзя отдавать в тестирование: критерии приёмки есть для всех требований{blocked_tail}")
    test = {"ok": ok, "text": text + prelim, "coverage_pct": pct, "uncovered": unc}
    return {"dev": dev, "test": test}


# --- «как проверим?» ---------------------------------------------------------------------------------------


def _next_qid(questions: list[Question]) -> int:
    n = 0
    for q in questions:
        m = re.fullmatch(r"В-(\d+)", q.qid or "")
        if m:
            n = max(n, int(m.group(1)))
    return n + 1


def _has_question(rid: str, result: Result) -> bool:
    """A question about how to check this requirement exists already: it names the requirement and asks about a
    criterion, or it is linked to the open finding of the rule that lists requirements without a criterion."""
    rule_fids = {f.fid for f in result.findings if RULE_NO_CRITERION in f.rule_ids}
    for q in result.questions:
        if rid not in q.req_ids:
            continue
        if _ASKED.search(q.text or "") or rule_fids & set(q.finding_ids):
            return True
    return False


def add_check_questions(result: Result) -> int:
    """For every requirement without an acceptance criterion, a question «как проверим?» unless one exists. Returns how
    many were added (at most ``QUESTION_CAP``). Idempotent: a second call finds the questions it added."""
    if result.diagnostic is not None:
        return 0
    cov = coverage(result)
    if cov["state"] != "ok":
        return 0
    by_rid = {r.rid: r for r in result.requirements}
    added = 0
    for rid in cov["uncovered"]:
        if added >= QUESTION_CAP:
            break
        if _has_question(rid, result):
            continue
        r = by_rid[rid]
        name = r.doc_id.rstrip(".") if r.doc_id else rid
        result.questions.append(Question(
            qid=f"В-{_next_qid(result.questions):02d}",
            text=f"Как проверим требование {name}: каким критерием приёмки подтвердить, что оно выполнено?",
            why="Ни один критерий приёмки в документе не называет это требование: нечем подтвердить, что оно выполнено.",
            addressee="qa",
            options=["Добавить критерий приёмки в документ", "Отдельный критерий не нужен — пояснить почему"],
            priority="nice", req_ids=[rid]))
        added += 1
    return added


__all__ = ["COVERABLE", "coverage", "compute", "add_check_questions", "QUESTION_CAP"]
