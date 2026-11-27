"""Пример-свидетель: one concrete case on which two places of the document demand different things.

For every open contradiction (criterion ``consistency`` / ``set_consistency`` or a blocker «contradiction») the model
proposes one case with concrete values and the two places of the document with what each of them demands in that
case. The program keeps a case only when BOTH quotes are found in the document word for word (``QuoteIndex``, exact
match), the two places are different and the two expected outcomes differ. The finding itself was already confirmed
by the re-check; the witness only shows it on concrete values, it never adds, removes or re-levels a finding.

    python tools/witness.py reports/report.json --input docs/requirements.md
"""
from __future__ import annotations

import re
from typing import Any

from .model import Finding
from .quotes import QuoteIndex
from .textfmt import md, plain_quote, shorten

STAGE = "witness"
CRITERIA = ("consistency", "set_consistency")
MAX_TARGETS = 20

HEAD = """ЗАДАЧА: построить примеры-свидетели к противоречиям.
Ниже замечания о противоречиях в документе; каждое уже перепроверено. Для каждого построй ОДИН конкретный случай с конкретными значениями (сумма, дата и время, статус и валюта карты, ответ сервиса и т. п.), на котором два места документа требуют разного, так что выполнить оба сразу нельзя.
Для каждого примера:
- case — сам случай одной-двумя фразами, с конкретными значениями;
- a_quote — дословный фрагмент первого места документа, 4–25 слов, символ в символ, без номера строки; a_outcome — одно короткое предложение (до 20 слов): что по этому месту должно произойти в этом случае;
- b_quote — дословный фрагмент второго места документа; b_outcome — одно короткое предложение: что по нему должно произойти в этом же случае.
a_outcome и b_outcome должны исключать друг друга.
Если для замечания нельзя построить такой случай — например, расходятся только названия, оформление или перечни, а не поведение системы, — НЕ включай его в ответ. Не придумывай требований, которых нет в документе: обе цитаты берутся из документа.

ФОРМА ОТВЕТА:
{"witnesses":[{"fid":"D-022","case":"…","a_quote":"…","a_outcome":"…","b_quote":"…","b_outcome":"…"}]}
"""

_T = {"type": "string", "maxLength": 500}
SCHEMA = {"type": "object", "required": ["witnesses"], "properties": {
    "witnesses": {"type": "array", "x-drop-invalid-items": True, "items": {
        "type": "object", "required": ["fid", "case", "a_quote", "a_outcome", "b_quote", "b_outcome"],
        "properties": {"fid": {"type": "string"}, "case": _T, "a_quote": _T, "a_outcome": _T,
                       "b_quote": _T, "b_outcome": _T}}}}}


def targets(findings: list[Finding]) -> list[Finding]:
    """Open counted contradictions, most severe first, then in id order."""
    rank = {"CRITICAL": 0, "MAJOR": 1, "MINOR": 2}
    out = [f for f in findings if f.status == "open" and not f.advisory
           and (f.criterion in CRITERIA or f.blocker_reason == "contradiction")]
    out.sort(key=lambda f: (rank.get(f.severity, 3), f.fid))
    return out[:MAX_TARGETS]


def _line(f: Finding) -> str:
    qs = " / ".join(f"строка {q.line_start}: «{shorten(' '.join(q.text.split()), 220)}»" for q in f.quotes[:3])
    return " | ".join([f.fid, f.severity, f.title, shorten(" ".join(f.problem.split()), 500), qs])


def user_prompt(items: list[Finding], inj: str = "") -> str:
    return HEAD + inj + "\n\nЗАМЕЧАНИЯ:\n" + "\n".join(_line(f) for f in items)


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", " ", str(s or "").lower()).strip()


