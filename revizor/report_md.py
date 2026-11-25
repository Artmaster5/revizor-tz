"""Markdown report (GitHub-flavoured). Everything from the document or the model is escaped."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime

from . import answers as ans_mod, baseline as baseline_mod, score, verdict
from .config import version_label
from .merge import FAMILY, STANDARDS
from .model import (ADDRESSEE_RU, BLOCKER_RU, CHECKLIST_VERDICT_RU, CRITERION_RU, GAP_RU, KIND_RU, PRIORITY_RU, REQ_CRITERIA, SET_CRITERIA,
                    SEVERITY_ICON, SKIP_CATEGORY_RU, TEST_KIND_RU, Finding, Question, Requirement, Result)
from .prompts import FORMAT_RU, LENS_BY_STAGE, SET_NAME
from .textfmt import (plain_quote, W_BLOCK, W_CALL, W_DEFECT, W_OBSERVATION, W_QUESTION, W_REQ, anchor, blockquote, count, dec,
                      duration_text, last_section, lines_text, location, md, md_cell, minutes_text, num, plural,
                      quote_location, shorten, size_text, usd)

TABLE_QUOTE = 160
CARD_QUOTE = 500

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


# --- small helpers ------------------------------------------------------------------------------


def counted(result: Result) -> list[Finding]:
    return [f for f in result.findings if f.status == "open" and not f.advisory]


def advisory(result: Result) -> list[Finding]:
    return [f for f in result.findings if f.status == "open" and f.advisory]


def sev_counts(findings: list[Finding]) -> dict[str, int]:
    return {s: sum(1 for f in findings if f.severity == s) for s in ("CRITICAL", "MAJOR", "MINOR")}


def link(item_id: str) -> str:
    return f"[{item_id}](#{anchor(item_id)})" if item_id else ""


def links(ids: list[str]) -> str:
    return ", ".join(link(i) for i in ids)


def is_offline(result: Result) -> bool:
    return result.run.mode == "offline"


def _sev(f: Finding) -> str:
    return f"{SEVERITY_ICON.get(f.severity, '')} {f.severity}"


def _source(f: Finding) -> str:
    if f.source == "both":
        return "модель и автоматическое правило " + ", ".join(f"`{r}`" for r in f.rule_ids)
    if f.source.startswith("rule:"):
        return "автоматическое правило " + ", ".join(f"`{r}`" for r in f.rule_ids)
    lens = LENS_BY_STAGE.get(f.stage)
    if lens:
        return f"модель (линза «{lens.name}»)"
    if f.stage in SET_NAME:
        return f"модель (проверка набора: {SET_NAME[f.stage]})"
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
    return [f for f in counted(result) if f.severity == "CRITICAL"]


def _where(f: Finding) -> str:
    if not f.quotes:
        return ""
    if f.quotes[0].page:
        pages = sorted({q.page for q in f.quotes if q.page})
        return ("стр. " if len(pages) == 1 else "стр. ") + ", ".join(str(p) for p in pages)
    ls = sorted({q.line_start for q in f.quotes})
    if len(ls) == 1:
        return lines_text(f.quotes[0].line_start, f.quotes[0].line_end)
    return "строки " + ", ".join(str(x) for x in ls[:5]) + ("…" if len(ls) > 5 else "")


def verdict_after_blockers(result: Result) -> tuple[str, str] | None:
    blocker_q = {q.qid for q in result.questions if q.priority == "blocker"}
    if not blocker_q:
        return None
    rest = [f for f in counted(result) if f.question_id not in blocker_q]
    return verdict.decide(rest, offline=is_offline(result))


# --- render ------------------------------------------------------------------------------------


def render(result: Result) -> str:
    if result.diagnostic is not None:
        return render_diagnostic(result)
    parts = [
        _head(result),
        _passport(result),
        _slicing(result),
        _per_requirement(result),
        _set_level(result),
        _summary(result),
        _final(result),
        _questions(result),
    ]
    if result.tests:
        parts.append(_tests(result))
    parts.append(_appendix(result))
    return "\n\n".join(p.strip("\n") for p in parts if p) + "\n"


def _h2(key: str) -> str:
    return f'<a id="{key}"></a>\n## {SECTIONS[key]}'


def _head(result: Result) -> str:
    r = result.run
    c = counted(result)
    sc = sev_counts(c)
    adv = advisory(result)
    lines = ["# Отчёт о статической проверке требований", ""]
    when = _when(r.started_at)
    model = "без модели — только автоматические правила" if is_offline(result) else f"модель {md(r.model)}"
    lines.append(f"**Документ:** `{md_code(r.doc_name)}` · проверен {when} · {model}")
    lines.append("")
    lines.append(f"> **Итог:** {md_plain(result.verdict_text)}")
    lines.append(f"> Требований: {num(len(result.requirements))} · Дефектов: {num(len(c))} "
                 f"(🔴 CRITICAL {sc['CRITICAL']} · 🟡 MAJOR {sc['MAJOR']} · 🟢 MINOR {sc['MINOR']}) · "
                 f"Вопросов автору: {num(len(result.questions))}")
    if r.score is not None:
        prelim = ", предварительно" if is_offline(result) else ""
        lines.append(f"> Индекс готовности документа: **{r.score} из 100** — {score.readiness_band(r.score)}{prelim}. "
                     "Вердикт определяют блокеры, а не индекс.")
    sm = ans_mod.summary(result.audit)
    if sm and sm.get("total"):
        line = (f"> 💬 Ответы автора учтены: {sm.get('answered', 0)} из {sm['total']}. "
                f"Закрыто замечаний: {len(sm.get('closed') or [])}.")
        if result.verdict_after_answers_text:
            line += f" После внесения ответов в документ итог: {md_plain(result.verdict_after_answers_text)}."
        lines.append(line)
    if is_offline(result):
        lines.append("> ⚠️ Смысловые противоречия, пропущенные сценарии и пограничные случаи без модели не искались: "
                     "чистый результат здесь не означает, что документ готов.")
    if r.injection_suspected:
        inj = [f for f in c if "prompt-injection" in f.rule_ids or ("hidden-text" in f.rule_ids and f.severity == "CRITICAL")]
        lines.append("> 🛡️ В документе есть текст, обращённый к проверяющему ИИ"
                     + (f" ({links([f.fid for f in inj])})" if inj else "") + ". Агент его не выполнял.")
    lines.append("")
    lines += _baseline_block(result)
    blockers = _blockers(result)
    if blockers:
        lines.append(f"**Блокеры — исправить до начала работы ({len(blockers)}):**")
        lines.append("")
        for k, f in enumerate(blockers, 1):
            lines.append(f"{k}. {link(f.fid)} {md(f.title)} — {_where(f)}")
    else:
        majors = [f for f in c if f.severity == "MAJOR"]
        if majors:
            lines.append(f"**Блокеров нет.** Существенных замечаний: {len(majors)} — "
                         + links([f.fid for f in majors[:8]]) + ("…" if len(majors) > 8 else "") + ".")
        else:
            lines.append("**Блокеров и существенных замечаний нет.**")
    if adv:
        lines.append("")
        verb = plural(len(adv), ("вынесено отдельно и на итог не влияет", "вынесены отдельно и на итог не влияют",
                                 "вынесено отдельно и на итог не влияет"))
        lines.append(f"Ещё {count(len(adv), W_OBSERVATION)} низкой точности {verb} ([раздел 4](#s4-obs)).")
    lines.append("")
    lines.append("**Разделы:** " + " · ".join(
        f"[{t}](#{k})" for k, t in SECTIONS.items() if k != "s8" or result.tests))
    return "\n".join(lines)


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
            "skeptic": "перепроверка", "questions": "вопросы автору", "answers": "ответы автора",
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


def _passport(result: Result) -> str:
    r = result.run
    rows: list[tuple[str, str]] = []
    doc = f"`{md_code(r.doc_name)}` · {FORMAT_RU.get(r.doc_format, r.doc_format)} · {size_text(r.doc_size_bytes)} · "
    doc += f"{num(r.doc_lines)} {plural(r.doc_lines, ('строка', 'строки', 'строк'))}"
    if r.doc_pages:
        doc += f" · {r.doc_pages} {plural(r.doc_pages, ('страница', 'страницы', 'страниц'))}"
    doc += f" · {num(r.doc_words)} {plural(r.doc_words, ('слово', 'слова', 'слов'))} · SHA-256 `{r.doc_sha256[:12]}`"
    rows.append(("Документ", doc))
    rows.append(("Название документа", md(r.doc_title) if r.doc_title else "не найдено"))
    if r.context_files:
        rows.append(("Контекст (--context)", ", ".join(f"`{md_code(n)}`" for n in r.context_files)
                     + " — только для понимания терминов; замечания и цитаты — по основному документу"))
    rows.append(("Дата и время прогона", _when(r.started_at)))
    if is_offline(result):
        model = "без модели"
        if r.provider not in ("offline", "", "fake") and r.model:
            model += f" (провайдер {md(r.provider)} / {md(r.model)} недоступен — см. предупреждения)"
    else:
        model = f"{md(r.provider)} / {md(r.model)}" + (f", effort {md(r.effort)}" if r.effort else "")
    rows.append(("Модель", model))
    rows.append(("Версия агента", version_label(md(r.agent_version), md_code(r.agent_commit), md(r.rules_version),
                                                tag=md_code(r.agent_tag), dirty=r.agent_dirty)))
    rows.append(("Воспроизводимость", "правила, номера, проверка цитат, индексы и вердикт детерминированы; "
                 "замечания модели могут отличаться от прогона к прогону"))
    rows.append(("Режим", MODE_RU.get(r.mode, r.mode)))
    rows.append(("Время анализа", duration_text(r.duration_s)))
    if r.llm_calls or not is_offline(result):
        rows.append(("Обращения к модели",
                     f"{num(r.llm_calls)} (из кэша ещё {num(r.llm_cache_hits)}); токены: вход {num(r.input_tokens)}, "
                     f"выход {num(r.output_tokens)}, чтение кэша {num(r.cache_read_tokens)}, "
                     f"запись кэша {num(r.cache_write_tokens)}"))
    else:
        rows.append(("Обращения к модели", "0"))
    rows.append(("Стоимость прогона", f"{usd(r.cost_usd)} ({md(r.cost_note)})" if r.cost_note else usd(r.cost_usd)))
    qline = f"подтверждено {num(r.quotes_confirmed)}"
    if r.quotes_restored:
        qline += f" (из них восстановлено по сходству {num(r.quotes_restored)})"
    qline += f", отброшено {num(r.quotes_dropped)}"
    if result.dropped:
        qline += f"; выброшено замечаний модели без подтверждённой цитаты: {len(result.dropped)}"
    rows.append(("Проверка цитат", qline))
    if r.mode == "full":
        rows.append(("Перепроверка", f"оставлено {r.skeptic_kept}, отклонено {r.skeptic_rejected}, "
                                     f"объединено {r.skeptic_merged}"))
    else:
        rows.append(("Перепроверка", "не выполнялась (" + ("быстрый режим" if r.mode == "fast" else "без модели") + ")"))
    if r.critical_demoted:
        rows.append(("Уровни критичности", f"понижено блокеров без основания: {r.critical_demoted} "
                                           "(CRITICAL → MAJOR: не названо ни одно из пяти оснований блокера)"))
    if r.dup_merged:
        rows.append(("Повторы замечаний", f"объединено кодом {r.dup_merged} (то же место, тот же дефект, один вопрос); "
                                          "список — в конце отчёта среди объединённых"))
    rows.append(("Защита данных", f"замаскировано {num(r.masked_values)} "
                 f"{plural(r.masked_values, ('значение', 'значения', 'значений'))}, похожих на номера карт или ИИН"
                 if r.masked_values else "значений, похожих на номера карт или ИИН, не найдено"))
    if r.injection_suspected:
        rows.append(("Подсадной текст", "найден текст, обращённый к проверяющему ИИ; агент его не выполнял, "
                                        "замечания правил защищены от отклонения моделью"))
    out = [_h2("s1"), "", "| Поле | Значение |", "| :--- | :--- |"]
    out += [f"| {k} | {v} |" for k, v in rows]
    out += _stage_table(stage_rows(result))
    if r.warnings:
        out += ["", "**Предупреждения:**", ""]
        out += [f"- {md(w)}" for w in r.warnings]
    return "\n".join(out)


# --- section 2 ------------------------------------------------------------------------------------


def _clarity_by_rid(result: Result) -> dict[str, float | None]:
    return {rv.rid: rv.clarity for rv in result.reviews}


def _slicing(result: Result) -> str:
    clar = _clarity_by_rid(result)
    out = [_h2("s2"), ""]
    how = "моделью" if any(r.origin == "model" for r in result.requirements) else "эвристикой (без модели)"
    out.append(f"Требования выделены {how}; номера REQ-… присвоены программой по порядку документа. "
               f"Найдено {count(len(result.requirements), W_REQ)}. Столбец «Ясность» — индекс от 1 до 10: "
               "10 — замечаний нет, ниже 4 — в таком виде использовать нельзя (такую оценку получает только "
               "требование, у которого есть блокер).")
    out.append("")
    if result.requirements:
        out.append("| ID в отчёте | ID в документе | Раздел | Тип | Формулировка (цитата) | Где | Ясность |")
        out.append("| :--- | :--- | :--- | :--- | :--- | :--- | ---: |")
        for r in result.requirements:
            c = clar.get(r.rid)
            out.append(f"| {link(r.rid)} | {md_cell(r.doc_id)} | {md_cell(last_section(r.section), 60)} | "
                       f"{KIND_RU.get(r.kind, r.kind)} | {md_cell(plain_quote(r.quote), TABLE_QUOTE)} | "
                       f"{location(r.line_start, r.line_end, r.page)} | {dec(c) if c is not None else '—'} |")
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


def card(f: Finding, *, show_req_links: bool) -> str:
    crit = CRITERION_RU.get(f.criterion, f.criterion)
    lines = [f'<a id="{anchor(f.fid)}"></a>', f"**{f.fid} · {_sev(f)} · {crit}.** {md(f.title)}", ""]
    if f.problem:
        lines += [md(f.problem), ""]
    items = []
    if len(f.quotes) == 1:
        items.append("Цитата " + _quote_line(f.quotes[0], with_rid=show_req_links, paren=True))
    else:
        items.append("Цитаты:")
        for q in f.quotes:
            items.append("  - " + _quote_line(q, with_rid=show_req_links))
    if f.see_also:
        items.append("См. также (это место пришло из объединённого повтора; на отметки требований оно не влияет):")
        for q in f.see_also[:5]:
            items.append("  - " + _quote_line(q, with_rid=show_req_links))
        if len(f.see_also) > 5:
            items.append(f"  - и ещё {len(f.see_also) - 5}")
    if f.recommendation:
        items.append("Как исправить: " + md(f.recommendation))
    if f.fixed_wording:
        items.append("Исправленная формулировка: «" + md(f.fixed_wording) + "»")
    if f.question_id:
        items.append("Вопрос автору: " + link(f.question_id))
    if show_req_links:
        extra = [r for r in f.req_ids if not any(q.rid == r for q in f.quotes)]
        if extra:
            items.append("Затронутые требования: " + links(extra))
    if f.severity == "CRITICAL" and f.status == "open" and not f.advisory:
        items.append("Почему блокер: " + BLOCKER_RU.get(f.blocker_reason, "основание не указано"))
    if f.status_reason:
        items.append("Перепроверка: " + md(f.status_reason))
    items.append("Источник: " + _source(f))
    items.append("Основание: " + STANDARDS.get(f.criterion, "—"))
    for it in items:
        lines.append(it if it.startswith("  - ") else "- " + it)
    return "\n".join(lines)


# --- section 3 --------------------------------------------------------------------------------------


def _per_requirement(result: Result) -> str:
    out = [_h2("s3"), ""]
    offline = is_offline(result)
    if offline:
        out.append("Без модели каждое требование проверено только автоматическими правилами (размытые слова, "
                   "заглушки, сроки без условий, незакрытые перечни, несколько требований в одном пункте и др.). "
                   "Смысловые критерии без модели не проверялись.")
    else:
        out.append("Обозначения: ✅ нарушений нет · ⚠️ есть замечание · — критерий неприменим · ❔ не проверялось.")
    c = counted(result)
    placed: dict[str, list[Finding]] = {}
    for f in c:
        if f.scope == "requirement" and f.req_ids:
            placed.setdefault(f.req_ids[0], []).append(f)
    reviews = {rv.rid: rv for rv in result.reviews}
    for r in result.requirements:
        out.append("")
        out.append(_req_block(r, placed.get(r.rid, []), c, reviews.get(r.rid), offline))
    return "\n".join(out)


def _req_block(r: Requirement, own: list[Finding], all_counted: list[Finding], rv, offline: bool) -> str:
    title = [r.rid]
    if r.doc_id:
        title.append(md(r.doc_id))
    sec = last_section(r.section)
    if sec:
        title.append(md(shorten(sec, 70)))
    title.append(location(r.line_start, r.line_end, r.page))
    lines = [f'<a id="{anchor(r.rid)}"></a>', "### " + " · ".join(title), "", blockquote(plain_quote(r.quote)), ""]
    meta = f"Тип: {KIND_RU.get(r.kind, r.kind)}"
    if rv is not None and rv.clarity is not None:
        meta += f" · Ясность: {dec(rv.clarity)} из 10 ({score.clarity_band(rv.clarity)})"
    if r.section and r.section != sec:
        meta += f" · Раздел: {md(r.section)}"
    lines += [meta, ""]
    crit = rv.criteria if rv is not None else {}
    see = [f for f in all_counted if r.rid in f.req_ids and f not in own]
    shared = (" Но требование входит в замечания, охватывающие несколько мест документа, — они снижают его ясность "
              "(см. ниже)." if see and rv is not None and rv.clarity is not None and rv.clarity < 10 else "")
    if offline:
        if own:
            names = ", ".join(CRITERION_RU[x] for x in REQ_CRITERIA if any(f.criterion == x for f in own))
            lines.append(f"Критерии с замечаниями правил: {names}. Остальные критерии без модели не проверялись.")
        else:
            lines.append("В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись."
                         + shared)
    else:
        lines.append("| " + " | ".join(CRITERION_RU[x] for x in REQ_CRITERIA) + " |")
        lines.append("| " + " | ".join(":---:" for _ in REQ_CRITERIA) + " |")
        lines.append("| " + " | ".join(STATUS_ICON.get(crit[x].status if x in crit else "unchecked", "❔")
                                       for x in REQ_CRITERIA) + " |")
        unchecked = sorted({crit[x].note for x in REQ_CRITERIA if x in crit and crit[x].status == "unchecked" and crit[x].note})
        if unchecked:
            lines += ["", "Не проверено: " + md("; ".join(unchecked))]
        if not own:
            lines += ["", "✅ В самой формулировке нарушений не найдено." + shared]
            notes = [f"{CRITERION_RU[x]} — {crit[x].note}" for x in REQ_CRITERIA
                     if x in crit and crit[x].status in ("ok", "na") and crit[x].note]
            if notes:
                lines.append("Заметки проверяющего: " + md("; ".join(notes[:3])) + ".")
    for f in own:
        lines += ["", card(f, show_req_links=False)]
    if see:
        lines += ["", "См. также: " + "; ".join(f"{link(f.fid)} · {_sev(f)} — {md(shorten(f.title, 90))}" for f in see)]
    return "\n".join(lines)


# --- section 4 -------------------------------------------------------------------------------------------


def _set_level(result: Result) -> str:
    out = [_h2("s4"), ""]
    offline = is_offline(result)
    out.append("Здесь замечания к документу в целом: противоречия между разделами, пропуски, стандарты, термины, "
               "нумерация. " + ("Без модели — только то, что находят автоматические правила." if offline else ""))
    c = counted(result)
    orphans = [f for f in c if f.scope == "requirement" and not f.req_ids]
    rid_set = {r.rid for r in result.requirements}
    for numb, title, crit, fams in SUBSECTIONS:
        items = [f for f in c if f.scope == "set" and f.criterion == crit]
        items += [f for f in orphans if FAMILY.get(f.criterion) in fams]
        out += ["", f"### {numb} {title}", ""]
        if items:
            out.append("\n\n".join(card(f, show_req_links=True) for f in items))
        else:
            out.append("Нарушений не найдено." if not offline else
                       "Правилами нарушений не найдено; без модели этот критерий проверен лишь частично.")
        if crit == "traceability":
            out += ["", _coverage_block(result, rid_set, offline)]
    adv = advisory(result)
    out += ["", '<a id="s4-obs"></a>', "### Наблюдения (проверьте сами, на итог не влияют)", ""]
    if adv:
        out.append("Эти признаки находятся словарными правилами низкой точности: часто это нормальная формулировка. "
                   "Они не входят в число дефектов, индексы и вердикт.")
        out.append("")
        for f in adv:
            q = f.quotes[0]
            rid = f" · {link(q.rid)}" if q.rid else ""
            out.append(f'- <a id="{anchor(f.fid)}"></a>**{f.fid}** · {quote_location(q)}{rid} · {md(f.title)}: '
                       f"«{_quote_text(q.text, 200)}»")
    else:
        out.append("Нет.")
    if result.checklist:
        out += ["", _checklist_block(result)]
    return "\n".join(out)


def _checklist_block(result: Result) -> str:
    """The document's own unfilled review checklist and how the review answers it (a hint, not a signature)."""
    items = result.checklist
    by_model = any(it.by_model for it in items)
    out = ['<a id="s4-checklist"></a>', "### Чек-лист ревью из документа: как на него отвечает проверка", ""]
    out.append(f"В документе найден чек-лист ревью ({count(len(items), ('пункт', 'пункта', 'пунктов'))}), отметки в "
               "котором не проставлены. Ниже — ответы, которые предлагает проверка по тексту документа и найденным "
               "замечаниям. Это подсказка для проверяющего, а не его подпись: решение и отметку ставит человек. "
               "Ответы не меняют замечания, индексы и итог."
               + ("" if by_model else " В этом прогоне модель на пункты не отвечала, поэтому они только перечислены."))
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
    sc = sev_counts(c)
    with_f = {rid for f in c for rid in f.req_ids}
    n_req = len(result.requirements)
    n_with = sum(1 for x in result.requirements if x.rid in with_f)
    out = [_h2("s5"), "", "| Показатель | Значение |", "| :--- | ---: |"]
    rows = [
        ("Требований всего", num(n_req)),
        ("— без замечаний", num(n_req - n_with)),
        ("— с замечаниями", num(n_with)),
        ("Дефектов всего", num(len(c))),
        ("— 🔴 CRITICAL (блокеры)", num(sc["CRITICAL"])),
        ("— 🟡 MAJOR (существенные)", num(sc["MAJOR"])),
        ("— 🟢 MINOR (замечания)", num(sc["MINOR"])),
        ("Наблюдений (на итог не влияют)", num(len(advisory(result)))),
        ("Вопросов автору", num(len(result.questions))),
    ]
    if result.tests:
        t_ready, t_rest = test_totals(result)
        rows += [("Тестов можно написать уже сейчас", num(t_ready)),
                 ("Тестов нельзя написать без ответа автора", num(t_rest))]
    out += [f"| {k} | {v} |" for k, v in rows]
    out += ["", "**Дефекты по критериям качества**", "", "| Критерий | Дефектов |", "| :--- | ---: |"]
    for crit in REQ_CRITERIA + SET_CRITERIA:
        n = sum(1 for f in c if f.criterion == crit)
        if n:
            out.append(f"| {CRITERION_RU[crit]} | {n} |")
    if not c:
        out.append("| — | 0 |")

    out += ["", "**Индекс готовности документа**", ""]
    if r.score is not None:
        prelim = " (предварительно: применены только правила)" if is_offline(result) else ""
        out.append(f"**{r.score} из 100 — {score.readiness_band(r.score)}**{prelim}. Шкала: 85 и выше — готов, "
                   "60–84 — готов после правок, 40–59 — нужна существенная доработка, ниже 40 — не готов.")
        out.append("")
        out.append(f"Как посчитан: {md(r.score_formula)}. Ясность требования = 10 − 6,5 за каждый блокер, − 2,5 за "
                   "существенное, − 0,75 за мелкое замечание (замечания к набору, где требование упомянуто, — "
                   "вдвое меньше), но не ниже 1; если блокера у требования нет — не ниже 4.")
        clar = {rv.rid: rv.clarity for rv in result.reviews if rv.clarity is not None}
        dist = score.band_distribution(clar)
        out.append("")
        out.append("Требования по ясности: " + " · ".join(f"{label} — {n}" for label, n in dist) + ".")
    else:
        out.append(md(r.score_formula) or "Индекс не считается.")
    dens = len(c) / n_req if n_req else 0.0
    per_k = len(c) * 1000 / r.doc_words if r.doc_words else 0.0
    out += ["", f"Плотность: {dec(dens, 2)} дефекта на одно требование · {dec(per_k)} на 1000 слов документа."]
    out.append("Вердикт определяют блокеры, а не индекс: индекс показывает, сколько работы осталось.")

    blockers = _blockers(result)
    out += ["", "**Критичные дефекты**", ""]
    if blockers:
        for f in blockers:
            out.append(f"- {link(f.fid)} — {md(f.title)} ({_where(f)})")
    else:
        out.append("Нет.")

    out += ["", "**Время и стоимость**", ""]
    out.append(f"- Агент: {duration_text(r.duration_s)}, {usd(r.cost_usd)}"
               + (f" ({md(r.cost_note)})" if r.cost_note else "") + ".")
    out.append(f"- Ручное ревью (оценка, не замер): {num(n_req)} треб. × {4} мин + 45 мин на отчёт ≈ "
               f"{minutes_text(r.manual_minutes_estimate)}.")
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
        for k, a in enumerate(result.fix_first, 1):
            refs = f" ({links(a.finding_ids)})" if a.finding_ids else ""
            out.append(f"{k}. {md(a.action)}{refs}")
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
        out.append("Модель не отметила сильных сторон.")
    return "\n".join(out)


