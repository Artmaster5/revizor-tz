"""Rules on the wording of single requirements (requirement blocks only, never code)."""
from __future__ import annotations

import re

from ._common import Ctx, ask, finding, sentences, short, strip_id, visible

# (pattern, hint for the recommendation)
_SPEED = "укажите время в секундах или миллисекундах, долю запросов (например, 95%) и нагрузку, при которой оно выполняется"
_COND = "перечислите условия, при которых действие выполняется, или уберите оговорку"
_UI = "задайте проверяемый критерий: число шагов до цели, время выполнения задачи пользователем или ссылку на утверждённый макет"
_QTY = "укажите конкретное число и единицу измерения"
_QUAL = "назовите измеримый показатель и его целевое значение"
_HOW = "опишите, что именно должно быть сделано: конкретное действие, его результат и как это проверить"

VAGUE = [
    (r"быстр(?:о|ый|ая|ое|ые|ого|ым|ой)?(?!\s+(?:платеж|платёж|перевод\w*\s+по\s+сбп))", _SPEED),
    (r"оперативн(?:о|ый|ая|ое|ые)", _SPEED),
    (r"мгновенн(?:о|ый|ая|ое|ые|ого)", _SPEED),
    (r"своевременн(?:о|ый|ая|ое|ые)", _SPEED),
    (r"удобн(?:о|ый|ая|ое|ые|ым|ого|ой)", _UI),
    (r"интуитивн(?:о|ый|ая|ое|ые)(?:\s+понятн\w*)?", _UI),
    (r"понятн(?:о|ый|ая|ое|ые|ым|ой)", _UI),
    (r"дружественн\w*", _UI),
    (r"современн(?:ый|ая|ое|ые|ым)", _UI),
    (r"достаточн(?:о|ый|ая|ое|ые|ого)", _QTY),
    (r"оптимальн(?:о|ый|ая|ое|ые|ого)", _QUAL),
    (r"эффективн(?:о|ый|ая|ое|ые|ого)", _QUAL),
    (r"над[её]жн(?:о|ый|ая|ое|ые|ого)", _QUAL),
    (r"гибк(?:о|ий|ая|ое|ие)", _QUAL),
    (r"масштабируем(?:о|ый|ая|ое|ые|ость)", _QUAL),
    (r"максимально(?!\s+\d)", _QTY),
    (r"минимально(?!\s+\d)", _QTY),
    (r"по возможности", _COND),
    (r"при необходимости", _COND),
    (r"в случае необходимости", _COND),
    (r"как правило", _COND),
    (r"обычно", _COND),
    (r"примерно", _QTY),
    (r"около(?=\s+\d)", _QTY),
    (r"приблизительно", _QTY),
    (r"высок(?:ая|ую|ой|ий|ие|ое)\s+(?:нагрузк\w*|производительност\w*|доступност\w*|скорост\w*)", _QUAL),
    (r"всегда\s+доступ\w*", _QUAL),
    (r"(?:соответствующим|надлежащим|должным)\s+образом", _HOW),
    # English equivalents
    (r"fast(?:er)?", _SPEED), (r"quickly", _SPEED), (r"promptly", _SPEED), (r"instantly", _SPEED),
    (r"in a timely manner", _SPEED), (r"real[- ]time", _SPEED), (r"as soon as possible|asap", _SPEED),
    (r"user[- ]friendly", _UI), (r"easy to use|easy", _UI), (r"intuitive(?:ly)?", _UI), (r"convenient", _UI),
    (r"as appropriate", _COND), (r"if (?:necessary|needed|required|possible)", _COND), (r"where possible", _COND),
    (r"as needed", _COND), (r"accordingly|appropriately|properly", _HOW), (r"usually", _COND), (r"typically", _COND),
    (r"approximately|approx\.|about(?=\s+\d)|around(?=\s+\d)|roughly", _QTY),
    (r"sufficient(?:ly)?|enough", _QTY), (r"efficient(?:ly)?", _QUAL), (r"robust", _QUAL),
    (r"flexible", _QUAL), (r"scalable", _QUAL), (r"reliable|reliably", _QUAL), (r"optimal(?:ly)?", _QUAL),
    (r"high (?:load|performance|availability)", _QUAL), (r"modern", _UI),
]
_VAGUE_RE = [(re.compile(r"(?<![\w-])(?:" + p + r")(?![\w-])", re.I), h) for p, h in VAGUE]

