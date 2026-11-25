"""Side files next to the report: the questionnaire for the author and the proposed rewrites."""
from __future__ import annotations

import csv
import io

from . import answers as ans_mod
from .model import ADDRESSEE_RU, GAP_RU, TEST_KIND_RU, Question, Result
from .textfmt import blockquote, lines_text, plain_quote, md, md_cell, quote_location, shorten

PRIORITY_ONE = {"blocker": "блокирующий", "important": "важный", "nice": "желательный"}
LETTERS = "абвг"
ANSWERS_SUPPORTED = True


def rerun_line(input_path: str, output_path: str, questions_path: str) -> str:
    if ANSWERS_SUPPORTED:
        return (f"Когда ответите, запустите: `python main.py --input {input_path} --output {output_path} "
                f"--answers {questions_path}`")
    return ("Повторный прогон с ответами (`--answers`) появится в следующей версии агента; пока передайте файл "
            "автору документа и внесите ответы в документ.")


def _answer_cell(q: Question) -> str:
    """The answer as the author would type it: the option letter when one was chosen, else the words."""
    for k, o in enumerate(q.options):
        if o == q.answer and k < len(LETTERS):
            return LETTERS[k]
    return (q.answer or "").replace("\n", " ")


def questions_md(result: Result, *, input_path: str, output_path: str, questions_path: str) -> str:
    r = result.run
    out = [f"# Вопросы автору документа «{md(r.doc_name)}»", "",
           "Как отвечать: под каждым вопросом впишите после «Ответ:» букву варианта (а, б, в, г) или свой ответ "
           "одной-двумя фразами. Пустой ответ или «не знаю» означает «пока не знаю». Номера вопросов и слово «Ответ:» "
           "не меняйте. Порядок — от блокирующих к желательным. Затем запустите команду из последней строки файла: "
           "агент учтёт ответы, перепишет отчёт и покажет, что изменится.", ""]
    for q in result.questions:
        who = ADDRESSEE_RU.get(q.addressee, q.addressee)
        out.append(f"### {q.qid} · {PRIORITY_ONE.get(q.priority, q.priority)} · кому: {md(who)}")
        out.append("")
        out.append(md(q.text))
        out.append("")
        if q.gap:
            out.append(f"Тип пробела: {GAP_RU.get(q.gap, q.gap)}.")
        if q.why:
            out.append(f"Почему важно: {md(q.why)}")
        if q.quote is not None:
            out += ["", f"Место в документе ({quote_location(q.quote)}):", "", blockquote(shorten(plain_quote(q.quote.text), 500))]
        if q.options:
            out.append("")
            for k, o in enumerate(q.options):
                eff = q.option_effects[k] if k < len(q.option_effects) and q.option_effects[k] else ""
                out.append(f"{LETTERS[k]}) {md(o)}" + (f" — что изменится: {md(eff)}" if eff else ""))
        if q.finding_ids:
            out += ["", "Замечания в отчёте: " + ", ".join(q.finding_ids)]
        out += ["", "Ответ:" + (" " + _answer_cell(q) if q.answer else ""), ""]
    out.append(rerun_line(input_path, output_path, questions_path))
    return "\n".join(out) + "\n"


def _closing(result: Result) -> tuple[dict[str, list[str]], list[dict]]:
    sm = ans_mod.summary(result.audit) or {}
    return dict(sm.get("closes") or {}), list(sm.get("fixed") or [])


def fixed_md(result: Result) -> str | None:
    """Proposed rewrites («было → стало») from the reviewers and from the author's answers; None when empty."""
    closes, _fixed = _closing(result)
    rows = [f for f in result.findings if f.status == "open" and not f.advisory and f.fixed_wording]
    chosen = [f for f in result.findings if f.status == "open" and f.closed_by_answer and not f.fixed_wording]
    waiting = [f for f in result.findings if f.status == "open" and not f.advisory and not f.fixed_wording
               and not f.closed_by_answer and f.question_id and f.scope == "requirement"]
    if not rows and not chosen:
        return None
    qa = {q.qid: q for q in result.questions}
    req_quote = {r.rid: r.quote for r in result.requirements}

    def basis(f) -> str:
        qids = closes.get(f.fid, [])
        return (f"ответ {', '.join(qids)}, " if qids else "") + f"замечание {f.fid}"

    out = [f"# Предлагаемые формулировки для «{md(result.run.doc_name)}»", "",
           "Черновики исправленных требований: написанные проверяющим без решения автора и учитывающие ответы автора. "
           "Перед внесением в документ их должен прочитать автор: модель могла ошибиться.", ""]
    if rows:
        out += ["| Требование | Было | Стало | Основание |", "| :--- | :--- | :--- | :--- |"]
        for f in rows:
            rid = f.req_ids[0] if f.req_ids else ""
            before = plain_quote(req_quote.get(rid) or (f.quotes[0].text if f.quotes else ""))
            out.append(f"| {rid or '—'} | {md_cell(before, 400)} | {md_cell(f.fixed_wording, 600)} | {basis(f)} |")
    if chosen:
        out += ["", "**Что внести в документ по ответам автора** (формулировку выбранного варианта автор вписывает сам):", "",
                "| Требование | Было | Что внести | Основание |", "| :--- | :--- | :--- | :--- |"]
        for f in chosen:
            rid = f.req_ids[0] if f.req_ids else ""
            before = plain_quote(req_quote.get(rid) or (f.quotes[0].text if f.quotes else ""))
            ans = "; ".join(f"{qid}: {qa[qid].answer}" for qid in closes.get(f.fid, []) if qid in qa)
            out.append(f"| {rid or '—'} | {md_cell(before, 400)} | {md_cell(ans, 400)} | {basis(f)} |")
    if waiting:
        out += ["", "**Ждут ответа автора** (без него формулировку не исправить):", ""]
        for f in waiting:
            out.append(f"- {f.req_ids[0] if f.req_ids else '—'} · {f.fid} · {md(f.title)} — вопрос {f.question_id}")
    return "\n".join(out) + "\n"