# --- section 7 ------------------------------------------------------------------------------------------


def _questions(result: Result) -> str:
    out = [_h2("s7"), ""]
    if not result.questions:
        out.append("Вопросов нет.")
        return "\n".join(out)
    after = verdict_after_blockers(result)
    if after is not None:
        out.append(f"Если автор ответит на блокирующие вопросы и внесёт правки, вердикт станет: **{md(after[1])}**.")
        out.append("")
    src = "по шаблонам автоматических правил" if is_offline(result) else "моделью по подтверждённым замечаниям"
    out.append(f"{count(len(result.questions), W_QUESTION).capitalize()} составлено {src}. "
               "Ответы удобно вписать в файл-анкету рядом с отчётом (`*.questions.md`).")
    by_fid = {f.fid: f for f in result.findings}
    out += _dialog(result)
    for prio in ("blocker", "important", "nice"):
        qs = [q for q in result.questions if q.priority == prio]
        if not qs:
            continue
        out += ["", f"### {PRIORITY_RU[prio]} ({len(qs)})"]
        for q in qs:
            out += ["", question_block(q, by_fid)]
    return "\n".join(out)


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
    return out


def question_block(q: Question, by_fid: dict[str, Finding]) -> str:
    lines = [f'<a id="{anchor(q.qid)}"></a>', f"**{q.qid}.** {md(q.text)}", ""]
    who = ADDRESSEE_RU.get(q.addressee, q.addressee)
    meta = f"- Кому: {md(who)}"
    if q.gap:
        meta += f" · Тип пробела: {GAP_RU.get(q.gap, q.gap)}"
    lines.append(meta)
    if q.why:
        lines.append(f"- Почему важно: {md(q.why)}")
    if q.quote is not None:
        lines.append(f"- Где в документе: {_quote_line(q.quote)}")
    if q.options:
        lines.append("- Варианты ответа:")
        for k, o in enumerate(q.options):
            eff = q.option_effects[k] if k < len(q.option_effects) and q.option_effects[k] else ""
            lines.append(f"  - {LETTERS[k]}) {md(o)}" + (f" — что изменится: {md(eff)}" if eff else ""))
    rel = [link(fid) for fid in q.finding_ids] + [link(r) for r in q.req_ids]
    if rel:
        lines.append("- Относится к: " + ", ".join(rel))
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
    out = [_h2("s8"), ""]
    ready, rest = test_totals(result)
    out.append(f"Можно написать уже сейчас: {ready} · Нельзя написать без ответа автора: {rest}. "
               "Полные тексты (Дано / Когда / Тогда) — в файле `*.tests.md` рядом с отчётом.")
    out += ["", "| Тест | Тип | Требования | Что проверяем | Статус |", "| :--- | :--- | :--- | :--- | :--- |"]
    for t in result.tests:
        if t.then and not t.blocked_by:
            status = "готов"
        elif not t.then:
            status = "ожидаемый результат в документе не описан — нужен ответ автора" + (
                " (" + links(t.blocked_by) + ")" if t.blocked_by else "")
        else:
            status = "заблокирован: ждёт ответа " + links(t.blocked_by)
        out.append(f'| <a id="{anchor(t.tid)}"></a>{t.tid} | {TEST_KIND_RU.get(t.kind, md(t.kind))} | {links(t.req_ids)} | '
                   f"{md_cell(t.title, 100)} | {status} |")
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
    out.append(f"3. Автоматические правила (набор {md(r.rules_version)}) ищут формальные дефекты: размытые слова, "
               "заглушки, единицы измерения, коды и методы HTTP, пустые разделы, подсадной текст для ИИ.")
    if not is_offline(result):
        out.append("4. Модель проверяет каждое требование по семи критериям ISO/IEC/IEEE 29148:2018 п. 5.2.5 "
                   + ("тремя отдельными проходами (ясность; полнота и проверяемость; корректность)" if r.mode == "full"
                      else "одним проходом")
                   + " и весь набор по пяти критериям п. 5.2.6"
                   + ("; затем второй, независимый проход перепроверяет каждое замечание." if r.mode == "full" else "."))
    else:
        out.append("4. Модель не использовалась: проверки по смыслу (противоречия, пропущенные сценарии, "
                   "пограничные случаи) не выполнялись.")
    out.append("5. Каждую цитату программа ищет в тексте документа; замечание без найденной цитаты выбрасывается, "
               "в отчёт попадает исходный текст, а не пересказ. Номера, индексы и вердикт считает программа.")
    out += ["", "**Честно о границах.** Это первый проход, который экономит время проверяющего; решение остаётся за "
            "человеком. Опубликованные замеры ИИ-проверки требований показывают, что лучшие модели находят примерно "
            "половину настоящих проблем и ошибочно помечают около одного требования из десяти. Поэтому цитаты "
            "сверяет программа"
            + (", замечания модели перепроверяются вторым проходом" if not is_offline(result) and r.mode == "full" else "")
            + ", а полнота агента измеряется на контрольных документах, которых он раньше не видел."
            + (" В этом прогоне модель не работала, поэтому найдено заведомо меньше." if is_offline(result) else "")]

    rejected = [f for f in result.rejected if f.status == "rejected"]
    merged = [f for f in result.rejected if f.status == "merged"]
    out += ["", "**Отклонено при перепроверке**", ""]
    if rejected or merged:
        for f in rejected:
            out.append(f"- {f.fid} · {_sev(f)} · {md(f.title)} — отклонено: {md(f.status_reason)}")
        for f in merged:
            out.append(f"- {f.fid} · {md(f.title)} — объединено с {link(f.merged_into)}: {md(f.status_reason)}")
    else:
        out.append("Ничего." if r.mode == "full" else "Перепроверка не выполнялась.")
    if result.dropped:
        out += ["", f"**Выброшено из-за неподтверждённой цитаты: {len(result.dropped)}** — список в JSON-версии "
                    "отчёта (поле `dropped`)."]
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
    calls = sum(1 for a in result.audit if a.get("type") == "model_call")
    decisions = sum(1 for a in result.audit if a.get("type") == "decision")
    out += ["", f"**Журнал решений** (JSON-версия отчёта, поле `audit`; только хэши текстов, без содержимого "
                f"документа): {count(calls, W_CALL)} к модели, "
                f"{count(decisions, ('решение', 'решения', 'решений'))} программы (отброшенные цитаты, объединения, "
                "защита замечаний правил, переходы без модели)."]
    return "\n".join(out)


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
