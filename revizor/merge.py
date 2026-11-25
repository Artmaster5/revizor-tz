"""Model answers → verified findings; duplicates merged; rule findings folded in; ids; skeptic decisions.

Everything here is code-controlled: a model finding survives only with at least one quote that the
QuoteIndex found in the source text, and the printed quote is always the original source span.
"""
from __future__ import annotations

import re
from typing import Any

from .model import (BLOCKER_REASONS, GAP_TYPES, REQ_CRITERIA, SET_CRITERIA, SEVERITIES, DroppedQuote, Finding,
                    QuestionDraft, Quote)
from .quotes import QuoteIndex, normalize

SEV_RANK = {"CRITICAL": 0, "MAJOR": 1, "MINOR": 2}

# Criteria that describe the same kind of defect at requirement and at set level.
FAMILY = {
    "completeness": "complete", "set_completeness": "complete",
    "consistency": "consistent", "set_consistency": "consistent",
    "feasibility": "feasible", "set_feasibility": "feasible", "necessity": "feasible",
    "unambiguity": "clear", "comprehensibility": "clear", "singularity": "clear",
    "verifiability": "verifiable",
    "traceability": "trace",
}
SET_STAGE_DEFAULT = {"set_consistency": "set_consistency", "set_completeness": "set_completeness",
                     "set_standards": "set_feasibility"}

# Clause each finding rests on (addendum A6).
STANDARDS = {
    "completeness": "ISO/IEC/IEEE 29148:2018 п. 5.2.5 (Complete), IEEE 830-1998 п. 4.3.3",
    "unambiguity": "ISO/IEC/IEEE 29148:2018 п. 5.2.5 (Unambiguous), IEEE 830-1998 п. 4.3.2",
    "consistency": "IEEE 830-1998 п. 4.3.4 (внутренняя согласованность)",
    "verifiability": "ISO/IEC/IEEE 29148:2018 п. 5.2.5 (Verifiable), IEEE 830-1998 п. 4.3.6",
    "feasibility": "ISO/IEC/IEEE 29148:2018 п. 5.2.5 (Feasible)",
    "necessity": "ISO/IEC/IEEE 29148:2018 п. 5.2.5 (Necessary, Appropriate)",
    "singularity": "ISO/IEC/IEEE 29148:2018 п. 5.2.5 (Singular)",
    "set_completeness": "ISO/IEC/IEEE 29148:2018 п. 5.2.6 (Complete), IEEE 830-1998 п. 4.3.3",
    "set_consistency": "ISO/IEC/IEEE 29148:2018 п. 5.2.6 (Consistent), IEEE 830-1998 п. 4.3.4",
    "set_feasibility": "ISO/IEC/IEEE 29148:2018 п. 5.2.6 (Feasible)",
    "comprehensibility": "ISO/IEC/IEEE 29148:2018 п. 5.2.6 (Comprehensible)",
    "traceability": "IEEE 830-1998 п. 4.3.8 (Traceable)",
}


# Rules whose findings can be CRITICAL → the blocker reason they rest on. Criterion is the fallback for a rule
# not listed here, so every open CRITICAL carries a reason.
RULE_BLOCKER = {
    "get-mutation": "protocol",
    "error-in-success": "protocol",
    "prompt-injection": "loss",
    "hidden-text": "loss",
}
CRITERION_BLOCKER = {
    "consistency": "contradiction", "set_consistency": "contradiction",
    "feasibility": "protocol", "set_feasibility": "protocol",
    "completeness": "main_flow", "set_completeness": "missing_part",
}


def blocker_code(v: Any) -> str:
    """A valid blocker reason or "" (none, empty, unknown)."""
    v = str(v or "").strip().lower()
    return v if v in BLOCKER_REASONS else ""


def rule_blocker(f: Finding) -> str:
    for r in f.rule_ids:
        if r in RULE_BLOCKER:
            return RULE_BLOCKER[r]
    return CRITERION_BLOCKER.get(f.criterion, "main_flow")


