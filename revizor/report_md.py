"""Markdown report (GitHub-flavoured). Everything from the document or the model is escaped.

Layout rules (brief 5A): the first screen tells what to do; every count, mark and blocker list comes from
``consistency`` so the head, the body and the JSON agree; a document line is printed once per card; full requirement
cards only where there is something to say; run details live in the diagnostic side file (``render_run_details``).

Layout 2: the main report (``render``) prints CRITICAL findings as full cards, MAJOR as three-line cards and MINOR as
one table per section; the full cards, the requirement blocks, the completeness map and the questions in full go to
the details file (``render_details``, ``<stem>.details.md``, anchors prefixed ``x-``)."""
from __future__ import annotations

import contextvars
import re
from collections import OrderedDict
from datetime import datetime

from . import answers as ans_mod, baseline as baseline_mod, clarify, consistency, gapcheck, impact as impact_mod, readiness as readiness_mod, rules as rules_mod, score, standard as standard_mod, verdict
from .config import version_label
from .consistency import BLOCKER_SHORT, blockers as _blocker_list, fix_links, marks_of, tally
from .merge import FAMILY, FOLD_STAGE
from .model import (ADDRESSEE_RU, BLOCKER_RU, CHECKLIST_VERDICT_RU, CRITERION_RU, GAP_RU, KIND_RU, PRIORITY_RU, REQ_CRITERIA, SET_CRITERIA,
                    SEVERITY_ICON, SKIP_CATEGORY_RU, TEST_KIND_RU, Finding, Question, Quote, Requirement, Result)
from .prompts import FORMAT_RU, GAPS_NAME, GAPS_STAGE, LENS_BY_STAGE, SET_NAME
from .quotes import normalize
from .textfmt import (plain_quote, W_BLOCK, W_CALL, W_FINDING, W_OBSERVATION, W_QUESTION, W_REQ, anchor, blockquote, count, dec,
                      duration_text, last_section, lines_text, location, md, md_cell, num, plural,
                      quote_location, shorten, size_text, usd)

TABLE_QUOTE = 120   # section 2: the requirement's exact text up to this many characters, cut at a word boundary
WHERE_QUOTE = 100   # the first quote on a compact card of the main report (the spec allows up to 140)
Q_TABLE = 90        # a question in the table of section 7 of the main report
BRIEF_QUOTES = 3    # quotes on a full card of the main report; the rest are counted, all are in the details
Q_ASSUMPTION = 140  # the assumption in the table of questions
DIALOG_CUT = 90     # question and answer on a line of the dialog in the main report
COVERAGE_WORDS = 120  # a longer coverage block goes to the details file, the main report keeps one line
CARD_QUOTE = 500
QUOTES_SHOWN = 4    # quotes printed per finding; the rest are counted
ALSO_SHOWN = 3      # titles of merged findings printed per card
SEE_ALSO_SHOWN = 3  # places of a merged repeat printed per card
BROAD_SET = 5       # a set finding naming more requirements than this does not by itself earn a requirement card
FIRST_Q = 5         # questions on the first screen
FIRST_FIX = 5       # fix-first lines on the first screen

SECTIONS = OrderedDict([
    ("s1", "1. Паспорт прогона"),
    ("s2", "2. Как агент нарезал требования"),
    ("s3", "3. Проверка каждого требования"),
    ("s4", "4. Проверка всего набора требований"),
    ("s5", "5. Сводка"),
    ("s6", "6. Итог"),
    ("s7", "7. Уточняющие вопросы к автору ТЗ"),
    ("s8", "8. Черновики приёмочных тестов"),
    ("app", "Приложение. Как получен отчёт"),
])
SUBSECTIONS = [  # (number, title, set criterion, families of requirement-level findings without a requirement)
    ("4.1", "Полнота набора", "set_completeness", ("complete", "verifiable")),
    ("4.2", "Взаимная непротиворечивость", "set_consistency", ("consistent",)),
    ("4.3", "Реализуемость, стандарты и безопасность", "set_feasibility", ("feasible",)),
    ("4.4", "Понятность и термины", "comprehensibility", ("clear",)),
    ("4.5", "Прослеживаемость", "traceability", ("trace",)),
]
STATUS_ICON = {"ok": "✅", "issue": "⚠️", "na": "—", "unchecked": "❔"}
MODE_RU = {"full": "полный: нарезка и три линзы проверки моделью, проверка набора, перепроверка",
           "fast": "быстрый: один проход модели по требованиям, проверка набора, без перепроверки",
           "offline": "без модели: только автоматические правила"}
LETTERS = "абвг"
CLARITY_LEGEND = ("Ясность — 10 минус вес замечаний, которые называют требование: блокер 6,5, существенное 2,5, "
                  "мелкое 0,75 (замечание к набору — вдвое меньше); не ниже 1, а без блокера не ниже 4.")


# --- small helpers ------------------------------------------------------------------------------


def counted(result: Result) -> list[Finding]:
    return consistency.counted(result.findings)


def advisory(result: Result) -> list[Finding]:
    return [f for f in result.findings if f.status == "open" and f.advisory]


def sev_counts(findings: list[Finding]) -> dict[str, int]:
    t = tally(findings)
    return {s: t[s] for s in ("CRITICAL", "MAJOR", "MINOR")}


def link(item_id: str) -> str:
    return f"[{item_id}](#{anchor(item_id)})" if item_id else ""


def links(ids: list[str]) -> str:
    return ", ".join(link(i) for i in ids)


def is_offline(result: Result) -> bool:
    return result.run.mode == "offline"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _sev(f: Finding) -> str:
    return f"{SEVERITY_ICON.get(f.severity, '')} {f.severity}"


def _source(f: Finding) -> str:
    """Who found it, in two or three words; which pass of the model did is in the diagnostic file."""
    rules = ", ".join(f"`{r}`" for r in f.rule_ids)
    if f.source == "both":
        return "модель и правило " + rules
    if f.source.startswith("rule:"):
        return "правило " + rules
    if f.stage == GAPS_STAGE and f.gap_ids:
        return "модель, контрольный вопрос " + ", ".join(f.gap_ids)
    return "модель"


def _quote_text(text: str, limit: int = CARD_QUOTE) -> str:
    t = " ".join(plain_quote(text).split())
    if len(t) > limit:
        return md(shorten(t, limit)) + " (цитата сокращена)"
    return md(t)


def _quote_line(q, *, with_rid: bool = True, paren: bool = False) -> str:
    where = quote_location(q)
    if with_rid and q.rid:
        where += ", " + link(q.rid)
    if paren:
        where = f"({where})"
    restored = " (восстановлена по сходству)" if not q.exact else ""
    return f"{where}: «{_quote_text(q.text)}»{restored}"


def _blockers(result: Result) -> list[Finding]:
    return _blocker_list(result.findings)


def _where(f: Finding) -> str:
    if not f.quotes:
        return ""
    if f.quotes[0].page:
        pages = sorted({q.page for q in f.quotes if q.page})
        return "стр. " + ", ".join(str(p) for p in pages)
    ls = sorted({q.line_start for q in f.quotes})
    if len(ls) == 1:
        return lines_text(f.quotes[0].line_start, f.quotes[0].line_end)
    return "строки " + ", ".join(str(x) for x in ls[:5]) + ("…" if len(ls) > 5 else "")


def verdict_after_blockers(result: Result) -> tuple[str, str] | None:
    blocker_q = {q.qid for q in result.questions if q.priority == "blocker"}
    if not blocker_q:
        return None
    rest = [f for f in counted(result) if f.question_id not in blocker_q]
    code, _text = verdict.decide(rest, offline=is_offline(result))
    return code, verdict.TEXT.get(code, _text) + (" (предварительно)" if is_offline(result) else "")


def failed_steps_line(result: Result) -> str:
    """«Модель не ответила на N шагов из M…» from the audit's model calls; "" when every step was answered."""
    # a call marked «retried» was repeated in the economy second round: the repeat stands for the step
    calls = [a for a in result.audit if a.get("type") == "model_call" and not a.get("retried")]
    failed = sum(1 for a in calls if a.get("ok") is False)
    if not failed:
        return ""
    return (f"Модель не ответила на {failed} {plural(failed, ('шаг', 'шага', 'шагов'))} из {len(calls)}. Запустите ту же "
            "команду ещё раз: готовые ответы возьмутся из кэша, повторятся только неудавшиеся шаги.")


def model_label(r) -> str:
    """The model's name, and the exact version in brackets when the provider reports a different one."""
    v = getattr(r, "model_version", "") or ""
    return md(r.model) + (f" ({md(v)})" if v and v != r.model else "")


def counts_line(result: Result) -> str:
    """«блокеров N · существенных N · мелких N» — the same numbers everywhere (``consistency.tally``)."""
    t = tally(result.findings)
    return f"блокеров {t['CRITICAL']} · существенных {t['MAJOR']} · мелких {t['MINOR']}"


# --- render ------------------------------------------------------------------------------------


_SAID: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar("said", default=None)
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[«\"(A-ZА-ЯЁ0-9])")
SAID_MIN_WORDS = 6  # a shorter sentence is not worth a reference


def _once(text: str, fid: str) -> tuple[str, list[str]]:
    """``text`` without the sentences an earlier card of this report already printed word for word, and the ids of
    those cards. Outside ``render`` (no report-wide memory) the text is returned unchanged."""
    said = _SAID.get()
    if said is None or not text:
        return text, []
    keep: list[str] = []
    refs: list[str] = []
    for sent in _SENT_SPLIT.split(text.strip()):
        key = " ".join(normalize(sent).split())
        if len(sent.split()) >= SAID_MIN_WORDS and said.get(key, fid) != fid:
            if said[key] not in refs:
                refs.append(said[key])
            continue
        if len(sent.split()) >= SAID_MIN_WORDS:
            said.setdefault(key, fid)
        keep.append(sent)
    return " ".join(keep), refs


def _said_ref(refs: list[str]) -> str:
    return "как в " + links(refs[:3])


DEFAULT_NAME = "report.md"
LAYOUT = "2: короткий отчёт и файл подробностей"
RERENDERED = "; перерисован из сохранённого результата, модель не вызывалась"


def details_name(name: str = DEFAULT_NAME) -> str:
    """«report.md» → «report.details.md»: the details file that goes with the main report."""
    stem = name[:-3] if name.endswith(".md") else name.rsplit(".", 1)[0] if "." in name else name
    return (stem or "report") + ".details.md"


def render(result: Result, name: str = DEFAULT_NAME, *, rerendered: bool = False) -> str:
    """The main report («вёрстка 2»): compact findings that link to ``details_name(name)``.
    ``name`` is the file name the report is written under; ``rerendered`` marks a re-render without the model."""
    if result.diagnostic is not None:
        return render_diagnostic(result)
    token = _SAID.set({})
    try:
        return _render(result, name or DEFAULT_NAME, rerendered)
    finally:
        _SAID.reset(token)


def _render(result: Result, name: str, rerendered: bool) -> str:
    dname = details_name(name)
    parts = [
        _head(result, dname),
        _passport(result, rerendered),
        _slicing(result),
        _per_requirement(result, dname),
        _set_level(result, dname),
        _summary(result),
        _final(result),
        _questions(result, name),
    ]
    if result.tests:
        parts.append(_tests(result))
    parts.append(_appendix(result))
    return fix_links("\n\n".join(p.strip("\n") for p in parts if p) + "\n")


def _h2(key: str) -> str:
    return f'<a id="{key}"></a>\n## {SECTIONS[key]}'


def _head(result: Result, dname: str = "") -> str:
    r = result.run
    lines = ["# Отчёт о статической проверке требований", ""]
    when = _when(r.started_at)
    model = "без модели — только автоматические правила" if is_offline(result) else f"модель {model_label(r)}"
    lines.append(f"**Документ:** `{md_code(r.doc_name)}` · проверен {when} · {model}")
    lines.append("")
    lines += first_screen(result)
    lines.append("")
    lines.append("**Разделы:** " + " · ".join(
        f"[{t}](#{k})" for k, t in SECTIONS.items() if k != "s8" or result.tests))
    if dname:
        lines += ["", "Полные карточки с цитатами, разбор по каждому требованию, карта полноты и вопросы с вариантами "
                  f"ответов — в файле `{md_code(dname)}`."]
    return "\n".join(lines)


def readiness_lines(result: Result) -> list[str]:
    """Two plain lines under the verdict: ready for development, ready for testing (``readiness.compute``)."""
    rd = result.readiness or readiness_mod.compute(result)
    out = []
    for key in ("dev", "test"):
        part = rd.get(key) if isinstance(rd, dict) else None
        if isinstance(part, dict) and part.get("text"):
            out.append(f"> {'✅' if part.get('ok') else '⛔'} {md(str(part['text']))}.")
    return out


