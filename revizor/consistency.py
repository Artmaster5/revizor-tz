"""The report never contradicts itself.

The renderers, the JSON twin and the pipeline take every count, mark, blocker list and question priority from the
functions here, so the head of the report, its body and the JSON cannot disagree. ``settle`` brings a result into a
consistent state (the pipeline calls it at the end, ``tools/rerender.py`` calls it on an old JSON report);
``check_consistency`` lists what is still wrong — the pipeline puts any line it returns into the run's warnings.

Invariants (each has a check below):
1. level and note — the last entry of a finding's level trail is the card's level;
2. marks — ⚠️ for a requirement and a criterion exactly when an open counted finding names the requirement and maps
   to the criterion (``MARK_CRITERION``);
3. clarity — the stored clarity is the one ``score.clarity_of`` gives on the open findings;
4. strengths — none names a requirement with an open CRITICAL or MAJOR finding or quotes a place a finding quotes;
5. head = body — the blockers at the top are the open counted CRITICAL findings in document order, and the counts come
   from ``tally``;
6. questions — «блокирующий» exactly while linked to an open CRITICAL finding;
7. links — every in-report link points to an anchor of the same file.
"""
from __future__ import annotations

import re
from typing import Any

from . import impact as impact_mod, score, verdict
from .merge import SEV_RANK, sort_key
from .model import REQ_CRITERIA, CriterionVerdict, Finding, Question, RequirementReview, Result
from .quotes import normalize

# A finding of the set stage marks the per-requirement criterion it speaks about; traceability has no such mark
# (its findings are listed under the requirement as «затронуто»).
MARK_CRITERION: dict[str, str] = {c: c for c in REQ_CRITERIA}
MARK_CRITERION.update({"set_consistency": "consistency", "set_completeness": "completeness",
                       "set_feasibility": "feasibility", "comprehensibility": "unambiguity"})

LEVEL_WORD = {"CRITICAL": "блокер", "MAJOR": "существенное", "MINOR": "мелкое"}
BLOCKER_SHORT = {"contradiction": "противоречие", "main_flow": "основной сценарий не определён",
                 "protocol": "нарушение протокола", "loss": "потеря денег или данных",
                 "missing_part": "нет обязательной части"}


# --- counts and lists every part of the report takes ---------------------------------------------------------


def counted(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.status == "open" and not f.advisory]


def tally(findings: list[Finding]) -> dict[str, int]:
    """Open counted findings by level, plus the total. The head, section 5 and the JSON summary use this."""
    c = counted(findings)
    out = {s: sum(1 for f in c if f.severity == s) for s in ("CRITICAL", "MAJOR", "MINOR")}
    out["total"] = len(c)
    return out


def blockers(findings: list[Finding]) -> list[Finding]:
    """Open counted CRITICAL findings in document order: the list at the top of the report and in sections 5 and 6."""
    return sorted((f for f in counted(findings) if f.severity == "CRITICAL"), key=sort_key)


def blocker_word(f: Finding) -> str:
    return BLOCKER_SHORT.get(f.blocker_reason, "основание не указано")


# --- marks -----------------------------------------------------------------------------------------------


def linked(rid: str, findings: list[Finding]) -> list[Finding]:
    """Open counted findings that name the requirement (``req_ids``), in document order."""
    return sorted((f for f in counted(findings) if rid in f.req_ids), key=sort_key)


def marks_of(rid: str, findings: list[Finding]) -> tuple[dict[str, list[Finding]], list[Finding]]:
    """({criterion: findings that set ⚠️}, findings that only touch the requirement — traceability)."""
    by_crit: dict[str, list[Finding]] = {}
    touched: list[Finding] = []
    for f in linked(rid, findings):
        c = MARK_CRITERION.get(f.criterion)
        if c is None:
            touched.append(f)
        else:
            by_crit.setdefault(c, []).append(f)
    return by_crit, touched


