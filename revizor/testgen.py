"""Acceptance-test drafts without a model: boundary cases from numeric limits and plain «when … then …» sentences.

Also the shared finishing steps for drafts that come from the model: merge without duplicates, deterministic ids,
and blocking by open questions. Generic patterns only; nothing here knows a particular document.
"""
from __future__ import annotations

import re
from typing import Any

from .model import Finding, Question, Requirement, TestDraft
from .textfmt import shorten

MAX_BOUNDARY_PER_REQ = 3
MAX_SCENARIO_DRAFTS = 12
MAX_MODEL_DRAFTS = 25

_NUM = r"\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_UNIT = (r"(?:\s*(?:%|₸|тенге|тг|руб\w*|\$|usd|eur|символ\w*|знак\w*|букв\w*|цифр\w*|секунд\w*|сек\b|мин(?:ут\w*)?\b|"
         r"час\w*|дн\w*|сут\w*|недел\w*|месяц\w*|раз\w*|попыт\w*|шт\b|штук\w*|мб|кб|гб|мс|кбайт\w*|мбайт\w*|"
         r"запрос\w*|уведомлен\w*|операци\w*|транзакци\w*|пользовател\w*|клиент\w*|порог\w*|цел\w*))?")

# What is cut out before looking for limits: code, links, dates, identifiers, versions.
_NOISE = [
    re.compile(r"```.*?```", re.S), re.compile(r"`[^`\n]*`"), re.compile(r"https?://\S+"),
    re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b"), re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"), re.compile(r"\b[\w]+(?:[-‑–]\d+)+\b"),
    re.compile(r"\bv?\d+\.\d+\.\d+\b"), re.compile(r"\b(?:19|20)\d{2}\s*(?:г\.?|года?)\b"),
    re.compile(r"\b(?:HTTP|http|код|status|статус)\s*[:=]?\s*[1-5]\d\d\b"), re.compile(r"\b[1-5]\d\d\s+(?:OK|Created|Bad|Not|Unauthorized|Forbidden)\b"),
]

