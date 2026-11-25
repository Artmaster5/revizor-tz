"""Rules on the document as a whole (scope "set")."""
from __future__ import annotations

import difflib
import re

from ..slicer import is_meta_section, is_option_cell, is_placeholder_text
from ..structure import detect_doc_id
from ._common import Ctx, ask, finding, fmt_list, short, strip_id, visible

# --- missing-ids -------------------------------------------------------------------


def rule_missing_ids(ctx: Ctx, rule):
    reqs = [r for r in ctx.requirements]
    if len(reqs) < 3:
        return []
    with_id = [r for r in reqs if r.doc_id]
    if len(with_id) * 2 >= len(reqs):
        return []
    sections: list[str] = []
    firsts = []
    by_id = {b.bid: b for b in ctx.doc.blocks}
    for r in reqs:
        if r.doc_id:
            continue
        name = r.section.split(" › ")[-1] if r.section else "(без раздела)"
        if name not in sections:
            sections.append(name)
            if len(firsts) < 3 and r.block_ids[0] in by_id:
                firsts.append(ctx.q_block(by_id[r.block_ids[0]]))
    return [finding(
        rule, firsts,
        title="У большинства требований нет номеров",
        problem=(f"Номер из документа есть только у {len(with_id)} из {len(reqs)} требований. Без номера на требование нельзя "
                 "сослаться в задаче, тест-кейсе или замечании, и трудно проверить, что каждое требование покрыто тестом. "
                 f"Разделы без номеров: {fmt_list(['«' + s + '»' for s in sections], 8)}."),
        recommendation=("Присвойте каждому требованию уникальный номер (например, Ф-5, НФТ-1, ДАН-1, API-3) "
                        "и ссылайтесь на эти номера в критериях приёмки."),
        question=ask("Какую схему номеров использовать для требований без номера?",
                     ["Продолжить существующую нумерацию на все разделы", "Свой префикс для каждого раздела (НФТ-, ДАН-, API-)"]),
        req_ids=[],
    )]


# --- unit-mix ------------------------------------------------------------------------

CURRENCIES = [
    ("тиын", "тенге", re.compile(r"\bти[ыи]н\w*", re.I), re.compile(r"\bтенге\b|₸|\bKZT\b", re.I)),
    ("копейки", "рубли", re.compile(r"\bкопе(?:йк\w*|ек)\b|\bкоп\.", re.I), re.compile(r"\bрубл\w*|\bруб\.|₽|\bRUB\b", re.I)),
    ("центы", "доллары", re.compile(r"\bцент(?:а|ов|ы|ам|ах)?\b|\bcents?\b", re.I),
     re.compile(r"\bдоллар\w*|\$\s?\d|\d\s?\$|\bUSD\b|\bdollars?\b", re.I)),
    ("евроценты", "евро", re.compile(r"\bевроцент\w*|\beuro ?cents?\b", re.I), re.compile(r"\bевро\b|€|\bEUR\b", re.I)),
]
_CONVERSION = re.compile(
    r"1\s*(?:тенге|₸|рубл\w*|руб\.|доллар\w*|евро|kzt|rub|usd|eur)\s*=\s*100|100\s*(?:ти[ыи]н\w*|копе\w*|цент\w*|cents?)\b|"
    r"(?:дел\w*|умнож\w*|divid\w*|multipl\w*)\s+(?:на|by)\s+100\b|[×x*/]\s?100\b",
    re.I,
)
TIME_SUBJECTS = re.compile(r"(тайм-?аут\w*|timeout|время ответа|время обработки|время ожидания|response time|задержк\w*)", re.I)
_GAP_WORDS = r"(?:\s+[^\s\d—:=.;,()]+){0,4}?"  # up to four words between the subject and its value
_QUAL = r"\s*(?:[—:=-]\s*)?(?:не более(?: чем)?(?: за)?|не превыша\w*|составляет|равн\w*|до|max\.?|up to|is|of)?\s*"
TIME_UNITS = [
    ("мс", re.compile(r"^" + _GAP_WORDS + _QUAL + r"\d[\d\s]*(?:мс|ms|миллисекунд\w*|milliseconds?)\b", re.I)),
    ("с", re.compile(r"^" + _GAP_WORDS + _QUAL + r"\d[\d\s]*(?:с|сек\.?|секунд\w*|s|sec\w*|seconds?)\b", re.I)),
    ("мин", re.compile(r"^" + _GAP_WORDS + _QUAL + r"\d[\d\s]*(?:мин\.?|минут\w*|min\w*|minutes?)\b", re.I)),
]


