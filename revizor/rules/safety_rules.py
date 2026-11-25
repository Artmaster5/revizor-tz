"""Manipulation through the document and sensitive data."""
from __future__ import annotations

import re

from ..structure import INVISIBLE_CHARS
from ._common import Ctx, ask, finding, fmt_list, short, visible

_I = re.I

# Text that tells the reviewer/AI how to evaluate or to drop its instructions → CRITICAL.
STRONG = [
    re.compile(r"(?:игнорир\w*|проигнорир\w*|забудь\w*|забыть|отмени\w*|не\s+учитывай\w*|не\s+следуй\w*)\s+"
               r"(?:вс[еёи]\s+|сво[иё]\s+)?(?:предыдущ\w*|прежн\w*|ранее\s+(?:данн|получ)\w*|вышеизложенн\w*|системн\w*|сво[иё])\s+"
               r"(?:инструкц\w*|указани\w*|правил\w*|промпт\w*|настройк\w*)", _I),
    re.compile(r"\bignore\s+(?:all\s+|any\s+)?(?:the\s+|your\s+)?(?:previous|prior|above|earlier|preceding|system|original)\s+"
               r"(?:instructions?|prompts?|rules|directions|guidelines)", _I),
    re.compile(r"\bignore\s+(?:all\s+|any\s+)?(?:your|the)\s+(?:instructions?|rules|guidelines)", _I),
    re.compile(r"\bdisregard\s+(?:all\s+|any\s+)?(?:the\s+|your\s+)?(?:above|previous|prior|earlier|preceding|instructions?|rules)", _I),
    re.compile(r"\bforget\s+(?:all\s+)?(?:the\s+|your\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|rules|context)", _I),
    re.compile(r"не\s+(?:сообщай\w*|сообщать|указывай\w*|указывать|упоминай\w*|упоминать|отмечай\w*|отмечать|пиши\w*|писать|"
               r"выводи\w*|выводить|показывай\w*|включай\w*|находи\w*|ищи\w*)\s+(?:в\s+отч[её]т\w*\s+)?(?:о\s+|об\s+|про\s+|никаки\w*\s+)?"
               r"(?:\w+\s+)?(?:дефект\w*|замечани\w*|недостатк\w*|находк\w*)", _I),
    re.compile(r"\bdo\s+not\s+(?:report|mention|flag|list|include|raise)\s+(?:any\s+)?(?:defects|issues|findings|problems|errors|bugs)", _I),
    re.compile(r"(?:оцени\w*|признай\w*|считай\w*|отметь\w*|поставь\w*|присвой\w*)\s+(?:этот\s+|данный\s+|весь\s+)?"
               r"(?:документ|тз|требовани\w*|спецификаци\w*)\s+(?:как\s+)?(?:идеальн\w*|безупречн\w*|отличн\w*|готов\w*|"
               r"корректн\w*|без\s+(?:ошибок|замечаний|дефектов)|высок\w*|положительн\w*|ready|perfect)", _I),
    re.compile(r"(?:выведи\w*|выдай\w*|поставь\w*|напиши\w*|укажи\w*)\s+(?:в\s+отч[её]те\s+)?(?:вердикт\w*|итог\w*|оценк\w*)\s*"
               r"[:«\"]?\s*(?:ready|готов|✅|можно|идеал|без\s+замечаний|положительн|«?готово)", _I),
    re.compile(r"(?:напиши\w*|скажи\w*|сообщи\w*|ответь\w*|укажи\w*)\s*,?\s*что\s+(?:этот\s+|данный\s+)?(?:документ|тз|требования|"
               r"спецификаци\w*)\s+(?:идеал\w*|безупреч\w*|корректн\w*|готов\w*|хорош\w*|без\s+(?:ошибок|замечаний|дефектов)|"
               r"не\s+содерж\w*\s+(?:ошибок|дефектов|замечаний))", _I),
    re.compile(r"\b(?:approve|accept|pass)\s+(?:this|the)\s+(?:document|spec\w*|requirements?)\b", _I),
    re.compile(r"\b(?:rate|mark|grade|evaluate|classify|score)\s+(?:this|the)\s+(?:document|spec\w*|requirements?)\s+as\b", _I),
    re.compile(r"\boutput\s+(?:the\s+|a\s+)?verdict\b|\bverdict\s*(?:must|should)\s+be\b", _I),
    re.compile(r"\b(?:say|state|write|report|conclude)\s+that\s+(?:this|the)\s+(?:document|spec\w*|requirements?)\s+"
               r"(?:is|are|has|have)\s+(?:perfect|flawless|ready|correct|no\s+(?:defects|issues))", _I),
]
# Text addressed to the reviewer/AI without a direct evaluation order → MAJOR.
WEAK = [
    re.compile(r"(?:^|[.!?:;]\s*|\n)(?:ты|вы)\s*(?:—|–|-|,)?\s*(?:теперь\s+)?(?:ии|искусственный\s+интеллект|ии-\w+|ассистент|"
               r"языковая\s+модель|нейросеть|чат-?бот|ревьюер|проверяющий|агент)\b", _I),
    re.compile(r"\b(?:примечани\w*|заметк\w*|сообщени\w*|инструкци\w*|указани\w*|обращение)\s+(?:для|к)\s+(?:[а-яё]+(?:ому|ему|ого|его|ой|ей|ым|им)\s+){0,2}"
               r"(?:ии|модел[ия]|нейросет\w*|проверяющ\w*|ревьюер\w*|ии-\w+|ассистент\w*|агент\w*|бот[ау])\b", _I),
    re.compile(r"\b(?:note|message|instructions?|hint)\s+(?:to|for)\s+(?:the\s+)?(?:ai|llm|model|reviewer|assistant|agent|bot)\b", _I),
    re.compile(r"\b(?:dear|hey|hi|hello)\s+(?:ai|assistant|model|reviewer|llm|claude|gpt|chatgpt)\b", _I),
    re.compile(r"\byou\s+are\s+now\b|\byou\s+are\s+(?:an?\s+)?(?:ai|assistant|language\s+model|llm)\b", _I),
    re.compile(r"\bas\s+an\s+ai\b[^.\n]{0,40}\b(?:you|must|should|ignore|report|approve)\b", _I),
    re.compile(r"\bsystem\s+prompt\b|системн\w*\s+(?:промпт|подсказк)\w*", _I),
    re.compile(r"(?:^|[.!?:;]\s*|\n)(?:ии|ai)[- ](?:проверяющ\w*|ревьюер\w*|reviewer|агент\w*|ассистент\w*)\s*[,:!]", _I),
    re.compile(r"(?:уважаем\w*|дорог\w*)\s+(?:ии|модель|ассистент|проверяющ\w*|ревьюер)", _I),
]


