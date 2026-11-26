"""Model answers → verified findings; duplicates merged; rule findings folded in; ids; helpers of the re-check.

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
                     "set_standards": "set_feasibility", "set_gaps": "set_completeness"}


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


def note_level(f: Finding, by: str, level: str, why: str = "", with_: str = "") -> None:
    """Record a change of level in ``f.level_trail`` (the first entry is the level the finding was born with).

    ``by``: "skeptic" (the re-check), "merge" (taken from a merged finding), "code" (a rule of the program),
    "answer" (the author's answer). The caller sets ``f.severity`` itself; the last entry always equals it."""
    if level == f.severity and f.level_trail:
        return
    if not f.level_trail:
        origin = "rule" if f.source.startswith("rule:") else "model"
        f.level_trail.append({"by": origin, "level": f.severity})
    if level == f.severity:
        return
    entry: dict[str, Any] = {"by": by, "level": level}
    if why:
        entry["why"] = why[:300]
    if with_:
        entry["with"] = with_
    f.level_trail.append(entry)


def demote_critical(f: Finding, audit: list[dict] | None, stage: str) -> None:
    """CRITICAL without a valid blocker reason → MAJOR, recorded in the audit trail."""
    note_level(f, "code", "MAJOR", "не названо ни одно из пяти оснований блокера")
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
                                  reason="цитата не найдена в документе" if shown else "замечание без цитаты",
                                  problem=_str(raw.get("problem"), 1500))
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


ALSO_MAX = 12  # titles kept in ``Finding.also`` (the card prints the first three)


def _trace(target: Finding, other: Finding) -> None:
    """The merged finding leaves a visible trace on the survivor: its title (and what had been merged into it)."""
    for t in [other.title] + list(other.also):
        t = (t or "").strip()
        if t and t != target.title and t not in target.also and len(target.also) < ALSO_MAX:
            target.also.append(t)


ABSORBED_QUOTE_MAX = 600
BY_VALUES = ("recheck", "dedup", "code")


def absorbed_record(other: Finding, by: str, severity: str = "") -> dict:
    """The full record of a finding that is merged away: what the reader needs to see it on the survivor's card.
    ``severity`` is the level it has at this moment, i.e. before anything in the same step could lower it."""
    return {"title": other.title, "problem": other.problem, "recommendation": other.recommendation,
            "severity": severity or other.severity, "criterion": other.criterion,
            "quotes": [{"line": q.line_start, "text": (q.text or "")[:ABSORBED_QUOTE_MAX]} for q in other.quotes],
            "source": other.source, "by": by if by in BY_VALUES else "code"}


def _record_absorbed(target: Finding, other: Finding, by: str, severity: str = "") -> None:
    """Add ``other`` and everything already merged into it to ``target.absorbed`` (nested merges are flattened)."""
    for rec in [absorbed_record(other, by, severity)] + [dict(x) for x in other.absorbed]:
        if not any(x.get("title") == rec.get("title") and x.get("problem") == rec.get("problem")
                   and x.get("by") == rec.get("by") for x in target.absorbed):
            target.absorbed.append(rec)