_PRECISION = re.compile(r"(?:точност\w*|округл\w*)\s+до\s+(?:одного\s+|1\s+)?$|"
                        r"(?:precision|accuracy|accurate|round(?:ed|ing)?)\s+(?:of\s+|to\s+)(?:the\s+)?(?:nearest\s+)?(?:one\s+|a\s+|1\s+)?$",
                        re.I)


def rule_unit_mix(ctx: Ctx, rule):
    out = []
    blocks = [b for b in ctx.doc.blocks if b.kind not in ("code", "heading")]
    full = "\n".join(visible(b.text) for b in blocks)
    for minor_name, major_name, minor_rx, major_rx in CURRENCIES:
        # "в тенге с точностью до тиына" names the precision, not a second unit
        minor = [(b, m) for b in blocks for m in [next((x for x in minor_rx.finditer(visible(b.text))
                                                        if not _PRECISION.search(visible(b.text)[:x.start()])), None)] if m]
        major = [(b, m) for b in blocks for m in [major_rx.search(visible(b.text))] if m]
        if not minor or not major or _CONVERSION.search(full):
            continue
        b1, m1 = minor[0]
        quotes = [ctx.q_sentence(b1, m1.start())]
        same = major_rx.search(quotes[0].text)
        other = next(((b, m) for b, m in major if b.bid != b1.bid and b.bid in ctx.rid_of and re.search(r"\d", b.text)),
                     next(((b, m) for b, m in major if b.bid != b1.bid), None))
        if other:
            quotes.append(ctx.q_sentence(other[0], other[1].start()))
        elif not same:
            continue
        out.append(finding(
            rule, quotes,
            title=f"Денежные суммы в разных единицах: {minor_name} и {major_name}",
            problem=(f"Одни суммы заданы в единицах «{minor_name}», другие — в «{major_name}», а правило пересчёта нигде не записано. "
                     "Разработчик может сохранить сумму в одних единицах, а сравнить её с порогом в других — "
                     "ошибка в 100 раз, которую не заметят до первого реального платежа."),
            recommendation=(f"Выберите одну единицу для хранения и передачи сумм (обычно {minor_name} — целым числом) и одну "
                            f"для показа клиенту; явно запишите правило: «1 {major_name.rstrip('и')} = 100 {minor_name}» "
                            "и в каком месте процесса выполняется пересчёт."),
            question=ask("В каких единицах хранить, передавать и сравнивать суммы?",
                         [f"Везде в {minor_name}, клиенту показывать в {major_name}",
                          f"Везде в {major_name} с двумя знаками после запятой",
                          "Как сейчас, но с явным правилом пересчёта при сравнении"], "architect"),
        ))
    # time units for the same limit
    by_subject: dict[str, list] = {}
    for b in blocks:
        t = visible(b.text)
        for m in TIME_SUBJECTS.finditer(t):
            tail = t[m.end():m.end() + 40]
            for unit, rx in TIME_UNITS:
                if rx.match(tail):
                    key = m.group(1).lower().replace("-", "")[:8]
                    by_subject.setdefault(key, []).append((unit, b, m.start()))
                    break
    for key, items in by_subject.items():
        units = []
        for u, b, p in items:
            if u not in [x[0] for x in units]:
                units.append((u, b, p))
        if len(units) < 2:
            continue
        quotes = [ctx.q_sentence(b, p) for _, b, p in units[:3]]
        out.append(finding(
            rule, quotes,
            title=f"Одно ограничение времени задано в разных единицах: {', '.join(u for u, _, _ in units)}",
            problem=("Одно и то же ограничение времени в разных местах указано в разных единицах. Легко ошибиться "
                     "в 1000 или 60 раз при переносе значения в настройки, а тестировщик не знает, какое значение верное."),
            recommendation="Укажите ограничение в одних единицах во всех местах документа (например, везде в миллисекундах).",
            question=ask("В каких единицах задавать это ограничение времени?", ["Миллисекунды", "Секунды"], "architect"),
        ))
    return out