def demote_critical(f: Finding, audit: list[dict] | None, stage: str) -> None:
    """CRITICAL without a valid blocker reason → MAJOR, recorded in the audit trail."""
    f.severity = "MAJOR"
    f.blocker_reason = ""
    if audit is not None:
        audit.append({"type": "decision", "what": "critical_without_reason", "stage": stage, "fid": f.fid,
                      "title": f.title, "to": "MAJOR"})


class QuoteStats:
    def __init__(self) -> None:
        self.confirmed = 0
        self.restored = 0
        self.dropped = 0


def _str(v: Any, limit: int = 4000) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "")).strip()[:limit]


def _question(raw: Any) -> QuestionDraft | None:
    if not isinstance(raw, dict):
        return None
    text = _str(raw.get("text"), 400)
    if not text:
        return None
    opts = [_str(o, 300) for o in (raw.get("options") or []) if _str(o)][:4]
    gap = raw.get("gap") if raw.get("gap") in GAP_TYPES else ""
    addr = raw.get("addressee") or "analyst"
    return QuestionDraft(text=text, options=opts, addressee=addr, gap=gap)


def verify_quotes(qi: QuoteIndex, texts: list[str], prefer_rid: str, stats: QuoteStats) -> tuple[list[Quote], list[str]]:
    ok: list[Quote] = []
    bad: list[str] = []
    for t in texts:
        t = str(t or "").strip()
        if not t:
            continue
        q = qi.find(t, prefer_rid=prefer_rid)
        if q is None:
            stats.dropped += 1
            bad.append(t)
            continue
        if any(x.text == q.text and x.line_start == q.line_start for x in ok):
            continue
        stats.confirmed += 1
        if not q.exact:
            stats.restored += 1
        ok.append(q)
    return ok, bad


def model_finding(raw: dict, *, scope: str, stage: str, qi: QuoteIndex, stats: QuoteStats, known_rids: set[str],
                  rid: str = "", allowed: tuple[str, ...] = (),
                  audit: list[dict] | None = None) -> tuple[Finding | None, DroppedQuote | None]:
    """One finding from a model answer, or (None, dropped) when no quote is confirmed.

    CRITICAL needs one of the closed blocker reasons; without it the finding is lowered to MAJOR (audited)."""
    title = _str(raw.get("title"), 140) or "Замечание без заголовка"
    if scope == "requirement":
        crit = raw.get("criterion") if raw.get("criterion") in REQ_CRITERIA else (allowed[0] if allowed else "completeness")
        texts = [raw.get("quote") or ""]
    else:
        crit = raw.get("criterion") if raw.get("criterion") in SET_CRITERIA else SET_STAGE_DEFAULT.get(stage, "set_completeness")
        qs = raw.get("quotes")
        texts = qs if isinstance(qs, list) else [qs or ""]
    sev = raw.get("severity") if raw.get("severity") in SEVERITIES else "MINOR"
    quotes, bad = verify_quotes(qi, [str(t) for t in texts], rid, stats)
    if not quotes:
        shown = next((b for b in bad if b), "")
        return None, DroppedQuote(stage=stage, title=title, quote=shown[:300],
                                  reason="цитата не найдена в документе" if shown else "замечание без цитаты")
    req_ids: list[str] = []
    if rid:
        req_ids.append(rid)
    for r in raw.get("req_ids") or []:
        r = _str(r, 20)
        if r in known_rids and r not in req_ids:
            req_ids.append(r)
    for q in quotes:
        if q.rid and q.rid not in req_ids and (scope == "set" or not rid):
            req_ids.append(q.rid)
    f = Finding(scope=scope, criterion=crit, severity=sev, title=title, problem=_str(raw.get("problem"), 1500),
                recommendation=_str(raw.get("recommendation"), 1500), quotes=quotes, req_ids=req_ids,
                question=_question(raw.get("question")), source="model", stage=stage,
                fixed_wording=_str(raw.get("rewrite"), 2000) if scope == "requirement" else "")
    if sev == "CRITICAL":
        f.blocker_reason = blocker_code(raw.get("blocker"))
        if not f.blocker_reason:
            demote_critical(f, audit, stage)
    return f, None