OPEN_LIST = re.compile(
    r"(?<!\w)(?:и\s+т\.\s?д\.?|и\s+т\.\s?п\.?|и\s+(?:все\s+)?други[ем]\w*(?=[\s.,;:)]|$)|и\s+др\.|и\s+прочее|и\s+прочие|и\s+так\s+далее|"
    r"и\s+далее|etc\.?|and\s+so\s+on|and\s+others|and\s+more)(?!\w)",
    re.I,
)

PLACEHOLDER_IN_TEXT = re.compile(
    r"(?<![\w-])(?:TBD|TBA|TBC|TODO|FIXME|to be (?:defined|determined|confirmed|done))(?![\w-])|\?{3,}|"
    r"(?:(?<=[:(—–])|(?<=[:(—–]\s)|^)\s*(?:уточнить|уточняется|определить позже|будет определено|будет уточнено)\b|"
    r"\b(?:согласовать позже|будет определено позже|будет уточнено позже|заполня(?:ется|ются)\s+\w+)\b|"
    r"<\s*(?:указать|вставить|заполнить|название|имя|дата|значение|insert|fill|tbd)[^<>]{0,30}>|"
    r"\[\s*(?:указать|вставить|заполнить|значение|insert|fill)[^\[\]]{0,30}\]",
    re.I | re.M,
)
_LABEL_DASH = re.compile(r"(?<![\w])([A-Za-zА-Яа-яЁё][\w ]{0,20}):\s*[—–]\s*(?=$|[.;,|])")
_EMPTY_VALUES = {"—", "–", "-", "n/a", "na", "?", "??", "tbd", "нет данных"}

TIME_LIMIT = re.compile(
    r"(?:в течение|не более(?: чем)?|не дольше(?: чем)?|не позднее(?: чем через)?|не превыша\w*|до|за|within|in under|under|no more than|at most|less than)\s+"
    r"(?:(?:\d+(?:[.,]\d+)?)\s*(?:мс|миллисекунд\w*|ms|milliseconds?|с|сек\.?|секунд\w*|s|sec\w*|seconds?|мин\.?|минут\w*|min\w*|minutes?)"
    r"|(?:одной\s+|одну\s+)?(?:секунды|секунду|минуты|минуту|a second|a minute|one second|one minute))(?![\w])",
    re.I,
)
TIME_CONDITION = re.compile(
    r"%|процент|перцентил|percentile|квантил|\bp\d{2}\b|\d+-?[йм]\s+(?:перцентил|процентил)|при нагрузке|under load|"
    r"\brps\b|\btps\b|запрос\w* в секунду|requests per second|одновременн|concurrent|в среднем|on average|в \d+ случа",
    re.I,
)
_ARROW = re.compile(r"→|->|=>")


def _block_text(b) -> str:
    return visible(b.text)


def rule_vague_word(ctx: Ctx, rule):
    out = []
    for b in ctx.req_blocks:
        if b.kind in ("code", "heading"):
            continue
        t = _block_text(b)
        hits = []
        for rx, hint in _VAGUE_RE:
            for m in rx.finditer(t):
                hits.append((m.start(), m.group(0), hint))
        if not hits:
            continue
        # longest match first at each position; drop hits that sit inside an already kept one
        hits.sort(key=lambda h: (h[0], -len(h[1])))
        kept = []
        for h in hits:
            if any(k[0] <= h[0] and h[0] + len(h[1]) <= k[0] + len(k[1]) for k in kept):
                continue
            kept.append(h)
        hits = kept
        words = []
        for _, w, _ in hits:
            if w.lower() not in [x.lower() for x in words]:
                words.append(w)
        pos, first, hint = hits[0]
        quotes = [ctx.q_sentence(b, pos)]
        listed = "», «".join(words[:5])
        out.append(finding(
            rule, quotes,
            title=f"Размытая формулировка: «{first}»" if len(words) == 1 else f"Размытые формулировки: «{listed}»",
            problem=(f"Слово «{first}» каждый понимает по-своему: заказчик, разработчик и тестировщик представят разное. "
                     + ("Здесь же: «" + "», «".join(words[1:5]) + "». " if len(words) > 1 else "")
                     + "По такой формулировке нельзя написать тест с однозначным ожидаемым результатом."),
            recommendation=f"Замените «{first}» проверяемым условием: {hint}.",
            question=ask(f"Что конкретно означает «{first}» в этом требовании?",
                         ["Задать число и условия измерения (дописать в требование)",
                          "Перечислить случаи, когда это применяется",
                          "Убрать слово — оно ничего не добавляет"], "analyst"),
        ))
    return out


