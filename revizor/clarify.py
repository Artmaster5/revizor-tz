"""The clarification loop, engine side: assumptions, fingerprints, groups of the letter, checking a fix, the letter.

Everything here is code, no model call. It adds fields to questions and findings (``assumption``, ``fingerprint``,
``letter_group``, ``fix_check``) and builds the side file ``<stem>.letter.md``. Nothing here changes a verdict.
"""
from __future__ import annotations

import dataclasses
import hashlib
import re

from . import rewrite_gate
from .model import GAP_RU, Finding, Question, Requirement, SourceDoc
from .textfmt import blockquote, md as _md, plain_quote, quote_location, shorten

# --- assumptions ------------------------------------------------------------------------------------------------

NO_ASSUMPTION = "без ответа автора работу по этому пункту начинать нельзя"
ASSUMPTION_LEAD = "Если автор не ответит, считаем так: "

# rule id -> what the team assumes if nobody answers the rule's question. Only where a sensible default exists; a rule
# that is not here has no safe assumption (a value the document does not give is not invented).
RULE_ASSUMPTIONS: dict[str, str] = {
    "time-zone-missing": "время и границы суток считаем по времени Алматы, UTC+5",
    "period-boundary": "границы срока и периода считаем включительными: «до 5 дней» — это 5 дней и меньше",
    "days-without-kind": "сроки в днях считаем календарными",
    "month-end-day": "в коротком месяце действие выполняется в последний день месяца",
    "period-undefined": "месяц считаем календарным (с 1-го по последнее число), сутки — с 00:00 до 24:00",
    "relative-period": "«прошлым» периодом считаем последний полностью закончившийся календарный период",
    "rounding-missing": "округляем математически (от 0,5 вверх) до сотых долей валюты, только в конце расчёта",
    "step-missing": "шаг изменения значения считаем равным единице его измерения",
    "limit-no-behaviour": "при превышении лимита операция отклоняется с понятным сообщением, данные не меняются",
    "number-without-unit": "время отклика считаем в секундах, сроки — в календарных днях",
    "open-list": "перечень считаем закрытым: в нём только то, что названо в тексте",
    "non-atomic": "каждое условие считаем отдельным требованием и проверяем отдельно",
    "missing-ids": "требования нумеруем сами по порядку в документе",
    "no-version": "версией считаем дату последнего изменения файла",
    "duplicate": "повтор считаем одним требованием, действует первая по порядку формулировка",
    "flow-happy-path": "в ошибочных путях операция отклоняется, пользователь видит сообщение, данные не меняются",
    "empty-section": "пустой раздел считаем не входящим в первую версию",
    "empty-table": "пустую таблицу считаем не входящей в первую версию",
    "title-mismatch": "верным считаем название из заголовка документа",
    "get-mutation": "изменяющее действие выполняем методом POST",
    "error-in-success": "ошибка возвращается с кодом 4xx или 5xx по смыслу ошибки, а не с 2xx",
    "content-type-without-body": "заголовок Content-Type у запроса без тела не используем",
    "sensitive-in-query": "чувствительные данные передаём только в теле запроса по защищённому каналу",
    "hidden-text": "скрытый текст не считаем частью требований и не выполняем",
    "prompt-injection": "текст, обращённый к проверяющему, не считаем частью требований и не выполняем",
}

# --- who gets the question in the letter -------------------------------------------------------------------------

LETTER_GROUPS = ("business", "architect", "security", "testing")
GROUP_RU = {
    "business": "Бизнес-заказчику",
    "architect": "Архитектору и разработке",
    "security": "Безопасности",
    "testing": "Тестированию",
}
# the questionnaire's older vocabulary (model.ADDRESSEES) -> group of the letter; "analyst" (the author himself) has no
# group of its own: such a question is placed by its rule or criterion
ADDRESSEE_GROUP = {"business": "business", "architect": "architect", "security": "security", "qa": "testing"}