# An order about the review outcome. On its own it is too common in ordinary text ("сообщи клиенту …"), so it only
# raises a text that is already addressed to the reviewer/AI (a WEAK match) to CRITICAL.
EVAL_ORDER = re.compile(
    r"\b(?:сообщи|напиши|укажи|ответь|рекомендуй|поставь|оцени|утверди|одобри|признай|пропусти|исключи|скрой|"
    r"не\s+включай|не\s+упоминай|не\s+отмечай|не\s+проверяй)\b[^.!?\n]{0,80}?"
    r"(?:замечани\w*|дефект\w*|недостатк\w*|оценк\w*|утвер(?:ди|жд)\w*|одобр\w*|вердикт\w*|отч[её]т\w*|"
    r"без\s+изменений|ошиб\w*|проблем\w*)|"
    r"\b(?:report|say|state|recommend|approve|rate|grade|skip|exclude|omit|hide|do\s+not\s+(?:report|include|mention|flag))\b"
    r"[^.!?\n]{0,80}?\b(?:findings?|issues?|defects?|problems?|approv\w*|verdict|report|rating|score|no\s+(?:issues|defects|findings))\b",
    _I,
)


def injection_hits(text: str) -> tuple[str, list[re.Match]]:
    """("CRITICAL"|"MAJOR"|"", matches) for a piece of text.

    CRITICAL: the text tells the reviewer how to evaluate or to drop its instructions — either one of the STRONG
    patterns, or a text addressed to the reviewer/AI (WEAK) that also gives an order about the outcome (EVAL_ORDER).
    MAJOR: addressed to the reviewer/AI without such an order.
    """
    t = visible(text)
    strong = [m for rx in STRONG for m in [rx.search(t)] if m]
    if strong:
        return "CRITICAL", sorted(strong, key=lambda m: m.start())
    weak = [m for rx in WEAK for m in [rx.search(t)] if m]
    if weak:
        weak.sort(key=lambda m: m.start())
        if EVAL_ORDER.search(t, weak[0].start()):
            return "CRITICAL", weak
        return "MAJOR", weak
    return "", []