def first_screen(result: Result) -> list[str]:
    """What a reader who stops after one screen needs: the verdict, the counts, the blockers with their reasons, the
    questions whose answers change the most, what to fix first, time and cost. Also written to ``*.summary.md``."""
    r = result.run
    c = counted(result)
    t = tally(result.findings)
    head = result.verdict_text
    if is_offline(result) and result.verdict in verdict.TEXT and head.endswith(verdict.OFFLINE_TAIL):
        head = verdict.TEXT[result.verdict] + " (итог предварительный)"  # the ⚠️ line below says why
    sm = ans_mod.summary(result.audit)
    answered = bool(sm and sm.get("total"))
    lines = [f"> **Итог:** {md_plain(head)}",
             f"> Замечаний {num(t['total'])}: {counts_line(result)} · вопросов автору {num(len(result.questions))} · "
             f"требований {num(len(result.requirements))}" + (" · " + closed_by_answers_text(result) if answered else "")]
    lines += readiness_lines(result)
    if r.score is not None:
        prelim = ", предварительно" if is_offline(result) else ""
        clash = (result.verdict == "BLOCKED" and r.score >= 60) or (result.verdict == "CONDITIONAL" and r.score >= 85)
        lines.append(f"> Индекс готовности документа: **{r.score} из 100** — {score.readiness_band(r.score)}{prelim}."
                     + (" Вердикт определяют блокеры, а не индекс." if clash else "")
                     + (" Индекс считается по тексту документа; пока ответы не внесены в текст, он прежний."
                        if answered else ""))
    if sm and sm.get("total"):
        line = (f"> 💬 Ответы автора учтены: {sm.get('answered', 0)} из {sm['total']}. "
                f"Закрыто замечаний: {len(sm.get('closed') or [])}.")
        if result.verdict_after_answers_text:
            left = [f.fid for f in counted(result) if f.severity == "CRITICAL" and not f.closed_by_answer]
            if result.verdict_after_answers == result.verdict and left:
                line += (" Итог после внесения ответов не меняется: ответы не закрывают "
                         + ("блокер " if len(left) == 1 else "блокеры ") + links(left) + ".")
            else:
                after = result.verdict_after_answers_text
                if is_offline(result) and result.verdict_after_answers in verdict.TEXT:
                    after = verdict.TEXT[result.verdict_after_answers] + " (итог предварительный)"
                line += f" После внесения ответов в документ итог: {md_plain(after)}."
        lines.append(line)
    failed = failed_steps_line(result)
    if failed:
        lines.append("> ⚠️ " + md(failed))
    if is_offline(result):
        lines.append("> ⚠️ Без модели смысл не проверялся: противоречия и пропущенные сценарии не искались.")
    if r.injection_suspected:
        inj = [f for f in c if "prompt-injection" in f.rule_ids or ("hidden-text" in f.rule_ids and f.severity == "CRITICAL")]
        lines.append("> 🛡️ В документе есть текст, обращённый к проверяющему ИИ"
                     + (f" ({links([f.fid for f in inj])})" if inj else "") + ". Агент его не выполнял.")
    lines.append("")
    lines += _baseline_block(result)
    bl = _blockers(result)
    if bl:
        lines.append(f"**Блокеры — исправить до начала работы ({len(bl)}):**")
        lines.append("")
        for k, f in enumerate(bl, 1):
            lines.append(f"{k}. {link(f.fid)} {md(shorten(f.title, 90))} — {BLOCKER_SHORT.get(f.blocker_reason, 'основание не указано')}, "
                         f"{_where(f)}")
    else:
        majors = [f for f in c if f.severity == "MAJOR"]
        if majors:
            lines.append(f"**Блокеров нет.** Существенных замечаний: {len(majors)} — "
                         + links([f.fid for f in majors[:8]]) + ("…" if len(majors) > 8 else "") + ".")
        else:
            lines.append("**Блокеров и существенных замечаний нет.**")
    qs = first_questions(result)
    if qs:
        lines += ["", "**Сначала спросить автора:**", ""]
        for k, q in enumerate(qs, 1):
            lines.append(f"{k}. {link(q.qid)} {md(shorten(q.text, 75))}{_q_gain(q, result)}")
    fix = _merged_actions(result.fix_first)[:FIRST_FIX]
    if fix:
        lines += ["", "**Что исправить в первую очередь:**", ""]
        listed = {f.fid: f for f in bl}
        for k, (text, ids) in enumerate(fix, 1):
            if ids and all(i in listed for i in ids):  # its title is already on the blocker line above
                title = listed[ids[0]].title.strip().rstrip(".")
                if text.startswith(title):
                    text = _cap(text[len(title):].lstrip(". "))
            refs = f" ({links(ids[:3])}{' и ещё ' + str(len(ids) - 3) if len(ids) > 3 else ''})" if ids else ""
            lines.append(f"{k}. {md(shorten(text, 80))}{refs}")
    cost = usd(r.cost_usd) if not is_offline(result) else "$0, модель не вызывалась"
    lines += ["", f"Прогон: {duration_text(r.duration_s)} · {cost}."]
    return lines


def closed_by_answers_text(result: Result) -> str:
    """«ответы автора закрывают 16 замечаний, из них блокеров 4; после внесения ответов в текст останется 50, блокеров 1» from
    ``closed_by_answer`` of the counted findings."""
    c = counted(result)
    closed = [f for f in c if f.closed_by_answer]
    if not closed:
        return "ответы автора замечаний не закрывают"
    crit = sum(1 for f in closed if f.severity == "CRITICAL")
    left = len(c) - len(closed)
    left_crit = sum(1 for f in c if f.severity == "CRITICAL" and not f.closed_by_answer)
    return (f"ответы автора закрывают {count(len(closed), W_FINDING)}, из них блокеров {crit}; "
            f"после внесения ответов в текст останется {left}, блокеров {left_crit}")


def _merged_actions(actions) -> list[tuple[str, list[str]]]:
    """Fix actions with the same text become one line that links every finding they fix."""
    out: dict[str, list[str]] = OrderedDict()
    for a in actions:
        ids = out.setdefault(" ".join(a.action.split()), [])
        ids += [i for i in a.finding_ids if i not in ids]
    return list(out.items())


def first_questions(result: Result) -> list[Question]:
    """Up to FIRST_Q questions without an answer, ordered by what the answer changes (blockers closed, then the gain
    of the readiness index), then by their number."""
    pos = {q.qid: k for k, q in enumerate(result.questions)}
    open_q = [q for q in result.questions if not q.answer]
    open_q.sort(key=lambda q: (-int((q.impact or {}).get("blockers_closed") or 0),
                               -int((q.impact or {}).get("index_delta") or 0), pos[q.qid]))
    return open_q[:FIRST_Q]


def _q_gain(q: Question, result: Result) -> str:
    im = q.impact or {}
    k = int(im.get("blockers_closed") or 0)
    if k:
        crit = [fid for fid in im.get("closes") or q.finding_ids
                if any(f.fid == fid and f.severity == "CRITICAL" for f in counted(result))]
        if k == 1 and crit:
            asked = [x for x in result.questions if not x.answer and crit[0] in x.finding_ids]
            if len(asked) > 1:
                return f" — один из {len(asked)} вопросов к блокеру {link(crit[0])}"
            return f" — снимет блокер {link(crit[0])}"
        return f" — снимет {k} {plural(k, ('блокер', 'блокера', 'блокеров'))}"
    d = int(im.get("index_delta") or 0)
    return f" — индекс +{d}" if d > 0 else ""


def summary_md(result: Result, report_name: str = "") -> str:
    """The first screen as a page of its own (``*.summary.md``) for a wiki or a task tracker. Links lead into the
    full report when its file name is given, otherwise they become plain identifiers."""
    r = result.run
    title = f"# Ревизор ТЗ: «{md(r.doc_title or r.doc_name)}»"
    lines = [title, "", f"Документ `{md_code(r.doc_name)}` · проверен {_when(r.started_at)}", ""]
    if result.diagnostic is not None:
        lines.append(f"> **Итог:** ❔ Документ не проверен: {md(result.diagnostic.title)}")
        return "\n".join(lines) + "\n"
    lines += first_screen(result)
    text = "\n".join(lines) + "\n"
    if report_name:
        return text.replace("](#", f"]({report_name}#")
    import re

    return re.sub(r"\[([^\]\n]+)\]\(#[^)\s]+\)", r"\1", text)


def _baseline_block(result: Result) -> list[str]:
    b = baseline_mod.entry(result.audit)
    if not b:
        return []
    fixed, still, new = b.get("fixed") or [], b.get("still_open") or [], b.get("new") or []
    when = f" от {_when(b['old_when'])}" if b.get("old_when") else ""
    out = [f"**С прошлой проверки** (`{md_code(b.get('file', ''))}`{when}): исправлено {len(fixed)}, "
           f"осталось {len(still)}, новых {len(new)}.", ""]

    def part(title: str, items: list[dict], new_ids: bool) -> None:
        if not items:
            return
        out.append(f"- {title}:")
        for it in items[:8]:
            ref = link(it["new_fid"]) if new_ids and it.get("new_fid") else (link(it["fid"]) if new_ids else f"{md(it.get('fid') or '—')} (прежний номер)")
            out.append(f"  - {ref} · {md(it.get('severity', ''))} · {md(it.get('title', ''))}")
        if len(items) > 8:
            out.append(f"  - … и ещё {len(items) - 8}")

    part("Исправлено", fixed, False)
    part("Осталось", still, True)
    part("Появилось", new, True)
    out.append("")
    return out


def md_plain(s: str) -> str:
    """Our own fixed texts with emoji: escape only what could form markup."""
    return md(s)


def md_code(s: str) -> str:
    return (s or "").replace("`", "'").replace("\n", " ")


STAGE_RU = {"slice": "нарезка", "slice_rest": "нарезка (остаток)", "review": "проверка требований",
            "review_clarity": "линза «ясность»", "review_completeness": "линза «полнота и проверяемость»",
            "review_correctness": "линза «корректность»", "set_consistency": "набор: непротиворечивость",
            "set_completeness": "набор: полнота", "set_standards": "набор: стандарты и безопасность",
            "set_gaps": "набор: проход-критик «чего не хватает»",
            "skeptic": "перепроверка", "dedup": "перепроверка: повторы", "questions": "вопросы автору", "answers": "ответы автора",
            "tests": "черновики тестов", "checklist": "чек-лист из документа"}


def stage_rows(result: Result) -> list[dict]:
    for a in result.audit:
        if a.get("type") == "stage_usage":
            return list(a.get("rows") or [])
    return []


