"""Answers of the document's author to the clarifying questions.

Three parts: a tolerant parser of the questionnaire file (``<stem>.questions.md``), an interactive asker for the
terminal, and the application of the answers to findings. The answers are DATA: the verdict is never decided from
them by a model, and an answer that talks to the reviewer (an injection) is not applied at all.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .model import ADDRESSEES, GAP_TYPES, Finding, Question, QuestionDraft, Requirement
from .rules.safety_rules import injection_hits
from .textfmt import shorten

LETTERS = "абвг"
LATIN_TO_CYR = {"a": "а", "b": "б", "c": "в", "d": "г"}
MAX_ANSWER_CHARS = 2000
MAX_INTERACTIVE = 7

NOT_KNOWN = ("не знаю", "пока не знаю", "незнаю", "затрудняюсь", "пропустить", "пропуск", "без ответа",
             "нет ответа", "skip", "не помню", "не уверен", "не уверена")

_HEAD = re.compile(
    r"^[\s>#*_`\-]*(?:\d+[.)]\s*)?[*_`]*\s*([ВвBb])[-–—‑]?(\d{1,3})\b(?=\s*(?:$|[·•|:.,;—–\-)\]*_`]))")
_ANSWER_LINE = re.compile(r"^[\s>*_`#\-]*(?:ответ|answer)\s*[*_`]*\s*[:：]\s*[*_`]*(.*)$", re.I)
_FINGERPRINT = re.compile(r"^[\s>*_`#\-]*отпечаток вопроса\s*[:：]\s*[*_`]*\s*([0-9a-f]{6,64})\b", re.I)
_DOC_FINGERPRINT = re.compile(r"^[\s>*_`#\-]*отпечаток документа\s*[:：]\s*[*_`]*\s*([0-9a-f]{6,64})\b", re.I | re.M)
_RERUN = re.compile(r"^\s*(?:когда ответите|повторный прогон)", re.I)
_LETTER = re.compile(r"^\(?\s*([абвгАБВГa-dA-D])\s*(?:[).:]|$)\s*(.*)$", re.S)


def norm_qid(num: int) -> str:
    return f"В-{num:02d}"


def _flat(s: str) -> str:
    return re.sub(r"[\W_]+", " ", (s or "").casefold().replace("ё", "е")).strip()


def _strip_decor(s: str) -> str:
    t = (s or "").strip()
    t = re.sub(r"^[*_`\s]+|[*_`\s]+$", "", t)
    return t.strip()


@dataclass
class Answer:
    qid: str
    raw: str  # as the author wrote it
    text: str  # expanded: the option text for a letter, else the author's words
    letter: str = ""  # "а".."г" when an option was chosen
    kind: str = "free"  # "option" | "free"
    note: str = ""  # words written after the letter


@dataclass
class Parsed:
    answers: dict[str, Answer] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)  # ids in the file that the current questionnaire does not have
    warnings: list[str] = field(default_factory=list)  # Russian, for the passport
    sha256: str = ""  # hash of the answers file text (or of the typed answers): ``RunInfo.answers_sha256``
    doc_fingerprint: str = ""  # «Отпечаток документа» the questionnaire was issued for, "" when the file has none
    stale: dict[str, str] = field(default_factory=dict)  # qid → why the answer was not applied (fingerprint mismatch)


def is_unanswered(text: str) -> bool:
    t = (text or "").strip()
    if t in ("", "?", "-", "—", "–"):
        return True
    f = _flat(t)
    if not f:
        return True
    return any(f == _flat(n) or f.startswith(_flat(n) + " ") for n in NOT_KNOWN)


def interpret(qid: str, raw: str, options: list[str]) -> Answer | None:
    """One raw answer → Answer, or None when the author did not answer."""
    raw = (raw or "").strip()
    text = _strip_decor(raw)[:MAX_ANSWER_CHARS]
    if not text or is_unanswered(text):
        return None
    m = _LETTER.match(text)
    if m:
        lt = m.group(1).casefold()
        lt = LATIN_TO_CYR.get(lt, lt)
        k = LETTERS.find(lt)
        if 0 <= k < len(options):
            note = m.group(2).strip()
            return Answer(qid, raw, options[k], LETTERS[k], "option", note)
    ft = _flat(text)
    for k, o in enumerate(options):
        if ft and ft == _flat(o):
            return Answer(qid, raw, options[k], LETTERS[k], "option")
    return Answer(qid, raw, text, "", "free")


def parse(text: str, questions: list[Question]) -> Parsed:
    """Questionnaire text → answers by question id. Never raises; problems go to ``warnings``."""
    out = Parsed()
    out.sha256 = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
    m_doc = _DOC_FINGERPRINT.search(text or "")
    out.doc_fingerprint = m_doc.group(1).lower() if m_doc else ""
    by_id = {q.qid: q for q in questions}
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[tuple[str, list[str]]] = []
    for ln in lines:
        m = _HEAD.match(ln)
        if m:
            blocks.append((norm_qid(int(m.group(2))), [ln]))
        elif blocks:
            blocks[-1][1].append(ln)
    if not blocks:
        out.warnings.append("В файле ответов не найдено ни одного вопроса вида «В-01»: ответы не применены.")
        return out
    unknown: list[str] = []
    for qid, body in blocks:
        q = by_id.get(qid)
        if q is None:
            if qid not in unknown:
                unknown.append(qid)
            continue
        cut = next((i for i, ln in enumerate(body) if _RERUN.match(ln)), len(body))
        body = body[:cut]
        idx = [i for i, ln in enumerate(body) if i > 0 and _ANSWER_LINE.match(ln)]
        if not idx:
            continue
        first = idx[-1]
        parts = [_ANSWER_LINE.match(body[first]).group(1)] + body[first + 1:]
        raw = "\n".join(parts).strip()
        a = interpret(qid, raw, q.options)
        if a is None:
            continue
        given = next((m.group(1).lower() for m in (_FINGERPRINT.match(ln) for ln in body[1:first]) if m), "")
        if given and q.fingerprint and given != q.fingerprint.lower():
            out.stale[qid] = given
            out.warnings.append(
                f"Ответ на {qid} не применён: вопрос или документ изменились с тех пор, как выдан файл ответов "
                "(отпечаток вопроса не совпал). Ответьте на вопрос в новой анкете.")
            continue
        if qid in out.answers:
            out.warnings.append(f"Вопрос {qid} встречается в файле ответов несколько раз: взят последний непустой ответ.")
        out.answers[qid] = a
    if unknown:
        out.unknown = unknown
        out.warnings.append("В файле ответов есть вопросы, которых нет в текущем отчёте (" + ", ".join(unknown)
                            + "): они пропущены. Возможно, анкета от другого прогона.")
    return out


def from_raw(raw: dict[str, str], questions: list[Question]) -> Parsed:
    """Answers collected in the terminal (qid → what the author typed) through the same interpretation."""
    out = Parsed()
    out.sha256 = hashlib.sha256("\n".join(f"{k}={v}" for k, v in sorted(raw.items())).encode("utf-8")).hexdigest()
    by_id = {q.qid: q for q in questions}
    for qid, r in raw.items():
        q = by_id.get(qid)
        if q is None:
            out.unknown.append(qid)
            continue
        a = interpret(qid, r, q.options)
        if a is not None:
            out.answers[qid] = a
    return out


# --- interactive ---------------------------------------------------------------------------------------


def ask(questions: list[Question], *, input_fn: Callable[[str], str] = input,
        print_fn: Callable[[str], None] = print, limit: int = MAX_INTERACTIVE) -> Parsed:
    """Ask at most ``limit`` questions, blockers first. Enter skips a question, ``q`` stops, EOF and Ctrl-C keep
    the answers given so far."""
    order = {"blocker": 0, "important": 1, "nice": 2}
    todo = sorted(questions, key=lambda q: (order.get(q.priority, 1), q.qid))[:limit]
    raw: dict[str, str] = {}
    if not todo:
        return Parsed()
    print_fn(f"Уточняющие вопросы: задам не больше {len(todo)}. Ответ — буква варианта или своими словами; "
             "Enter — пропустить; q — закончить.")
    for k, q in enumerate(todo, 1):
        print_fn("")
        print_fn(f"[{k}/{len(todo)}] {q.qid} · {q.priority}: {q.text}")
        if q.quote is not None:
            print_fn(f"    Место в документе (строка {q.quote.line_start}): «{shorten(q.quote.text, 200)}»")
        for i, o in enumerate(q.options):
            print_fn(f"    {LETTERS[i]}) {o}")
        try:
            ans = input_fn("Ответ: ")
        except (EOFError, KeyboardInterrupt):
            print_fn("")
            print_fn("Ввод прерван: учитываю ответы, данные до этого момента.")
            break
        ans = (ans or "").strip()
        if ans.casefold() in ("q", "й", "quit", "выход"):
            break
        if ans:
            raw[q.qid] = ans
    return from_raw(raw, questions)


# --- application -----------------------------------------------------------------------------------------


def same_question(q: Question, f: Finding) -> bool:
    """The question was built from this finding's own template (not made up by a model)."""
    d: QuestionDraft | None = f.question
    return d is not None and _flat(d.text) == _flat(q.text)


