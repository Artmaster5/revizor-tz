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
from dataclasses import replace
from typing import Any

from .model import Question, Requirement, Result
from .textfmt import count

# Kinds of requirement that an acceptance criterion is expected to check.
COVERABLE = ("functional", "nonfunctional", "data", "integration", "api", "ui", "process")
_CRIT_SECTION = re.compile(r"при[её]мк|acceptance", re.I)
RULE_NO_CRITERION = "requirement-without-criterion"
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


def written_detail(text: str) -> str:
    """What stands after the first colon of a readiness text, without the «(предварительно)» tail: «3 блокера»."""
    t = (text or "").replace(" (предварительно)", "")
    return t.split(": ", 1)[1] if ": " in t else t


def _ids(fids: list[str], cap: int = 3) -> str:
    return ", ".join(fids[:cap]) + (f" и ещё {len(fids) - cap}" if len(fids) > cap else "")


def compute(result: Result) -> dict[str, Any]:
    """Both kinds of readiness for a checked document; {} for a result with a diagnostic (nothing was checked).

    When the author's answers were applied (``verdict_after_answers`` is set) the same two verdicts are also computed
    on the findings that stay open after the answers and put under ``"after_answers"``: a finding the answers closed
    does not count; one whose fix check says «remains» was reopened by the check and counts."""
    if result.diagnostic is not None:
        return {}
    out = _compute(result)
    if result.verdict_after_answers and out:
        live = [f for f in result.findings if not f.closed_by_answer]
        out["after_answers"] = _compute(replace(result, findings=live))
    return out


def _compute(result: Result) -> dict[str, Any]:
    from . import consistency  # local: consistency imports this module's neighbours
    blockers = [f.fid for f in consistency.blockers(result.findings)]
    t = consistency.tally(result.findings)
    prelim = " (предварительно)" if result.run.mode == "offline" else ""
    if blockers:
        dev_text = f"Нельзя отдавать в разработку: {count(len(blockers), ('блокер', 'блокера', 'блокеров'))}"
    else:
        dev_text = "Можно отдавать в разработку: блокеров нет"
    dev = {"ok": not blockers, "text": dev_text + prelim, "blockers": blockers}

    cov = coverage(result)
    pct, unc = cov["pct"], cov["uncovered"]
    if cov["state"] == "none":
        ok, text = False, "Нельзя отдавать в тестирование: в документе нет критериев приёмки"
    elif cov["state"] == "none_to_check":
        ok, text = False, "Пока нельзя отдавать в тестирование: нет требований, для которых нужны критерии"
    elif cov["state"] == "unlinked":
        ok, text = False, "Пока нельзя отдавать в тестирование: критерии не названы по требованиям, долю покрытия не посчитать"
    elif unc:
        ok = False
        text = (f"Нельзя отдавать в тестирование: критериями приёмки покрыто {pct}% требований "
                f"({len(cov['covered'])} из {cov['eligible']}), остальные — в списке без критерия")
    else:
        ok = not blockers
        text = ("Можно отдавать в тестирование: критерии приёмки есть для всех требований" if ok else
                "Пока нельзя отдавать в тестирование: критерии есть для всех требований, но в документе есть блокеры")
    test = {"ok": ok, "text": text + prelim, "coverage_pct": pct, "uncovered": unc}
    return {"dev": dev, "test": test}


# --- «как проверим?»: one grouped question ---------------------------------------------------------------------------------------


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


GROUP_CAP = 10  # requirements named in the text of the grouped question; the rest is in the details file
_GROUP_TAIL = "Как проверим каждое из них?"
_GROUP_TAIL_ONE = "Как проверим его?"
_GROUP_HEAD = re.compile(r"^Для \d+ требовани\w+ не назван критерий приёмки\. Как проверим (?:каждое из них|его)\?")
_LEAD_JUNK = re.compile(r"^[\s*_`#>|•\-–—\d.):]+")