# --- empty-section -----------------------------------------------------------------


def rule_empty_section(ctx: Ctx, rule):
    out = []
    blocks = ctx.doc.blocks
    for k, h in enumerate(blocks):
        if h.kind != "heading":
            continue
        content = []
        for b in blocks[k + 1:]:
            if b.kind == "heading" and b.level <= h.level:
                break
            content.append(b)
        sev = "MINOR" if is_meta_section(h.section) else None
        nxt = blocks[k + 1] if k + 1 < len(blocks) else None
        if not content and h.level == 6 and nxt is not None and nxt.kind == "heading":
            continue  # plain text: a guessed heading right before another one is usually a group title, nesting unknown
        if not content:
            out.append(finding(
                rule, [ctx.q_block(h)],
                title=f"Раздел «{short(h.text, 60)}» пуст",
                problem=("У раздела есть заголовок, но нет содержания. Читатель не знает, забыли ли его заполнить или "
                         "требований здесь действительно нет; то, что должно было здесь быть, не будет ни реализовано, ни проверено."),
                recommendation="Заполните раздел или удалите его и коротко напишите, почему он не нужен (например, «не требуется: …»).",
                question=ask(f"Что должно быть в разделе «{short(h.text, 50)}»?",
                             ["Заполнить до начала разработки", "Раздел не нужен — удалить и пояснить почему"]),
                severity=sev,
            ))
            continue
        if any(b.kind == "heading" for b in content):
            continue
        if all(b.kind == "table_row" and b.is_empty for b in content):
            continue  # reported by empty-table
        if all(b.kind != "code" and is_placeholder_text(visible(b.text)) for b in content):
            quotes = [ctx.q_block(h)] + [ctx.q_block(b) for b in content[:2]]
            ph = short(content[0].text, 40)
            out.append(finding(
                rule, quotes,
                title=f"Раздел «{short(h.text, 60)}» не заполнен: вместо содержания заглушка",
                problem=(f"Вместо содержания раздела стоит заглушка «{ph}». Значит, эта часть требований не готова: "
                         "по ней нельзя начать работу и нельзя проверить результат, а пробел легко пропустить, потому что раздел "
                         "формально есть."),
                recommendation=("Заполните раздел до начала разработки: для раздела о тестировании — что проверяется, на каких данных, "
                                "кто и когда проводит проверку, какие критерии приёмки. Если раздел заполняет другая команда — "
                                "назовите ответственного и срок."),
                question=ask(f"Кто и когда заполнит раздел «{short(h.text, 50)}»?",
                             ["Автор документа — до начала разработки", "Другая команда — указать ответственного и срок",
                              "Раздел не нужен — удалить"]),
                severity=sev,
            ))
    return out


# --- empty-table / unfilled-checklist ----------------------------------------------------

_APPROVAL = re.compile(r"фио|подпис|согласу|утвержд|approv|sign|ответственн", re.I)
_SIGN_COL = re.compile(r"фио|подпис|дата|date|sign|name|имя", re.I)


def rule_empty_table(ctx: Ctx, rule):
    out = []
    for t in ctx.tables:
        partial = False
        if not all(r.is_empty for r in t.rows):
            # an approval table that lists departments but no names, signatures or dates is just as unsigned
            sign_cols = [k for k, h in enumerate(t.header) if _SIGN_COL.search(h)]
            if not sign_cols or not any(_APPROVAL.search(h) for h in t.header) or not all(
                    all(not (r.cells[k] if k < len(r.cells) else "").strip() for k in sign_cols) for r in t.rows):
                continue
            partial = True
        first = t.rows[0]
        heading = ctx.heading_for(first)
        quotes = []
        if heading is not None:
            quotes.append(ctx.q_block(heading))
        if t.header_line:
            quotes.append(ctx.qi.from_lines(t.header_line))
        else:
            quotes.append(ctx.q_block(first))
        sec = heading.text if heading is not None else "без раздела"
        approval = any(_APPROVAL.search(h) for h in t.header) or (heading is not None and _APPROVAL.search(heading.text))
        out.append(finding(
            rule, quotes,
            title=f"Таблица в разделе «{short(sec, 50)}» не заполнена",
            problem=(("В таблице перечислены участники, но нет ни ФИО, ни подписей, ни дат. " if partial else
                      "Таблица есть, но ни одна её строка не заполнена. ")
                     + ("Это таблица согласования: документ формально никем не согласован, и непонятно, кто отвечает за "
                        "требования и чья редакция действует." if approval else
                        "Сведения, ради которых она заведена, в документе отсутствуют.")),
            recommendation=("Заполните таблицу: " + ("укажите подразделения, ФИО ответственных и дату согласования."
                                                     if approval else "внесите данные или удалите таблицу, если она не нужна.")),
            question=ask("Кто должен быть в этой таблице?" if approval else "Нужна ли эта таблица?",
                         ["Заполнить до начала разработки", "Таблица не нужна — удалить"],
                         "business" if approval else "analyst"),
        ))
    return out