# --- overlap and merging -------------------------------------------------------------------


def _words(s: str) -> set[str]:
    return set(w for w in re.findall(r"\w+", s) if len(w) > 2)


def quotes_overlap(a: Quote, b: Quote) -> bool:
    if a.line_end < b.line_start or b.line_end < a.line_start:
        return False
    na, nb = normalize(a.text), normalize(b.text)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    wa, wb = _words(na), _words(nb)
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.5


def findings_overlap(a: Finding, b: Finding) -> bool:
    return any(quotes_overlap(x, y) for x in a.quotes for y in b.quotes)


def same_family(a: Finding, b: Finding) -> bool:
    return FAMILY.get(a.criterion, a.criterion) == FAMILY.get(b.criterion, b.criterion)


def is_rule_sourced(f: Finding) -> bool:
    return f.source.startswith("rule:") or f.source == "both" or bool(f.rule_ids)


def _same_quote(a: Quote, b: Quote) -> bool:
    return a.text == b.text and a.line_start == b.line_start


def _same_place(q: Quote, own: list[Quote]) -> bool:
    """``q`` is at a place the finding already rests on: it overlaps one of its quotes or lies in the same requirement."""
    return any(quotes_overlap(q, x) or (q.rid and q.rid == x.rid) for x in own)


def absorb(target: Finding, other: Finding, *, all_places: bool = False) -> None:
    """Fold ``other`` into ``target``: rule ids, severity, question, wording; quotes and requirement links.

    The decision is by topic. When both speak about one thing (``same_topic``) or ``all_places=True`` (a rule folded
    into a model finding: its places are code-verified places of this very defect), every verified quote of ``other``
    becomes a basis of ``target``: the same defect seen from several places. Otherwise (e.g. the skeptic merged two
    unrelated findings) a quote of ``other`` stays a basis only at a place ``target`` already rests on (overlapping quote
    or the same requirement); any other place goes to ``target.see_also`` («см. также») and gives no link.

    Requirement ids of ``other`` are kept only when they are about the kept basis: on a same-topic merge, those the
    model or rule named for ``other`` (which already means «named or a quote lies there»); otherwise only when a kept
    quote lies in that requirement. No new link is ever made just because a quote happens to lie in a requirement.

    Quotes are deduplicated and kept in document order; the first one is the most telling: a quote that came from a
    code rule (it points at the exact offending line) if there is one, otherwise the survivor's own first quote."""
    topic = all_places or same_topic(target, other)
    own = list(target.quotes)
    lead = own[0] if own else None
    if not is_rule_sourced(target) and is_rule_sourced(other) and other.quotes:
        lead = other.quotes[0]
    for q, basis in [(q, True) for q in other.quotes] + [(q, False) for q in other.see_also]:
        if any(_same_quote(q, x) for x in target.quotes + target.see_also):
            continue
        if basis and (topic or _same_place(q, own)):
            target.quotes.append(q)
        else:
            target.see_also.append(q)
    # a place that became a basis is no longer «see also»
    target.see_also = [s for s in target.see_also if not any(_same_quote(s, x) for x in target.quotes)]
    if lead is not None:
        first = next((x for x in target.quotes if _same_quote(x, lead)), None)
        rest = sorted((x for x in target.quotes if x is not first), key=lambda x: (x.line_start, x.line_end))
        target.quotes = ([first] if first is not None else []) + rest
    kept_rids = {q.rid for q in target.quotes if q.rid}
    for r in other.req_ids:
        if r not in target.req_ids and (topic or r in kept_rids):
            target.req_ids.append(r)
    for r in other.rule_ids:
        if r not in target.rule_ids:
            target.rule_ids.append(r)
    model_involved = target.source in ("model", "both") or other.source in ("model", "both")
    if target.rule_ids and model_involved:
        target.source = "both"
    if SEV_RANK.get(other.severity, 9) < SEV_RANK.get(target.severity, 9):
        target.severity = other.severity
        target.blocker_reason = other.blocker_reason
    elif target.severity == "CRITICAL" and not target.blocker_reason:
        target.blocker_reason = other.blocker_reason if other.severity == "CRITICAL" else ""
    if not target.fixed_wording and other.fixed_wording:
        target.fixed_wording = other.fixed_wording
    if target.question is None and other.question is not None:
        target.question = other.question