def _genitive(n: int) -> str:
    return "требования" if n % 10 == 1 and n % 100 != 11 else "требований"


def requirement_label(r: Requirement) -> str:
    """The requirement as its author can find it: its own number from the document («Ф-1»), else «строка 14: первые
    слова…». The tool's own numbers (REQ-031) are never shown: the author has not seen them."""
    if (r.doc_id or "").strip():
        return r.doc_id.strip().rstrip(".")
    src = (r.quote or r.summary or "")
    src = _LEAD_JUNK.sub("", src.replace("**", "").replace("__", ""))
    words = src.split()
    shown = " ".join(words[:7])
    if len(shown) > 60:
        shown = shown[:60].rsplit(" ", 1)[0]
    more = len(shown.split()) < len(words)
    return f"строка {r.line_start}: «{shown}{'…' if more else ''}»" if shown else f"строка {r.line_start}"


def group_headline(n: int) -> str:
    return f"Для {n} {_genitive(n)} не назван критерий приёмки. " + (_GROUP_TAIL if n > 1 else _GROUP_TAIL_ONE)


def group_text(labels: list[str]) -> str:
    """«Для N требований не назван критерий приёмки. Как проверим каждое из них? Ф-1; Ф-2 … и ещё K — полный список в
    подробностях.»"""
    shown = labels[:GROUP_CAP]
    text = group_headline(len(labels)) + " " + "; ".join(shown)
    if len(labels) > GROUP_CAP:
        text += f" и ещё {len(labels) - GROUP_CAP} — полный список в подробностях"
    return text + "."


def is_group_question(q: Question) -> bool:
    return bool(_GROUP_HEAD.match(q.text or ""))


def short_text(q: Question) -> str:
    """The text for a narrow place (a table cell, a one-line summary): the grouped question without its list."""
    m = _GROUP_HEAD.match(q.text or "")
    return m.group(0) if m else (q.text or "")


def group_labels(q: Question, result: Result) -> list[str]:
    """Every requirement the grouped question is about, as the author can find it (for the details file)."""
    by_rid = {r.rid: r for r in result.requirements}
    return [requirement_label(by_rid[x]) for x in q.req_ids if x in by_rid]


def add_check_questions(result: Result) -> int:
    """One grouped question «как проверим?» for the requirements that no acceptance criterion names and that no question
    about a criterion covers yet. Returns how many were added (0 or 1). Idempotent: a second call finds the question."""
    if result.diagnostic is not None:
        return 0
    cov = coverage(result)
    if cov["state"] != "ok":
        return 0
    from . import clarify  # local: clarify does not import this module, but keep the import graph flat
    by_rid = {r.rid: r for r in result.requirements}
    todo = [rid for rid in cov["uncovered"] if rid in by_rid and not _has_question(rid, result)]
    if not todo:
        return 0
    fids = [f.fid for f in result.findings if RULE_NO_CRITERION in f.rule_ids and f.status == "open"]
    q = Question(
        qid=f"В-{_next_qid(result.questions):02d}",
        text=group_text([requirement_label(by_rid[rid]) for rid in todo]),
        why="Ни один критерий приёмки в документе не называет эти требования: нечем подтвердить, что они выполнены.",
        addressee="qa",
        options=["Добавить критерии приёмки в документ", "Отдельные критерии не нужны — пояснить почему"],
        priority="nice", finding_ids=fids, req_ids=list(todo), letter_group="testing")
    default = clarify.RULE_ASSUMPTIONS[RULE_NO_CRITERION]
    q.assumption = default[:1].upper() + default[1:] + ("" if default.endswith(".") else ".")
    q.fingerprint = clarify.fingerprint(q)
    result.questions.append(q)
    return 1


__all__ = ["COVERABLE", "coverage", "compute", "add_check_questions", "GROUP_CAP", "requirement_label",
           "is_group_question", "short_text", "group_labels"]