def apply_marks(result: Result) -> None:
    """The per-requirement verdicts follow the open findings: ⚠️ with a linked finding, never without one."""
    reviews = {rv.rid: rv for rv in result.reviews}
    for r in result.requirements:
        rv = reviews.get(r.rid)
        if rv is None:
            rv = RequirementReview(rid=r.rid, criteria={c: CriterionVerdict("unchecked") for c in REQ_CRITERIA})
            result.reviews.append(rv)
            reviews[r.rid] = rv
        by_crit, _touched = marks_of(r.rid, result.findings)
        for c in REQ_CRITERIA:
            cur = rv.criteria.get(c) or CriterionVerdict("unchecked")
            hits = by_crit.get(c)
            if hits:
                rv.criteria[c] = CriterionVerdict("issue", hits[0].title[:300])
            elif cur.status == "issue":  # the model saw a problem that did not survive the quote check or the re-check
                rv.criteria[c] = CriterionVerdict("ok", "")
            else:
                rv.criteria[c] = cur


# --- level trail -----------------------------------------------------------------------------------------

_RECHECK = re.compile(r"уровень изменён перепроверкой:\s*(CRITICAL|MAJOR|MINOR)\s*→\s*(CRITICAL|MAJOR|MINOR)\.?\s*(.*)",
                      re.S)


def rebuild_trail(f: Finding, merged_into_it: list[Finding]) -> None:
    """An older report has no trail: rebuild it from the re-check's note and the findings merged into this one."""
    if f.level_trail:
        return
    m = _RECHECK.search(f.status_reason or "")
    if m:
        f.level_trail = [{"by": "model", "level": m.group(1)},
                         {"by": "skeptic", "level": m.group(2), **({"why": m.group(3).strip()[:300]} if m.group(3).strip() else {})}]
    if f.level_trail and f.level_trail[-1]["level"] != f.severity:
        donor = next((g for g in merged_into_it if g.severity == f.severity), None)
        f.level_trail.append({"by": "merge", "level": f.severity, "why": "уровень взят по старшему из объединённых",
                              **({"with": donor.fid} if donor is not None and donor.fid else {})})


BY_RU = {"model": "модель", "rule": "правило", "skeptic": "перепроверка", "merge": "объединение", "code": "программа",
         "answer": "ответ автора"}


def trail_line(f: Finding) -> str:
    """One line: who set the level and why; "" when the level never changed. Ends with the card's level."""
    t = f.level_trail
    if len(t) < 2:
        return ""
    parts = []
    for k, e in enumerate(t):
        who = BY_RU.get(e.get("by", ""), e.get("by", ""))
        if e.get("by") == "merge" and e.get("with"):
            who = f"объединение с {e['with']}"
        part = f"{who}: {e.get('level', '')}"
        if k and e.get("why"):
            why = e["why"].rstrip(".")
            part += f" ({why[:150].rsplit(' ', 1)[0] + '…' if len(why) > 150 else why})"
        parts.append(part)
    return " → ".join(parts)


# --- questions -------------------------------------------------------------------------------------------


def question_priority(q: Question, by_fid: dict[str, Finding]) -> str:
    """«blocker» exactly while the question is linked to an open counted CRITICAL finding; otherwise «important» for
    a linked MAJOR and «nice» for the rest. A question without open links keeps a lower priority it had."""
    sevs = [by_fid[i].severity for i in q.finding_ids if i in by_fid]
    if "CRITICAL" in sevs:
        return "blocker"
    if "MAJOR" in sevs:
        return "important"
    if sevs:
        return "nice"
    return "important" if q.priority == "blocker" else q.priority


def settle_questions(questions: list[Question], findings: list[Finding]) -> int:
    """Recompute every question's priority from its open findings. Returns how many changed."""
    by_fid = {f.fid: f for f in counted(findings)}
    n = 0
    for q in questions:
        p = question_priority(q, by_fid)
        if p != q.priority:
            q.priority = p
            n += 1
    return n


# --- strengths -------------------------------------------------------------------------------------------

_FRAGMENT = re.compile(r"[«\"“]([^»\"”]{8,})[»\"”]")


