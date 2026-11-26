"""Side files next to the report: the questionnaire for the author and the proposed rewrites."""
from __future__ import annotations

import csv
import io
import os

from . import answers as ans_mod, clarify, rewrite_gate
from .report_md import impact_text
from .model import ADDRESSEE_RU, GAP_RU, TEST_KIND_RU, Question, Result
from .textfmt import blockquote, lines_text, plain_quote, md, md_cell, quote_location, shorten

PRIORITY_ONE = {"blocker": "блокирующий", "important": "важный", "nice": "желательный"}
LETTERS = "абвг"
ANSWERS_SUPPORTED = True


def command_path(path: str, sep: str = os.sep) -> str:
    """A path as it goes into a command printed in a file: forward slashes on Windows too (Python accepts them, and the
    file reads the same on every system), in double quotes when it has a space (cmd, PowerShell and sh all take that)."""
    s = str(path)
    if sep == "\\":
        s = s.replace("\\", "/")
    return f'"{s}"' if any(ch.isspace() for ch in s) else s


def rerun_line(input_path: str, output_path: str, questions_path: str) -> str:
    if ANSWERS_SUPPORTED:
        return (f"Когда ответите, запустите: `python main.py --input {command_path(input_path)} "
                f"--output {command_path(output_path)} --answers {command_path(questions_path)}`")
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
           "агент учтёт ответы, перепишет отчёт и покажет, что изменится. Строки «Отпечаток вопроса» и «Отпечаток документа» "
           "тоже не меняйте: по ним агент убеждается, что ответ относится именно к этому вопросу и этой версии документа. "
           "Если ответа не будет, команда считает так, как написано в строке «Если автор не ответит».", ""]
    if r.doc_sha256:
        out += [f"Отпечаток документа: {r.doc_sha256[:12]}", ""]
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
        line = impact_text(q, result)
        if line:
            out.append(md(line) + ".")
        if q.quote is not None:
            out += ["", f"Место в документе ({quote_location(q.quote)}):", "", blockquote(shorten(plain_quote(q.quote.text), 500))]
        if q.options:
            out.append("")
            for k, o in enumerate(q.options):
                eff = q.option_effects[k] if k < len(q.option_effects) and q.option_effects[k] else ""
                out.append(f"{LETTERS[k]}) {md(o)}" + (f" — что изменится: {md(eff)}" if eff else ""))
        if q.finding_ids:
            out += ["", "Замечания в отчёте: " + ", ".join(q.finding_ids)]
        line = clarify.assumption_line(q)
        if line:
            out += ["", md(line)]
        if q.fingerprint:  # the answers file carries it back: an answer is applied only to the question it was given for
            out += ["", f"Отпечаток вопроса: {q.fingerprint}"]
        out += ["", "Ответ:" + (" " + _answer_cell(q) if q.answer else ""), ""]
    out.append(rerun_line(input_path, output_path, questions_path))
    return "\n".join(out) + "\n"


def _closing(result: Result) -> tuple[dict[str, list[str]], list[dict]]:
    sm = ans_mod.summary(result.audit) or {}
    return dict(sm.get("closes") or {}), list(sm.get("fixed") or [])