def check(answer: dict[str, Any], items: list[Finding], qi: QuoteIndex, model: str = "") -> tuple[dict[str, dict], list[dict]]:
    """Verify the model's witnesses by code. Returns ({fid: witness}, [{"fid", "reason"} for every dropped one])."""
    by_id = {f.fid: f for f in items}
    kept: dict[str, dict] = {}
    dropped: list[dict] = []
    for w in answer.get("witnesses") or []:
        if not isinstance(w, dict):
            continue
        fid = str(w.get("fid") or "").strip()
        why = ""
        qa = qb = None
        if fid not in by_id:
            why = "нет такого противоречия в списке"
        elif fid in kept:
            why = "второй пример к тому же замечанию"
        elif len(" ".join(str(w.get("case") or "").split())) < 15:
            why = "случай не описан"
        else:
            qa, qb = qi.find(str(w.get("a_quote") or "")), qi.find(str(w.get("b_quote") or ""))
            if qa is None or qb is None or not qa.exact or not qb.exact:
                why = "цитата не найдена в документе дословно"
            elif (qa.line_start, _norm(qa.text)) == (qb.line_start, _norm(qb.text)) or _norm(qa.text) == _norm(qb.text):
                why = "обе цитаты — одно и то же место"
            elif not _norm(w.get("a_outcome")) or not _norm(w.get("b_outcome")):
                why = "не сказано, чего требует одно из мест"
            elif _norm(w.get("a_outcome")) == _norm(w.get("b_outcome")):
                why = "оба места требуют одного и того же"
        if why:
            dropped.append({"fid": fid, "reason": why})
            continue
        assert qa is not None and qb is not None
        kept[fid] = {"case": " ".join(str(w["case"]).split()),
                     "a": {"text": qa.text, "line": qa.line_start, "outcome": " ".join(str(w["a_outcome"]).split())},
                     "b": {"text": qb.text, "line": qb.line_start, "outcome": " ".join(str(w["b_outcome"]).split())},
                     "by": model}
    return kept, dropped


def apply(findings: list[Finding], kept: dict[str, dict]) -> int:
    n = 0
    for f in findings:
        if f.fid in kept:
            f.witness = kept[f.fid]
            n += 1
    return n


def passport_line(result: Any) -> str:
    """«N из M противоречий …» for the report passport; "" when no finding has a witness."""
    have = [f for f in result.findings if f.status == "open" and isinstance(f.witness, dict)]
    if not have:
        return ""
    total = len(targets(result.findings))
    by = sorted({str(f.witness.get("by") or "") for f in have} - {""})
    cost = next((a.get("cost_usd") for a in reversed(result.audit or [])
                 if isinstance(a, dict) and a.get("what") == "witness" and a.get("cost_usd") is not None), None)
    how = ", ".join(x for x in ((f"модель {md(', '.join(by))}" if by else ""),
                                (f"отдельный запрос ${cost:.2f}" if isinstance(cost, (int, float)) else "")) if x)
    return (f"{len(have)} из {total} противоречий" + (f" ({how})" if how else "")
            + "; обе цитаты каждого примера найдены в документе программой, число замечаний и вердикт пример не меняет")


def _end(s: str) -> str:
    s = s.strip()
    return s if s[-1:] in ".?!…" else s + "."


def _side(side: dict) -> str:
    q = shorten(" ".join(plain_quote(str(side.get("text") or "")).split()), 140)
    out = " ".join(str(side.get("outcome") or "").split())
    if len(out) > 1 and out[0].isupper() and out[1].islower():  # after the colon: «второй push…», not «Второй push…»
        out = out[0].lower() + out[1:]
    return f"  - По строке {side.get('line')} («{md(q)}»): {_end(md(out))}"


def witness_lines(f: Finding) -> list[str]:
    """The case and its two sides as card lines (the sides as a nested list); [] when the finding has no witness."""
    w = f.witness
    if not isinstance(w, dict) or not w.get("a") or not w.get("b"):
        return []
    return ["Пример-свидетель: " + _end(md(str(w.get("case") or ""))), _side(w["a"]), _side(w["b"]),
            "  - Оба сразу выполнить нельзя. Обе цитаты программа нашла в документе дословно."]


def witness_text(f: Finding) -> str:
    """The same as one text block (for tests and plain output)."""
    return "\n".join(witness_lines(f))