def absorb(target: Finding, other: Finding, *, all_places: bool = False, trace: bool = True,
           by: str = "code", before: tuple[str, str] | None = None) -> None:
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
    code rule (it points at the exact offending line) if there is one, otherwise the survivor's own first quote.

    ``by`` names the path of the merge for the record ``target.absorbed`` ("recheck", "dedup", "code"). A merged finding
    is never lowered first: ``before`` = (level, blocker reason) that ``other`` had before the step lowered it; the
    survivor takes the higher of the two levels and the trail says so."""
    topic = all_places or same_topic(target, other)
    o_sev, o_block = (before if before else (other.severity, other.blocker_reason))
    if o_sev == "CRITICAL" and not blocker_code(o_block):
        o_sev, o_block = "MAJOR", ""
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
    for g in other.gap_ids:  # the completeness map follows a gap to the finding that survives
        if g not in target.gap_ids:
            target.gap_ids.append(g)
    model_involved = target.source in ("model", "both") or other.source in ("model", "both")
    if target.rule_ids and model_involved:
        target.source = "both"
    _record_absorbed(target, other, by, o_sev)
    if trace:
        _trace(target, other)
    if SEV_RANK.get(o_sev, 9) < SEV_RANK.get(target.severity, 9):
        # the callers that merge two findings of the review put the more severe one first (``more_severe_first``),
        # so this happens only when a rule's level is folded into a model finding on the rule's own line, or when
        # the merged finding had been lowered earlier in the same step
        note_level(target, "merge", o_sev,
                   "уровень взят по старшему из объединённых" if not other.rule_ids or other.source == "both"
                   else "уровень задан автоматическим правилом", with_=other.fid or (
                       "правило " + ", ".join(other.rule_ids) if other.rule_ids else other.title[:80]))
        target.severity = o_sev
        target.blocker_reason = o_block if o_sev == "CRITICAL" else ""
    elif target.severity == "CRITICAL" and not target.blocker_reason:
        target.blocker_reason = o_block if o_sev == "CRITICAL" else ""
    if not target.fixed_wording and other.fixed_wording:
        target.fixed_wording = other.fixed_wording
    if target.question is None and other.question is not None:
        target.question = other.question


TOPIC_MIN = 0.22          # titles: share of common topic words (see ``_roots``) that makes two findings one defect
TOPIC_MIN_PROBLEM = 0.30  # problems are longer and share stock phrases, so the bar is higher

# Words that open most findings («не описано…», «не задано…») and say nothing about what the finding is about.
_BOILER = {"опис", "зада", "указ", "опре", "сказ", "прои", "пове", "дела", "буде", "быть", "есть", "треб", "доку",
           "неяс", "след", "долж", "може", "нужн", "кото", "случ",
           "defi", "spec", "desc", "miss", "requ", "docu", "beha", "happ", "shou", "does", "what", "when"}
_TOPIC_STOP = {"если", "когда", "уже", "еще", "the", "and", "for", "not", "with", "are", "but"}


def _roots(s: str) -> set[str]:
    """Topic words of a text: lower case, ё→е, cut to four letters (a crude root that survives Russian endings);
    stop words and stock openers dropped; numbers of three digits or more kept (response codes, limits)."""
    out: set[str] = set()
    for w in re.findall(r"\w+", (s or "").lower().replace("ё", "е")):
        if w.isdigit():
            if len(w) >= 3:
                out.add(w)
            continue
        if len(w) < 3 or w in _STOP or w in _TOPIC_STOP:
            continue
        r = w[:4]
        if r not in _BOILER:
            out.add(r)
    return out


def _share(a: str, b: str) -> float:
    x, y = _roots(a), _roots(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def words_alike(a: Finding, b: Finding) -> bool:
    """Two findings say the same thing, judged by their words alone: near-identical titles, one title's topic words
    all inside the other's, or enough shared topic words in the titles or in the problems."""
    if near_same_title(a.title, b.title):
        return True
    x, y = _roots(a.title), _roots(b.title)
    if len(x) >= 2 and len(y) >= 2 and (x <= y or y <= x):
        return True
    return _share(a.title, b.title) >= TOPIC_MIN or _share(a.problem, b.problem) >= TOPIC_MIN_PROBLEM


TITLE_STRICT = 0.5  # share of topic words in two titles that makes them one statement without other evidence


_NAMED = re.compile(r"«([^»]{1,80})»")


def different_names(a: str, b: str) -> bool:
    """Both titles name something in quotes, and no name is shared: different parameters, sections or terms."""
    na = {x.casefold() for x in _NAMED.findall(a or "")}
    nb = {x.casefold() for x in _NAMED.findall(b or "")}
    return bool(na and nb and not na & nb)


def titles_alike(a: str, b: str) -> bool:
    """The titles say the same thing: near-identical, one's topic words all inside the other's, or half shared.
    Titles that name different things in quotes («amount» and «enabled») are about different subjects."""
    if different_names(a, b):
        return False
    if near_same_title(a, b):
        return True
    x, y = _roots(a), _roots(b)
    if len(x) >= 3 and len(y) >= 3 and (x <= y or y <= x):
        return True
    return _share(a, b) >= TITLE_STRICT


def same_topic(a: Finding, b: Finding) -> bool:
    """Two findings are about one defect, not merely about one place.

    A shared quote is never enough: one sentence of a specification often carries several different defects (a
    missing step of the value, an undefined term and an undescribed refund can all quote the same rule), and merging
    them by place silently erases all but one. Findings of the completeness checklist are compared by their checklist
    items: the same item is the same topic, different items never are. Everything else is compared by words."""
    if a.gap_ids and b.gap_ids:
        return bool(set(a.gap_ids) & set(b.gap_ids))
    return words_alike(a, b)


CODE_GUARD = "topic"  # the guard of the merges the program makes itself (same choice as the re-check's MERGE_GUARD)


def guard_allows(target: Finding, f: Finding, guard: str) -> bool:
    """May ``f`` be folded into ``target``? ``guard``: "topic" (one defect by words or checklist item),
    "place_or_topic" (an overlapping quote, a shared requirement or one topic), "none" (always).
    One function for the re-check (``gate.merge_allowed``) and for the merges by code."""
    if guard == "none":
        return True
    if guard == "place_or_topic":
        return findings_overlap(target, f) or bool(set(target.req_ids) & set(f.req_ids)) or same_topic(target, f)
    return same_topic(target, f)


