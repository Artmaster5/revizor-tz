"""The verdict: decided by code from the open findings, never by the model."""
from __future__ import annotations

import re

from .model import Finding
from .textfmt import count

TEXT = {
    "BLOCKED": "⛔ Начинать разработку и тестирование нельзя: сначала устранить блокеры",
    "CONDITIONAL": "⚠️ Начинать можно только после ответов на вопросы и исправления существенных замечаний",
    "READY": "✅ Можно начинать разработку и тестирование",
    "NOT_CHECKED": "❔ Документ не проверен",
}
OFFLINE_TAIL = " (модель не использовалась: проверены только автоматические правила, итог предварительный)"


def decide(findings: list[Finding], *, offline: bool = False) -> tuple[str, str]:
    open_ = [f for f in findings if f.status == "open" and not f.advisory]
    if any(f.severity == "CRITICAL" for f in open_):
        code = "BLOCKED"
    elif any(f.severity == "MAJOR" for f in open_):
        code = "CONDITIONAL"
    else:
        code = "READY"
    text = TEXT[code]
    if offline:
        text += OFFLINE_TAIL
    return code, text


def short_text(code: str) -> str:
    """Verdict without the leading emoji, for running text."""
    t = TEXT.get(code, "")
    return t.split(" ", 1)[1] if " " in t else t


def fail_threshold_reached(findings: list[Finding], fail_on: str) -> bool:
    open_ = [f for f in findings if f.status == "open" and not f.advisory]
    if fail_on == "critical":
        return any(f.severity == "CRITICAL" for f in open_)
    if fail_on == "major":
        return any(f.severity in ("CRITICAL", "MAJOR") for f in open_)
    return False


# --- counts in the model-written verdict paragraph --------------------------------------------------------------
# Every count in the report comes from code. A sentence of the model's paragraph that names a count next to
# «блокер…», «замечан…», «требован…», «вопрос…», «дефект…» is removed; the code-written sentence goes in front.

_NUMBER_WORDS = frozenset((
    "один одна одно одного одной одну одним одном два две двух двум двумя три трёх трех трём трем тремя "
    "четыре четырёх четырех четырём четырем четырьмя пять пяти пятью шесть шести шестью семь семи семью "
    "восемь восьми восемью девять девяти девятью десять десяти десятью одиннадцать одиннадцати двенадцать "
    "двенадцати тринадцать тринадцати четырнадцать четырнадцати пятнадцать пятнадцати шестнадцать шестнадцати "
    "семнадцать семнадцати восемнадцать восемнадцати девятнадцать девятнадцати двадцать двадцати тридцать "
    "тридцати сорок сорока пятьдесят пятидесяти сто ста").split())
_COUNTED_STEMS = ("блокер", "замечан", "требован", "вопрос", "дефект")
_NEAR = 3  # words between the number and the counted noun


def _is_number(word: str) -> bool:
    return word.isdigit() or word in _NUMBER_WORDS


def has_count(sentence: str) -> bool:
    """True when a number (digits or a Russian number word) stands next to a counted noun."""
    words = re.findall(r"\w+", sentence.lower())
    nums = [i for i, w in enumerate(words) if _is_number(w)]
    nouns = [i for i, w in enumerate(words) if w.startswith(_COUNTED_STEMS)]
    return any(abs(i - j) <= _NEAR for i in nums for j in nouns)


def counts_sentence(findings: list[Finding]) -> str:
    """One sentence with the exact numbers of open counted findings, written by code."""
    open_ = [f for f in findings if f.status == "open" and not f.advisory]
    c = sum(1 for f in open_ if f.severity == "CRITICAL")
    m = sum(1 for f in open_ if f.severity == "MAJOR")
    n = sum(1 for f in open_ if f.severity == "MINOR")
    parts = [count(c, ("блокер", "блокера", "блокеров")) if c else "блокеров нет",
             count(m, ("существенное замечание", "существенных замечания", "существенных замечаний")),
             count(n, ("мелкое замечание", "мелких замечания", "мелких замечаний"))]
    return "По подсчёту программы: " + ", ".join(parts) + "."


def guard_reason(text: str, findings: list[Finding]) -> str:
    """Model's verdict paragraph without sentences that state counts, led by the code-written count sentence."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", (text or "").strip()) if s.strip()]
    kept = [s for s in sentences if not has_count(s)]
    return " ".join([counts_sentence(findings)] + kept)