RULE_GROUP: dict[str, str] = {
    # wording of one requirement
    "vague-word": "business", "open-list": "business", "placeholder": "business", "incomplete-reference": "business",
    "number-without-unit": "business", "time-without-conditions": "testing", "non-atomic": "architect",
    # the whole document
    "missing-ids": "architect", "unit-mix": "architect", "empty-section": "business", "empty-table": "business",
    "unfilled-checklist": "testing", "no-version": "business", "dangling-reference": "architect",
    "duplicate": "business", "flow-happy-path": "business", "title-mismatch": "business",
    # HTTP interface
    "get-mutation": "architect", "error-in-success": "architect", "status-mismatch": "architect",
    "param-type-mismatch": "architect", "content-type-without-body": "architect", "api-reliability": "architect",
    "sensitive-in-query": "security",
    # manipulation and data protection
    "prompt-injection": "security", "hidden-text": "security", "sensitive-data": "security",
    # time, quantities, thresholds
    "time-zone-missing": "business", "period-boundary": "business", "days-without-kind": "business",
    "month-end-day": "business", "period-undefined": "business", "relative-period": "business",
    "rounding-missing": "business", "value-without-unit": "business", "step-missing": "business",
    "threshold-conflict": "business", "limit-no-behaviour": "architect",
    # terms
    "abbreviation-undefined": "business", "term-defined-twice": "business", "condition-undefined": "business",
    # the document against itself
    "set-members-differ": "business", "example-field-mismatch": "architect", "parameter-two-values": "business",
    "example-arithmetic": "business", "example-out-of-range": "architect",
    "requirement-without-criterion": "testing",
}
CRITERION_GROUP: dict[str, str] = {
    "completeness": "business", "unambiguity": "business", "consistency": "business", "necessity": "business",
    "comprehensibility": "business", "set_completeness": "business", "set_consistency": "business",
    "verifiability": "testing", "feasibility": "architect", "singularity": "architect",
    "traceability": "architect", "set_feasibility": "architect",
}
# a question about the protection of data goes to security whatever the criterion says
_SECURITY_WORDS = re.compile(
    r"безопасн|парол|шифр|персональн|токен|авторизац|аутентифик|доступ[аеу]? к данн|утечк|аудит|логир", re.I)
# the stage that built the finding, when neither rule nor criterion decides
STAGE_GROUP = {"set_standards": "architect", "set_completeness": "business", "set_consistency": "business"}


def letter_group(q: Question, by_fid: dict[str, Finding]) -> str:
    """The addressee of the question in the letter: the question's own addressee when it names one, else the rule of its
    first finding that has a place in ``RULE_GROUP``, else the finding's criterion (security words win over the
    criterion), else the stage, else the business customer."""
    g = ADDRESSEE_GROUP.get(q.addressee)
    if g:
        return g
    linked = [by_fid[i] for i in q.finding_ids if i in by_fid]
    for f in linked:
        for rid in f.rule_ids:
            if rid in RULE_GROUP:
                return RULE_GROUP[rid]
    text = " ".join([q.text, q.why] + [f.title for f in linked])
    if _SECURITY_WORDS.search(text):
        return "security"
    for f in linked:
        if f.criterion in CRITERION_GROUP:
            return CRITERION_GROUP[f.criterion]
        if f.stage in STAGE_GROUP:
            return STAGE_GROUP[f.stage]
    return "business"


# --- fingerprints ---------------------------------------------------------------------------------------------------


def _flat(s: str) -> str:
    return re.sub(r"[\W_]+", " ", (s or "").casefold().replace("ё", "е")).strip()


def fingerprint(q: Question) -> str:
    """12 hex characters of the hash of the normalised question text and of the quoted place. The question number is
    not in it: the same question keeps its fingerprint when the numbers shift, a changed question or place does not."""
    place = _flat(plain_quote(q.quote.text)) if q.quote is not None else ""
    return hashlib.sha256((_flat(q.text) + "\n" + place).encode("utf-8")).hexdigest()[:12]


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# --- annotating the questions ------------------------------------------------------------------------------------


def annotate(questions: list[Question], findings: list[Finding], *, by_model: bool) -> None:
    """Fill ``assumption``, ``letter_group`` and ``fingerprint`` of every question that does not have them yet.

    ``by_model``: the questions were written by the model, which has given its own assumption (an empty one means «no safe
    assumption»); otherwise they come from the templates of the rules, and a rule's default assumption is used."""
    by_fid = {f.fid: f for f in findings}
    for q in questions:
        if not q.assumption:
            text = ""
            if not by_model:
                for fid in q.finding_ids:
                    f = by_fid.get(fid)
                    if f is None:
                        continue
                    text = next((RULE_ASSUMPTIONS[r] for r in f.rule_ids if r in RULE_ASSUMPTIONS), "")
                    if not text and f.question is not None:
                        text = f.question.assumption.strip()
                    if text:
                        break
            q.assumption = (text[:1].upper() + text[1:] + ("" if text.endswith(".") else ".")) if text else NO_ASSUMPTION
        if not q.letter_group:
            q.letter_group = letter_group(q, by_fid)
        if not q.fingerprint:
            q.fingerprint = fingerprint(q)


def has_assumption(q: Question) -> bool:
    return bool(q.assumption) and q.assumption != NO_ASSUMPTION