@dataclass
class Applied:
    answered: int = 0  # answers that were taken into account
    total: int = 0  # questions before follow-ups
    closed: list[str] = field(default_factory=list)  # finding ids
    follow_ups: int = 0
    rejected_injection: list[str] = field(default_factory=list)  # question ids
    by_model: bool = False
    closes: dict[str, list[str]] = field(default_factory=dict)  # fid → [qid]
    fixed: list[dict[str, str]] = field(default_factory=list)  # {rid, wording, qid, fid}


OFFLINE_OPTION = "Выбран вариант «{opt}»: замечания {fids} закрыты ответом; внесите этот вариант в документ."
OFFLINE_FREE = ("Ответ учтён, требуется правка текста. Без модели агент не проверяет, закрывает ли ответ замечание: "
                "оно остаётся открытым.")
OFFLINE_OPTION_NOCLOSE = ("Выбран вариант «{opt}». Ответ учтён, требуется правка текста: вопрос составлен не по "
                          "шаблону одного замечания, замечание остаётся открытым.")


def apply_offline(questions: list[Question], findings: list[Finding], parsed: Parsed, applied: Applied,
                  only: set[str] | None = None) -> None:
    by_fid = {f.fid: f for f in findings}
    for q in questions:
        a = parsed.answers.get(q.qid)
        if a is None or (only is not None and q.qid not in only):
            continue
        q.answer = a.text
        linked = [by_fid[i] for i in q.finding_ids if i in by_fid and by_fid[i].status == "open"]
        if a.kind == "option":
            close = [f for f in linked if same_question(q, f)]
            if close:
                for f in close:
                    f.closed_by_answer = True
                    applied.closed.append(f.fid)
                    applied.closes.setdefault(f.fid, []).append(q.qid)
                q.answer_effect = OFFLINE_OPTION.format(opt=shorten(a.text, 160), fids=", ".join(f.fid for f in close))
            else:
                q.answer_effect = OFFLINE_OPTION_NOCLOSE.format(opt=shorten(a.text, 160))
        else:
            q.answer_effect = OFFLINE_FREE
        applied.answered += 1