def _roots(s: str) -> set[str]:
    return {w[:5] for w in re.findall(r"\w+", (s or "").lower().replace("ё", "е")) if len(w) > 3 and not w.isdigit()}


def strength_conflict(text: str, req_ids: list[str], result: Result) -> str:
    """Why a strength may not be printed ("" when it may): the place it praises has a finding."""
    c = counted(result.findings)
    for f in c:
        if f.severity in ("CRITICAL", "MAJOR") and set(f.req_ids) & set(req_ids):
            return f"требование названо в {f.fid}"
    frags = [normalize(m) for m in _FRAGMENT.findall(text or "")]
    words = _roots(text)
    for f in c:
        for q in f.quotes:
            nq = normalize(q.text)
            if any(fr and (fr in nq or nq in fr) for fr in frags):
                return f"цитата совпадает с {f.fid}"
            qr = _roots(q.text)
            if len(qr) >= 3 and len(qr & words) / len(qr) >= 0.6:
                return f"то же место, что в {f.fid}"
    by_fid = {f.fid: f for f in c}
    for cell in result.gap_map:
        if cell.status != "gap":
            continue
        for fid in cell.finding_ids:
            f = by_fid.get(fid)
            if f is not None and set(f.req_ids) & set(req_ids):
                return f"пробел {cell.id} указывает на {fid}"
    return ""


def settle_strengths(result: Result) -> list[str]:
    """Drop every strength that praises a place with a finding. Returns the reasons (for the audit)."""
    kept, dropped = [], []
    for s in result.strengths:
        why = strength_conflict(s.text, s.req_ids, result)
        if why:
            dropped.append(why)
        else:
            kept.append(s)
    result.strengths = kept
    return dropped


# --- settle and check -------------------------------------------------------------------------------------


def settle(result: Result) -> None:
    """Bring a result into a consistent state: trails, marks, clarity, readiness, verdict, question priorities,
    strengths. Idempotent; safe on a result read back from an older JSON report."""
    if result.diagnostic is not None:
        return
    merged: dict[str, list[Finding]] = {}
    for g in result.rejected:
        if g.status == "merged" and g.merged_into:
            merged.setdefault(g.merged_into, []).append(g)
    for f in result.findings:
        rebuild_trail(f, merged.get(f.fid, []))
        if not f.also and f.stage != "fold_singularity":  # a folded remark lists its places, not titles
            f.also = [g.title for g in merged.get(f.fid, []) if g.title and g.title != f.title][:12]
    result.findings.sort(key=sort_key)
    apply_marks(result)
    clar = score.clarities(result.requirements, result.findings)
    for rv in result.reviews:
        if rv.rid in clar:
            rv.clarity = clar[rv.rid]
    if result.requirements:
        result.run.score, result.run.score_formula = score.readiness(clar, result.findings)
    offline = result.run.mode == "offline"
    code, text = verdict.decide(result.findings, offline=offline)
    if code != result.verdict:
        result.verdict, result.verdict_text = code, text
    settle_questions(result.questions, result.findings)
    if any(not q.impact for q in result.questions):  # older reports: what each answer changes is computed here
        impact_mod.apply(result.questions, counted(result.findings), result.requirements, offline=offline)
    dropped = settle_strengths(result)
    if dropped:
        result.audit.append({"type": "decision", "what": "strengths_dropped", "count": len(dropped),
                             "reasons": dropped[:20]})
    result.run.manual_minutes_estimate = None


_LINK = re.compile(r"\]\(#([^)\s]+)\)")
_ANCHOR = re.compile(r'<a id="([^"]+)"></a>')
_HEAD_COUNTS = re.compile(r"блокеров (\d+) · существенных (\d+) · мелких (\d+)")