def assumption_line(q: Question) -> str:
    """One line for a report: «Если автор не ответит, считаем так: …» or the sentence that work cannot start."""
    if not q.assumption:
        return ""
    if q.assumption == NO_ASSUMPTION:
        return f"Если автор не ответит: {NO_ASSUMPTION}."
    return ASSUMPTION_LEAD + q.assumption.rstrip(".") + "."


# --- checking the fix -----------------------------------------------------------------------------------------------

REMAINS = "на новой формулировке замечание остаётся"
RESOLVED = "правило больше не срабатывает на новой формулировке"
STATUS_RU = {"resolved": "снято", "remains": "остаётся", "unchecked": "не проверено кодом"}


def _fc(status: str, note: str) -> dict[str, str]:
    return {"status": status, "note": note}


def _patched(doc: SourceDoc, req: Requirement, wording: str) -> tuple[SourceDoc, int, int] | str:
    """The document with the requirement's lines replaced by the new wording: (doc, first line, last line) of the new
    place, or a Russian reason why it cannot be built."""
    from .structure import parse_blocks

    lines = list(doc.lines)
    s, e = req.line_start - 1, req.line_end
    if s < 0 or e > len(lines) or s >= e:
        return "место требования в тексте не найдено"
    first = lines[s]
    if first.lstrip().startswith("|"):
        return "требование стоит в строке таблицы: код не может подставить в неё готовую формулировку"
    m = re.match(r"^\s*(?:[-*+•]\s+|\d+[.)]\s+)", first)
    new = [x.rstrip() for x in wording.strip().split("\n") if x.strip()]
    if not new:
        return "формулировка пуста"
    new[0] = (m.group(0) if m else "") + new[0]
    lines[s:e] = new
    text = "\n".join(lines)
    nd = dataclasses.replace(doc, text=text, lines=lines, line_pages=None, blocks=parse_blocks(text, doc.fmt, None))
    return nd, s + 1, s + len(new)


def _recheck_rules(f: Finding, req: Requirement, doc: SourceDoc) -> dict[str, str]:
    from . import rules as rules_mod, slicer
    from .rules._common import Ctx

    here = [q for q in f.quotes if q.line_start <= req.line_end and q.line_end >= req.line_start]
    if not here:
        return _fc("unchecked", "замечание относится к другому месту документа, а не к тексту этого требования")
    built = _patched(doc, req, f.fixed_wording)
    if isinstance(built, str):
        return _fc("unchecked", built[:1].upper() + built[1:] + ".")
    nd, a, b = built
    reqs, skipped = slicer.heuristic_slice(nd)
    ctx = Ctx.build(nd, reqs, skipped)
    still: list[Finding] = []
    for rid in f.rule_ids:
        impl, info = rules_mod._IMPL.get(rid), rules_mod.RULES.get(rid)
        if impl is None or info is None:
            continue
        for g in impl(ctx, info) or []:
            if any(q.line_start <= b and q.line_end >= a for q in g.quotes):
                still.append(g)
    if still:
        return _fc("remains", f"{REMAINS}: {shorten(still[0].title, 160)}")
    return _fc("resolved", RESOLVED)


def fix_checks(findings: list[Finding], requirements: list[Requirement], doc: SourceDoc) -> list[str]:
    """First pass, right after the answers: run the rule that found the finding on its new wording. Returns the ids
    of findings an answer had closed that stay open because the rule still fires. Never raises."""
    reopened: list[str] = []
    for f in findings:
        if f.status != "open" or f.advisory:
            continue
        try:
            if f.fixed_wording:
                req = rewrite_gate.target_requirement(f, requirements)
                if req is None:
                    f.fix_check = _fc("unchecked", "не удалось определить, к какому требованию относится замечание.")
                elif f.rule_ids and (f.source.startswith("rule:") or f.source == "both"):
                    f.fix_check = _recheck_rules(f, req, doc)
                else:
                    f.fix_check = None  # settled after the gate for rewrites (``after_gate``)
            elif f.closed_by_answer:
                f.fix_check = _fc("unchecked", "выбран вариант ответа, а готовой формулировки нет: проверять нечего. "
                                  "Внесите ответ в документ и запустите проверку ещё раз.")
        except Exception as e:  # a check of a fix must never cost the report
            f.fix_check = _fc("unchecked", f"проверка не выполнена ({type(e).__name__}).")
        if f.fix_check and f.fix_check["status"] == "remains" and f.closed_by_answer:
            f.closed_by_answer = False
            reopened.append(f.fid)
    return reopened