def is_injection(text: str) -> bool:
    try:
        return bool(injection_hits(text)[0])
    except Exception:  # a detector bug must never stop the run
        return False


def question_block(q: Question, by_fid: dict[str, Finding], req_text: dict[str, str]) -> str:
    """One question with its answer and the linked findings, for the model prompt."""
    lines = [f"qid: {q.qid}", f"вопрос: {q.text}"]
    if q.options:
        lines.append("варианты: " + " | ".join(f"{LETTERS[i]}) {o}" for i, o in enumerate(q.options)))
    from .prompts import neutralize
    lines.append("ответ автора: " + neutralize(q.answer))
    lines.append("связанные замечания:")
    for fid in q.finding_ids:
        f = by_fid.get(fid)
        if f is None or f.status != "open":
            continue
        qt = "; ".join(shorten(x.text, 300) for x in f.quotes[:2])
        lines.append(f"  - {f.fid} | {f.title} | {shorten(f.problem, 400)} | цитата: {qt}")
    rids: list[str] = []
    for fid in q.finding_ids:
        f = by_fid.get(fid)
        for r in (f.req_ids if f else []):
            if r not in rids:
                rids.append(r)
    if rids:
        lines.append("требования:")
        for r in rids[:6]:
            lines.append(f"  - {r}: {shorten(req_text.get(r, ''), 500)}")
    return "\n".join(lines)