TOPIC_MIN = 0.25


def same_topic(a: Finding, b: Finding) -> bool:
    """Two findings speak about one thing: their main quotes overlap, or their titles or problems share words.

    A set-level finding lists many places, so one shared table row is not enough to call it the same defect.
    Near-identical titles (equal after normalising, or one title's words all inside the other's) always count."""
    if a.quotes and b.quotes and quotes_overlap(a.quotes[0], b.quotes[0]):
        return True
    return near_same_title(a.title, b.title) or topic_score(a, b) >= TOPIC_MIN


def near_same_title(a: str, b: str) -> bool:
    na, nb = normalize(a or "").lower(), normalize(b or "").lower()
    if na and na == nb:
        return True
    x, y = _stems(a), _stems(b)
    return len(x) >= 2 and len(y) >= 2 and (x <= y or y <= x)


def topic_score(a: Finding, b: Finding) -> float:
    """How strongly two findings share words: the better of title-to-title and problem-to-problem similarity."""
    return max(jaccard(a.title, b.title), jaccard(a.problem, b.problem))


def model_twins(a: Finding, b: Finding) -> bool:
    """Same criterion family and an overlapping quote; when a set-level finding is involved, also the same topic."""
    if not (same_family(a, b) and findings_overlap(a, b)):
        return False
    return (a.scope == "requirement" and b.scope == "requirement") or same_topic(a, b)


def dedupe_model(findings: list[Finding], audit: list[dict]) -> list[Finding]:
    """Merge model findings that describe the same defect (same criterion family, overlapping quotes, same topic)."""
    order = sorted(range(len(findings)), key=lambda i: (SEV_RANK.get(findings[i].severity, 9), i))
    kept: list[Finding] = []
    for i in order:
        f = findings[i]
        twin = next((k for k in kept if model_twins(k, f)), None)
        if twin is None:
            kept.append(f)
            continue
        absorb(twin, f)
        audit.append({"type": "decision", "what": "duplicate_merged", "stage": f.stage, "title": f.title,
                      "into_title": twin.title})
    return kept


def fold_rules(rule_findings: list[Finding], model_findings: list[Finding], audit: list[dict]) -> list[Finding]:
    """A rule finding that overlaps a model finding of the same family and about the same topic is folded into it
    (source «both»). When several model findings sit at the rule's place, the one whose title or problem shares the
    most words with the rule's wins (ties: the earlier one). If none of them is about the rule's topic, the rule
    finding stands on its own rather than disappearing into a finding about something else."""
    out = list(model_findings)
    for rf in rule_findings:
        if rf.advisory:
            out.append(rf)
            continue
        at_place = [m for m in model_findings if same_family(m, rf) and findings_overlap(m, rf)]
        topical = [m for m in at_place if same_topic(m, rf)]
        twin = max(topical, key=lambda m: topic_score(m, rf)) if topical else None
        if twin is None:
            out.append(rf)
            continue
        absorb(twin, rf, all_places=True)
        twin.source = "both"
        audit.append({"type": "decision", "what": "rule_folded", "rule": rf.rule_ids[0] if rf.rule_ids else "",
                      "into_title": twin.title})
    return out


def sort_key(f: Finding) -> tuple:
    q = f.quotes[0] if f.quotes else None
    return (q.line_start if q else 10**9, 0 if f.scope == "requirement" else 1, SEV_RANK.get(f.severity, 9),
            f.criterion, f.title)


def number(findings: list[Finding]) -> list[Finding]:
    """D-001… for counted findings, Н-01… for advisory observations; document order."""
    findings = sorted(findings, key=sort_key)
    d = n = 0
    for f in findings:
        if f.advisory:
            n += 1
            f.fid = f"Н-{n:02d}"
        else:
            d += 1
            f.fid = f"D-{d:03d}"
    return findings