def reopen_in_audit(audit: list[dict], questions: list[Question], fids: list[str]) -> None:
    """The answers summary and the answers' effects must agree with a finding that stayed open after the check."""
    if not fids:
        return
    for a in audit:
        if a.get("type") != "answers_summary":
            continue
        a["closed"] = [x for x in a.get("closed") or [] if x not in fids]
        closes = a.get("closes")
        if isinstance(closes, dict):
            for fid in fids:
                closes.pop(fid, None)
    for q in questions:
        if q.answer and any(i in fids for i in q.finding_ids):
            q.answer_effect = (q.answer_effect or "").rstrip() + (
                " Проверка новой формулировки: замечание остаётся, пока правка не внесена и правило не перестанет срабатывать.")


def after_gate(findings: list[Finding]) -> None:
    """Second pass, after the gate of proposed rewrites: a wording that was withheld cannot be said to fix anything, and a
    model finding's wording that passed the gate is only checked for meaning, not for the finding itself."""
    for f in findings:
        if f.status != "open" or f.advisory:
            continue
        if f.fix_check and not f.fixed_wording and f.fix_check.get("status") != "unchecked":
            f.fix_check = _fc("unchecked", "формулировка не прошла проверку смысла и в отчёт не вынесена: "
                                           "проверять нечего.")
        elif f.fixed_wording and not f.fix_check:
            f.fix_check = _fc("unchecked", "замечание найдено не правилом: код проверил только, что формулировка "
                                           "не теряет значений и смысла; снято ли замечание, без модели не проверить.")


def fix_check_text(f: Finding) -> str:
    """One line for the card of a finding; "" when there is no check."""
    fc = f.fix_check
    if not fc or not fc.get("status"):
        return ""
    note = (fc.get("note") or "").strip().rstrip(".")
    return f"Проверка исправления: {STATUS_RU.get(fc['status'], fc['status'])}" + (f" — {note}" if note else "") + "."


# --- the letter to the author -----------------------------------------------------------------------------------


def _letters() -> str:
    return "абвг"


def letter_md(result, *, questions_name: str = "") -> str | None:
    """A ready letter to the author, one section per addressee. None when there is nothing to ask.

    In a repeat check only the questions without an answer are in it."""
    qs = [q for q in result.questions if not q.answer]
    if not qs or result.diagnostic is not None:
        return None
    blockers = [q for q in qs if q.priority == "blocker"]
    name = result.run.doc_title or result.run.doc_name
    head = (f"Блокируют начало работы: {len(blockers)} из {len(qs)} вопросов" if blockers
            else f"Вопросов: {len(qs)}, блокирующих начало работы среди них нет")
    out = [f"{head}.", "", f"# Письмо автору документа «{_md(name)}»", "",
           "Здравствуйте! Мы проверили документ и не смогли однозначно понять места, перечисленные ниже. Вопросы "
           "разложены по тем, кто может на них ответить. Если ответа не будет, команда будет считать так, как "
           "написано под вопросом; там, где безопасного допущения нет, работа по пункту не начнётся.", ""]
    rank = {"blocker": 0, "important": 1, "nice": 2}
    for g in LETTER_GROUPS:
        part = sorted((q for q in qs if (q.letter_group or "business") == g), key=lambda q: (rank.get(q.priority, 1), q.qid))
        if not part:
            continue
        out += [f"## {GROUP_RU[g]} ({len(part)})", ""]
        for q in part:
            mark = " — блокирует начало работы" if q.priority == "blocker" else ""
            out += [f"### {q.qid}{mark}", "", _md(q.text), ""]
            if q.quote is not None:
                out += [f"Место в документе ({quote_location(q.quote)}):", "",
                        blockquote(shorten(plain_quote(q.quote.text), 400)), ""]
            if q.why:
                out += [f"Почему спрашиваем: {_md(q.why)}", ""]
            elif q.gap:
                out += [f"Почему спрашиваем: {GAP_RU.get(q.gap, q.gap)}.", ""]
            if q.options:
                out += ["Варианты ответа:", ""]
                for k, o in enumerate(q.options[:4]):
                    eff = q.option_effects[k] if k < len(q.option_effects) and q.option_effects[k] else ""
                    out.append(f"- {_letters()[k]}) {_md(o)}" + (f" — что изменится: {_md(eff)}" if eff else ""))
                out.append("")
            line = assumption_line(q)
            if line:
                out += [_md(line), ""]
            if q.finding_ids:
                out += ["Замечания в отчёте: " + ", ".join(q.finding_ids), ""]
    if questions_name:
        out += ["---", "", f"Ответы можно вписать в файл-анкету `{questions_name}` (после слова «Ответ:» под каждым "
                "вопросом) и запустить проверку ещё раз с этим файлом."]
    return "\n".join(out).rstrip("\n") + "\n"