def fixed_md(result: Result) -> str | None:
    """Proposed rewrites («было → стало») that passed the gate of ``rewrite_gate``, the author's answers, and the
    rewrites that did not pass (the requirement keeps its text, the row says what must change). None when empty."""
    closes, _fixed = _closing(result)
    rows, withheld = rewrite_gate.plan(result)
    seen = {fid for w in withheld for fid in w.fids}
    withheld += [w for w in rewrite_gate.recorded(result) if not set(w.fids) & seen]
    chosen = [f for f in result.findings if f.status == "open" and f.closed_by_answer and not f.fixed_wording]
    waiting = [f for f in result.findings if f.status == "open" and not f.advisory and not f.fixed_wording
               and not f.closed_by_answer and f.question_id and f.scope == "requirement"
               and f.fid not in {fid for w in withheld for fid in w.fids}]
    if not rows and not chosen and not withheld:
        return None
    qa = {q.qid: q for q in result.questions}
    req_quote = {r.rid: r.quote for r in result.requirements}
    n_withheld = sum(len(w.fids) for w in withheld)
    by_fid = {f.fid: f for f in result.findings}

    def basis(f) -> str:
        qids = closes.get(f.fid, [])
        return (f"ответ {', '.join(qids)}, " if qids else "") + f"замечание {f.fid}"

    out = [f"# Предлагаемые формулировки для «{md(result.run.doc_name)}»", "",
           "Черновики исправленных требований: написанные проверяющим без решения автора и учитывающие ответы автора. "
           "Перед внесением в документ их должен прочитать автор: модель могла ошибиться.", "",
           f"Проверка смысла: предложено формулировок {len(rows) + n_withheld}, напечатано {len(rows)}, "
           f"не напечатано {n_withheld}. Формулировка печатается, только если она описывает то же требование, не теряет "
           "числа, коды и идентификаторы исходного текста, не сокращает его правила и не вносит значений, которых нет "
           "в документе и в ответах автора. У остальных в таблице ниже остаётся исходный текст и сказано, что менять.", ""]
    if rows:
        out += ["| Требование | Было | Стало | Основание |", "| :--- | :--- | :--- | :--- |"]
        for r in rows:
            note = "; сведены из нескольких замечаний" if r.composed else ""
            why = "; ".join(basis(by_fid[i]) if i in by_fid else f"замечание {i}" for i in r.fids)
            out.append(f"| {r.rid or '—'} | {md_cell(plain_quote(r.before), 400)} | {md_cell(r.after, 600)} | "
                       f"{why}{note} |")
    if withheld:
        out += ["", "**Формулировка не предложена: требование остаётся как есть, нужно изменить следующее**", "",
                "| Требование | Остаётся | Что нужно изменить | Вопрос | Почему не предложена формулировка |",
                "| :--- | :--- | :--- | :--- | :--- |"]
        for w in withheld:
            need = "; ".join(md_cell(x, 300) for x in w.needs if x) + (" " + " ".join(w.gaps) if w.gaps else "")
            ques = "; ".join(f"{q}: {md_cell(qa[q].text, 160)}" if q in qa else q for q in w.qids) or "—"
            out.append(f"| {w.rid or '—'} | {md_cell(plain_quote(w.before), 300)} | {need or '—'} | {ques} | "
                       f"{md_cell('; '.join(w.reasons), 400)} ({', '.join(w.fids)}) |")
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


def _unverifiable(result: Result) -> list[dict]:
    items: list[dict] = []
    for a in result.audit:
        if a.get("type") == "test_unverifiable":
            items += [x for x in a.get("items") or [] if isinstance(x, dict)]
    return items


def tests_md(result: Result) -> str | None:
    """Acceptance-test drafts with Дано / Когда / Тогда and the list of what cannot be checked yet; None when both are empty."""
    unver = _unverifiable(result)
    if not result.tests and not unver:
        return None
    qa = {q.qid: q for q in result.questions}
    fa = {f.fid: f for f in result.findings}
    ready = sum(1 for t in result.tests if t.then and not t.blocked_by)
    waiting: dict[str, int] = {}
    for t in result.tests:
        if not (t.then and not t.blocked_by):
            for qid in t.blocked_by or ["без вопроса"]:
                waiting[qid] = waiting.get(qid, 0) + 1
    n_wait = len(result.tests) - ready
    out = [f"# Черновики приёмочных тестов для «{md(result.run.doc_name)}»", "",
           "Ожидаемый результат взят только из документа. Черновик печатается, только если в нём есть конкретное "
           "действие и конкретный ожидаемый результат; придумывать поведение за автора агент не вправе. Это черновики "
           "для тестировщика, не готовые тест-кейсы.", "",
           f"Готовы к прогону: {ready}. Ждут ответа автора: {n_wait}"
           + (" (" + ", ".join(f"{k} — {v}" for k, v in sorted(waiting.items())) + ")" if waiting else "")
           + f". Нельзя проверить, пока нет ответа: {len(unver)} (список в конце файла).", ""]
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
    if unver:
        out += ["## Что нельзя проверить, пока нет ответа", "",
                "Для этих требований в документе нет конкретного действия или ожидаемого результата, поэтому черновик "
                "теста не составлен.", ""]
        for x in unver:
            qids = ", ".join(x.get("blocked_by") or [])
            done = ", ".join(x.get("answered") or [])
            fids = x.get("finding_ids") or []
            what = md(x.get("what") or "")
            rids = ", ".join(x.get("req_ids") or [])
            head = what if all(r in what for r in (x.get("req_ids") or [])) else f"{rids} · {what}"
            tail = (f"ответ: {qids}" if qids else
                    f"ответ {done} получен: впишите его в документ, тогда тест можно составить" if done
                    else "вопроса к автору пока нет")
            out.append(f"- {head} — {tail}" + (f"; замечание {', '.join(fids)}" if fids else ""))
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