def parse_model(ans: dict[str, Any], questions: list[Question], findings: list[Finding],
                requirements: list[Requirement], sent: set[str], applied: Applied, audit: list[dict]) -> list[Question]:
    """Validate the model's answer-analysis and apply it. Unknown ids are dropped. Returns follow-up questions."""
    by_q = {q.qid: q for q in questions}
    by_fid = {f.fid: f for f in findings}
    known_rids = {r.rid for r in requirements}
    follow: list[Question] = []
    seen: set[str] = set()
    for x in ans.get("answers") or []:
        if not isinstance(x, dict):
            continue
        qid = str(x.get("qid") or "").strip()
        q = by_q.get(qid)
        if q is None or qid not in sent or qid in seen:
            audit.append({"type": "decision", "what": "answer_unknown_qid", "qid": qid[:20]})
            continue
        seen.add(qid)
        linked = [i for i in q.finding_ids if i in by_fid and by_fid[i].status == "open"]
        understood = str(x.get("understood") or "").strip()
        closes = []
        for fid in x.get("closes") or []:
            fid = str(fid).strip()
            if fid in linked and fid not in closes:
                closes.append(fid)
            else:
                audit.append({"type": "decision", "what": "answer_close_dropped", "qid": qid, "fid": fid[:20]})
        fixed_rows: list[dict[str, str]] = []
        for fx in x.get("fixed") or []:
            if not isinstance(fx, dict):
                continue
            rid = str(fx.get("rid") or "").strip()
            wording = str(fx.get("wording") or "").strip()
            if rid not in known_rids or not wording:
                continue
            host = next((i for i in linked if rid in by_fid[i].req_ids), None)
            if host is None:
                audit.append({"type": "decision", "what": "answer_fixed_dropped", "qid": qid, "rid": rid})
                continue
            fixed_rows.append({"rid": rid, "wording": wording, "qid": qid, "fid": host})
        for fid in closes:
            f = by_fid[fid]
            f.closed_by_answer = True
            applied.closed.append(fid)
            applied.closes.setdefault(fid, []).append(qid)
        for row in fixed_rows:
            f = by_fid[row["fid"]]
            f.fixed_wording = row["wording"]
            applied.fixed.append(row)
        eff = understood or "Ответ учтён."
        if closes:
            eff += " Закрыты замечания: " + ", ".join(closes) + " (после внесения ответа в документ)."
        else:
            eff += " Замечания остаются открытыми."
        q.answer_effect = eff
        applied.answered += 1
        fu = x.get("follow_up")
        if isinstance(fu, dict) and str(fu.get("text") or "").strip():
            opts = [str(o).strip() for o in fu.get("options") or [] if str(o).strip()][:4]
            addr = fu.get("addressee") if fu.get("addressee") in ADDRESSEES else "analyst"
            follow.append(Question(qid="", text=str(fu["text"]).strip(), why=f"Возник из ответа на {qid}.",
                                   addressee=addr, options=opts, priority="important",
                                   finding_ids=list(q.finding_ids[:1]), req_ids=list(q.req_ids), quote=q.quote,
                                   gap=fu.get("gap") if fu.get("gap") in GAP_TYPES else ""))
    for qid in sent - seen:
        q = by_q[qid]
        q.answer_effect = "Модель не разобрала этот ответ; замечания остаются открытыми."
    return follow


def summary(audit: list[dict]) -> dict[str, Any] | None:
    for a in audit:
        if a.get("type") == "answers_summary":
            return a
    return None