def _stage_table(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    out = ["", "**Расход по этапам** (выход включает рассуждения модели — они оплачиваются как выход):", "",
           "| Этап | Вызовов | Вход | Выход | Чтение кэша | Время | Стоимость |",
           "| :--- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in rows:
        out.append(f"| {md(STAGE_RU.get(r.get('stage', ''), r.get('stage', '')))} | {num(r.get('calls') or 0)} | "
                   f"{num(r.get('input_tokens') or 0)} | {num(r.get('output_tokens') or 0)} | "
                   f"{num(r.get('cache_read') or 0)} | {dec(r.get('seconds') or 0, 0)} с | {usd(r.get('cost_usd'))} |")
    return out


def _when(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or "—"
    off = dt.strftime("%z")
    off = f"UTC{off[:3]}:{off[3:]}" if off else ""
    return dt.strftime("%d.%m.%Y %H:%M") + (f" ({off})" if off else "")


# --- section 1 ------------------------------------------------------------------------------

DETAIL_WARNINGS = ("Усилие модели",)  # run details for the diagnostic file, not for the passport


def _audit_count(result: Result, what: str) -> int:
    return sum(1 for a in result.audit if a.get("what") == what)


def recheck_line(result: Result) -> str:
    r = result.run
    if r.mode != "full":
        return "не выполнялась (" + ("быстрый режим" if r.mode == "fast" else "без модели") + ")"
    line = (f"оставлено {r.skeptic_kept}"
            + (f", в наблюдения {r.skeptic_advisory}" if r.skeptic_advisory else "")
            + (f", отклонено {r.skeptic_rejected}" if r.skeptic_rejected else "")
            + f", объединено {r.skeptic_merged}" + (f" (из них повторов: {r.skeptic_dedup})" if r.skeptic_dedup else ""))
    refused_m = (_audit_count(result, "skeptic_merge_refused") + _audit_count(result, "gate_merge_refused")
                 + _audit_count(result, "dedup_merge_refused"))
    refused_d = _audit_count(result, "gate_drop_refused")
    refused_r = _audit_count(result, "skeptic_raise_refused")
    if refused_m:
        line += (f"; не дано объединить замечания о разных дефектах: {refused_m} "
                 "(оба замечания остались)")
    if refused_d:
        line += f"; не дано отбросить без достаточного основания: {refused_d} (замечание осталось)"
    if refused_r:
        line += f"; не дано поднять уровень по доводу о другом дефекте: {refused_r}"
    if r.critical_demoted:
        line += f"; понижено блокеров без основания: {r.critical_demoted}"
    return line


def _passport(result: Result, rerendered: bool = False) -> str:
    r = result.run
    rows: list[tuple[str, str]] = []
    doc = f"`{md_code(r.doc_name)}` · {FORMAT_RU.get(r.doc_format, r.doc_format)} · {size_text(r.doc_size_bytes)} · "
    doc += f"{num(r.doc_lines)} {plural(r.doc_lines, ('строка', 'строки', 'строк'))}"
    if r.doc_pages:
        doc += f" · {r.doc_pages} {plural(r.doc_pages, ('страница', 'страницы', 'страниц'))}"
    doc += f" · {num(r.doc_words)} {plural(r.doc_words, ('слово', 'слова', 'слов'))} · SHA-256 `{r.doc_sha256[:12]}`"
    if r.doc_title:
        doc += f" · «{md(r.doc_title)}»"
    rows.append(("Документ", doc))
    if r.context_files:
        rows.append(("Контекст (--context)", ", ".join(f"`{md_code(n)}`" for n in r.context_files)
                     + " — только для понимания терминов; замечания и цитаты — по основному документу"))
    rows.append(("Дата и время прогона", _when(r.started_at)))
    if is_offline(result):
        model = "без модели"
        if r.provider not in ("offline", "", "fake") and r.model:
            model += f" (провайдер {md(r.provider)} / {md(r.model)} недоступен — см. предупреждения)"
    else:
        model = f"{md(r.provider)} / {model_label(r)}" + (f", effort {md(r.effort)}" if r.effort else "")
    rows.append(("Модель", model))
    rows.append(("SHA-256 документа", f"`{md_code(r.doc_sha256)}`" if r.doc_sha256 else "—"))
    if r.answers_sha256:
        rows.append(("SHA-256 файла ответов", f"`{md_code(r.answers_sha256)}`"))
    if r.rules_version:
        rows.append(("Набор правил", f"версия {md(r.rules_version)}"))
    rows.append(("Версия агента", version_label(md(r.agent_version), md_code(r.agent_commit), md(r.rules_version),
                                                tag=md_code(r.agent_tag), dirty=r.agent_dirty)))
    rows.append(("Режим", MODE_RU.get(r.mode, r.mode)))
    rows.append(("Вёрстка отчёта", LAYOUT + (RERENDERED if rerendered else "")))
    rows.append(("Время анализа", duration_text(r.duration_s)))
    rows.append(("Стоимость прогона", (f"{usd(r.cost_usd)} ({md(r.cost_note)})" if r.cost_note else usd(r.cost_usd))
                 + (f"; обращений к модели {num(r.llm_calls)}" if r.llm_calls else "")))
    qline = f"подтверждено {num(r.quotes_confirmed)}, отброшено {num(r.quotes_dropped)}"
    if r.quotes_restored:
        qline += f" (восстановлено по сходству {num(r.quotes_restored)})"
    rows.append(("Проверка цитат", qline))
    rows.append(("Перепроверка", recheck_line(result)))
    rows.append(("Защита данных", f"замаскировано {num(r.masked_values)} "
                 f"{plural(r.masked_values, ('значение', 'значения', 'значений'))}, похожих на номера карт, ИИН/БИН, "
                 "адреса почты или телефоны" if r.masked_values else
                 "значений, похожих на номера карт, ИИН/БИН, адреса почты или телефоны, не найдено"))
    if failed_steps_line(result):
        rows.append(("Неудавшиеся шаги", md(failed_steps_line(result))))
    if r.injection_suspected:
        rows.append(("Подсадной текст", "найден текст, обращённый к проверяющему ИИ; агент его не выполнял, "
                                        "замечания правил защищены от отклонения моделью"))
    out = [_h2("s1"), "", "| Поле | Значение |", "| :--- | :--- |"]
    out += [f"| {k} | {v} |" for k, v in rows]
    warn = [w for w in r.warnings if not w.startswith(DETAIL_WARNINGS)]
    if warn:
        out += ["", "**Предупреждения:**", ""]
        out += [f"- {md(w)}" for w in warn]
    return "\n".join(out)


# --- section 2 ------------------------------------------------------------------------------------


def _clarity_by_rid(result: Result) -> dict[str, float | None]:
    return {rv.rid: rv.clarity for rv in result.reviews}


def _slicing(result: Result) -> str:
    out = [_h2("s2"), ""]
    how = "моделью" if any(r.origin == "model" for r in result.requirements) else "эвристикой (без модели)"
    out.append(f"Требования выделены {how}; номера REQ-… присвоены программой по порядку документа. "
               f"Найдено {count(len(result.requirements), W_REQ)}. Формулировка — точная цитата; длинная обрезана, "
               "полный текст — в файле подробностей. Ясность каждого требования — в матрице раздела 3.")
    out.append("")
    if result.requirements:
        out.append("| ID (в документе) | Тип | Формулировка (цитата) | Где |")
        out.append("| :--- | :--- | :--- | :--- |")
        for r in result.requirements:
            out.append(f'| <a id="{anchor(r.rid)}"></a>{r.rid}{" " + md(r.doc_id) if r.doc_id else ""} | '
                       f"{KIND_RU.get(r.kind, r.kind)} | {md_cell(plain_quote(r.quote), TABLE_QUOTE)} | "
                       f"{location(r.line_start, r.line_end, r.page)} |")
    else:
        out.append("Требований в документе не найдено.")
    if result.skipped:
        out += ["", f"**Не считал требованиями: {count(len(result.skipped), W_BLOCK)}.**", ""]
        groups: "OrderedDict[tuple[str, str], list]" = OrderedDict()
        for s in result.skipped:
            groups.setdefault((s.category, s.reason), []).append(s)
        for (cat, reason), items in groups.items():
            where = ", ".join(str(s.line_start) for s in items[:15]) + (f" и ещё {len(items) - 15}" if len(items) > 15 else "")
            label = "строка" if len(items) == 1 else "строки"
            example = f" — «{md(shorten(plain_quote(items[0].text), 70))}»" if len(items) == 1 and cat not in ("heading",) else ""
            out.append(f"- {SKIP_CATEGORY_RU.get(cat, cat)}: {md(reason) or '—'} · {label} {where}{example}")
    return "\n".join(out)


# --- cards ------------------------------------------------------------------------------------------


class Shown:
    """Document lines already printed in this card: a line is printed once per card and once per finding at most."""

    def __init__(self) -> None:
        self.items: list[tuple[int, int, str]] = []

    def add(self, line_start: int, line_end: int, text: str) -> None:
        self.items.append((line_start, line_end, normalize(text)))

    def has(self, q: Quote) -> bool:
        nq = normalize(q.text)
        return any(a <= q.line_start and q.line_end <= b and nq and nq in t for a, b, t in self.items)


def _quote_items(f: Finding, shown: Shown | None, with_rid: bool, cap: int | None = QUOTES_SHOWN) -> list[str]:
    """Each distinct quote once. A quote inside one this card has already printed is skipped; a quote of the
    requirement printed right above the card (``shown``) is named by its place only."""
    above = shown if shown is not None else Shown()
    mine = Shown()
    seen: set[tuple[int, int, str]] = set()
    items: list[str] = []
    rest: list[Quote] = []
    for q in f.quotes:
        key = (q.line_start, q.line_end, normalize(q.text))
        if key in seen:
            continue
        seen.add(key)
        if cap is not None and len(items) >= cap:
            rest.append(q)
            continue
        if mine.has(q):
            continue
        if above.has(q):
            where = quote_location(q) + (f", {link(q.rid)}" if with_rid and q.rid else "")
            if where not in items:
                items.append(where)
        else:
            items.append(_quote_line(q, with_rid=with_rid))
            mine.add(q.line_start, q.line_end, q.text)
    if rest:
        nums = list(dict.fromkeys(q.line_start for q in rest))  # one line may carry several quotes
        ls = ", ".join(str(n) for n in nums[:12]) + ("…" if len(nums) > 12 else "")
        word = "строка" if len(nums) == 1 else "строки"
        items.append(f"и ещё {len(rest)} {plural(len(rest), ('цитата', 'цитаты', 'цитат'))}: {word} {ls}")
    return items


def _place_list(f: Finding) -> list[str]:
    """A folded formal remark: every place as «строка — начало пункта», all of them."""
    out = []
    for q in f.quotes:
        rid = f" ({link(q.rid)})" if q.rid else ""
        out.append(f"{quote_location(q)}{rid} — «{md(shorten(plain_quote(q.text), 70))}»")
    return out


HOW_SET = {"set_consistency": "сверка разделов между собой", "set_completeness": "проверка полноты набора требований",
           "set_standards": "проверка стандартов и безопасности"}


def how_found(f: Finding, plain: bool = False) -> str:
    """One short line «how this finding was found»: a rule with its name, or the stage and the criterion of the model,
    or both. Used on every card (here and by other modules)."""
    esc = (lambda x: x) if plain else md
    code = (lambda x: x) if plain else (lambda x: f"`{md_code(x)}`")
    ids = list(f.rule_ids) or ([f.source[5:]] if f.source.startswith("rule:") else [])

    def rule_part() -> str:
        names = []
        for i in ids:
            info = rules_mod.RULES.get(i)
            names.append((f"«{esc(info.title)}» " if info else "") + f"({code(i)})")
        return ("правило" if len(names) == 1 else "правила") + ": " + "; ".join(names)

    crit = CRITERION_RU.get(f.criterion, f.criterion).lower()
    if f.stage == FOLD_STAGE:
        return "программа собрала в одно замечание однотипные замечания об атомарности пунктов"
    if f.stage == GAPS_STAGE:
        model = "модель, контрольный вопрос " + ", ".join(f.gap_ids) if f.gap_ids else "модель, проход «чего не хватает»"
    elif f.stage in HOW_SET:
        model = "модель, " + HOW_SET[f.stage]
    elif f.stage == "review" or f.stage in LENS_BY_STAGE:
        model = f"модель, проверка требования по критерию «{esc(crit)}»"
    else:
        model = f"модель, критерий «{esc(crit)}»"
    if f.source == "both":
        return (rule_part() if ids else "правило") + "; и модель: " + model.split(", ", 1)[-1]
    if f.source.startswith("rule:") or (ids and f.source != "model"):
        return "правило без модели: " + rule_part().split(": ", 1)[1]
    return model


def standard_tail(f: Finding) -> str:
    """«. Основание: ISO/IEC/IEEE 29148:2018, п. 5.2.5 — …» for the end of the «Как найдено» line; "" when unknown."""
    line = f.standard or standard_mod.standard_of(f)
    return f". Основание: {md(line)}" if line else ""


ABSORBED_SHOWN = 5   # merged-in findings printed in full per card; the rest by title
ABSORBED_PROBLEM = 240
BRIEF_ABSORBED = 2    # the same on a card of the main report; the details file has them all


def _trim_words(text: str, limit: int = ABSORBED_PROBLEM) -> str:
    """The text cut at a word boundary to about ``limit`` characters."""
    t = " ".join((text or "").split())
    if len(t) <= limit:
        return t
    cut = t[:limit]
    sp = cut.rfind(" ")
    if sp > limit * 0.6:
        cut = cut[:sp]
    return cut.rstrip(" ,;:.—-") + "…"


def _absorbed_place(a: dict) -> str:
    qs = [q for q in (a.get("quotes") or []) if isinstance(q, dict)]
    if not qs:
        return ""
    q = qs[0]
    line = int(q.get("line") or 0)
    text = " ".join(plain_quote(str(q.get("text") or "")).split())
    more = f" (и ещё {len(qs) - 1} {plural(len(qs) - 1, ('цитата', 'цитаты', 'цитат'))})" if len(qs) > 1 else ""
    return (f"строка {line}: " if line else "") + f"«{md(shorten(text, 160))}»" + more


def _absorbed_line(a: dict) -> str:
    title = md(shorten(str(a.get("title") or ""), 140))
    problem = _trim_words(str(a.get("problem") or ""))
    sep = "" if title.rstrip()[-1:] in ".?!" else "."
    parts = [f"**{title}**{sep}"]
    if problem:
        parts.append(md(problem))
    place = _absorbed_place(a)
    if place:
        parts.append(place + ".")
    return " ".join(parts)


def absorbed_lines(f: Finding, cap: int = ABSORBED_SHOWN) -> list[str]:
    """the findings merged into this card («Объединено с похожими замечаниями»), each with its title, problem and place. The first line is the
    heading, the others are nested items (they start with two spaces and a dash). A report written before the full
    record existed has only titles (``also``): they are printed alone. A folded formal remark lists its places instead."""
    if f.stage == FOLD_STAGE:
        return []
    items = [a for a in f.absorbed if isinstance(a, dict) and a.get("title")]
    if not items:
        if not f.also:
            return []
        shown = "; ".join(f"«{md(shorten(t, 90))}»" for t in f.also[:ALSO_SHOWN])
        more = f" и ещё {len(f.also) - ALSO_SHOWN}" if len(f.also) > ALSO_SHOWN else ""
        return ["Объединено с похожими замечаниями: " + shown + more]
    out = [f"Объединено с похожими замечаниями ({len(items)}):"]
    out += ["  - " + _absorbed_line(a) for a in items[:cap]]
    rest = items[cap:]
    if rest:
        out.append(f"  - И ещё {len(rest)}: " + "; ".join(f"«{md(shorten(str(a.get('title')), 90))}»" for a in rest))
    return out


def absorbed_plain(f: Finding) -> list[str]:
    """The same record as plain lines for a tracker import (no Markdown)."""
    out = []
    for a in f.absorbed:
        if not isinstance(a, dict) or not a.get("title"):
            continue
        line = " ".join(str(a.get("title")).split())
        problem = _trim_words(str(a.get("problem") or ""))
        if problem:
            line += ". " + problem
        qs = [q for q in (a.get("quotes") or []) if isinstance(q, dict)]
        if qs:
            line += f" (строка {int(qs[0].get('line') or 0)}: «{shorten(' '.join(plain_quote(str(qs[0].get('text') or '')).split()), 160)}»)"
        out.append("- " + line)
    if not out and f.also:
        out = ["- " + " ".join(t.split()) for t in f.also]
    return out


def _see_also_line(f: Finding, with_rid: bool) -> str:
    if not f.see_also:
        return ""
    parts = [_quote_line(q, with_rid=with_rid) for q in f.see_also[:SEE_ALSO_SHOWN]]
    more = f"; и ещё {len(f.see_also) - SEE_ALSO_SHOWN}" if len(f.see_also) > SEE_ALSO_SHOWN else ""
    return "См. также (место из объединённого повтора, на отметки не влияет): " + "; ".join(parts) + more


_BY_RU = {"model": "модель", "rule": "правило", "skeptic": "перепроверка", "merge": "объединение", "code": "программа",
          "answer": "ответ автора"}
_FID = re.compile(r"^D-\d+$")


def _merged_with(w: str) -> str:
    """What ``merge.note_level`` stored in ``with``: a finding id, «правило a, b» or the start of a title."""
    w = (w or "").strip()
    if not w:
        return "объединено с другим замечанием"
    if _FID.match(w):
        return f"объединено с замечанием {w}"
    if w.startswith("правило "):
        ids = [x.strip() for x in w[len("правило "):].split(",") if x.strip()]
        word = "правилом" if len(ids) == 1 else "правилами"
        return f"объединено с {word} " + ", ".join(f"`{md_code(x)}`" for x in ids)
    return f"объединено с замечанием «{md(shorten(w, 80))}»"


def level_trail_text(f: Finding) -> str:
    """«модель — MINOR → объединено с правилом `x` — MAJOR (причина) → перепроверка — MINOR (причина)»;
    "" when the level never changed. Ends with the card's level."""
    t = f.level_trail
    if len(t) < 2:
        return ""
    parts = []
    for k, e in enumerate(t):
        by = e.get("by", "")
        if by == "merge":
            who = _merged_with(e.get("with", ""))
        elif by == "rule" and f.rule_ids:
            who = ("правило " if len(f.rule_ids) == 1 else "правила ") + ", ".join(f"`{md_code(x)}`" for x in f.rule_ids)
        else:
            who = md(_BY_RU.get(by, by))
        part = f"{who} — {md(e.get('level', ''))}"
        if k and e.get("why"):
            why = e["why"].rstrip(".")
            part += f" ({md(why[:150].rsplit(' ', 1)[0] + '…' if len(why) > 150 else why)})"
        parts.append(part)
    return " → ".join(parts)


def more_link(f: Finding, dname: str) -> str:
    return f"[подробно]({dname}#x-{anchor(f.fid)})" if dname else ""


def card(f: Finding, *, show_req_links: bool, shown: Shown | None = None, brief: bool = False, more: str = "") -> str:
    """A finding. CRITICAL and MAJOR get a full card; MINOR costs two or three lines.
    ``brief`` (the main report): without merged titles, places of repeats, the level trail and the source;
    ``more`` is appended to the last line (the link to the details file)."""
    crit = CRITERION_RU.get(f.criterion, f.criterion)
    head = f"**{f.fid} · {_sev(f)} · {crit}.** {md(f.title)}"
    folded = f.stage == FOLD_STAGE
    quotes = _place_list(f) if folded else _quote_items(f, shown, show_req_links, BRIEF_QUOTES if brief else QUOTES_SHOWN)
    tail: list[str] = []
    if f.question_id:
        tail.append("вопрос автору " + link(f.question_id))
    if show_req_links:
        extra = [r for r in f.req_ids if not any(q.rid == r for q in f.quotes)]
        if extra:
            tail.append("затронуты " + links(extra[:8]) + (f" и ещё {len(extra) - 8}" if len(extra) > 8 else ""))
    if more:
        tail.append(more)
    trail = level_trail_text(f)
    found_line = "Как найдено: " + how_found(f) + standard_tail(f)
    extra_lines = [found_line] + ([] if brief else [x for x in (_see_also_line(f, show_req_links),
                                                               ("Уровень: " + trail) if trail else "") if x])
    extra_lines += absorbed_lines(f, BRIEF_ABSORBED if brief else ABSORBED_SHOWN)
    problem, p_refs = _once(f.problem, f.fid)
    problem = md(problem) + ((" " if problem else "") + _cap(_said_ref(p_refs)) + "." if p_refs else "")
    rec, r_refs = _once(f.recommendation, f.fid)
    rec = md(rec) + ((" " if rec else "") + ("Дальше — " if rec else "") + _said_ref(r_refs) + "." if r_refs else "")
    rec = _cap(rec)
    if f.severity == "MINOR" and not folded:
        sep = "" if f.title.rstrip()[-1:] in ".?!" else "."
        body = [head + sep + (" " + problem if problem else "")]
        where = "; ".join(quotes)
        line = (f"Где: {where}." if where else "")
        if rec:
            line += (" " if line else "") + "Как исправить: " + rec
        if f.fixed_wording:
            line += " Готовая формулировка — в `*.fixed.md`."
        if f.fix_check:
            line += " " + md(clarify.fix_check_text(f))
        items = [line] + extra_lines + [_cap("; ".join(tail))]
        return "\n".join([f'<a id="{anchor(f.fid)}"></a>'] + body + [""]
                         + [(x if x.startswith("  - ") else f"- {x}") for x in items if x])
    lines = [f'<a id="{anchor(f.fid)}"></a>', head, ""]
    if problem:
        lines += [problem, ""]
    items = []
    if len(quotes) == 1:
        items.append("Цитата: " + quotes[0])
    elif quotes:
        items.append("Места:" if folded else "Цитаты:")
        items += ["  - " + x for x in quotes]
    if rec:
        items.append("Как исправить: " + rec)
    if f.fixed_wording:
        items.append("Готовая формулировка — в файле `*.fixed.md` рядом с отчётом.")
    if f.fix_check:
        items.append(md(clarify.fix_check_text(f)))
    if f.severity == "CRITICAL" and f.status == "open" and not f.advisory:
        items.append("Почему блокер: " + BLOCKER_RU.get(f.blocker_reason, "основание не указано"))
    items += extra_lines
    if tail:
        items.append(_cap("; ".join(tail)))
    for it in items:
        lines.append(it if it.startswith("  - ") else "- " + it)
    return "\n".join(lines)



def _req_refs(f: Finding, cap: int = 3) -> str:
    return links(f.req_ids[:cap]) + (f" и ещё {len(f.req_ids) - cap}" if len(f.req_ids) > cap else "")


def _place(f: Finding, cap: int = 3) -> str:
    """Lines and requirements of a finding for the compact forms; never empty."""
    parts = [x for x in (_where(f), _req_refs(f, cap)) if x]
    return ", ".join(parts) if parts else "весь документ"


def compact_card(f: Finding, dname: str) -> str:
    """A MAJOR finding in the main report: head, place with the first quote, the full recommendation."""
    crit = CRITERION_RU.get(f.criterion, f.criterion)
    head = f"**{f.fid} · {_sev(f)} · {crit}.** {md(f.title)}"
    where = _place(f)
    if f.quotes:
        where += f": «{md(shorten(' '.join(plain_quote(f.quotes[0].text).split()), WHERE_QUOTE))}»"
    tail = [x for x in ((("вопрос автору " + link(f.question_id)) if f.question_id else ""), more_link(f, dname)) if x]
    rec = _cap(md(f.recommendation)) if f.recommendation else ""
    if f.fixed_wording:
        rec += (" " if rec else "") + "Готовая формулировка — в файле `*.fixed.md`."
    last = ("Как исправить: " + rec) if rec else ""
    if tail:
        last = f"{last} · {'; '.join(tail)}" if last else _cap("; ".join(tail))
    lines = [f'<a id="{anchor(f.fid)}"></a>', head, "", f"- Где: {where}"]
    if last:
        lines.append("- " + last)
    lines.append("- Как найдено: " + how_found(f) + standard_tail(f))
    lines += [x if x.startswith("  - ") else "- " + x for x in absorbed_lines(f, BRIEF_ABSORBED)]
    return "\n".join(lines)


def minor_table(items: list[Finding]) -> str:
    out = ["| Замечание | Где | Что не так |", "| :--- | :--- | :--- |"]
    for f in items:
        crit = CRITERION_RU.get(f.criterion, f.criterion)
        out.append(f'| <a id="{anchor(f.fid)}"></a>{f.fid} · {md(crit)} | {_place(f, 2)} | {md_cell(f.title)} |')
    return "\n".join(out)


def findings_brief(items: list[Finding], dname: str) -> str:
    """The main report's form of a group of findings: CRITICAL — full card, MAJOR — compact card, MINOR — one table."""
    out = []
    for f in items:
        if f.severity == "CRITICAL":
            out.append(card(f, show_req_links=True, brief=True, more=more_link(f, dname)))
    out += [compact_card(f, dname) for f in items if f.severity == "MAJOR"]
    minors = [f for f in items if f.severity not in ("CRITICAL", "MAJOR")]
    if minors:
        out.append(f"Мелкие замечания ({len(minors)}):\n\n" + minor_table(minors))
    return "\n\n".join(out)


# --- section 3 --------------------------------------------------------------------------------------


CRITERION_SHORT = {"completeness": "Полнота", "unambiguity": "Однозн.", "consistency": "Непротив.",
                   "verifiability": "Провер.", "feasibility": "Реализ.", "necessity": "Необх.", "singularity": "Атом."}


def card_plan(result: Result) -> tuple[dict[str, list[Finding]], list[Requirement], list[Requirement]]:
    """(requirement-level findings printed in each requirement's card, requirements with a card, requirements
    listed in the short table without a card). A requirement gets a card when a finding of its own names it or a
    set finding about a few places (at most BROAD_SET requirements) does; a set finding about the whole document
    does not by itself make a card."""
    c = counted(result)
    placed: dict[str, list[Finding]] = {}
    for f in c:
        if f.scope == "requirement" and f.req_ids:
            placed.setdefault(f.req_ids[0], []).append(f)
    with_card, without = [], []
    for r in result.requirements:
        named = [f for f in c if r.rid in f.req_ids and (f.scope == "requirement" or len(f.req_ids) <= BROAD_SET)]
        (with_card if named else without).append(r)
    return placed, with_card, without


def _per_requirement(result: Result, dname: str = "") -> str:
    """Main report: legend, matrix, the findings about single requirements in compact form."""
    out = [_h2("s3"), ""]
    out += _s3_legend(result, "")
    reviews = {rv.rid: rv for rv in result.reviews}
    if result.requirements:
        out += ["", _matrix(result, reviews)]
    placed, with_card, _without = card_plan(result)
    own = [f for r in with_card for f in placed.get(r.rid, [])]
    sc = sev_counts(own)
    out.append("")
    if own:
        out += [f"**Замечания к отдельным требованиям ({len(own)}): блокеров {sc['CRITICAL']} · существенных "
                f"{sc['MAJOR']} · мелких {sc['MINOR']}**", "", findings_brief(own, dname)]
    else:
        out.append("**Замечаний к отдельным требованиям нет.**")
    if dname:
        out += ["", f"Цитаты, ход перепроверки и разбор по каждому требованию — в файле `{md_code(dname)}`, раздел А."]
    return "\n".join(out)


def _s3_legend(result: Result, tail: str) -> list[str]:
    out = []
    if is_offline(result):
        out.append("Без модели каждое требование проверено только автоматическими правилами (размытые слова, "
                   "заглушки, сроки без условий, незакрытые перечни, несколько требований в одном пункте и др.); "
                   "смысловые критерии не проверялись (❔).")
    else:
        out.append("Обозначения: ✅ нарушений нет · ⚠️ есть замечание (ссылка ведёт к нему) · — неприменим · "
                   "❔ не проверялось.")
    out.append(CLARITY_LEGEND + tail)
    return out


def _per_requirement_details(result: Result) -> str:
    """Details file, part А: a block per requirement with its findings in full, then the clean requirements."""
    out = ['<a id="part-a"></a>', "## А. Разбор по каждому требованию", ""]
    offline = is_offline(result)
    out += _s3_legend(result, " Карточки ниже — только у требований, к которым есть замечания; матрица "
                              "«требование × критерий» — в разделе 3 отчёта.")
    reviews = {rv.rid: rv for rv in result.reviews}
    placed, with_card, without = card_plan(result)
    for r in with_card:
        out.append("")
        out.append(_req_block(r, placed.get(r.rid, []), result, reviews.get(r.rid), offline))
    if without:
        out += ["", _no_findings_table(result, without)]
    return "\n".join(out)


def _matrix(result: Result, reviews: dict) -> str:
    """One row per requirement, one column per criterion of a single requirement; ⚠️ links to the first finding.
    The marks are the ones ``consistency.apply_marks`` stored, so the matrix, the cards and the JSON agree."""
    out = ['<a id="s3-matrix"></a>', "**Матрица «требование × критерий»**", "",
           "| Требование | " + " | ".join(CRITERION_SHORT[x] for x in REQ_CRITERIA) + " | Ясность |",
           "| :--- | " + " | ".join(":---:" for _ in REQ_CRITERIA) + " | ---: |"]
    empty = 0
    blank: list[Requirement] = []
    for r in result.requirements:
        rv = reviews.get(r.rid)
        crit = rv.criteria if rv is not None else {}
        by_crit, _t = marks_of(r.rid, result.findings)
        if not by_crit and all((crit[x].status if x in crit else "unchecked") == "unchecked" for x in REQ_CRITERIA):
            blank.append(r)  # a row of seven ❔ says nothing: such rows are named in one line under the table
            empty += len(REQ_CRITERIA)
            continue
        cells = []
        for x in REQ_CRITERIA:
            st = crit[x].status if x in crit else "unchecked"
            hit = by_crit.get(x)
            if hit:
                cells.append(f"[⚠️](#{anchor(hit[0].fid)})")
            else:
                if st == "unchecked":
                    empty += 1
                cells.append(STATUS_ICON.get("ok" if st == "issue" else st, "❔"))
        label = link(r.rid) + (f" {md(r.doc_id)}" if r.doc_id else "")
        cl = rv.clarity if rv is not None else None
        out.append(f"| {label} | " + " | ".join(cells) + f" | {dec(cl) if cl is not None else '—'} |")
    if blank:
        out += ["", f"Не проверены ни по одному критерию (во всех клетках было бы ❔): {count(len(blank), W_REQ)} — "
                + ", ".join(link(r.rid) for r in blank) + "."]
    if empty:
        out += ["", f"Пустых клеток (не проверено): {empty}."]
    return "\n".join(out)


def _ref(f: Finding) -> str:
    """A finding named on a requirement card: its id and level; a remark about the whole document says so."""
    if f.scope == "set" and len(f.req_ids) > BROAD_SET:
        return f"{link(f.fid)} (к набору)"
    return f"{link(f.fid)} {SEVERITY_ICON.get(f.severity, '')}".rstrip()


def _clar(rv) -> str:
    return "ясность " + dec(rv.clarity) if rv is not None and rv.clarity is not None else "ясность —"


def _marks_line(r: Requirement, result: Result, rv, offline: bool) -> str:
    by_crit, touched = marks_of(r.rid, result.findings)
    crit = rv.criteria if rv is not None else {}
    parts = []
    for x in REQ_CRITERIA:
        if by_crit.get(x):
            parts.append(f"{CRITERION_SHORT[x]} " + ", ".join(_ref(f) for f in by_crit[x]))
    line = ("⚠️ " + "; ".join(parts)) if parts else ""
    rest = [x for x in REQ_CRITERIA if not by_crit.get(x)]
    unchecked = [x for x in rest if (crit[x].status if x in crit else "unchecked") == "unchecked"]
    if offline:
        tail = "прочие без модели не проверялись"
    elif unchecked and len(unchecked) == len(rest):
        tail = "остальные не проверены"
    elif unchecked:
        tail = "не проверены: " + ", ".join(CRITERION_SHORT[x] for x in unchecked) + "; прочие ✅"
    else:
        tail = "прочие ✅" if parts else "по всем семи критериям нарушений нет"
    line = (line + " · " + tail) if line else _cap(tail)
    if touched:
        line += " · затронуто: " + ", ".join(_ref(f) for f in touched)
    return line


def _req_block(r: Requirement, own: list[Finding], result: Result, rv, offline: bool) -> str:
    title = [r.rid]
    if r.doc_id:
        title.append(md(r.doc_id))
    sec = last_section(r.section)
    if sec:
        title.append(md(shorten(sec, 70)))
    title.append(location(r.line_start, r.line_end, r.page))
    lines = [f'<a id="{anchor(r.rid)}"></a>', "### " + " · ".join(title), "", blockquote(plain_quote(r.quote)), ""]
    shown = Shown()
    shown.add(r.line_start, r.line_end, r.quote)
    linked = consistency.linked(r.rid, result.findings)
    meta = ""
    if rv is not None and rv.clarity is not None:
        # a verdict word only next to what lowers the score (the marks line lists every linked finding)
        band = f" — {score.clarity_band(rv.clarity)}" if linked or rv.clarity >= 10 else ""
        meta = f"Ясность {dec(rv.clarity)}{band} · "
    lines += [meta + _marks_line(r, result, rv, offline), ""]
    for f in own:
        lines += [card(f, show_req_links=False, shown=shown), ""]
    return "\n".join(lines).rstrip()


def _no_findings_table(result: Result, reqs: list[Requirement]) -> str:
    """Requirements without a card: number, place, the start of the text (full text in section 2) and the
    whole-document findings that name them."""
    broad = {r.rid: consistency.linked(r.rid, result.findings) for r in reqs}
    keys = {tuple(f.fid for f in v) for v in broad.values()}
    common = list(next(iter(broad.values()))) if len(keys) == 1 else []
    out = ['<a id="s3-clean"></a>', f"**Требования без замечаний ({len(reqs)})**", ""]
    if common:
        out += ["Их называют только замечания ко всему документу: " + ", ".join(_ref(f) for f in common) + ".", ""]
    out += ["| Требование | Где | Начало текста (полностью — в [разделе 2](#s2)) |", "| :--- | :--- | :--- |"]
    for r in reqs:
        lab = f'<a id="{anchor(r.rid)}"></a>{r.rid}' + (f" {md(r.doc_id)}" if r.doc_id else "")
        refs = "" if common or not broad[r.rid] else " · " + ", ".join(_ref(f) for f in broad[r.rid])
        out.append(f"| {lab} | {location(r.line_start, r.line_end, r.page)} | "
                   f"{md_cell(plain_quote(r.quote), 40)}{refs} |")
    return "\n".join(out)


# --- section 4 -------------------------------------------------------------------------------------------


def set_groups(result: Result) -> list[tuple[str, str, str, list[Finding]]]:
    """(number, title, set criterion, findings) of the subsections 4.1–4.5."""
    c = counted(result)
    orphans = [f for f in c if f.scope == "requirement" and not f.req_ids]
    out = []
    for numb, title, crit, fams in SUBSECTIONS:
        items = [f for f in c if f.scope == "set" and f.criterion == crit]
        # folded formal remarks keep their requirement criterion: they go to the subsection of its family
        items += [f for f in c if f.scope == "set" and f.criterion not in SET_CRITERIA and FAMILY.get(f.criterion) in fams]
        items += [f for f in orphans if FAMILY.get(f.criterion) in fams]
        out.append((numb, title, crit, items))
    return out


def _set_subsections(result: Result, dname: str, details: bool) -> list[str]:
    offline = is_offline(result)
    rid_set = {r.rid for r in result.requirements}
    out: list[str] = []
    for numb, title, crit, items in set_groups(result):
        out += ["", f"### {numb} {title}", ""]
        if items:
            out.append("\n\n".join(card(f, show_req_links=True) for f in items) if details
                       else findings_brief(items, dname))
        else:
            out.append("Нарушений не найдено." if not offline else
                       "Правилами нарушений не найдено; без модели этот критерий проверен лишь частично.")
        if crit == "traceability":
            block = _coverage_block(result, rid_set, offline)
            if not details and len(block.split()) > COVERAGE_WORDS:
                block = _coverage_line(result, rid_set, dname)
            out += ["", block]
    return out


def _set_level_details(result: Result) -> str:
    """Details file, part Б: today's 4.1–4.5 with full cards and the coverage block."""
    return "\n".join(['<a id="part-b"></a>', "## Б. Замечания к набору требований"]
                     + _set_subsections(result, "", True))


def _coverage_line(result: Result, rid_set: set[str], dname: str) -> str:
    covered = {rid for row in result.coverage for rid in row.covers}
    acc = {row.acceptance_rid for row in result.coverage}
    need = [r.rid for r in result.requirements
            if r.rid not in covered and r.rid not in acc and r.kind not in ("acceptance", "goal", "other")]
    return (f"Покрытие: {count(len(acc), ('критерий', 'критерия', 'критериев'))} приёмки проверяют "
            f"{count(len(covered & rid_set), W_REQ)}; без критерия приёмки — {len(need)}. Таблица покрытия — в файле "
            f"`{md_code(dname)}`, раздел Б.")


def _gap_line(result: Result, dname: str) -> str:
    t = gapcheck.totals(result.gap_map)
    return "\n".join(['<a id="s4-gaps"></a>', "### Карта полноты: контрольные вопросы к документу", "",
                      f"Вопросов {len(result.gap_map)}: описано {t['covered']} · пробелов {t['gap']} · не относится "
                      f"{t['na']} · не оценено {t['unchecked']}. Ответ на каждый вопрос и на чём он основан — в файле "
                      f"`{md_code(dname)}`, раздел В."])


def _set_level(result: Result, dname: str = "") -> str:
    out = [_h2("s4"), ""]
    offline = is_offline(result)
    out.append("Здесь замечания к документу в целом: противоречия между разделами, пропуски, стандарты, термины, "
               "нумерация. " + ("Без модели — только то, что находят автоматические правила." if offline else ""))
    out += _set_subsections(result, dname, False)
    if result.gap_map:
        out += ["", _gap_line(result, dname)]
    adv = advisory(result)
    out += ["", '<a id="s4-obs"></a>', "### Наблюдения (проверьте сами, на итог не влияют)", ""]
    if adv:
        by_rules = any(not f.advisory_reason for f in adv)
        by_gate = any(f.advisory_reason for f in adv)
        if not by_gate:
            out.append("Эти признаки находятся словарными правилами низкой точности: часто это нормальная формулировка. "
                       "Они не входят в число дефектов, индексы и вердикт.")
        elif not by_rules:
            out.append("Эти мелкие замечания перепроверка отнесла ко второму плану: причина указана под каждым. "
                       "Они не входят в число дефектов, индексы и вердикт.")
        else:
            out.append("Часть признаков находят словарные правила низкой точности: часто это нормальная формулировка. "
                       "Остальное — мелкие замечания, которые перепроверка отнесла ко второму плану: причина указана под каждым. "
                       "Всё это не входит в число дефектов, индексы и вердикт.")
        out.append("")
        for f in sorted(adv, key=_obs_key):
            q = f.quotes[0]
            rid = f" · {link(q.rid)}" if q.rid else ""
            out.append(f'- <a id="{anchor(f.fid)}"></a>**{f.fid}** · {quote_location(q)}{rid} · {md(f.title)}: '
                       f"«{_quote_text(q.text, 120)}»")
            if f.advisory_reason:
                out.append(f"  - Почему не в основном списке: {md(f.advisory_reason)}")
    else:
        out.append("Нет.")
    if result.checklist:
        out += ["", _checklist_block(result)]
    return "\n".join(out)


def _obs_key(f: Finding) -> tuple:
    """Observations by id: rule observations are numbered in document order, the re-check's continue the numbering."""
    tail = f.fid[2:] if f.fid.startswith("Н-") else ""
    return (int(tail), "") if tail.isdigit() else (10**6, f.fid)


GAP_CHECKED_MAX = 140


def gap_answer(cell) -> str:
    st = cell.status
    if st == "covered":
        return f"✅ описано — строка {cell.line}" if cell.line else "✅ описано"
    if st == "gap":
        return "⚠️ пробел — " + (links(cell.finding_ids) or "замечание не сохранилось")
    if st == "na":
        return "— не относится"
    if st == "unverified":
        return "? цитата не подтверждена"
    if st == "rejected":
        return "✅ пробел не подтвердился при перепроверке"
    return "? не оценено"


def _gap_map_block(result: Result, heading: bool = True) -> str:
    cells = result.gap_map
    t = gapcheck.totals(cells)
    out = ['<a id="s4-gaps"></a>', "### Карта полноты: контрольные вопросы к документу", ""] if heading else []
    out += [f"Агент обязан ответить на каждый из {len(cells)} вопросов — так ни один типовой пробел не пропускается молча. "
           "Карта сама не меняет индексы и итог: на них влияют только связанные замечания.", "",
           f"Итого: описано {t['covered']} · пробелов {t['gap']} · не относится {t['na']} · не оценено {t['unchecked']}."]
    rated = [c for c in cells if c.status != "unchecked"]
    unrated = [c for c in cells if c.status == "unchecked"]
    if unrated and len(unrated) * 2 >= len(cells):
        if is_offline(result):  # without the model only the rules answer: naming the rest adds nothing
            out += ["", f"Без модели на остальные {t['unchecked']} вопросов никто не отвечал; их список — в "
                    "JSON-версии отчёта (поле `gap_map`)."]
        else:
            out += ["", "Не оценены: " + ", ".join(f"{c.id} {md(c.title)}" for c in unrated) + "."]
        if rated:
            out += ["", "| Вопрос | Ответ | На чём основан ответ |", "| :--- | :--- | :--- |"]
            for c in rated:
                out.append(f"| {c.id}. {md(c.title)} | {gap_answer(c)} | {md_cell(c.note or ', '.join(c.checked), GAP_CHECKED_MAX)} |")
        return "\n".join(out)
    for group in gapcheck.GROUPS:
        rows = [c for c in cells if c.group == group]
        if not rows:
            continue
        out += ["", f"**{md(group)}**"]
        if group in gapcheck.GROUP_NOTE:
            out += ["", md(gapcheck.GROUP_NOTE[group])]
        out += ["", "| Вопрос | Ответ | Что перебрано / на чём основан ответ |", "| :--- | :--- | :--- |"]
        for c in rows:
            basis = ", ".join(c.checked) if c.checked else c.note
            if c.checked and c.note and c.status in ("na", "unchecked", "unverified", "rejected"):
                basis = c.note + "; " + basis
            out.append(f"| {c.id}. {md(c.title)} | {gap_answer(c)} | {md_cell(basis, GAP_CHECKED_MAX) or '—'} |")
    return "\n".join(out)


def _checklist_block(result: Result) -> str:
    """The document's own unfilled review checklist and how the review answers it (a hint, not a signature)."""
    items = result.checklist
    by_model = any(it.by_model for it in items)
    out = ['<a id="s4-checklist"></a>', "### Чек-лист ревью из документа: как на него отвечает проверка", ""]
    out.append(f"В документе найден чек-лист ревью ({count(len(items), ('пункт', 'пункта', 'пунктов'))}), отметки в "
               "котором не проставлены. "
               + ("Ниже — ответы, которые предлагает проверка по тексту документа и найденным замечаниям. Это "
                  "подсказка для проверяющего, а не его подпись: решение и отметку ставит человек. Ответы не меняют "
                  "замечания, индексы и итог." if by_model else
                  "В этом прогоне модель на пункты не отвечала: они перечислены без оценки, отметку ставит человек."))
    if not by_model:
        lines = [it.line for it in items if it.line]
        where = f" ({lines_text(min(lines), max(lines))})" if lines else ""
        out[-1] = (f"В документе найден чек-лист ревью ({count(len(items), ('пункт', 'пункта', 'пунктов'))}){where}, "
                   "отметки в котором не проставлены. В этом прогоне модель на пункты не отвечала: отметку ставит "
                   "человек.")
        return "\n".join(out)
    out += ["", "| № | Пункт (цитата, строка) | Ответ агента (Да / Нет / Нельзя определить по тексту) | Основание | Замечания |",
            "| ---: | :--- | :--- | :--- | :--- |"]
    for it in items:
        where = lines_text(it.line, it.line) + (f", стр. {it.page}" if it.page else "")
        out.append(f"| {it.number} | «{md_cell(plain_quote(it.text), 200)}» ({where}) | "
                   f"{CHECKLIST_VERDICT_RU.get(it.verdict, it.verdict)} | {md_cell(it.basis, 300)} | "
                   f"{links(it.findings) or '—'} |")
    return "\n".join(out)


def _coverage_block(result: Result, rid_set: set[str], offline: bool) -> str:
    if not result.coverage:
        if offline:
            return "Покрытие требований критериями приёмки строит модель; без неё оно не оценивалось."
        return "Модель не вернула покрытие требований критериями приёмки."
    lines = ["**Покрытие: какой критерий приёмки какие требования проверяет**", "",
             "| Критерий приёмки | Проверяет требования |", "| :--- | :--- |"]
    covered: set[str] = set()
    for row in result.coverage:
        covered.update(row.covers)
        lines.append(f"| {link(row.acceptance_rid)} | {links(row.covers) or '—'} |")
    acc = {row.acceptance_rid for row in result.coverage}
    need = [r.rid for r in result.requirements
            if r.rid not in covered and r.rid not in acc and r.kind not in ("acceptance", "goal", "other")]
    lines.append("")
    if need:
        lines.append(f"Без критерия приёмки: {count(len(need), W_REQ)} — " + links(need) + ".")
    else:
        lines.append("Каждое функциональное требование покрыто хотя бы одним критерием приёмки.")
    return "\n".join(lines)


# --- section 5 ----------------------------------------------------------------------------------------


def _summary(result: Result) -> str:
    r = result.run
    c = counted(result)
    t = tally(result.findings)
    with_f = {rid for f in c for rid in f.req_ids}
    n_req = len(result.requirements)
    n_with = sum(1 for x in result.requirements if x.rid in with_f)
    out = [_h2("s5"), "", "| Показатель | Значение |", "| :--- | ---: |"]
    rows = [
        ("Требований всего", num(n_req)),
        ("— без замечаний", num(n_req - n_with)),
        ("— с замечаниями", num(n_with)),
        ("Замечаний всего", num(t["total"])),
        ("— 🔴 CRITICAL (блокеры)", num(t["CRITICAL"])),
        ("— 🟡 MAJOR (существенные)", num(t["MAJOR"])),
        ("— 🟢 MINOR (мелкие)", num(t["MINOR"])),
        ("Наблюдений (на итог не влияют)", num(len(advisory(result)))),
        ("Вопросов автору", num(len(result.questions))),
    ]
    if result.gap_map:
        g = gapcheck.totals(result.gap_map)
        rows.append(("Контрольных вопросов к документу", f"{num(len(result.gap_map))}, из них пробелов {num(g['gap'])}"))
    if result.tests:
        t_ready, t_rest = test_totals(result)
        rows += [("Тестов можно написать уже сейчас", num(t_ready)),
                 ("Тестов нельзя написать без ответа автора", num(t_rest))]
    out += [f"| {k} | {v} |" for k, v in rows]
    out += ["", "**Замечания по критериям качества**", "", "| Критерий | Замечаний | Основание |", "| :--- | ---: | :--- |"]
    for crit in REQ_CRITERIA + SET_CRITERIA:
        n = sum(1 for f in c if f.criterion == crit)
        if n:
            out.append(f"| {CRITERION_RU[crit]} | {n} | {standard_mod.standard_for(crit) or '—'} |")
    if not c:
        out.append("| — | 0 | — |")

    out += ["", "**Индекс готовности документа**", ""]
    if r.score is not None:
        prelim = " (предварительно: применены только правила)" if is_offline(result) else ""
        out.append(f"**{r.score} из 100 — {score.readiness_band(r.score)}**{prelim}. Шкала: 85 и выше — готов, "
                   "60–84 — готов после правок, 40–59 — нужна существенная доработка, ниже 40 — не готов.")
        out.append("")
        out.append(f"Как посчитан: {md(r.score_formula)}. {CLARITY_LEGEND}")
        clar = {rv.rid: rv.clarity for rv in result.reviews if rv.clarity is not None}
        dist = score.band_distribution(clar)
        out.append("")
        out.append("Требования по ясности: " + " · ".join(f"{label} — {n}" for label, n in dist) + ".")
    else:
        out.append(md(r.score_formula) or "Индекс не считается.")
    dens = len(c) / n_req if n_req else 0.0
    per_k = len(c) * 1000 / r.doc_words if r.doc_words else 0.0
    out += ["", f"Плотность: {dec(dens, 2)} дефекта на одно требование · {dec(per_k)} на 1000 слов документа. "
                "Вердикт определяют блокеры, а не индекс: индекс показывает, сколько работы осталось."]

    blockers = _blockers(result)
    out += ["", "**Критичные дефекты:** " + (links([f.fid for f in blockers]) + " — с основаниями и строками "
                                             "перечислены в начале отчёта." if blockers else "нет.")]

    out += ["", f"**Время и стоимость:** {duration_text(r.duration_s)}, {usd(r.cost_usd)}"
            + (f" ({md(r.cost_note)})" if r.cost_note else "") + "."]
    return "\n".join(out)


# --- section 6 ------------------------------------------------------------------------------------------


def _final(result: Result) -> str:
    out = [_h2("s6"), "", f"**{md(result.verdict_text)}**", ""]
    if result.verdict_after_answers_text:
        out = [_h2("s6"), "", f"По документу как он есть: **{md(result.verdict_text)}**", "",
               f"После внесения ответов автора в документ: **{md(result.verdict_after_answers_text)}**", "",
               "Ответы закрывают замечания только после правки документа: пока текст не изменён, действует первый вердикт.", ""]
    if result.verdict_reason:
        out += [md(result.verdict_reason), ""]
    out.append("**Что исправить в первую очередь**")
    out.append("")
    if result.fix_first:
        by_fid = {f.fid: f for f in result.findings}
        for k, (text, ids) in enumerate(_merged_actions(result.fix_first), 1):
            first = by_fid.get(ids[0]) if ids else None
            title = first.title.strip().rstrip(".") if first is not None else ""
            if title and text.startswith(title):  # «title. recommendation»: the recommendation is on the card
                text = title + "."
            refs = f" ({links(ids)})" if ids else ""
            out.append(f"{k}. {md(text)}{refs}")
    else:
        out.append("Нечего исправлять в первую очередь.")
    out += ["", "**Что в документе уже хорошо**", ""]
    if result.strengths:
        for s in result.strengths:
            refs = f" ({links(s.req_ids)})" if s.req_ids else ""
            out.append(f"- {md(s.text)}{refs}")
    elif is_offline(result):
        out.append("Без модели сильные стороны документа не оцениваются.")
    else:
        out.append("Агент не нашёл мест, которые можно назвать хорошими без оговорок: к каждому замеченному месту "
                   "есть замечание или вопрос.")
    return "\n".join(out)


# --- section 7 ------------------------------------------------------------------------------------------


PRIORITY_ONE = {"blocker": "блокирующий", "important": "важный", "nice": "желательный"}


def _questions(result: Result, name: str = "") -> str:
    """Main report: the intro, one line per answered question, a table of the questions."""
    out = [_h2("s7"), ""]
    if not result.questions:
        out.append("Вопросов нет.")
        return "\n".join(out)
    out += _questions_intro(result)
    out += _dialog_brief(result)
    out += ["", "| № | Вопрос | Кому | Приоритет | К замечаниям | Если автор не ответит, считаем так |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |"]
    for prio in ("blocker", "important", "nice"):
        for q in (x for x in result.questions if x.priority == prio):
            who = ADDRESSEE_RU.get(q.addressee, q.addressee)
            to = links(q.finding_ids) if q.finding_ids else (quote_location(q.quote) if q.quote is not None else "—")
            pr = PRIORITY_ONE.get(q.priority, q.priority) + ("; есть ответ" if q.answer else "")
            ass = md_cell(clarify.assumption_tail(q), Q_ASSUMPTION) if q.assumption else "—"
            out.append(f'| <a id="{anchor(q.qid)}"></a>{q.qid} | {md_cell(q.text, Q_TABLE)} | {md_cell(who)} | {pr} | {to} | {ass} |')
    stem = name[:-3] if name.endswith(".md") else name
    qfile = f"`{md_code(stem)}.questions.md`" if stem else "`*.questions.md`"
    where = f"; вопросы полностью — в файле `{md_code(details_name(name))}`, раздел Г" if name else ""
    out += ["", f"Варианты ответа и что изменит каждый — в файле-анкете {qfile}{where}."]
    return "\n".join(out)


def _questions_details(result: Result) -> str:
    """Details file, part Г: today's section 7 in full."""
    out = ['<a id="part-g"></a>', "## Г. Вопросы автору полностью", ""]
    if not result.questions:
        out.append("Вопросов нет.")
        return "\n".join(out)
    out += _questions_intro(result)
    by_fid = {f.fid: f for f in result.findings}
    out += _dialog(result)
    for prio in ("blocker", "important", "nice"):
        qs = [q for q in result.questions if q.priority == prio]
        if not qs:
            continue
        out += ["", f"### {PRIORITY_RU[prio]} ({len(qs)})"]
        for q in qs:
            out += ["", question_block(q, by_fid, impact_text(q, result))]
    return "\n".join(out)


def _questions_intro(result: Result) -> list[str]:
    out: list[str] = []
    after = verdict_after_blockers(result)
    if after is not None:
        out.append(f"Если автор ответит на блокирующие вопросы и внесёт правки, вердикт станет: **{md(after[1])}**.")
        out.append("")
    need_q, no_q = impact_mod.blocker_questions(result.questions, counted(result))
    if need_q:
        out += ["Чтобы снять все блокеры, нужны ответы на вопросы: " + links(need_q) + ".", ""]
    if no_q:
        by_fid = {f.fid: f for f in result.findings}
        ready = [fid for fid in no_q if by_fid[fid].fixed_wording]
        plain = [fid for fid in no_q if fid not in ready]
        if ready:
            out += ["Блокеры " + links(ready) + " исправляются без вопроса автору: готовая формулировка — в файле "
                    "`*.fixed.md`.", ""]
        if plain:
            out += ["К блокерам " + links(plain) + " вопроса автору нет: что исправить, сказано в замечании.", ""]
    src = "по шаблонам автоматических правил" if is_offline(result) else "моделью по подтверждённым замечаниям"
    out.append(f"{count(len(result.questions), W_QUESTION).capitalize()} составлено {src}. "
               "Ответы удобно вписать в файл-анкету рядом с отчётом (`*.questions.md`): там у каждого вопроса есть "
               "варианты ответа.")
    return out


def _closed_by(result: Result) -> dict[str, list[str]]:
    """question id → the counted findings its answer closed (``answers_summary.closes`` of the audit)."""
    sm = ans_mod.summary(result.audit) or {}
    live = {f.fid for f in counted(result) if f.closed_by_answer}
    out: dict[str, list[str]] = {}
    closes = sm.get("closes")
    if isinstance(closes, dict):
        for fid, qids in closes.items():
            if fid in live:
                for qid in qids or []:
                    out.setdefault(qid, []).append(fid)
    else:  # an older result: the findings of the question that are closed
        for q in result.questions:
            out[q.qid] = [i for i in q.finding_ids if i in live]
    return out


def assumptions_in_force(result: Result) -> list[str]:
    """«Принято без ответа автора»: in a repeat check, the assumption of every question the author left unanswered.
    Empty when the run had no answers or every question has one (a question without a safe assumption is not listed:
    nothing is assumed for it)."""
    sm = ans_mod.summary(result.audit)
    if not sm or not sm.get("total"):
        return []
    rows = [q for q in result.questions if not q.answer and clarify.has_assumption(q)]
    blocked = [q for q in result.questions if not q.answer and q.assumption == clarify.NO_ASSUMPTION]
    if not rows and not blocked:
        return []
    out = ["", "### Принято без ответа автора", "",
           "На эти вопросы автор не ответил. Пока ответа нет, команда считает так:", ""]
    for q in rows:
        out.append(f"- {link(q.qid)}: {md(shorten(q.text, DIALOG_CUT))} → {md(clarify.assumption_tail(q))}.")
    if blocked:
        out += ["", "Без ответа автора работу по этим пунктам начинать нельзя: " + ", ".join(link(q.qid) for q in blocked) + "."]
    return out


def _dialog_brief(result: Result) -> list[str]:
    """«Диалог с автором» in the main report: a line per answered question."""
    sm = ans_mod.summary(result.audit)
    if not sm or not sm.get("total"):
        return []
    closed = _closed_by(result)
    out = ["", "### Диалог с автором", "",
           f"Ответы автора учтены: {sm.get('answered', 0)} из {sm['total']}. Закрыто замечаний: {len(sm.get('closed') or [])}"
           + (f"; новых вопросов из ответов: {sm['follow_ups']}" if sm.get("follow_ups") else "") + ".", ""]
    for q in (x for x in result.questions if x.answer):
        fids = closed.get(q.qid) or []
        eff = ("закрывает " + links(fids)) if fids else "замечания остаются"
        out.append(f"- {link(q.qid)}: {md(shorten(q.text, DIALOG_CUT))} → «{md(shorten(q.answer, DIALOG_CUT))}» → {eff}")
    left_q = [q.qid for q in result.questions if not q.answer]
    open_n = sum(1 for f in counted(result) if not f.closed_by_answer)
    out += ["", f"Что остаётся открытым: замечаний {open_n} из {len(counted(result))}"
            + (f"; вопросов без ответа: {len(left_q)}" if left_q else "") + "."]
    return out + assumptions_in_force(result)


def _dialog(result: Result) -> list[str]:
    sm = ans_mod.summary(result.audit)
    if not sm or not sm.get("total"):
        return []
    answered = [q for q in result.questions if q.answer]
    out = ["", "### Диалог с автором", "",
           f"Ответы автора учтены: {sm.get('answered', 0)} из {sm['total']}. Закрыто замечаний: {len(sm.get('closed') or [])}"
           + (f"; новых вопросов из ответов: {sm['follow_ups']}" if sm.get("follow_ups") else "") + "."]
    if not sm.get("by_model"):
        out.append("Ответы записаны; без модели агент не проверяет, закрывает ли ответ замечание, кроме случая, когда "
                   "выбран вариант из вопроса, составленного по шаблону этого замечания.")
    out.append("")
    for q in answered:
        out.append(f"- {link(q.qid)}: {md(shorten(q.text, 160))} → ответ: «{md(shorten(q.answer, 240))}» → "
                   f"{md(q.answer_effect or 'учтён')}")
    left_q = [q.qid for q in result.questions if not q.answer]
    open_n = sum(1 for f in counted(result) if not f.closed_by_answer)
    out += ["", f"Что остаётся открытым: замечаний {open_n} из {len(counted(result))}"
            + (f"; вопросы без ответа: {', '.join(link(i) for i in left_q)}" if left_q else "") + "."]
    return out + assumptions_in_force(result)


def impact_text(q: Question, result: Result) -> str:
    """«Что изменит ответ в итоге: …» — computed by code (impact.py); "" when there is nothing to say."""
    im = q.impact or {}
    if not im:
        return ""
    total = sum(1 for f in counted(result) if f.severity == "CRITICAL")
    k = int(im.get("blockers_closed") or 0)
    after = im.get("verdict_after") or ""
    if k:
        head = f"закроет {k} из {total} " + plural(total, ("блокера", "блокеров", "блокеров"))
        if after and after != result.verdict:
            head += f", вердикт станет: «{verdict.short_text(after)}»"
    elif after and after != result.verdict and result.verdict in verdict.TEXT:
        head = f"вердикт станет: «{verdict.short_text(after)}»"
    else:
        head = "на вердикт не влияет"
        if not int(im.get("index_delta") or 0):
            return ""
    idx = im.get("index_after")
    delta = int(im.get("index_delta") or 0)
    if idx is not None and delta:
        head += f" · индекс готовности {idx - delta} → {idx}"
    return "Что изменит ответ в итоге: " + head


def _why_repeats(q: Question, by_fid: dict[str, Finding]) -> bool:
    """The «why» of a template question is the first sentence of its finding's problem: printing it again is noise."""
    w = normalize(q.why or "")
    return bool(w) and any(w in normalize(by_fid[i].problem) for i in q.finding_ids if i in by_fid)


def question_block(q: Question, by_fid: dict[str, Finding], impact_line: str = "") -> str:
    """A question in two or three lines; the questionnaire (``*.questions.md``) has the full form."""
    who = ADDRESSEE_RU.get(q.addressee, q.addressee)
    meta = [f"кому: {md(who)}"]
    if q.finding_ids:  # the place and the kind of gap are on the finding's card and in the questionnaire
        meta.append("к " + links(q.finding_ids))
    elif q.quote is not None:
        meta.append(quote_location(q.quote))
    lines = [f'<a id="{anchor(q.qid)}"></a>', f"**{q.qid}.** {md(q.text)} ({'; '.join(meta)})"]
    if q.why and not _why_repeats(q, by_fid):
        lines.append(f"- Почему важно: {md(q.why)}")
    if q.options:
        if q.priority == "blocker" and any(k < len(q.option_effects) and q.option_effects[k]
                                           for k in range(len(q.options))):
            lines.append("- Варианты ответа:")
            for k, o in enumerate(q.options):
                eff = q.option_effects[k] if k < len(q.option_effects) and q.option_effects[k] else ""
                lines.append(f"  - {LETTERS[k]}) {md(o)}" + (f" — что изменится: {md(eff)}" if eff else ""))
        elif q.priority == "blocker" or q.answer:
            lines.append("- Варианты: " + "; ".join(f"{LETTERS[k]}) {md(o)}" for k, o in enumerate(q.options)))
        # other questions: the options are in the questionnaire (``*.questions.md``), where the author answers
    if q.assumption:
        lines.append(f"- {md(clarify.assumption_line(q))}")
    if impact_line:
        lines.append(f"- {impact_line}")
    if q.answer:
        lines.append(f"- Ответ автора: {md(q.answer)}")
        if q.answer_effect:
            lines.append(f"- Что сделал агент: {md(q.answer_effect)}")
    return "\n".join(lines)


# --- section 8 (filled in by the test-draft stage) ------------------------------------------------------


def test_totals(result: Result) -> tuple[int, int]:
    ready = sum(1 for t in result.tests if t.then and not t.blocked_by)
    return ready, len(result.tests) - ready


def _tests(result: Result) -> str:
    """Counts and which answer each blocked draft waits for; the drafts themselves are in ``*.tests.md``."""
    out = [_h2("s8"), ""]
    ready, rest = test_totals(result)
    out.append(f"Можно написать уже сейчас: {ready} · Нельзя написать без ответа автора: {rest}. "
               "Полные тексты (Дано / Когда / Тогда) — в файле `*.tests.md` рядом с отчётом.")
    out.append("")
    ok = [t for t in result.tests if t.then and not t.blocked_by]
    if ok:
        out.append("- Готовы: " + ", ".join(f'<a id="{anchor(t.tid)}"></a>{t.tid} ({links(t.req_ids[:2])})' for t in ok) + ".")
    waits: "OrderedDict[str, list]" = OrderedDict()
    for t in result.tests:
        if t.then and not t.blocked_by:
            continue
        key = ", ".join(t.blocked_by) if t.blocked_by else ""
        waits.setdefault(key, []).append(t)
    for key, ts in waits.items():
        what = f"ждут ответа {links(key.split(', '))}" if key else "ожидаемый результат в документе не описан"
        out.append(f"- {_cap(what)}: " + ", ".join(f'<a id="{anchor(t.tid)}"></a>{t.tid}' for t in ts) + ".")
    return "\n".join(out)


# --- appendix ----------------------------------------------------------------------------------------------


def _appendix(result: Result) -> str:
    r = result.run
    out = [_h2("app"), ""]
    out.append("**Метод.**")
    out.append("")
    out.append("1. Документ читается и делится на блоки (абзацы, пункты, строки таблиц); номера строк берутся из "
               "исходного текста. Значения, похожие на номера карт и ИИН, маскируются до любой обработки.")
    out.append("2. Блоки делятся на требования и служебные части " + (
        "моделью; программа проверяет, что каждый блок попал ровно в одно место, и сама присваивает номера REQ-…."
        if not is_offline(result) else "эвристикой; номера REQ-… присваивает программа."))
    n_main, n_adv = rules_mod.rule_counts()
    out.append(f"3. Автоматические правила (набор {md(r.rules_version)}: {n_main} основных и {n_adv} слабых «наблюдений») "
               "ищут формальные дефекты: размытые слова, заглушки, единицы измерения, коды и методы HTTP, пустые разделы, "
               "схемы без веток ошибок, подсадной текст для ИИ.")
    if not is_offline(result):
        out.append("4. Модель проверяет каждое требование по семи критериям ISO/IEC/IEEE 29148:2018 п. 5.2.5 "
                   + ("тремя отдельными проходами (ясность; полнота и проверяемость; корректность)" if r.mode == "full"
                      else "одним проходом")
                   + " и весь набор по пяти критериям п. 5.2.6"
                   + (f", а проход-критик отвечает на каждый из {len(result.gap_map)} контрольных вопросов к документу"
                      if result.gap_map else "")
                   + ("; затем второй, независимый проход перепроверяет каждое замечание." if r.mode == "full" else "."))
    else:
        out.append("4. Модель не использовалась: проверки по смыслу (противоречия, пропущенные сценарии, "
                   "пограничные случаи) не выполнялись.")
    out.append("5. Каждую цитату программа ищет в тексте документа; замечание без найденной цитаты выбрасывается, "
               "в отчёт попадает исходный текст, а не пересказ. Номера, индексы, вердикт и отметки в матрице считает "
               "программа; перед записью отчёта она проверяет, что его части не противоречат друг другу.")
    out += ["", "**Честно о границах.** Это первый проход, который экономит время проверяющего; решение остаётся за "
            "человеком. Опубликованные замеры ИИ-проверки требований показывают, что лучшие модели находят примерно "
            "половину настоящих проблем и ошибочно помечают около одного требования из десяти. Поэтому цитаты "
            "сверяет программа"
            + (", замечания модели перепроверяются вторым проходом" if not is_offline(result) and r.mode == "full" else "")
            + ", а полнота агента измеряется на контрольных документах, которых он раньше не видел."
            + (" В этом прогоне модель не работала, поэтому найдено заведомо меньше." if is_offline(result) else "")]
    out += ["", "**Ограничения этого прогона**", ""]
    lim = []
    if is_offline(result):
        lim.append("Модель не использовалась: найдено только то, что выявляют правила по формальным признакам.")
    if r.doc_format == "pdf":
        lim.append("Документ из PDF: таблицы теряют разметку, поэтому правила для таблиц могли не сработать.")
    lim.append("Рисунки и схемы-картинки не проверяются: учитывается только текст.")
    lim.append("Агент не знает внутренних стандартов банка и не видит связанных документов, если их не передали "
               "через --context.")
    out += [f"- {x}" for x in lim]
    rejected = sum(1 for f in result.rejected if f.status == "rejected")
    merged = sum(1 for f in result.rejected if f.status == "merged")
    calls = sum(1 for a in result.audit if a.get("type") == "model_call")
    decisions = sum(1 for a in result.audit if a.get("type") == "decision")
    out += ["", f"**Подробности прогона** — в файле `*.diagnostic.md` рядом с отчётом: расход по этапам, отклонённые "
                f"перепроверкой ({rejected}), объединённые повторы ({merged}), выброшенные без подтверждённой цитаты "
                f"({len(result.dropped)}). Журнал решений — в JSON-версии (поле `audit`, только хэши текстов): "
                f"{count(calls, W_CALL)} к модели, {count(decisions, ('решение', 'решения', 'решений'))} программы."]
    return "\n".join(out)


# --- details file (``*.details.md``) -------------------------------------------------------------------------

_A_ID = re.compile(r'<a id="([^"]+)"></a>')
_A_LINK = re.compile(r"\[([^\]\n]+)\]\(#([^)\s]+)\)")


def _has_withdrawn(result: Result) -> bool:
    return any(f.status == "rejected" for f in result.rejected) or bool(result.dropped)


def _withdrawn_block(result: Result) -> str:
    """Part Д of the details file: what the program or the re-check took out of the list and nowhere else shows in full.
    A finding is open only with a quote the program found in the document, so a remark without one is listed here, never
    counted. Findings merged into another card are on that card («Сюда же»), observations are in section 4."""
    rejected = [f for f in result.rejected if f.status == "rejected"]
    if not rejected and not result.dropped:
        return ""
    out = ['<a id="part-d"></a>', "## Д. Что было снято и почему", "",
           "Здесь — замечания, которые не вошли в список: перепроверка их не подтвердила или программа не нашла их "
           "цитату в документе. Они не влияют на итог; их видно здесь, чтобы ничего не пропало молча."]
    if rejected:
        out += ["", "**Не подтверждено перепроверкой**", ""]
        for f in rejected:
            line = f"- {f.fid} · {_sev(f)} · **{md(shorten(f.title, 140))}**."
            if f.problem:
                line += " " + md(_trim_words(f.problem))
            if f.quotes:
                q = f.quotes[0]
                line += f" {lines_text(q.line_start, q.line_end)}: «{md(shorten(' '.join(plain_quote(q.text).split()), 160))}»."
            if f.status_reason:
                line += " Почему снято: " + md(_trim_words(f.status_reason, 300))
            out.append(line)
    if result.dropped:
        out += ["", "**Цитата не найдена в документе**", ""]
        for d in result.dropped:
            line = f"- {md(STAGE_RU.get(d.stage, d.stage))} · **{md(shorten(d.title, 140))}**."
            if d.problem:
                line += " " + md(_trim_words(d.problem))
            if d.quote:
                line += f" Цитата модели: «{md(shorten(' '.join(d.quote.split()), 160))}»."
            line += " Почему снято: " + md(d.reason or "цитата не найдена в документе")
            out.append(line + ".")
    return "\n".join(out)


def render_details(result: Result, name: str = DEFAULT_NAME, main_text: str | None = None) -> str | None:
    """``<stem>.details.md``: what the main report prints in short — the requirement blocks with full cards (А), the
    set findings with full cards (Б), the completeness map (В) and the questions in full (Г). Every anchor here is
    prefixed ``x-``; a link to something that only the main report prints leads into ``name``. None for an
    unreadable document."""
    if result.diagnostic is not None:
        return None
    name = name or DEFAULT_NAME
    if main_text is None:
        main_text = render(result, name)
    main_ids = set(_A_ID.findall(main_text))
    token = _SAID.set({})
    try:
        r = result.run
        parts = [f"# Подробности проверки: «{md(r.doc_title or r.doc_name)}»",
                 f"Документ `{md_code(r.doc_name)}`. Этот файл дополняет отчёт `{md_code(name)}`: там — итог, матрица и "
                 "короткие формы замечаний.\nЗдесь — полные карточки с цитатами и ходом перепроверки, разбор по каждому "
                 "требованию, карта полноты и вопросы автору с вариантами ответов.",
                 "**Части:** [А. Разбор по каждому требованию](#part-a) · [Б. Замечания к набору требований](#part-b)"
                 + (" · [В. Карта полноты](#part-v)" if result.gap_map else "") + " · [Г. Вопросы автору полностью](#part-g)"
                 + (" · [Д. Что было снято и почему](#part-d)" if _has_withdrawn(result) else ""),
                 _per_requirement_details(result),
                 _set_level_details(result)]
        if result.gap_map:
            parts.append("\n".join(['<a id="part-v"></a>', "## В. Карта полноты", "", _gap_map_block(result, False)]))
        parts.append(_questions_details(result))
        withdrawn = _withdrawn_block(result)
        if withdrawn:
            parts.append(withdrawn)
        text = "\n\n".join(p.strip("\n") for p in parts if p) + "\n"
    finally:
        _SAID.reset(token)
    text = _A_ID.sub(lambda m: f'<a id="x-{m.group(1)}"></a>', text)
    own = set(_A_ID.findall(text))

    def sub(m: re.Match) -> str:
        target = m.group(2)
        if "x-" + target in own:
            return f"[{m.group(1)}](#x-{target})"
        if target in main_ids:
            return f"[{m.group(1)}]({name}#{target})"
        return m.group(1)

    return _A_LINK.sub(sub, text)


# --- run details: the diagnostic side file ------------------------------------------------------------------


def render_run_details(result: Result) -> str:
    """``*.diagnostic.md``: everything about HOW the run went that a reader of the report does not need first —
    model usage per stage, all warnings, the findings the re-check rejected or merged, the ones thrown away for an
    unconfirmed quote, and the program's decisions by kind."""
    r = result.run
    out = [f"# Подробности прогона: «{md(r.doc_name)}»", "",
           "Этот файл дополняет отчёт: здесь всё, что объясняет, как получен результат.", ""]
    out += ["| Поле | Значение |", "| :--- | :--- |",
            f"| Дата и время | {_when(r.started_at)} |",
            f"| Модель | {md(r.provider)} / {md(r.model) or '—'}" + (f", effort {md(r.effort)}" if r.effort else "") + " |",
            f"| Время | {duration_text(r.duration_s)} |",
            f"| Стоимость | {usd(r.cost_usd)}" + (f" ({md(r.cost_note)})" if r.cost_note else "") + " |",
            f"| Обращения к модели | {num(r.llm_calls)} (из кэша ещё {num(r.llm_cache_hits)}); вход {num(r.input_tokens)}, "
            f"выход {num(r.output_tokens)}, чтение кэша {num(r.cache_read_tokens)}, запись кэша {num(r.cache_write_tokens)} |",
            f"| Проверка цитат | подтверждено {num(r.quotes_confirmed)}, восстановлено по сходству {num(r.quotes_restored)}, "
            f"отброшено {num(r.quotes_dropped)} |",
            f"| Перепроверка | {recheck_line(result)} |",
            f"| Повторы, объединённые программой | {num(r.dup_merged)} |",
            f"| Блокеры без основания, пониженные до MAJOR | {num(r.critical_demoted)} |"]
    out += _stage_table(stage_rows(result))
    if r.warnings:
        out += ["", "**Предупреждения прогона:**", ""] + [f"- {md(w)}" for w in r.warnings]
    rejected = [f for f in result.rejected if f.status == "rejected"]
    merged = [f for f in result.rejected if f.status == "merged"]
    out += ["", "## Отклонено при перепроверке", ""]
    out += [f"- {f.fid} · {_sev(f)} · {md(f.title)} — {md(f.status_reason)}" for f in rejected] or ["Ничего."]
    out += ["", "## Объединено с другими замечаниями", ""]
    out += [f"- {f.fid} · {md(f.title)} → {f.merged_into}: {md(f.status_reason)}" for f in merged] or ["Ничего."]
    out += ["", "## Выброшено: цитата не найдена в документе", ""]
    out += [f"- {md(d.stage)} · {md(d.title)} — {md(d.reason)}" + (f": «{md(shorten(d.quote, 160))}»" if d.quote else "")
            for d in result.dropped] or ["Ничего."]
    kinds: "OrderedDict[str, int]" = OrderedDict()
    for a in result.audit:
        if a.get("type") == "decision":
            kinds[a.get("what", "")] = kinds.get(a.get("what", ""), 0) + 1
    if kinds:
        out += ["", "## Решения программы по видам", "", "| Решение | Сколько |", "| :--- | ---: |"]
        out += [f"| `{md_code(k)}` | {v} |" for k, v in kinds.items()]
    return "\n".join(out) + "\n"


# --- diagnostic report ----------------------------------------------------------------------------------


def render_diagnostic(result: Result) -> str:
    d = result.diagnostic
    r = result.run
    out = ["# Отчёт о статической проверке требований", "",
           f"> **Итог:** ❔ Документ не проверен: {md(d.title)}", ""]
    out += [_h2("s1"), "", "| Поле | Значение |", "| :--- | :--- |"]
    out.append(f"| Документ | `{md_code(d.name or d.path)}` |")
    out.append(f"| Размер | {size_text(d.size_bytes)} |")
    if d.detected_type:
        out.append(f"| Что это за файл на самом деле | {md(d.detected_type)} |")
    out.append(f"| Код проблемы | `{d.code}` |")
    out.append(f"| Дата и время прогона | {_when(r.started_at)} |")
    out.append(f"| Версия агента | {version_label(md(r.agent_version), md_code(r.agent_commit), tag=md_code(r.agent_tag), dirty=r.agent_dirty)} |")
    out.append("| Обращения к модели | 0 (документ не прочитан) |")
    out += ["", "## Документ не проверен", "", f"**Что случилось.** {md(d.what_happened)}", "",
            "**Что это значит.** Агент не смог получить из файла текст требований, поэтому ни правила, ни модель "
            "его не проверяли. Вердикт не выносится.", "", "**Что сделать:**", ""]
    for k, step in enumerate(d.what_to_do or ["Проверьте файл и запустите проверку ещё раз."], 1):
        out.append(f"{k}. {md(step)}")
    if d.details:
        out += ["", f"**Технические подробности:** {md(d.details)}"]
    for key, title in SECTIONS.items():
        if key in ("s1", "s8"):
            continue
        out += ["", f'<a id="{key}"></a>', f"## {title}", "", "Не выполнялось: документ не прочитан."]
    return "\n".join(out) + "\n"