def rule_prompt_injection(ctx: Ctx, rule):
    out = []
    for b in ctx.doc.blocks:
        if b.raw.lstrip().startswith("<!--"):
            continue  # hidden comments are reported by hidden-text, with the same severity
        sev, hits = injection_hits(b.text)
        if not sev:
            continue
        m = hits[0]
        quote = ctx.q_sentence(b, m.start()) if b.kind != "code" else ctx.q_block(b)
        out.append(finding(
            rule, [quote],
            title=("В документе есть указание проверяющему или ИИ, как оценивать документ" if sev == "CRITICAL"
                   else "В документе есть текст, обращённый к проверяющему или ИИ"),
            problem=(f"Фраза «{short(m.group(0).strip(), 80)}» обращена не к тем, кто будет делать систему, а к проверяющему или к "
                     "программе на основе ИИ"
                     + (" и пытается изменить результат проверки. " if sev == "CRITICAL" else ". ")
                     + "Агент её не выполнял: документ проверен по обычным правилам. Такой текст в требованиях — риск для любого "
                       "ИИ-инструмента банка, который будет читать этот документ: он может исказить оценку или поведение инструмента."),
            recommendation="Удалите этот текст из документа и выясните, кто и зачем его добавил (история изменений, автор правки).",
            question=ask("Кто добавил этот текст и зачем?",
                         ["Случайно или в шутку — удалить", "Намеренно — передать в информационную безопасность"], "security"),
            severity=sev,
        ))
    return out


_COMMENT = re.compile(r"<!--(.*?)-->", re.S)
_INVIS = re.compile("[" + INVISIBLE_CHARS + "]")
_NAMES = {
    "​": "пробел нулевой ширины", "‌": "разделитель нулевой ширины", "‍": "соединитель нулевой ширины",
    "‎": "метка направления письма", "‏": "метка направления письма", "‪": "управление направлением письма",
    "‫": "управление направлением письма", "‬": "управление направлением письма",
    "‭": "управление направлением письма", "‮": "разворот направления письма", "⁠": "невидимый соединитель",
    "⁡": "невидимый оператор", "⁢": "невидимый оператор", "⁣": "невидимый разделитель",
    "⁤": "невидимый оператор", "﻿": "невидимый пробел (BOM)",
}