_RANGE = re.compile(rf"(?<![\w.,])от\s+({_NUM})\s*{_UNIT}\s+до\s+({_NUM})({_UNIT})(\s+включительно)?", re.I)
_RANGE_DASH = re.compile(rf"(?<![\w.,\-])({_NUM})\s*[–—]\s*({_NUM})\s*((?:%|₸|тенге|тг|символ\w*|знак\w*|секунд\w*|сек\b|мин\w*|час\w*|дн\w*|раз\w*|попыт\w*|мб|кб|гб|мс))", re.I)
_MAX = re.compile(rf"(?<![\w.,])(не\s+(?:более|больше|выше|длиннее)|максимум|максимальн\w+(?:\s+\w+){{0,3}}?|≤|<=|до)\s*[:\-–—]?\s*({_NUM})({_UNIT})", re.I)
_MIN = re.compile(rf"(?<![\w.,])(не\s+(?:менее|меньше|ниже|короче)|минимум|минимальн\w+(?:\s+\w+){{0,3}}?|≥|>=|от)\s*[:\-–—]?\s*({_NUM})({_UNIT})", re.I)
_OVER = re.compile(rf"(?<![\w.,])(?<!не\s)(более|свыше|больше)\s+({_NUM})({_UNIT})", re.I)
_UNDER = re.compile(rf"(?<![\w.,])(?<!не\s)(менее|меньше)\s+({_NUM})({_UNIT})", re.I)
_REJECT = re.compile(r"отклон\w*|ошибк\w*|недопустим\w*|не\s+принима\w*|отказ\w*|запрещ\w*|не\s+допуска\w*|не\s+может\s+быть", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_INCL_WORDS = re.compile(r"включительно|включая\s+границ|(?:обе\s+)?границы\s+включен", re.I)
_EXCL_WORDS = re.compile(r"не\s+включая|исключая|не\s+включительно|(?:обе\s+)?границы\s+не\s+включен|строго\s+(?:больше|меньше|между)", re.I)
_STEP_KNOWN = re.compile(r"цел(?:ое|ых|ым|ого|ые)\b|целочисл|\bцелы\w+", re.I)
_STEP_QUESTION = re.compile(r"\bшаг|дробн|копе[ей]|тиын|округл|целое|целых|точност|десятичн", re.I)
_TEMPLATE_WHEN = re.compile(r"^\W*(?:выполнить|совершить|провести|осуществить)\s+(?:любое\s+|то\s+)?(?:действие|сценарий|шаг|операцию|проверку)\b", re.I)
_TEMPLATE_THEN = re.compile(r"^\W*(?:результат|поведение|ответ|система\s+работает)\s+(?:должн\w+\s+)?соответств\w+\s+(?:требовани|документ|описани|ожидани)", re.I)

_COND_START = re.compile(r"^(?:Если|Когда|При|После|Пока)\s+", re.I)
_HTTP_ROW = re.compile(r"^(?P<code>[1-5]\d\d)[.:]?\s+(?:Ответ\s+)?(?:в\s+случае|когда|если)[,\s]+(?:если\s+|когда\s+)?(?P<cond>.{6,200}?)[.]?$", re.I)
_ACC_KEYS = (("given", re.compile(r"исходн|дано|предусловие|given|начальн", re.I)),
             ("when", re.compile(r"действие|шаг|когда|when|операц", re.I)),
             ("then", re.compile(r"ожидаем|результат|тогда|then", re.I)))


def clean(text: str) -> str:
    t = text or ""
    for rx in _NOISE:
        t = rx.sub(" ", t)
    return re.sub(r"\s+", " ", t)


def to_number(token: str) -> float:
    t = re.sub(r"[\s  ]", "", token).replace(",", ".")
    return float(t)


def fmt(x: float, ref: str = "") -> str:
    """Number as the document wrote it: thousands separated by a space when the limit's own text did so."""
    grouped = bool(re.search(r"[\s\u00a0\u202f]", ref or ""))
    if float(x) == int(x):
        n = int(x)
        return f"{n:,}".replace(",", " ") if grouped else str(n)
    dec = len(repr(round(float(x), 6)).split(".")[1])
    return f"{x:.{dec}f}".replace(".", ",")


def _step(token: str) -> float:
    m = re.search(r"[.,](\d+)$", token.strip())
    return 10.0 ** -len(m.group(1)) if m else 1.0


def _unit(u: str) -> str:
    return re.sub(r"\s+", " ", (u or "").strip())


def _sentence(text: str, pos: int) -> str:
    start = max(text.rfind(".", 0, pos), text.rfind("\n", 0, pos)) + 1
    ends = [i for i in (text.find(".", pos), text.find("\n", pos)) if i != -1]
    end = min(ends) if ends else len(text)
    return text[start:end + 1].strip()


def _word_states(word: str) -> bool:
    """«не более», «не менее», «максимум», «≤» say that the number itself is allowed; a bare «до» or «от» does not."""
    return word.strip().lower() not in ("до", "от")


def limits(text: str) -> list[dict[str, Any]]:
    """Numeric limits in a requirement: [{kind, low, high, unit, tok, span, sentence, inclusive}]."""
    t = clean(text)
    found: list[dict[str, Any]] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    def add(kind, m, low=None, high=None, unit="", tok="", inclusive=True, stated=False):
        if not free(m.start(), m.end()):
            return
        taken.append((m.start(), m.end()))
        sent = _sentence(t, m.start())
        if kind == "range":  # the text may say outright whether the borders belong to the range
            if _EXCL_WORDS.search(sent):
                inclusive, stated = False, True
            elif stated or _INCL_WORDS.search(sent):
                inclusive, stated = True, True
        found.append({"kind": kind, "low": low, "high": high, "unit": _unit(unit), "tok": tok,
                      "sentence": sent, "inclusive": inclusive, "stated": stated, "pos": m.start(),
                      "phrase": m.group(0).strip(),
                      "step_known": bool(re.search(r"[.,]\d", tok or "") or _STEP_KNOWN.search(sent))})

    for m in _RANGE.finditer(t):
        lo, hi = to_number(m.group(1)), to_number(m.group(2))
        if lo < hi:
            add("range", m, lo, hi, m.group(3), m.group(1), True, bool(m.group(4)))
    for m in _RANGE_DASH.finditer(t):
        lo, hi = to_number(m.group(1)), to_number(m.group(2))
        if lo < hi:
            add("range", m, lo, hi, m.group(3), m.group(1), True)
    for m in _MAX.finditer(t):
        add("max", m, None, to_number(m.group(2)), m.group(3), m.group(2), True, _word_states(m.group(1)))
    for m in _MIN.finditer(t):
        add("min", m, to_number(m.group(2)), None, m.group(3), m.group(2), True, _word_states(m.group(1)))
    for m in _OVER.finditer(t):
        add("over", m, to_number(m.group(2)), None, m.group(3), m.group(2), False, True)
    for m in _UNDER.finditer(t):
        add("under", m, None, to_number(m.group(2)), m.group(3), m.group(2), False, True)
    found.sort(key=lambda d: d["pos"])
    return found


def _label(req: Requirement) -> str:
    return req.doc_id or req.rid


def _subject(req: Requirement, lim: dict[str, Any]) -> str:
    s = shorten(lim["sentence"] or req.summary or req.quote, 90)
    return s.rstrip(".")


def _vals(lim: dict[str, Any]) -> tuple[list[float], list[float], list[str]]:
    """(values that must pass, values just outside, texts of the limit)"""
    k = lim["kind"]
    st = _step(lim["tok"]) if lim["tok"] else 1.0
    lo, hi = lim["low"], lim["high"]
    if k == "range":
        if not lim.get("inclusive", True):
            return [lo + st, hi - st], [lo, hi], []
        return [lo, hi], [lo - st, hi + st], []
    if k == "max":
        return [hi], [hi + st], []
    if k == "min":
        return [lo], [lo - st], []
    if k == "over":  # strictly more than lo
        return [lo + st], [lo], []
    return [hi - st], [hi], []  # under: strictly less than hi


def _fmt_list(vals: list[float], unit: str, ref: str) -> str:
    return ", ".join(fmt(v, ref) for v in vals) + (f" {unit}" if unit else "")


STEP_NOTE = "шаг значения не задан: взяты соседние целые"


def boundary_drafts(reqs: list[Requirement]) -> list[TestDraft]:
    out: list[TestDraft] = []
    for req in reqs:
        text = req.quote or req.summary
        lims = limits(text)[:MAX_BOUNDARY_PER_REQ]
        for lim in lims:
            inside, outside, _ = _vals(lim)
            if min(inside + outside) < 0 and lim["kind"] != "range" and lim["unit"] not in ("%",):
                outside = [v for v in outside if v >= 0]
            subj = _subject(req, lim)
            ref = lim["tok"]
            unit = lim["unit"]
            lab = _label(req)
            stated = bool(lim.get("stated"))
            note = "" if lim.get("step_known", True) else f" ({STEP_NOTE}.)"
            own = [fmt(v, ref) for v in (lim["low"], lim["high"]) if v is not None]
            phrase = lim.get("phrase", "")
            if inside:
                then = (f"Значения принимаются без ошибки: по требованию {lab} допустимо «{phrase}»." if stated
                        else f"Значения принимаются без ошибки (требование {lab} допускает их).")
                out.append(TestDraft(
                    tid="", kind="boundary", req_ids=[req.rid], origin="rule", explicit_limit=stated, values=own,
                    title=f"{lab}: граничные допустимые значения {_fmt_list(inside, unit, ref)}",
                    given=f"Система работает в обычном режиме; проверяется требование {lab}: «{subj}».",
                    when=f"Ввести или передать значения на границе допустимого: {_fmt_list(inside, unit, ref)}.{note}",
                    then=then))
            if outside:
                reject = next((x for x in _SENT_SPLIT.split(text) if re.search(r"отклон|ошибк|не\s+принима|недопустим|не\s+допуска", x, re.I)), "")
                if reject:
                    then = f"Значения отклоняются так, как описано в требовании: «{shorten(reject, 160)}»."
                elif stated:
                    then = f"Значения не принимаются как допустимые: по требованию {lab} допустимо только «{phrase}»."
                else:
                    then = ""
                out.append(TestDraft(
                    tid="", kind="boundary", req_ids=[req.rid], origin="rule", explicit_limit=stated, values=own,
                    title=f"{lab}: значения сразу за границей {_fmt_list(outside, unit, ref)}",
                    given=f"Система работает в обычном режиме; проверяется требование {lab}: «{subj}».",
                    when=f"Ввести или передать значения сразу за границей: {_fmt_list(outside, unit, ref)}.{note}",
                    then=then))
    return out


# --- scenario drafts (plain sentences) ------------------------------------------------------------------------


def _split_when_then(sent: str) -> tuple[str, str] | None:
    """«Когда A, B.» → (A, B) for sentences with at most two commas; the last comma splits condition and result."""
    if not _COND_START.match(sent):
        return None
    body = _COND_START.sub("", sent, count=1)
    if body.count(",") not in (1, 2):
        return None
    cond, _, then = body.rpartition(",")
    cond, then = cond.strip(), then.strip().rstrip(".")
    if len(cond.split()) < 3 or len(then.split()) < 3:
        return None
    return cond, re.sub(r"^то\s+", "", then)


def scenario_drafts(reqs: list[Requirement], skip_rids: set[str]) -> list[TestDraft]:
    """«Когда <условие>, <результат>» and «400. Ответ в случае, если …» → given/when/then taken from the sentence."""
    out: list[TestDraft] = []
    for req in reqs:
        if len(out) >= MAX_SCENARIO_DRAFTS:
            break
        if req.rid in skip_rids or req.kind in ("goal", "constraint", "nonfunctional", "other"):
            continue
        lab = _label(req)
        text = re.sub(r"\s+", " ", clean(req.quote or req.summary))
        text = re.sub(r"^\W*(?:[А-ЯA-Z]{1,4}[-‑]\d+|REQ-\d+)[.:)\s]*", "", text).strip()
        if req.kind == "api":
            m = _HTTP_ROW.match(text)
            if m:
                code, cond = m.group("code"), m.group("cond").strip()
                ok = code.startswith("2")
                out.append(TestDraft(
                    tid="", kind="positive" if ok else "negative", req_ids=[req.rid], origin="rule",
                    title=f"{req.rid}: ответ {code} — {shorten(cond, 70)}",
                    given="Метод интерфейса доступен, тестовые данные подготовлены.",
                    when=f"Отправить запрос так, чтобы выполнилось условие: {cond}.",
                    then=f"Возвращается ответ с кодом {code} (так описано в документе)."))
            continue
        for sent in _SENT_SPLIT.split(text):
            sent = sent.strip(" |*")
            parts = _split_when_then(sent)
            if not parts:
                continue
            cond, then = parts
            neg = bool(re.search(r"ошибк|отказ|недоступн|не\s+удал|превыш|отклон|таймаут|тайм-аут", cond, re.I))
            out.append(TestDraft(
                tid="", kind="negative" if neg else "positive", req_ids=[req.rid], origin="rule",
                title=f"{lab}: если {shorten(cond, 80)}",
                given="Система работает в обычном режиме, данные для сценария подготовлены.",
                when=f"Создать условие: {cond}.", then=f"{then[0].upper() + then[1:]} (требование {lab})."))
            break
    return out


def acceptance_drafts(reqs: list[Requirement]) -> list[TestDraft]:
    """Rows of an acceptance table («Исходные условия | Действие | Ожидаемый результат») are tests already."""
    out: list[TestDraft] = []
    for req in reqs:
        if req.kind != "acceptance" or len(out) >= 12:
            continue
        cells = [c.strip() for c in (req.quote or "").split(" | ")]
        got: dict[str, str] = {}
        for c in cells:
            head, sep, val = c.partition(":")
            if not sep:
                continue
            for key, rx in _ACC_KEYS:
                if key not in got and rx.search(head):
                    got[key] = val.strip()
                    break
        if len(got) < 3 or not got["then"]:
            continue
        out.append(TestDraft(
            tid="", kind="positive", req_ids=[req.rid], origin="rule",
            title=f"{req.rid}: {shorten(got['when'], 80)}", given=got["given"], when=got["when"], then=got["then"]))
    return out


# --- blocking, merging, ids -----------------------------------------------------------------------------------------


def from_findings(reqs: list[Requirement], findings: list[Finding], taken: set[tuple[str, str]]) -> list[TestDraft]:
    """One blocked draft per CRITICAL/MAJOR finding that names a requirement and has a question: the expected result is unknown."""
    by_rid = {r.rid: r for r in reqs}
    out: list[TestDraft] = []
    for f in findings:
        if f.status != "open" or f.advisory or f.severity not in ("CRITICAL", "MAJOR"):
            continue
        if not f.question_id or not f.req_ids or f.req_ids[0] not in by_rid:
            continue
        req = by_rid[f.req_ids[0]]
        key = (req.rid, f.criterion)
        if key in taken:
            continue
        taken.add(key)
        lab = _label(req)
        kind = "negative" if f.criterion in ("completeness", "feasibility") else "positive"
        out.append(TestDraft(
            tid="", kind=kind, req_ids=[req.rid], origin="rule", finding_ids=[f.fid],
            title=f"{lab}: {_what(req)}",
            given=f"Система работает в обычном режиме; проверяется требование {lab}: «{shorten(req.summary or req.quote, 100)}».",
            when="Выполнить действие, описанное в требовании, в том месте, где найдено замечание.",
            then=""))
        if len(out) >= 15:
            break
    return out


def _what(req: Requirement) -> str:
    """What the test is about: the requirement's own short summary, without its id prefix."""
    t = re.sub(r"^\W*(?:[А-ЯA-Z]{1,4}[-‑]\d+|REQ-\d+)[.:)\s]*", "", req.summary or req.quote or "").strip()
    t = re.sub(r"\s+", " ", clean(t)).strip(" .:;)-")
    return shorten(_SENT_SPLIT.split(t)[0] if t else (req.summary or req.quote), 100).rstrip(".")


def _flat(s: str) -> str:
    return re.sub(r"[\W_]+", " ", (s or "").casefold()).strip()


def _nums(s: str) -> frozenset[str]:
    return frozenset(re.sub(r"[\s  ]", "", x) for x in re.findall(_NUM, s or ""))


def merge(det: list[TestDraft], extra: list[TestDraft]) -> list[TestDraft]:
    """Deterministic drafts first; model drafts that repeat one of them (same requirement and title, or the same
    numbers on the same requirement for boundary cases) are dropped."""
    out = list(det)
    keys = {(tuple(t.req_ids), _flat(t.title)) for t in out}
    for t in extra:
        key = (tuple(t.req_ids), _flat(t.title))
        if key in keys:
            continue
        nums = _nums(t.title + " " + t.when)
        if t.kind == "boundary" and nums and any(
                d.kind == "boundary" and set(d.req_ids) & set(t.req_ids) and nums & _nums(d.title + " " + d.when)
                for d in det):
            continue
        keys.add(key)
        out.append(t)
    return out


def is_template(t: TestDraft) -> bool:
    """A draft without a concrete action or a concrete expected result: the step is a stock phrase, or empty."""
    when, then = (t.when or "").strip(), (t.then or "").strip()
    if len(when.split()) < 3 or _TEMPLATE_WHEN.match(when):
        return True
    return not then or bool(_TEMPLATE_THEN.match(then))


def _words(s: str) -> set[str]:
    return set(_flat(s).split())


def _jaccard(a: str, b: str) -> float:
    x, y = _words(a), _words(b)
    return len(x & y) / len(x | y) if x | y else 1.0


def same_check(a: TestDraft, b: TestDraft) -> bool:
    """Same requirement, same input values, same expected result — one draft is enough."""
    return (set(a.req_ids) == set(b.req_ids) and a.kind == b.kind and _nums(a.when) == _nums(b.when)
            and _jaccard(a.then, b.then) >= 0.8 and _jaccard(a.when, b.when) >= 0.5)


def dedupe(drafts: list[TestDraft]) -> list[TestDraft]:
    out: list[TestDraft] = []
    for t in drafts:
        twin = next((o for o in out if same_check(o, t)), None)
        if twin is None:
            out.append(t)
            continue
        for q in t.blocked_by:
            if q not in twin.blocked_by:
                twin.blocked_by.append(q)
        for f in t.finding_ids:
            if f not in twin.finding_ids:
                twin.finding_ids.append(f)
        if not twin.then and t.then:  # keep the concrete one
            twin.then, twin.when, twin.given = t.then, t.when, t.given
    return out


def _disputes(f: Finding, t: TestDraft, req_text: str) -> bool:
    """The finding names one of the limit's own numbers in its text or in a quote narrower than the requirement."""
    from .rewrite_gate import NARROW_QUOTE, _norm_num, specials
    parts = [f.title or "", f.problem or "", f.recommendation or ""]
    parts += [q.text for q in f.quotes if len(q.text) < NARROW_QUOTE * max(len(req_text), 1)]
    seen = specials("\n".join(parts), label=True).numbers
    return any(_norm_num(v) in seen for v in t.values)


def finalize(drafts: list[TestDraft], reqs: list[Requirement], findings: list[Finding], questions: list[Question],
             audit: list[dict] | None = None) -> list[TestDraft]:
    """Deterministic order and ids; blocked_by from open CRITICAL/MAJOR findings whose question is still unanswered.

    A boundary draft that code built from a limit with stated inclusiveness is ready whatever other questions the
    requirement has; only an open finding that names the limit's own numbers blocks it, and only through its question.
    A draft without a concrete action or expected result is not a draft: it goes to the list «что нельзя проверить»
    (``audit`` entry ``test_unverifiable``)."""
    order = {r.rid: k for k, r in enumerate(reqs)}
    req_text = {r.rid: r.quote or r.summary for r in reqs}
    kind_rank = {"boundary": 0, "positive": 1, "negative": 2}
    origin_rank = {"rule": 0, "model": 1}
    ranked = sorted(enumerate(drafts), key=lambda p: (order.get(p[1].req_ids[0], 10**6) if p[1].req_ids else 10**6,
                                                       origin_rank.get(p[1].origin, 2), kind_rank.get(p[1].kind, 3), p[0]))
    q_answered = {q.qid: bool(q.answer) and "обращённый к проверяющему" not in q.answer_effect for q in questions}
    q_known = set(q_answered)
    live = [f for f in findings if f.status == "open" and not f.advisory and not f.closed_by_answer]
    kept: list[TestDraft] = []
    unverifiable: list[dict] = []
    for _i, t in ranked:
        blocked = [q for q in t.blocked_by if q in q_known and not q_answered.get(q)]
        if t.explicit_limit and t.then:
            blocked = []
            for f in live:
                if set(f.req_ids) & set(t.req_ids) and _disputes(f, t, req_text.get(t.req_ids[0], "")):
                    if f.fid not in t.finding_ids:
                        t.finding_ids.append(f.fid)
                    if f.question_id and not q_answered.get(f.question_id) and f.question_id not in blocked:
                        blocked.append(f.question_id)
            if t.finding_ids and not blocked:  # disputed, but the finding has no question: nothing to wait for
                unverifiable.append({"req_ids": t.req_ids, "what": t.title, "blocked_by": [], "finding_ids": t.finding_ids,
                                     "why": "граница оспорена замечанием, вопроса к нему нет"})
                continue
            step_q = next((q.qid for q in questions if not q_answered.get(q.qid) and set(q.req_ids) & set(t.req_ids)
                           and _STEP_QUESTION.search(q.text)), "")
            if STEP_NOTE in t.when and step_q:
                t.when += f" Шаг значения уточняет вопрос {step_q}."
        else:
            for f in live:
                if (f.severity in ("CRITICAL", "MAJOR") and f.question_id and set(f.req_ids) & set(t.req_ids)
                        and not q_answered.get(f.question_id) and f.question_id not in blocked):
                    blocked.append(f.question_id)
            if not t.then and not blocked:  # no expected result in the document: ask the author, if a question exists
                qn = next((q.qid for q in questions if not q_answered.get(q.qid) and set(q.req_ids) & set(t.req_ids)), "")
                if qn:
                    blocked.append(qn)
        t.blocked_by = blocked
        if is_template(t):
            done = [f.question_id for f in live if f.question_id and q_answered.get(f.question_id)
                    and set(f.req_ids) & set(t.req_ids)]
            unverifiable.append({"req_ids": t.req_ids, "what": t.title, "blocked_by": blocked, "answered": done,
                                 "finding_ids": t.finding_ids,
                                 "why": "в документе нет конкретного действия или ожидаемого результата"})
            continue
        kept.append(t)
    out = dedupe(kept)
    for n, t in enumerate(out, 1):
        t.tid = f"Т-{n:02d}"
    if audit is not None and unverifiable:
        audit.append({"type": "test_unverifiable", "items": unverifiable})
    return out


def parse_model(ans: dict[str, Any], reqs: list[Requirement], findings: list[Finding], questions: list[Question],
                audit: list[dict]) -> list[TestDraft]:
    """Validate the model's drafts: unknown requirement, finding and question ids are dropped."""
    known = {r.rid for r in reqs}
    fids = {f.fid for f in findings}
    qids = {q.qid for q in questions}
    out: list[TestDraft] = []
    for x in (ans.get("tests") or [])[:MAX_MODEL_DRAFTS]:
        if not isinstance(x, dict) or not str(x.get("title") or "").strip():
            continue
        rids = [r for r in (str(v).strip() for v in x.get("req_ids") or []) if r in known]
        dropped = [r for r in (str(v).strip() for v in x.get("req_ids") or []) if r not in known]
        if dropped:
            audit.append({"type": "decision", "what": "test_req_dropped", "rids": dropped[:5]})
        if not rids:
            continue
        kind = x.get("kind") if x.get("kind") in ("positive", "negative", "boundary") else "positive"
        dep = str(x.get("depends_on") or "").strip()
        m = re.search(r"[ВBв]-?(\d{1,3})", dep)
        qid = f"В-{int(m.group(1)):02d}" if m else ""
        then = str(x.get("then") or "").strip()
        out.append(TestDraft(
            tid="", title=str(x["title"]).strip()[:200], kind=kind, req_ids=rids,
            given=str(x.get("given") or "").strip(), when=str(x.get("when") or "").strip(), then=then,
            blocked_by=[qid] if qid in qids else [],
            finding_ids=[f for f in (str(v).strip() for v in x.get("finding_ids") or []) if f in fids],
            origin="model"))
    return out


def deterministic(reqs: list[Requirement], findings: list[Finding]) -> list[TestDraft]:
    """Everything that needs no model, in generation order."""
    b = boundary_drafts(reqs)
    skip = {r for t in b for r in t.req_ids}
    taken: set[tuple[str, str]] = set()
    a = acceptance_drafts(reqs)
    s = scenario_drafts(reqs, skip)
    f = from_findings(reqs, findings, taken)
    return b + a + s + f