def rule_open_list(ctx: Ctx, rule):
    out = []
    for b in ctx.req_blocks:
        if b.kind in ("code", "heading"):
            continue
        t = _block_text(b)
        m = OPEN_LIST.search(t)
        if not m:
            continue
        w = m.group(0).strip()
        out.append(finding(
            rule, [ctx.q_sentence(b, m.start())],
            title=f"Незакрытый перечень: «{w}»",
            problem=(f"Перечень заканчивается словами «{w}», поэтому непонятно, что ещё в него входит. "
                     "Разработчик реализует только названное, а тестировщик не знает, какие ещё случаи проверять."),
            recommendation=("Перечислите все значения полностью или назовите правило, по которому их можно определить "
                            "(например, «все коды 4xx из таблицы статусов» или ссылку на справочник)."),
            question=ask("Какие именно значения входят в перечень?",
                         ["Только перечисленные — убрать «" + w + "»",
                          "Перечисленные и ещё несколько — дописать их",
                          "Все значения из справочника — дать ссылку на него"]),
        ))
    return out


def rule_placeholder(ctx: Ctx, rule):
    out = []
    for b in ctx.req_blocks:
        if b.kind in ("code", "heading"):
            continue
        t = _block_text(b)
        hit = None
        m = PLACEHOLDER_IN_TEXT.search(t)
        if m:
            hit = (m.start(), m.group(0).strip(" :(—–"))
        elif b.kind == "table_row" and len(b.cells) == 2 and b.cells[0].strip() \
                and b.cells[1].strip().lower() in _EMPTY_VALUES:
            pos = t.rfind(b.cells[1].strip())
            hit = (max(pos, 0), b.cells[1].strip())
        else:
            pieces = [c for c in b.cells] if b.kind == "table_row" else [t]
            for piece in pieces:
                m = _LABEL_DASH.search(visible(piece))
                if m:
                    pos = t.find(m.group(0).strip())
                    hit = (max(pos, 0), m.group(0).strip())
                    break
        if not hit:
            continue
        pos, what = hit
        field_name = b.cells[0] if b.kind == "table_row" and b.cells else ""
        quote = ctx.q_block(b) if b.kind == "table_row" else ctx.q_sentence(b, pos)
        out.append(finding(
            rule, [quote],
            title=(f"Не заполнено поле «{short(field_name, 40)}»" if field_name and what in _EMPTY_VALUES | {"—", "–"}
                   else f"Заглушка вместо значения: «{short(what, 40)}»"),
            problem=(f"Вместо содержания стоит заглушка «{short(what, 40)}». Пока значение не определено, эту часть нельзя "
                     "ни реализовать, ни проверить: каждый подставит своё, и расхождение обнаружится только на приёмке."),
            recommendation=("Впишите значение. Если оно ещё неизвестно — укажите, кто и к какой дате его определит, "
                            "и не начинайте работу над этой частью до решения."),
            question=ask(f"Чем заменить заглушку «{short(what, 40)}»?",
                         ["Значение известно — вписать сейчас",
                          "Определит владелец продукта до начала разработки",
                          "Поле не нужно — удалить его"], "business"),
        ))
    return out


def rule_time_without_conditions(ctx: Ctx, rule):
    out = []
    for b in ctx.req_blocks:
        if b.kind in ("code", "heading"):
            continue
        t = _block_text(b)
        m = TIME_LIMIT.search(t)
        if not m or TIME_CONDITION.search(t):
            continue
        limit = m.group(0)
        out.append(finding(
            rule, [ctx.q_sentence(b, m.start())],
            title="Ограничение по времени без условий измерения",
            problem=(f"Задан срок «{limit}», но не сказано, для какой доли случаев он должен выполняться (например, 95% или 99%), "
                     "при какой нагрузке и от какого до какого момента его считать. Если хотя бы один случай окажется дольше, "
                     "разработчик скажет, что требование выполнено, а тестировщик — что нет."),
            recommendation=(f"Допишите условия измерения: «{limit} для 95% событий при нагрузке до N событий в секунду; "
                            "время считается от <момента начала> до <момента окончания>», и что делать, если срок не выдержан."),
            question=ask(f"Для какой доли случаев и при какой нагрузке должен выполняться срок «{limit}»?",
                         ["95% случаев при обычной нагрузке", "99% случаев при пиковой нагрузке (указать какой)",
                          "Каждый случай без исключений"], "business"),
        ))
    return out


_INTRO_SECTION = re.compile(
    r"(?:^|›\s*)(?:\d+(?:\.\d+)*\.?\s*)?(?:введение|область применения|назначение|обзор|общее описание|описание|"
    r"цел[ьи]|предпосылки|контекст|scope|overview|purpose|introduction|background|summary)\b[^›]*$",
    re.I,
)