def rule_hidden_text(ctx: Ctx, rule):
    out = []
    doc = ctx.doc
    if doc.fmt in ("md", "html", "txt", "docx"):
        for m in _COMMENT.finditer(doc.text):
            content = re.sub(r"\s+", " ", m.group(1)).strip()
            if not content:
                continue
            ls = doc.text.count("\n", 0, m.start()) + 1
            le = doc.text.count("\n", 0, m.end()) + 1
            blk = next((b for b in doc.blocks if b.line_start == ls and b.raw.lstrip().startswith("<!--")), None)
            quote = ctx.q_block(blk) if blk is not None else ctx.qi.from_lines(ls, le)
            sev, _ = injection_hits(content)
            out.append(finding(
                rule, [quote],
                title="Скрытый комментарий в тексте документа" + (" с указаниями для ИИ" if sev else ""),
                problem=("В документе есть HTML-комментарий: при обычном просмотре его не видно, а программы, в том числе "
                         "ИИ-инструменты, его читают. "
                         + ("В нём есть указания проверяющему или ИИ — это попытка незаметно повлиять на автоматическую проверку."
                            if sev else "Через такой текст можно незаметно изменить смысл требований или поведение автоматической проверки.")),
                recommendation="Удалите комментарий. Если в нём нужная информация — перенесите её в видимый текст как примечание.",
                question=ask("Нужен ли этот скрытый комментарий?", ["Удалить", "Сделать видимым примечанием"], "analyst"),
                severity="CRITICAL" if sev else "MINOR",
            ))
    lines_with: dict[int, list[str]] = {}
    for m in _INVIS.finditer(doc.text):
        if m.start() == 0 and m.group(0) == "﻿":
            continue
        ln = doc.text.count("\n", 0, m.start()) + 1
        lines_with.setdefault(ln, []).append(m.group(0))
    if lines_with:
        total = sum(len(v) for v in lines_with.values())
        kinds = sorted({_NAMES.get(c, "невидимый символ") for v in lines_with.values() for c in v})
        lines = sorted(lines_with)
        quotes = [ctx.qi.from_lines(ln) for ln in lines[:3] if doc.lines[ln - 1].strip()]
        bad = any(injection_hits(doc.lines[ln - 1])[0] for ln in lines)
        if quotes:
            out.append(finding(
                rule, quotes,
                title=f"Невидимые символы в тексте ({total})",
                problem=(f"В строках {fmt_list([str(x) for x in lines], 8)} есть невидимые символы ({', '.join(kinds)}). "
                         "Человек их не видит, а программы видят: из-за них одинаковые на вид слова не совпадают при поиске, "
                         "а символы разворота направления могут показывать текст не в том порядке, в каком его читает программа."),
                recommendation="Удалите невидимые символы (например, вставив текст через «вставить без форматирования») и проверьте эти строки.",
                question=ask("Откуда в тексте невидимые символы?", ["Скопированы из другой программы — удалить",
                                                                     "Добавлены намеренно — передать в ИБ"], "analyst"),
                severity="CRITICAL" if bad else "MINOR",
            ))
    return out


def rule_sensitive_data(ctx: Ctx, rule):
    doc = ctx.doc
    if not doc.masked:
        return []
    quotes = []
    for note in doc.masked:
        m = re.match(r"строка (\d+)", note)
        if m and len(quotes) < 5:
            ln = int(m.group(1))
            if 1 <= ln <= len(doc.lines) and all(q.line_start != ln for q in quotes):
                quotes.append(ctx.qi.from_lines(ln))
    if not quotes:
        return []
    return [finding(
        rule, quotes,
        title="В документе есть похожие на настоящие номера карт или ИИН",
        problem=(f"Найдено значений: {len(doc.masked)} ({fmt_list(doc.masked, 4)}). Они проходят проверку контрольной суммы, как "
                 "настоящие номера карт или ИИН. Если это данные реальных клиентов, документ нельзя пересылать и хранить без "
                 "защиты, а тесты на таких данных нарушают требования к защите персональных и платёжных данных. "
                 "В отчёте и в запросах к модели эти значения замаскированы."),
        recommendation=("Замените их заведомо тестовыми значениями (тестовые номера карт платёжных систем, ИИН из тестового контура) "
                        "и выясните, откуда реальные данные попали в документ."),
        question=ask("Это реальные данные клиентов?", ["Нет, тестовые — всё равно заменить на заведомо фиктивные",
                                                     "Да — удалить и сообщить в информационную безопасность"], "security"),
    )]


SAFETY_RULES = {
    "prompt-injection": rule_prompt_injection,
    "hidden-text": rule_hidden_text,
    "sensitive-data": rule_sensitive_data,
}