def near_same_title(a: str, b: str) -> bool:
    na, nb = normalize(a or "").lower(), normalize(b or "").lower()
    if na and na == nb:
        return True
    x, y = _stems(a), _stems(b)
    return len(x) >= 2 and len(y) >= 2 and (x <= y or y <= x)


def topic_score(a: Finding, b: Finding) -> float:
    """How strongly two findings share topic words: the better of title-to-title and problem-to-problem similarity."""
    return max(_share(a.title, b.title), _share(a.problem, b.problem))


# Optional, OFF by default: stricter word-based dedup before numbering. When True, ``model_twins`` needs alike titles
# (``titles_alike``) instead of ``same_topic``, and the last test of ``_same_defect`` also needs alike titles.
DEDUPE_STRICT = False


def model_twins(a: Finding, b: Finding) -> bool:
    """One defect reported twice: the same criterion family, an overlapping quote and the same topic."""
    if DEDUPE_STRICT:
        return same_family(a, b) and findings_overlap(a, b) and titles_alike(a.title, b.title)
    return same_family(a, b) and findings_overlap(a, b) and same_topic(a, b)


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
        absorb(twin, f, by="code")
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
        # a rule points at the exact offending line: a model finding whose main quote is that very line is about it
        topical = [m for m in at_place if (m.quotes and rf.quotes and quotes_overlap(m.quotes[0], rf.quotes[0]))
                   or same_topic(m, rf)]
        twin = max(topical, key=lambda m: topic_score(m, rf)) if topical else None
        if twin is None:
            out.append(rf)
            continue
        absorb(twin, rf, all_places=True, trace=False, by="code")  # the source line names the rule
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


# --- helpers of the re-check (stage 7, see gate.py) ---------------------------------------------


RAISE_FIT_MIN = 0.34


def _reason_fits(f: Finding, reason: str) -> bool:
    """A reason for a HIGHER level must speak about this very finding: it is not a new finding in disguise and at
    least a third of the topic words of the finding's title appear in it."""
    low = (reason or "").strip().lower()
    if not low or low.startswith("новое замечание") or low.startswith("new finding"):
        return False
    title = _roots(f.title)
    if not title:
        return True
    return len(title & _roots(reason)) / len(title) >= RAISE_FIT_MIN


def more_severe_first(a: Finding, b: Finding) -> tuple[Finding, Finding]:
    """(survivor, absorbed) of a merge of two findings: the more severe one survives, so that the title, the problem,
    the level and the reason of a blocker on the card always come from one finding. A tie keeps the order given."""
    return (b, a) if SEV_RANK.get(b.severity, 9) < SEV_RANK.get(a.severity, 9) else (a, b)


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


NEAR_LINES = 3  # main quotes this close in one section: a heading and its stub line, a rule and its next line


def _close_by(a: Finding, b: Finding) -> bool:
    """The main quotes (the first of each) lie in the same section (or a section and its subsection) within
    NEAR_LINES lines of each other."""
    if not a.quotes or not b.quotes:
        return False
    qa, qb = a.quotes[0], b.quotes[0]
    sa, sb = qa.section or "", qb.section or ""
    same = sa == sb or sa.startswith(sb + " › ") or sb.startswith(sa + " › ")  # a section and its own subsection
    return same and abs(qa.line_start - qb.line_start) <= NEAR_LINES


def _lines(f: Finding) -> set[int]:
    return {n for q in f.quotes for n in range(q.line_start, (q.line_end or q.line_start) + 1)}


def _covered_by_rule(model_f: Finding, rule_f: Finding) -> bool:
    """A model finding repeats a rule finding when every line it quotes is a line the rule finding quotes, both
    belong to the same family of criteria and their words agree (``words_alike``)."""
    if is_rule_sourced(model_f) or not is_rule_sourced(rule_f) or rule_f.severity == "MINOR" and \
            SEV_RANK.get(model_f.severity, 9) < SEV_RANK["MINOR"]:
        return False
    lines = _lines(model_f)
    if not lines or not lines <= _lines(rule_f):
        return False
    return FAMILY.get(model_f.criterion) == FAMILY.get(rule_f.criterion) and words_alike(model_f, rule_f)


def _same_defect(a: Finding, b: Finding) -> bool:
    """One defect, by code: the place-and-words test below AND the guard every merge obeys (one topic). Two findings
    that share a requirement, a criterion or a question but speak about different things both stay open."""
    return _same_defect_loose(a, b) and guard_allows(a, b, CODE_GUARD)