# a clause that states its own obligation: a modal or an explicitly normative verb
_OBLIG = re.compile(
    r"(?<![\w-])(?:долж(?:ен|на|но|ны)|обязан\w*|необходимо|требуется|следует|"
    r"обеспечива(?:ет|ют|ть)|обеспечит|"
    r"shall|must|should|is required to|are required to|needs? to|has to|have to|will)(?![\w-])",
    re.I,
)
# explicit enumeration of separate items inside one paragraph: «1) … 2) … 3)», «а) … б) …», «(i) … (ii) …»
_INLINE_ENUM = re.compile(r"(?:^|[\s:;,])\(?(?:[1-9]|[а-еa-e]|i{1,3}|iv)\)\s")
_INF = re.compile(r"(?<![\w-])[а-яё]{3,}(?:ть|ться|ти|чь)(?![\w-])", re.I)
# worked examples and explanations describe, they do not oblige
_EXAMPLE_START = re.compile(
    r"^\s*(?:\*\*|__)?\s*(?:пример\w*|например|для примера|для справки|примечани\w*|пояснени\w*|"
    r"иными словами|то есть|т\.\s?е\.|example|for example|e\.g\.|i\.e\.|note|for instance|in other words)\b",
    re.I,
)
_EXAMPLE_SECTION = re.compile(r"(?:^|›\s*)[^›]*\b(?:пример\w*|example\w*|поясн\w*|глоссари\w*|термин\w*|glossary)\b[^›]*$", re.I)
_ARITHMETIC = re.compile(r"\d\s*[-+×x*/]\s*\d[^=]{0,40}=\s*\d|\d\s*=\s*\d")


def _obligations(units: list[str]) -> int:
    return sum(len(_OBLIG.findall(u)) for u in units)


def _enumerates_actions(text: str) -> bool:
    """An explicit list of independent actions: inline markers «1) … 2) … 3)» or a modal with three or more infinitives."""
    if len(_INLINE_ENUM.findall(text)) >= 3:
        return True
    for m in _OBLIG.finditer(text):
        clause = re.split(r"[.;:!?](?:\s|$)", text[m.end():], maxsplit=1)[0]
        if len({w.lower() for w in _INF.findall(clause)}) >= 3:
            return True
    return False


def rule_non_atomic(ctx: Ctx, rule):
    out = []
    for b in ctx.req_blocks:
        if b.kind not in ("paragraph", "list_item"):
            continue
        if not b.doc_id and _INTRO_SECTION.search(b.section or ""):
            continue  # an unnumbered introduction paragraph describes, it is not one requirement
        text = _block_text(b)
        body = strip_id(text.strip())
        if _EXAMPLE_START.search(body) or _EXAMPLE_SECTION.search(b.section or "") or _ARITHMETIC.search(body):
            continue  # a worked example or an explanation, not a set of obligations
        sents = [s for s in sentences(text) if not _ARROW.search(s)]
        units = []
        for s in sents:
            units += [u for u in re.split(r";\s+", s) if len(u.split()) >= 3]
        n_obl = _obligations(units)
        strong = n_obl >= 3 or _enumerates_actions(body)
        if len(units) < 3 and not strong:
            continue
        n = max(len(units), n_obl)
        what = "обязательства" if n_obl >= 3 else "утверждения"
        if n >= 5:
            what = "обязательств" if n_obl >= 3 else "утверждений"
        f = finding(
            rule, [ctx.q_block(b)],
            title="В одном пункте несколько требований" if strong else "В одном пункте несколько утверждений",
            problem=(f"Пункт содержит {n} самостоятельных {what}. На него нельзя сослаться точечно: "
                     "если одно выполнено, а другое нет, непонятно, выполнено ли требование, "
                     "и тест по нему получается слишком широким."
                     if strong else
                     f"Пункт содержит {n} самостоятельных {what}. Возможно, это одно правило с пояснениями, "
                     "но если утверждения проверяются по отдельности, на них удобнее ссылаться под своими номерами."),
            recommendation=("Разделите пункт на отдельные требования — по одному обязательству в каждом, "
                            "с собственными номерами (например, X-2.1, X-2.2) и своим критерием приёмки."),
            question=ask("Можно ли разделить пункт на отдельные требования?",
                         ["Да — разделить, каждому дать номер", "Нет — это одно правило, переписать одним утверждением"]),
        )
        f.advisory = not strong  # several statements without separate obligations: an observation only
        out.append(f)
    return out


TEXT_RULES = {
    "vague-word": rule_vague_word,
    "open-list": rule_open_list,
    "placeholder": rule_placeholder,
    "time-without-conditions": rule_time_without_conditions,
    "non-atomic": rule_non_atomic,
}

__all__ = ["TEXT_RULES", "strip_id"]