# --- skeptic -------------------------------------------------------------------------------------


class SkepticOutcome:
    def __init__(self) -> None:
        self.kept = 0
        self.rejected = 0
        self.merged = 0
        self.protected = 0
        self.severity_changed = 0
        self.critical_demoted = 0


def apply_skeptic(findings: list[Finding], decisions: list[dict], *, injection: bool,
                  audit: list[dict]) -> SkepticOutcome:
    """Apply keep / reject / merge decisions in place. Findings absent from the answer stay unchanged.

    A rule-sourced finding is never removed (reject and, under a suspected injection, merge are ignored);
    under a suspected injection its severity cannot be lowered either.
    CRITICAL survives «keep» only with a valid ``blocker`` from the skeptic (a rule-sourced finding keeps its rule's
    reason); otherwise it is lowered to MAJOR and audited as «critical_without_reason»."""
    out = SkepticOutcome()
    by_id = {f.fid: f for f in findings if f.status == "open" and not f.advisory}
    reviewed: set[str] = set()

    def resolve(fid: str) -> Finding | None:
        seen = set()
        f = by_id.get(fid)
        while f is not None and f.status == "merged" and f.merged_into and f.merged_into not in seen:
            seen.add(f.merged_into)
            f = by_id.get(f.merged_into)
        return f if f is not None and f.status == "open" else None

    for d in decisions:
        if not isinstance(d, dict):
            continue
        fid = str(d.get("fid") or "").strip()
        f = by_id.get(fid)
        if f is None or f.status != "open" or fid in reviewed:
            continue
        reviewed.add(fid)
        decision = d.get("decision") or "keep"
        reason = _str(d.get("reason"), 400)
        protected = is_rule_sourced(f)
        if decision == "reject":
            if protected:
                out.protected += 1
                audit.append({"type": "decision", "what": "rule_finding_protected", "fid": fid, "attempt": "reject",
                              "reason": reason})
                decision = "keep"
            else:
                f.status = "rejected"
                f.status_reason = reason or "перепроверка не подтвердила дефект"
                out.rejected += 1
                audit.append({"type": "decision", "what": "skeptic_rejected", "fid": fid, "reason": f.status_reason})
                continue
        if decision == "merge":
            target = resolve(str(d.get("merge_into") or "").strip())
            if target is None or target is f:
                decision = "keep"
            elif protected and injection:
                out.protected += 1
                audit.append({"type": "decision", "what": "rule_finding_protected", "fid": fid, "attempt": "merge",
                              "reason": reason})
                decision = "keep"
            else:
                absorb(target, f)
                f.status = "merged"
                f.merged_into = target.fid
                f.status_reason = reason or f"то же, что {target.fid}"
                out.merged += 1
                audit.append({"type": "decision", "what": "skeptic_merged", "fid": fid, "into": target.fid,
                              "reason": f.status_reason})
                continue
        # keep, possibly with a new severity
        new_sev = d.get("severity") if d.get("severity") in SEVERITIES else f.severity
        blocker = blocker_code(d.get("blocker"))
        no_reason = False
        if new_sev == "CRITICAL" and not blocker:
            if protected and f.severity == "CRITICAL" and f.blocker_reason:
                blocker = f.blocker_reason  # a rule's reason stands on its own
            else:
                new_sev = "MAJOR" if f.severity == "CRITICAL" else f.severity
                no_reason = f.severity == "CRITICAL"
        if new_sev != f.severity:
            lowering = SEV_RANK[new_sev] > SEV_RANK[f.severity]
            if protected and injection and lowering:
                out.protected += 1
                audit.append({"type": "decision", "what": "rule_finding_protected", "fid": fid,
                              "attempt": f"severity {f.severity}→{new_sev}", "reason": reason})
                no_reason = False
            else:
                why = "не названо основание блокера" if no_reason else reason
                f.status_reason = f"уровень изменён перепроверкой: {f.severity} → {new_sev}" + (f". {why}" if why else "")
                audit.append({"type": "decision", "what": "skeptic_severity", "fid": fid, "from": f.severity,
                              "to": new_sev, "reason": reason})
                if no_reason:
                    audit.append({"type": "decision", "what": "critical_without_reason", "stage": "skeptic",
                                  "fid": fid, "title": f.title, "to": new_sev})
                    out.critical_demoted += 1
                f.severity = new_sev
                out.severity_changed += 1
        if f.severity == "CRITICAL":
            f.blocker_reason = blocker or f.blocker_reason
        else:
            f.blocker_reason = ""
        out.kept += 1
    out.kept += sum(1 for fid, f in by_id.items() if fid not in reviewed and f.status == "open")
    return out


