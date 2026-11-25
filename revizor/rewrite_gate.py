"""Deterministic gate for proposed rewrites of requirements («было → стало»).

A wrong rewrite is worse than none: the author may paste it into the document. A rewrite R of the requirement text T
for finding F is printed only when all five checks pass (the rules are general, nothing here knows a document):

1. right target   — F's first quote lies in T, and R keeps at least ``MIN_OVERLAP`` of T's content stems;
2. nothing lost   — every number (with its unit), identifier, code token and HTTP code of T is still in R, unless F
                    itself names that value as the thing to change (title, problem, or a quote narrower than T);
3. no shrinking   — when T has two or more sentences, R is at least ``MIN_LENGTH_SHARE`` of T and has at least
                    «sentences of T minus one» sentences;
4. no invention   — R has no number, code or Latin name that appears neither in the document nor in an author's
                    answer; a value the author must choose is written as ``⟦уточнить: … — В-NN⟧``;
5. one per req.   — several findings with rewrites for one requirement are composed only when each passes and they
                    change different sentences; otherwise none is printed and the needed changes are listed.

``plan`` is pure; ``apply`` writes the decisions into ``Result.audit`` and clears ``Finding.fixed_wording`` of a
withheld rewrite so that the report card does not print it either.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .model import Finding, Requirement, Result

# 40 % of T's content stems must survive in R: a rewrite of the same requirement keeps its nouns and verbs, a rewrite
# of a neighbouring requirement shares only the common words (карта, сервис, клиент). Calibrated on the evidence in
# ``tests/test_rewrite_gate.py`` (right: 0.5–1.0, wrong target: 0.0–0.3).
MIN_OVERLAP = 0.4
MIN_LENGTH_SHARE = 0.7
NARROW_QUOTE = 0.6  # a quote shorter than this share of T points at one value, a longer one is T itself
SAME_SENTENCE = 0.8  # stem overlap at which a sentence of R is «the same» sentence of T

GAP_RE = re.compile(r"⟦\s*уточнить[^⟧]*⟧", re.I)
_GENERIC_LATIN = {"http", "https", "json", "xml", "api", "rest", "url", "uri", "id", "ui", "ok", "get", "post",
                  "put", "delete", "patch", "ios", "android", "sms", "http2"}

_STOP = set("""если когда пока при для или что как это его она они быть должен должна должно должны может также только
все всех без над под после через чтобы который которая которые этот эта эти того тому там где так уже еще ещё
иначе либо если-то затем потом сервис система приложение клиент должна""".split())
_ENDINGS = sorted("ами ями ого его ому ему ыми ими ов ев ей ой ый ий ая яя ое ее ые ие ых их ам ям ах ях ом ем ую юю "
                  "а я у ю ы и е о ь й ть ет ут ют ит ат ят ся".split(), key=len, reverse=True)

_UNITS = [
    ("тенге", r"тенге|тг|kzt|₸"), ("тиын", r"тиын\w*"), ("%", r"%|процент\w*"), ("сек", r"сек\.?|секунд\w*"),
    ("мин", r"мин\.?|минут\w*"), ("час", r"час|часа|часов|часы|ч"), ("дн", r"дн|дня|дней|день|дни|сут\w*"),
    ("нед", r"недел\w*"), ("мес", r"мес|месяц\w*"), ("год", r"год|года|лет|годов"), ("мс", r"мс|миллисекунд\w*"),
    ("симв", r"символ\w*|знак\w*|букв\w*|цифр\w*"), ("раз", r"раз|раза"), ("шт", r"шт|штук\w*"),
    ("байт", r"байт\w*|кб|мб|гб|kb|mb|gb"), ("руб", r"руб\w*"), ("usd", r"usd|\$|долл\w*"),
    ("попытка", r"попыт\w*"), ("запрос", r"запрос\w*"),
]
_UNIT_RX = [(canon, re.compile(rx, re.I)) for canon, rx in _UNITS]

_NUM = r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_NUM_RE = re.compile(rf"(?<![\w.,])({_NUM})(?:\s*(₸|%|\$|[A-Za-zА-Яа-яЁё]+\.?))?")
_ID_PATTERNS = [
    re.compile(r"`([^`\n]+)`"),
    re.compile(r"https?://\S+"),
    re.compile(r"(?<![\w/])/[A-Za-z0-9_\-{}.]+(?:/[A-Za-z0-9_\-{}.]*)*"),
    re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),
    re.compile(r"\bv?\d+\.\d+\.\d+\b"),
    re.compile(r"(?<![\w-])[A-ZА-ЯЁ]{1,5}[-‑–]\d+(?:\.\d+)*(?![\w])"),
]
_LATIN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


# --- extraction ------------------------------------------------------------------------------------------------------


def _norm_num(tok: str) -> str:
    t = re.sub(r"[\s  ]", "", tok).replace(",", ".")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t.lstrip("0") or "0"


def _unit_of(word: str) -> str:
    w = (word or "").strip().lower().rstrip(".")
    if not w:
        return ""
    for canon, rx in _UNIT_RX:
        if rx.fullmatch(w) or (w == "₸" and canon == "тенге"):
            return canon
    return ""


def _looks_like_identifier(tok: str) -> bool:
    """A Latin token that names something (field, code, status), as opposed to an ordinary English word."""
    if len(tok) < 2 or tok.lower() in _GENERIC_LATIN:
        return False
    return (any(c.isdigit() for c in tok) or "_" in tok or tok.isupper()
            or bool(re.search(r"[a-z][A-Z]", tok)))


@dataclass
class Specials:
    numbers: dict[str, set[str]] = field(default_factory=dict)  # normalised number → units seen with it ("" = none)
    ids: set[str] = field(default_factory=set)  # identifiers, paths, dates, doc ids (lower case)
    latin: set[str] = field(default_factory=set)  # every Latin word (lower case), generic ones left out
    units: set[str] = field(default_factory=set)  # canonical units that occur anywhere in the text


_LEAD_LABEL = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|[A-ZА-ЯЁ]{1,5}[-‑–]\d+(?:\.\d+)*[.:)]?)\s+")


def strip_label(text: str) -> str:
    """The numbering or document id at the start («2.3.», «Ф-2.») is a label, not content: a rewrite may drop it."""
    return _LEAD_LABEL.sub("", text or "", count=1)


def specials(text: str, *, label: bool = False) -> Specials:
    out = Specials()
    t = GAP_RE.sub(" ", text or "")
    if not label:
        t = strip_label(t)
    for rx in _ID_PATTERNS:
        def take(m: re.Match) -> str:
            tok = (m.group(1) if m.groups() else m.group(0)).strip().lower()
            if tok:
                out.ids.add(tok)
            return " " * len(m.group(0))
        t = rx.sub(take, t)
    for m in _LATIN.finditer(t):
        tok = m.group(0)
        if tok.lower() in _GENERIC_LATIN:
            continue
        out.latin.add(tok.lower())
        if _looks_like_identifier(tok):
            out.ids.add(tok.lower())
    for m in _NUM_RE.finditer(t):
        unit = _unit_of(m.group(2) or "")
        out.numbers.setdefault(_norm_num(m.group(1)), set()).add(unit)
    for m in re.finditer(r"[₸%$]|[A-Za-zА-Яа-яЁё]+", t):
        u = _unit_of(m.group(0))
        if u:
            out.units.add(u)
    return out


def stems(text: str) -> set[str]:
    out: set[str] = set()
    for w in re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", GAP_RE.sub(" ", text or "")):
        w = w.lower().replace("ё", "е")
        if w in _STOP:
            continue
        for e in _ENDINGS:
            if w.endswith(e) and len(w) - len(e) >= 4:
                w = w[: -len(e)]
                break
        out.add(w[:6])
    return out


def sentences(text: str) -> list[str]:
    parts = [p.strip(" \t|*-•") for p in _SENT_SPLIT.split(text or "")]
    return [p for p in parts if len(p) >= 4 and re.search(r"[A-Za-zА-Яа-я]", p)]


def _overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a) if a else 1.0


# --- the five checks -----------------------------------------------------------------------------------------------------


@dataclass
class Check:
    ok: bool = True
    reasons: list[str] = field(default_factory=list)
    invented: list[str] = field(default_factory=list)
    overlap: float = 1.0


def _exempt(finding: Finding, req_text: str) -> Specials:
    """Values the finding itself calls the defect: in its title, problem or a quote narrower than the requirement."""
    parts = [finding.title or "", finding.problem or ""]
    for q in finding.quotes:
        if len(q.text) < NARROW_QUOTE * max(len(req_text), 1):
            parts.append(q.text)
    return specials("\n".join(parts))


def right_target(finding: Finding, req: Requirement, wording: str) -> tuple[bool, float, str]:
    q0 = finding.quotes[0] if finding.quotes else None
    if q0 is not None:
        inside = (q0.rid == req.rid) or (not q0.rid and req.line_start <= q0.line_start and q0.line_end <= req.line_end)
        if not inside:
            return False, 0.0, f"замечание {finding.fid} относится к другому месту документа, а не к требованию {req.rid}"
    t_stems = stems(req.quote)
    ov = _overlap(t_stems, stems(wording))
    if t_stems and (ov < MIN_OVERLAP or not (t_stems & stems(wording))):
        return False, ov, (f"формулировка описывает другое требование: из слов требования в ней сохранено "
                           f"{round(ov * 100)} % (нужно не меньше {round(MIN_OVERLAP * 100)} %)")
    return True, ov, ""


def lost_values(req_text: str, wording: str, exempt: Specials) -> list[str]:
    t, r = specials(req_text), specials(wording)
    lost: list[str] = []
    for num, units in t.numbers.items():
        if num in exempt.numbers:
            continue
        if num not in r.numbers:
            lost.append(num)
            continue
        wanted = {u for u in units if u}
        attached = {u for u in r.numbers[num] if u}  # the unit written next to this very number, else any in the text
        if wanted and not (wanted & (attached or r.units)):
            lost.append(f"{num} {', '.join(sorted(wanted))}")
    for ident in sorted(t.ids):
        if ident in exempt.ids or ident in r.ids or ident in r.latin:
            continue
        lost.append(ident)
    return lost


def shrunk(req_text: str, wording: str) -> str:
    ts = sentences(req_text)
    if len(ts) < 2:
        return ""
    rs = sentences(GAP_RE.sub("", wording))
    short = len(re.sub(r"\s+", " ", wording).strip()) < MIN_LENGTH_SHARE * len(re.sub(r"\s+", " ", req_text).strip())
    if short or len(rs) < len(ts) - 1:
        return (f"в требовании {len(ts)} предложения (правила), в формулировке {len(rs)}: часть правил пропала"
                if len(rs) < len(ts) - 1 else "формулировка заметно короче требования: часть содержания пропала")
    return ""


def invented_values(wording: str, corpus: Specials) -> list[str]:
    r = specials(wording)
    bad = [n for n in r.numbers if n not in corpus.numbers]
    bad += [i for i in sorted(r.ids) if i not in corpus.ids and i not in corpus.latin]
    bad += [w for w in sorted(r.latin) if w not in corpus.latin and w not in corpus.ids and w not in bad]
    return bad


def check_one(finding: Finding, req: Requirement, wording: str, corpus: Specials) -> Check:
    c = Check()
    ok, ov, why = right_target(finding, req, wording)
    c.overlap = ov
    if not ok:
        c.ok = False
        c.reasons.append(why)
    lost = lost_values(req.quote, wording, _exempt(finding, req.quote))
    if lost:
        c.ok = False
        c.reasons.append("в формулировке пропало: " + ", ".join(f"«{x}»" for x in lost[:6]))
    sh = shrunk(req.quote, wording)
    if sh:
        c.ok = False
        c.reasons.append(sh)
    inv = invented_values(wording, corpus)
    if inv:
        c.ok = False
        c.invented = inv
        c.reasons.append("добавлено то, чего нет в документе и в ответах автора: " + ", ".join(f"«{x}»" for x in inv[:6]))
    return c


# --- one requirement, several findings ------------------------------------------------------------------------------


def _match(sent: str, pool: list[str]) -> int | None:
    s = stems(sent)
    best, idx = 0.0, None
    for k, p in enumerate(pool):
        ps = stems(p)
        if not s and not ps:
            sc = 1.0 if sent.strip().lower() == p.strip().lower() else 0.0
        else:
            sc = len(s & ps) / max(len(s | ps), 1)
        if sc > best:
            best, idx = sc, k
    return idx if best >= SAME_SENTENCE else None


def _changes(req_text: str, wording: str) -> tuple[set[int], dict[int, list[str]]]:
    """(indexes of T's sentences that R changed or dropped, new sentences of R keyed by the T sentence before them)."""
    ts, rs = sentences(req_text), sentences(wording)
    kept: set[int] = set()
    new: dict[int, list[str]] = {}
    anchor = -1
    for s in rs:
        k = _match(s, ts)
        if k is not None:
            kept.add(k)
            anchor = k
        else:
            new.setdefault(anchor, []).append(s)
    return set(range(len(ts))) - kept, new


def compose(req_text: str, wordings: list[str]) -> str | None:
    """Merge rewrites that change different sentences into one text; None when two of them touch the same sentence."""
    ts = sentences(req_text)
    changed: list[tuple[set[int], dict[int, list[str]]]] = [_changes(req_text, w) for w in wordings]
    seen: set[int] = set()
    for ch, _new in changed:
        if not ch or ch & seen:
            return None
        seen |= ch
    out: list[str] = []
    for j in range(-1, len(ts)):
        if j >= 0 and j not in seen:
            out.append(ts[j])
        for _ch, new in changed:
            out.extend(new.get(j, []))
    return " ".join(out)


# --- the plan ----------------------------------------------------------------------------------------------------------------


@dataclass
class Row:
    """A correction that is printed."""

    rid: str
    before: str
    after: str
    fids: list[str]
    composed: bool = False


@dataclass
class Withheld:
    """A rewrite that is not printed: the row keeps the original text and says what must change."""

    rid: str
    before: str
    fids: list[str]
    reasons: list[str]
    needs: list[str]
    qids: list[str]
    gaps: list[str] = field(default_factory=list)


def target_requirement(f: Finding, reqs: list[Requirement]) -> Requirement | None:
    by_rid = {r.rid: r for r in reqs}
    if f.quotes:
        q0 = f.quotes[0]
        if q0.rid in by_rid:
            return by_rid[q0.rid]
        for r in reqs:
            if r.line_start <= q0.line_start and q0.line_end <= r.line_end:
                return r
    for rid in f.req_ids:
        if rid in by_rid:
            return by_rid[rid]
    return None


def corpus_of(result: Result) -> Specials:
    parts: list[str] = [r.quote + "\n" + r.summary for r in result.requirements]
    parts += [s.text for s in result.skipped]
    for f in result.findings + result.rejected:
        parts += [q.text for q in f.quotes]
    parts += [q.answer for q in result.questions if q.answer]
    return specials("\n".join(parts))


def _need(f: Finding) -> str:
    return (f.recommendation or f.problem or f.title or "").strip()


def plan(result: Result, findings: Iterable[Finding] | None = None) -> tuple[list[Row], list[Withheld]]:
    """Decide for every open finding with a rewrite. Pure: nothing in ``result`` changes."""
    fl = [f for f in (findings if findings is not None else result.findings)
          if f.status == "open" and not f.advisory and f.fixed_wording]
    corpus = corpus_of(result)
    qtext = {q.qid: q for q in result.questions}
    by_req: dict[str, list[tuple[Finding, Requirement, Check]]] = {}
    withheld: list[Withheld] = []

    def hold(f: Finding, req: Requirement | None, reasons: list[str], invented: list[str] | None = None) -> None:
        gaps = [f"⟦уточнить: значение «{x}» — {f.question_id or 'вопрос автору'}⟧" for x in (invented or [])[:4]]
        withheld.append(Withheld(rid=req.rid if req else (f.req_ids[0] if f.req_ids else ""),
                                 before=req.quote if req else (f.quotes[0].text if f.quotes else ""),
                                 fids=[f.fid], reasons=reasons, needs=[_need(f)],
                                 qids=[f.question_id] if f.question_id else [], gaps=gaps))

    for f in fl:
        req = target_requirement(f, result.requirements)
        if req is None:
            hold(f, None, ["не удалось определить, к какому требованию относится замечание"])
            continue
        c = check_one(f, req, f.fixed_wording, corpus)
        if not c.ok:
            hold(f, req, c.reasons, c.invented)
            continue
        by_req.setdefault(req.rid, []).append((f, req, c))

    # the same text offered for two requirements: it fits the one whose words it keeps best
    owner: dict[str, tuple[str, float]] = {}
    for rid, items in by_req.items():
        for f, _req, c in items:
            key = re.sub(r"\W+", " ", f.fixed_wording.casefold()).strip()
            if key not in owner or c.overlap > owner[key][1]:
                owner[key] = (rid, c.overlap)
    rows: list[Row] = []
    for rid, items in by_req.items():
        keep = []
        for f, req, c in items:
            key = re.sub(r"\W+", " ", f.fixed_wording.casefold()).strip()
            if owner[key][0] != rid:
                hold(f, req, ["та же формулировка предложена для другого требования и подходит ему больше"])
            else:
                keep.append((f, req, c))
        if not keep:
            continue
        req = keep[0][1]
        if len(keep) == 1:
            f = keep[0][0]
            rows.append(Row(rid, req.quote, f.fixed_wording, [f.fid]))
            continue
        merged = compose(req.quote, [f.fixed_wording for f, _r, _c in keep])
        ok = False
        if merged:
            probe = Finding(scope="requirement", criterion=keep[0][0].criterion, severity="MAJOR", title=" ".join(f.title for f, _r, _c in keep),
                            problem=" ".join(f.problem for f, _r, _c in keep), recommendation="", quotes=keep[0][0].quotes)
            ok = check_one(probe, req, merged, corpus).ok
        if ok:
            rows.append(Row(rid, req.quote, merged, [f.fid for f, _r, _c in keep], composed=True))
        else:
            withheld.append(Withheld(
                rid=rid, before=req.quote, fids=[f.fid for f, _r, _c in keep],
                reasons=["к одному требованию предложено несколько формулировок, и они правят одни и те же предложения: "
                         "склеить их без потери смысла нельзя"],
                needs=[_need(f) for f, _r, _c in keep],
                qids=[q for q in dict.fromkeys(f.question_id for f, _r, _c in keep) if q]))
    del qtext
    return rows, withheld


def apply(result: Result) -> int:
    """Run the gate on the result: clear the wording of every withheld rewrite and record why (``audit``).
    Returns the number of withheld findings. Safe to call twice."""
    rows, withheld = plan(result)
    n = 0
    by_fid = {f.fid: f for f in result.findings}
    for w in withheld:
        for fid in w.fids:
            f = by_fid.get(fid)
            if f is None or not f.fixed_wording:
                continue
            result.audit.append({"type": "decision", "what": "rewrite_withheld", "fid": fid, "rid": w.rid,
                                 "reasons": w.reasons, "needs": w.needs, "qids": w.qids, "gaps": w.gaps,
                                 "rewrite": f.fixed_wording[:600]})
            f.fixed_wording = ""
            n += 1
    return n


def recorded(result: Result) -> list[Withheld]:
    """Withheld rewrites that an earlier ``apply`` recorded in the audit (one entry per finding, grouped by requirement)."""
    by_req = {r.rid: r.quote for r in result.requirements}
    grouped: dict[tuple[str, str], Withheld] = {}
    for a in result.audit:
        if a.get("what") != "rewrite_withheld":
            continue
        key = (a.get("rid", ""), "|".join(a.get("reasons") or []))
        w = grouped.get(key)
        if w is None:
            w = grouped[key] = Withheld(rid=a.get("rid", ""), before=by_req.get(a.get("rid", ""), ""), fids=[],
                                        reasons=list(a.get("reasons") or []), needs=[], qids=[], gaps=[])
        w.fids.append(a.get("fid", ""))
        w.needs += [x for x in a.get("needs") or [] if x not in w.needs]
        w.qids += [x for x in a.get("qids") or [] if x not in w.qids]
        w.gaps += [x for x in a.get("gaps") or [] if x not in w.gaps]
    return list(grouped.values())