def check_consistency(result: Result, md_text: str | None = None) -> list[str]:
    """Every way the report could contradict itself, as Russian lines; [] when it is consistent."""
    if result.diagnostic is not None:
        return []
    out: list[str] = []
    c = counted(result.findings)
    by_fid = {f.fid: f for f in c}
    # 1. level and note
    for f in c:
        if f.level_trail and f.level_trail[-1].get("level") != f.severity:
            out.append(f"{f.fid}: уровень {f.severity}, а запись об уровне кончается на {f.level_trail[-1].get('level')}")
        m = _RECHECK.search(f.status_reason or "")
        if m and m.group(2) != f.severity and not f.level_trail:
            out.append(f"{f.fid}: уровень {f.severity}, а примечание говорит {m.group(2)}")
        if f.severity == "CRITICAL" and not f.blocker_reason:
            out.append(f"{f.fid}: блокер без основания")
    # 2. marks
    reviews = {rv.rid: rv for rv in result.reviews}
    for r in result.requirements:
        rv = reviews.get(r.rid)
        by_crit, _t = marks_of(r.rid, result.findings)
        for crit in REQ_CRITERIA:
            st = rv.criteria[crit].status if rv is not None and crit in rv.criteria else "unchecked"
            if by_crit.get(crit) and st != "issue":
                out.append(f"{r.rid}: {crit} без ⚠️, хотя есть {by_crit[crit][0].fid}")
            if not by_crit.get(crit) and st == "issue":
                out.append(f"{r.rid}: {crit} с ⚠️ без связанного замечания")
        # 3. clarity
        if rv is not None and rv.clarity is not None:
            want = score.clarity_of(r.rid, result.findings)
            if abs(want - rv.clarity) > 0.05:
                out.append(f"{r.rid}: ясность {rv.clarity}, по замечаниям {want}")
    # 4. strengths
    for s in result.strengths:
        why = strength_conflict(s.text, s.req_ids, result)
        if why:
            out.append(f"Сильная сторона «{s.text[:60]}»: {why}")
    # 5. verdict follows the blockers
    code, _t = verdict.decide(result.findings, offline=result.run.mode == "offline")
    if code != result.verdict:
        out.append(f"Вердикт {result.verdict}, а по замечаниям {code}")
    # 6. questions
    for q in result.questions:
        want = question_priority(q, by_fid)
        if (q.priority == "blocker") != (want == "blocker"):
            out.append(f"{q.qid}: приоритет {q.priority}, а по связанным замечаниям {want}")
    # rendered text: head = body, links
    if md_text is None:
        from . import report_md
        md_text = report_md.render(result)
    t = tally(result.findings)
    m = _HEAD_COUNTS.search(md_text)
    if m and tuple(int(x) for x in m.groups()) != (t["CRITICAL"], t["MAJOR"], t["MINOR"]):
        out.append("Числа в начале отчёта не совпадают с замечаниями")
    head = md_text.split('<a id="s1"></a>', 1)[0]
    listed = re.findall(r"^\d+\. \[(D-\d+)\]", head.split("**Блокеры", 1)[1], re.M) if "**Блокеры" in head else []
    listed = listed[:t["CRITICAL"] + 1] if listed else []
    want_ids = [f.fid for f in blockers(result.findings)]
    if listed[:len(want_ids)] != want_ids or (not want_ids and "**Блокеры" in head):
        out.append("Список блокеров в начале отчёта не совпадает с открытыми CRITICAL")
    anchors = set(_ANCHOR.findall(md_text))
    bad = sorted({a for a in _LINK.findall(md_text) if a not in anchors})
    if bad:
        out.append("Ссылки на несуществующие места отчёта: " + ", ".join(bad[:10]))
    return out


def fix_links(md_text: str) -> str:
    """A link to something the report does not print becomes plain text."""
    anchors = set(_ANCHOR.findall(md_text))

    def sub(m: re.Match) -> str:
        return m.group(0) if m.group(2) in anchors else m.group(1)

    return re.sub(r"\[([^\]\n]+)\]\(#([^)\s]+)\)", sub, md_text)


def severity_order(f: Finding) -> tuple[Any, ...]:
    return (SEV_RANK.get(f.severity, 9),) + sort_key(f)
