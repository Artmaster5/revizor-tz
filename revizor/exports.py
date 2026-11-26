"""Extra files next to the report: the details of the review (``*.details.md``), the page summary
(``*.summary.md``), the import file for a task tracker (``*.jira.csv``), the run details (``*.diagnostic.md``) and
the HTML page (``*.html``: the report and its details on one page).

``extra_files`` never raises: a file that cannot be built is skipped and a warning is added to the run."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from . import report_md
from .model import CRITERION_RU, Result
from .sidefiles import _csv_safe
from .textfmt import lines_text, plain_quote

JIRA_COLUMNS = ("Summary", "Description", "Priority", "Labels", "Issue Type")
JIRA_PRIORITY = {"CRITICAL": "Highest", "MAJOR": "High", "MINOR": "Low"}
VERDICT_CLASS = {"BLOCKED": "verdict-blocked", "CONDITIONAL": "verdict-conditional", "READY": "verdict-ready"}


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def jira_csv(result: Result) -> str | None:
    """Open counted findings in the columns a tracker import needs (UTF-8 with BOM, RFC 4180 quoting, CRLF rows).
    None when there is nothing to export."""
    if result.diagnostic is not None:
        return None
    rows = [f for f in result.findings if f.status == "open" and not f.advisory]
    if not rows:
        return None
    doc_id = {r.rid: r.doc_id for r in result.requirements}
    questions = {q.qid: q for q in result.questions}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(JIRA_COLUMNS)
    for f in rows:
        desc = [f"{f.fid} · {CRITERION_RU.get(f.criterion, f.criterion)}", "", _one_line(f.problem)]
        reqs = [f"{rid} ({doc_id[rid]})" if doc_id.get(rid) else rid for rid in f.req_ids]
        if reqs:
            desc += ["", "Требования: " + ", ".join(reqs)]
        if f.quotes:
            desc += ["", "Цитаты из документа:"]
            seen: set[str] = set()
            for q in f.quotes:
                text = _one_line(plain_quote(q.text))
                key = f"{q.line_start}|{text}"
                if key in seen:
                    continue
                seen.add(key)
                where = f"стр. {q.page}" if q.page else lines_text(q.line_start, q.line_end)
                desc.append(f"- {where}: «{text}»")
        if f.recommendation:
            desc += ["", "Как исправить: " + _one_line(f.recommendation)]
        q = questions.get(f.question_id)
        if q is not None:
            desc += ["", f"Вопрос автору {q.qid}: " + _one_line(q.text)]
        labels = " ".join(x for x in ("revizor", f.criterion, f.fid) if x)
        w.writerow([_csv_safe(f"[{f.severity}] {_one_line(f.title)}"), _csv_safe("\n".join(desc)),
                    JIRA_PRIORITY.get(f.severity, "Medium"), labels, "Bug"])
    return "﻿" + buf.getvalue()


def one_page_md(md_text: str, details_text: str | None, md_name: str) -> str:
    """The main report followed by its details as one Markdown text: the links between the two files become links
    inside the page, so the HTML has no link to a ``.md`` file."""
    if not details_text:
        return md_text
    dname = report_md.details_name(md_name)
    main = md_text.replace(f"]({dname}#", "](#")
    details = details_text.replace(f"]({md_name}#", "](#")
    if details.startswith("# "):
        details = '<a id="x-top"></a>\n## ' + details[2:]
    return main.rstrip("\n") + "\n\n" + details


def html_page(result: Result, md_text: str, details_text: str | None = None, md_name: str = "") -> str | None:
    """The report (and its details file, when given) as a self-contained HTML page when ``revizor.report_html`` is
    importable, else None. The verdict card gets a class by the verdict (``verdict-blocked`` / ``-conditional`` /
    ``-ready``)."""
    try:
        from . import report_html
    except ImportError:
        return None
    title = f"Ревизор ТЗ — {result.run.doc_title or result.run.doc_name}"
    page = report_html.render_html(one_page_md(md_text, details_text, md_name or report_md.DEFAULT_NAME), title)
    cls = VERDICT_CLASS.get(result.verdict or "")
    if cls:
        page = page.replace('<blockquote class="verdict">', f'<blockquote class="verdict {cls}">', 1)
    return page


def extra_files(result: Result, base: Path, md_text: str | None = None) -> list[tuple[Path, str]]:
    """(path, text) of the extra files for the report at ``base`` (the Markdown or JSON path). The HTML page comes
    last. A part that fails is skipped with a warning in ``result.run.warnings``; this function never raises."""
    out: list[tuple[Path, str]] = []

    def side(suffix: str) -> Path:
        return base.with_name(base.stem + suffix)

    def attempt(what: str, build) -> None:
        try:
            item = build()
        except Exception as e:  # optional export: never breaks the run
            result.run.warnings.append(f"{what} не записан(а): внутренняя ошибка ({type(e).__name__}).")
            return
        if item is not None:
            out.append(item)

    md_name = side(".md").name
    render_error = ""
    if md_text is None:
        try:
            md_text = report_md.render(result, md_name)
        except Exception as e:
            render_error = type(e).__name__
    details: list[str] = []
    if result.diagnostic is None:
        def build_details():
            text = report_md.render_details(result, md_name, md_text) if md_text is not None else None
            if text:
                details.append(text)
                # next to a JSON report no Markdown report is written: the details go only into the HTML page
                return (side(".details.md"), text) if base.suffix.lower() != ".json" else None
            return None

        if md_text is not None:
            attempt("Файл подробностей проверки", build_details)
        attempt("Краткая сводка", lambda: (side(".summary.md"), report_md.summary_md(result, md_name)))
        attempt("Файл для импорта в трекер", lambda: (lambda t: (side(".jira.csv"), t) if t else None)(jira_csv(result)))
        attempt("Файл подробностей прогона",
                lambda: (side(".diagnostic.md"), report_md.render_run_details(result)))
    if md_text is None:
        result.run.warnings.append(f"HTML-версия отчёта не записана: внутренняя ошибка ({render_error}).")
        return out
    attempt("HTML-версия отчёта", lambda: (lambda h: (side(".html"), h) if h else None)(
        html_page(result, md_text, details[0] if details else None, md_name)))
    return out