def tests_md(result: Result) -> str | None:
    """Acceptance-test drafts with Дано / Когда / Тогда; None when there are none."""
    if not result.tests:
        return None
    qa = {q.qid: q for q in result.questions}
    fa = {f.fid: f for f in result.findings}
    ready = sum(1 for t in result.tests if t.then and not t.blocked_by)
    out = [f"# Черновики приёмочных тестов для «{md(result.run.doc_name)}»", "",
           "Ожидаемый результат взят только из документа. Если документ его не описывает, в тесте так и написано: "
           "придумывать поведение за автора агент не вправе. Это черновики для тестировщика, не готовые тест-кейсы.", "",
           f"Можно написать уже сейчас: {ready} · Нельзя написать без ответа автора или описания результата: "
           f"{len(result.tests) - ready}", ""]
    for t in result.tests:
        if t.then and not t.blocked_by:
            status = "готов"
        elif not t.then:
            status = "ожидаемый результат в документе не описан — нужен ответ автора" + (
                " (" + ", ".join(t.blocked_by) + ")" if t.blocked_by else "")
        else:
            status = "заблокирован: ждёт ответа " + ", ".join(t.blocked_by)
        out += [f"### {t.tid} · {TEST_KIND_RU.get(t.kind, t.kind)} · {', '.join(t.req_ids)}", "",
                f"**{md(t.title)}** — {status}", ""]
        out.append(f"- **Дано:** {md(t.given) if t.given else '—'}")
        out.append(f"- **Когда:** {md(t.when) if t.when else '—'}")
        out.append(f"- **Тогда:** {md(t.then) if t.then else 'ожидаемый результат в документе не описан'}")
        for qid in t.blocked_by:
            q = qa.get(qid)
            out.append(f"- **Нельзя дописать, пока не получен ответ на {qid}:** {md(q.text) if q else ''}")
        if t.finding_ids:
            out.append("- Нельзя дописать, пока не снято замечание: " + "; ".join(
                f"{i} — {md(fa[i].title)}" if i in fa else i for i in t.finding_ids))
        out.append("")
    return "\n".join(out)


CSV_COLUMNS = ("id", "severity", "blocker_reason", "criterion", "scope", "requirement_ids", "doc_ids", "lines", "title",
               "problem", "recommendation", "quote", "question_id")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """Text from the document or the model: a leading «=», «+», «-», «@» would be run as a formula by a spreadsheet."""
    v = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    return "'" + v if v.startswith(_FORMULA_START) else v


def findings_csv(result: Result) -> str | None:
    """Open findings as CSV for Jira or a spreadsheet (UTF-8 with BOM, comma, one row per finding). Advisory
    observations are left out. None when there is nothing to export."""
    if result.diagnostic is not None:
        return None
    rows = [f for f in result.findings if f.status == "open" and not f.advisory]
    if not rows:
        return None
    doc_id = {r.rid: r.doc_id for r in result.requirements}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for f in rows:
        lines = []
        for q in f.quotes:
            t = lines_text(q.line_start, q.line_end).split(" ", 1)[-1]
            if t not in lines:
                lines.append(t)
        ids = [doc_id.get(r, "") for r in f.req_ids]
        w.writerow([f.fid, f.severity, f.blocker_reason, f.criterion, f.scope, "; ".join(f.req_ids),
                    "; ".join(_csv_safe(i) for i in ids if i), "; ".join(lines), _csv_safe(f.title),
                    _csv_safe(f.problem), _csv_safe(f.recommendation),
                    _csv_safe(" | ".join(" ".join(plain_quote(q.text).split()) for q in f.quotes)), f.question_id])
    return "\ufeff" + buf.getvalue()