def rule_unfilled_checklist(ctx: Ctx, rule):
    out = []
    for t in ctx.tables:
        cells = [(r, c) for r in t.rows for c in r.cells if c.strip() and is_option_cell(c)]
        if len(cells) < 3:
            continue
        rows = []
        for r, _ in cells:
            if r not in rows:
                rows.append(r)
        heading = ctx.heading_for(t.rows[0])
        quotes = ([ctx.q_block(heading)] if heading is not None else []) + [ctx.q_block(rows[0])]
        out.append(finding(
            rule, quotes,
            title="Чек-лист ревью не заполнен",
            problem=(f"В {len(cells)} ячейках таблицы ({len(rows)} строк) оставлены все варианты ответа, например «{cells[0][1]}». "
                     "Значит, ревью по чек-листу не проводилось: не видно, кто и что проверил и какие пункты не выполнены."),
            recommendation=("Проведите ревью по чек-листу: в каждой ячейке оставьте один ответ, для ответов «Нет» "
                            "впишите в примечания, что будет сделано и кем."),
            question=ask("Кто проводит ревью по чек-листу и когда?",
                         ["Аналитик и тестировщик — до начала разработки", "Чек-лист не нужен для этой задачи — удалить"], "qa"),
        ))
    return out


# --- no-version ---------------------------------------------------------------------------

_VERSION = re.compile(r"(?:верси[яи]|редакци[яи]|ред\.|version|rev\.?|revision)\s*[:№#]?\s*v?\d+(?:[.,]\d+)*|\bv\d+\.\d+", re.I)
_DATE = re.compile(
    r"\b\d{1,2}[./]\d{1,2}[./](?:19|20)?\d{2}\b|\b(?:19|20)\d{2}-\d{2}-\d{2}\b|"
    r"\b\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(?:19|20)\d{2}\b|"
    r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},?\s+(?:19|20)\d{2}\b",
    re.I,
)
_DATE_LABEL = re.compile(r"дата|date|от\s|утвержд|редакц|updated|обновл", re.I)
_EXPLICIT_MISSING = re.compile(
    r"(?:верси\w*|дат\w*)[^.\n]{0,40}не\s+(?:заполнен\w*|указан\w*|определен\w*)|"
    r"не\s+(?:заполнен\w*|указан\w*)[^.\n]{0,25}(?:верси\w*|дат\w*)|(?:version|date)[^.\n]{0,30}(?:missing|not (?:set|filled|specified))",
    re.I,
)


_DATE_BLANK = re.compile(
    r"^\W*(?:дата[^:\n]{0,25}|date[^:\n]{0,20}|верси[яи][^:\n]{0,15}|version[^:\n]{0,10}):\s*"
    r"(?:[_.\s/-]*_[_.\s/-]*(?:\d{2,4})?|дд[./]мм\S*|dd[./]mm\S*|tbd|\?+|—|–|-|\[[^\]]*\])\s*$",
    re.I,
)