def _same_defect_loose(a: Finding, b: Finding) -> bool:
    # same words, two lines apart: one defect even when the quotes do not overlap and no requirement is shared
    # (a heading of an empty section and its stub line). Neighbouring lines often carry different defects (two
    # missing branches are two findings), so the titles must agree: strongly, or moderately within one family.
    if _close_by(a, b) and not different_names(a.title, b.title) and (titles_alike(a.title, b.title)
                            or FAMILY.get(a.criterion) == FAMILY.get(b.criterion)
                            and _share(a.title, b.title) >= TOPIC_MIN):
        return True
    if _covered_by_rule(a, b) or _covered_by_rule(b, a):
        return True
    if not set(a.req_ids) & set(b.req_ids):
        return False
    if not (a.criterion == b.criterion or findings_overlap(a, b)):
        return False
    if a.question_id and a.question_id == b.question_id:
        return True
    alike = jaccard(a.title, b.title) >= JACCARD_MIN or jaccard(a.problem, b.problem) >= JACCARD_MIN
    return alike and titles_alike(a.title, b.title) if DEDUPE_STRICT else alike


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
        absorb(twin, f, by="code")  # ``cand`` is sorted by severity: the survivor is the more severe, its own text stays
        if not twin.question_id:
            twin.question_id = f.question_id
        f.status = "merged"
        f.merged_into = twin.fid
        f.status_reason = f"повтор: то же место и тот же дефект, что {twin.fid}"
        moved[f.fid] = twin.fid
        audit.append({"type": "decision", "what": "duplicate_merged_by_code", "fid": f.fid, "into": twin.fid})
    _remap(moved, findings, questions, fix_first)
    return len(moved)


def _remap(moved: dict[str, str], findings: list[Finding], questions: list, fix_first: list) -> None:
    """Questions and fix-first actions that named an absorbed finding now name the survivor."""
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


FOLD_STAGE = "fold_singularity"
FOLD_CRITERIA = ("singularity",)  # purely formal remarks: the same flaw at many places is one remark


def fold_formal(findings: list[Finding], questions: list, fix_first: list, audit: list[dict]) -> Finding | None:
    """Fold open MINOR findings of a purely formal criterion («several statements in one item») into one set-level
    finding that lists every place. Only formal remarks of the same criterion, MINOR, without a blocker reason;
    remarks about content are never folded (two missing branches are two findings). The first finding (document
    order) survives and keeps its id and its question; the others are merged into it. Returns the survivor."""
    group = [f for f in findings if f.status == "open" and not f.advisory and f.severity == "MINOR"
             and f.criterion in FOLD_CRITERIA and f.scope == "requirement" and not f.blocker_reason]
    if len(group) < 2:
        return None
    group.sort(key=sort_key)
    head, rest = group[0], group[1:]
    quotes, rids, rules = [], [], []
    for f in group:
        for q in f.quotes:
            if not any(q.line_start == x.line_start and q.text == x.text for x in quotes):
                quotes.append(q)
        rids += [r for r in f.req_ids if r not in rids]
        rules += [r for r in f.rule_ids if r not in rules]
    sources = {f.source for f in group}
    n = len(rids) or len(group)
    # the head's own wording is replaced below: it goes into the record first, then every other remark
    _record_absorbed(head, head, "code")
    for f in rest:
        _record_absorbed(head, f, "code")
    head.scope = "set"
    head.stage = FOLD_STAGE
    head.title = f"В {n} {_plural_items(n)} записано по нескольку утверждений"
    head.problem = ("В каждом из этих пунктов под одним номером записано несколько отдельных правил или "
                    "результатов. На каждое нельзя сослаться отдельно, а тест на такой пункт проверяет сразу "
                    "несколько вещей.")
    head.recommendation = ("Разбейте каждый пункт на отдельные пронумерованные требования: одно правило или один "
                           "проверяемый результат на номер.")
    head.quotes, head.req_ids, head.rule_ids = quotes, rids, rules
    head.source = "both" if len(sources) > 1 or "both" in sources else head.source
    head.fixed_wording = ""
    head.also = []
    note_level(head, "code", "MINOR", f"собрано из {len(group)} однотипных формальных замечаний")
    moved = {}
    for f in rest:
        f.status = "merged"
        f.merged_into = head.fid
        f.status_reason = f"собрано в одно замечание {head.fid}: тот же формальный недочёт в другом пункте"
        moved[f.fid] = head.fid
        if not head.question_id and f.question_id:
            head.question_id = f.question_id
    audit.append({"type": "decision", "what": "formal_folded", "into": head.fid, "fids": [f.fid for f in rest],
                  "criterion": head.criterion})
    _remap(moved, findings, questions, fix_first)
    return head


def _plural_items(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "пункте"
    return "пунктах"