# --- near-duplicates, by code (after the questions are linked) ---------------------------------------------

_STOP = {"для", "при", "что", "как", "или", "это", "его", "над", "под", "без", "они", "все", "нет", "так"}
JACCARD_MIN = 0.6


def _stems(s: str) -> set[str]:
    """Normalised Russian words: lower case, ё→е, longer than 2 letters, cut to 5 letters (crude stem)."""
    return {w[:5] for w in re.findall(r"\w+", (s or "").lower().replace("ё", "е"))
            if len(w) > 2 and w not in _STOP and not w.isdigit()}


def jaccard(a: str, b: str) -> float:
    x, y = _stems(a), _stems(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def _same_defect(a: Finding, b: Finding) -> bool:
    if not set(a.req_ids) & set(b.req_ids):
        return False
    if not (a.criterion == b.criterion or findings_overlap(a, b)):
        return False
    if a.question_id and a.question_id == b.question_id:
        return True
    return jaccard(a.title, b.title) >= JACCARD_MIN or jaccard(a.problem, b.problem) >= JACCARD_MIN


def merge_duplicates(findings: list[Finding], questions: list, fix_first: list, audit: list[dict]) -> int:
    """Merge OPEN counted findings that describe one defect (shared requirement, same criterion or overlapping
    quotes, same question or similar words). The survivor is the higher severity (set level wins a tie, then a
    rule-sourced one, then the earlier id); quotes, requirement ids and rule ids are united, so nothing a rule found
    is lost. Questions and fix-first actions that named the absorbed finding now name the survivor. Returns the count."""
    cand = [f for f in findings if f.status == "open" and not f.advisory]
    cand.sort(key=lambda f: (SEV_RANK.get(f.severity, 9), 0 if f.scope == "set" else 1,
                             0 if is_rule_sourced(f) else 1, f.fid))
    kept: list[Finding] = []
    moved: dict[str, str] = {}
    for f in cand:
        twin = next((k for k in kept if _same_defect(k, f)), None)
        if twin is None:
            kept.append(f)
            continue
        longer = f.problem if len(f.problem) > len(twin.problem) else twin.problem
        absorb(twin, f)
        twin.problem = longer
        if not twin.question_id:
            twin.question_id = f.question_id
        f.status = "merged"
        f.merged_into = twin.fid
        f.status_reason = f"повтор: то же место и тот же дефект, что {twin.fid}"
        moved[f.fid] = twin.fid
        audit.append({"type": "decision", "what": "duplicate_merged_by_code", "fid": f.fid, "into": twin.fid})
    if moved:
        by_fid = {f.fid: f for f in findings}
        for q in questions:
            ids = []
            for i in q.finding_ids:
                i = moved.get(i, i)
                if i not in ids:
                    ids.append(i)
            q.finding_ids = ids
            rids: list[str] = []
            for i in ids:
                for r in (by_fid[i].req_ids if i in by_fid else []):
                    if r not in rids:
                        rids.append(r)
            if rids:
                q.req_ids = rids
        for a in fix_first:
            ids = []
            for i in a.finding_ids:
                i = moved.get(i, i)
                if i not in ids:
                    ids.append(i)
            a.finding_ids = ids
    return len(moved)