def rule_no_version(ctx: Ctx, rule):
    doc = ctx.doc
    for k, line in enumerate(doc.lines[:60], 1):
        if _DATE_BLANK.match(line):
            f = _no_version(ctx, rule, [ctx.qi.from_lines(k, k)], explicit=True)
            f.title = "В шапке документа не заполнено поле «" + line.split(":")[0].strip(" *#|-") + "»"
            return [f]
    for b in doc.blocks:
        m = _EXPLICIT_MISSING.search(visible(b.text))
        if m:
            return [_no_version(ctx, rule, [ctx.q_sentence(b, m.start())], explicit=True)]
    head = "\n".join(doc.lines[:40])
    if _VERSION.search(doc.text):
        return []
    for k, line in enumerate(doc.lines):
        if _DATE.search(line) and (k < 40 or _DATE_LABEL.search(line)):
            return []
    if _DATE.search(head):
        return []
    first = next((b for b in doc.blocks if b.kind == "heading"), doc.blocks[0] if doc.blocks else None)
    if first is None:
        return []
    return [_no_version(ctx, rule, [ctx.q_block(first)], explicit=False)]


def _no_version(ctx, rule, quotes, explicit: bool):
    return finding(
        rule, quotes,
        title="У документа не указаны версия и дата",
        problem=(("В документе версия или дата оставлены незаполненными. " if explicit else
                  "В документе не найдено ни номера версии, ни даты. ")
                 + "Нельзя понять, какая редакция актуальна: замечания ревью, задачи и тесты нельзя привязать к конкретной версии, "
                   "а изменения требований — отследить."),
        recommendation="Добавьте в начало документа номер версии (например, 1.0), дату, автора и таблицу истории изменений.",
        question=ask("Какую версию и дату присвоить документу?", ["1.0 — первая согласуемая редакция", "0.x — черновик до согласования"]),
    )


# --- dangling references (sections, tables, appendices, requirement IDs) -----------------------

_REF_SECTION = re.compile(r"(?:см\.?\s+|согласно\s+|в\s+|по\s+)?(?:раздел[еау]?|пункт[еау]?|п\.|подраздел[еау]?|section|clause)\s*(\d+(?:\.\d+)*)", re.I)
_REF_TABLE = re.compile(r"(?:таблиц[аеуы]|табл\.|table)\s*(?:№\s*)?(\d+(?:\.\d+)*)", re.I)
# the appendix name must look like an identifier: a digit or a CAPITAL letter standing alone
# ("Приложение А", "appendix 2"), never "мобильное приложение и …"
_REF_APPX = re.compile(r"(?i:приложени[еия]|appendix|annex)\s*(?:№\s*)?([А-ЯЁA-Z]|\d+)(?![\w.-]*\w)")


def rule_dangling_reference(ctx: Ctx, rule):
    doc = ctx.doc
    headings = [b for b in doc.blocks if b.kind == "heading"]
    numbers = set()
    for h in headings:
        m = re.match(r"^\s*(?:раздел\s+|section\s+)?(\d+(?:\.\d+)*)\.?\s", h.text + " ", re.I)
        if m:
            numbers.add(m.group(1))
    for b in doc.blocks:  # numbered paragraphs/list items in plain text also define "пункты"
        m = re.match(r"^\s*(\d+(?:\.\d+)+)\.?\s", b.text)
        if m:
            numbers.add(m.group(1))
    table_captions = set(re.findall(r"(?im)^\W*(?:таблица|table)\s*(?:№\s*)?(\d+(?:\.\d+)*)", doc.text))
    n_tables = len(ctx.tables)
    appendices = set()
    for h in headings:
        m = re.search(r"(?:приложени[еия]|appendix|annex)\s*(?:№\s*)?([А-ЯA-Z]|\d+)\b", h.text, re.I)
        if m:
            appendices.add(m.group(1).upper())
    defined_ids = {b.doc_id for b in doc.blocks if b.doc_id}
    prefixes = {re.match(r"^([^\d]+)", i).group(1) for i in defined_ids if re.match(r"^[^\d]+", i)}
    out = []
    seen = set()
    for b in doc.blocks:
        if b.kind in ("heading", "code"):
            continue
        t = visible(b.text)
        for m in _REF_SECTION.finditer(t):
            num = m.group(1)
            if not headings or (not numbers):
                continue  # the document does not number its sections: nothing to check against
            if num in numbers or any(n.startswith(num + ".") for n in numbers):
                continue
            key = ("раздел", num)
            if key not in seen:
                seen.add(key)
                out.append(_dangling(ctx, rule, b, m.start(), f"раздел {num}"))
        for m in _REF_TABLE.finditer(t):
            num = m.group(1)
            # "таблица 5.1" may be captioned by a numbered line "5.1. Стоп-факторы …" right above the table
            if num in table_captions or num in numbers or (not table_captions and "." not in num and int(num) <= n_tables):
                continue
            key = ("таблица", num)
            if key not in seen:
                seen.add(key)
                out.append(_dangling(ctx, rule, b, m.start(), f"таблица {num}"))
        for m in _REF_APPX.finditer(t):
            name = m.group(1).upper()
            if name in appendices:
                continue
            # a heading-less appendix can still exist as a caption line elsewhere in the document
            caption = re.compile(r"(?im)^\W*(?:приложени[еия]|appendix|annex)\s*(?:№\s*)?" + re.escape(m.group(1)) + r"\b")
            if any(caption.match(ln) for k, ln in enumerate(doc.lines, 1) if not (b.line_start <= k <= b.line_end)):
                continue
            key = ("приложение", name)
            if key not in seen:
                seen.add(key)
                out.append(_dangling(ctx, rule, b, m.start(), f"приложение {m.group(1)}"))
        for pref in prefixes:
            for m in re.finditer(r"(?<![\w-])" + re.escape(pref) + r"\d+(?:\.\d+)*", t):
                ref = m.group(0)
                if ref in defined_ids or m.start() <= 4 and b.doc_id == ref:
                    continue
                if any(d.startswith(ref + ".") for d in defined_ids):
                    continue
                key = ("id", ref)
                if key not in seen:
                    seen.add(key)
                    out.append(_dangling(ctx, rule, b, m.start(), f"требование {ref}"))
    return out


def _dangling(ctx, rule, b, pos, what):
    return finding(
        rule, [ctx.q_sentence(b, pos)],
        title=f"Ссылка на несуществующий объект: {what}",
        problem=(f"Текст ссылается на «{what}», но в документе такого нет. Читатель не может открыть то, на что опирается требование, "
                 "и каждый додумает содержание по-своему."),
        recommendation=f"Исправьте ссылку на существующий раздел, таблицу или требование, либо добавьте «{what}» в документ.",
        question=ask(f"Что имелось в виду под «{what}»?", ["Опечатка — исправить номер", "Добавить недостающую часть в документ"]),
    )


# --- incomplete reference ----------------------------------------------------------------

_INCOMPLETE_REF = re.compile(
    r"(?:согласно|в соответствии с|по)\s+(?:действующ\w*|установленн\w*|утвержд[её]нн\w*|внутренн\w*|соответствующ\w*|применим\w*)\s+"
    r"(?:порядк\w*|регламент\w*|правил\w*|процедур\w*|политик\w*|норм\w*|требовани\w*|инструкци\w*|стандарт\w*)"
    r"(?![^.;]{0,40}(?:№|\bN\s?\d|\d{2,}[-/]\d|версии|ред\.|от\s+\d))|"
    r"\b(?:см\.|смотри)\s+(?:выше|ниже)\b|"
    r"\b(?:per|according to|in accordance with)\s+(?:the\s+)?(?:applicable|relevant|existing|current|internal)\s+"
    r"(?:procedures?|polic(?:y|ies)|rules|regulations|standards?)\b|\bsee (?:above|below)\b",
    re.I,
)


def rule_incomplete_reference(ctx: Ctx, rule):
    out = []
    for b in ctx.req_blocks:
        if b.kind in ("code", "heading"):
            continue
        m = _INCOMPLETE_REF.search(visible(b.text))
        if not m:
            continue
        w = m.group(0)
        out.append(finding(
            rule, [ctx.q_sentence(b, m.start())],
            title=f"Ссылка без точного адреса: «{short(w, 50)}»",
            problem=(f"Требование опирается на «{w}», но не говорит, какой именно документ, раздел или версия имеется в виду. "
                     "Разработчик и тестировщик найдут разные документы или не найдут ни одного."),
            recommendation="Укажите точную ссылку: название и номер документа, версию или дату, раздел (или номер требования в этом документе).",
            question=ask("На какой именно документ или раздел ссылается требование?",
                         ["Указать документ, номер и версию", "Перенести нужное правило прямо в этот документ"]),
        ))
    return out


# --- duplicates -------------------------------------------------------------------------------


def _dup_key(text: str) -> str:
    t = strip_id(visible(text)).lower().replace("ё", "е")
    return re.sub(r"[^\w\d]+", " ", t).strip()


def rule_duplicate(ctx: Ctx, rule):
    cands = []
    for b in ctx.req_blocks:
        if b.kind not in ("paragraph", "list_item"):
            continue
        key = _dup_key(b.text)
        if len(key.split()) >= 6:
            cands.append((b, key))
    out = []
    used = set()
    for i, (b1, k1) in enumerate(cands):
        if b1.bid in used:
            continue
        for b2, k2 in cands[i + 1:]:
            if b2.bid in used or ctx.rid_of.get(b1.bid) == ctx.rid_of.get(b2.bid):
                continue
            sm = difflib.SequenceMatcher(None, k1, k2, autojunk=False)
            if sm.real_quick_ratio() < 0.9 or sm.quick_ratio() < 0.9:
                continue
            ratio = sm.ratio()
            if ratio < 0.9:
                continue
            nums1 = re.findall(r"\d+", k1)
            nums2 = re.findall(r"\d+", k2)
            same = ratio > 0.995
            used.add(b2.bid)
            out.append(finding(
                rule, [ctx.q_block(b1), ctx.q_block(b2)],
                title="Одно и то же требование записано дважды" if same and nums1 == nums2 else "Почти одинаковые требования в двух местах",
                problem=("Два места документа говорят одно и то же" + ("" if same and nums1 == nums2 else
                         " с небольшими отличиями — непонятно, какая формулировка верная") +
                         ". При изменении одного места второе забудут поправить, и требования разойдутся."),
                recommendation="Оставьте требование в одном месте, а во втором сошлитесь на его номер.",
                question=ask("Какая из двух формулировок правильная?", ["Первая", "Вторая", "Обе нужны — это разные случаи, уточнить различие"]),
            ))
            break
    return out


# --- number without unit ------------------------------------------------------------------

_NO_UNIT = re.compile(
    r"\b(тайм-?аут|timeout|задержк[аи]|интервал\w*|время ожидания|время ответа|время обработки|время жизни|срок хранения|"
    r"срок действия|ttl|период\w*\s+(?:опроса|повтора|хранения)|retention|retry interval|response time|processing time)"
    + _GAP_WORDS + _QUAL +
    # a bare number that ends the clause: «— 30.», «не более 5;» — a unit or anything else after it means no finding
    r"(\d+(?:[.,]\d+)?)(?=\s*(?:[.;,)](?:\s|$)|$))",
    re.I,
)


def rule_number_without_unit(ctx: Ctx, rule):
    out = []
    for b in ctx.req_blocks:
        if b.kind in ("code", "heading"):
            continue
        t = visible(b.text)
        m = _NO_UNIT.search(t)
        if not m:
            continue
        out.append(finding(
            rule, [ctx.q_sentence(b, m.start())],
            title=f"Значение без единицы измерения: «{short(m.group(0), 40)}»",
            problem=(f"Для «{m.group(1)}» указано число {m.group(2)}, но не сказано, в чём оно измеряется. "
                     "Это могут быть миллисекунды, секунды или минуты — разница в тысячи раз."),
            recommendation=f"Допишите единицу измерения: например, «{m.group(1)} {m.group(2)} секунд».",
            question=ask(f"В каких единицах задано значение {m.group(2)}?", ["Миллисекунды", "Секунды", "Минуты"]),
        ))
    return out


SET_RULES = {
    "missing-ids": rule_missing_ids,
    "unit-mix": rule_unit_mix,
    "empty-section": rule_empty_section,
    "empty-table": rule_empty_table,
    "unfilled-checklist": rule_unfilled_checklist,
    "no-version": rule_no_version,
    "dangling-reference": rule_dangling_reference,
    "incomplete-reference": rule_incomplete_reference,
    "duplicate": rule_duplicate,
    "number-without-unit": rule_number_without_unit,
}

_ = detect_doc_id  # re-exported helper kept for readers of this module
