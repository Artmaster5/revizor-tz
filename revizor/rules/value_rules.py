"""Rules on quantities and thresholds: rounding, units, steps, conflicting boundaries, behaviour at a limit.

Every rule here is silent when the document answers the question anywhere (a general statement counts).
One finding per rule per document (rule ``threshold-conflict``: one per number); the first places are quoted.
"""
from __future__ import annotations

import re

from ._common import Ctx, ask, finding, short, visible
from .set_rules import _NO_UNIT
from .time_rules import emit, doc_has, scan_blocks, sentence_at

_NUM = r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_CUR = re.compile(r"₸|тенге|\bтг\b|KZT|тиын|тийын|руб|₽|\$|USD|EUR|€|долл|евро|\bсом\b|RUB|CNY|\bUSDT\b|валют|tenge|currency|dollars?|euros?|теңге|\bcents?\b", re.I)


def _norm_num(s: str) -> float | None:
    s = re.sub(r"[   ]", "", s).replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _neighborhood(ctx: Ctx, b) -> str:
    """Text of the block and of the block right after it (a rule is often continued in the next line)."""
    out = [visible(b.text)]
    blocks = ctx.doc.blocks
    try:
        k = blocks.index(b)
    except ValueError:
        return out[0]
    if k + 1 < len(blocks):
        nb = blocks[k + 1]
        if nb.kind not in ("heading", "code") and nb.line_start - b.line_end <= 2:
            out.append(visible(nb.text))
    return " ".join(out)


# --- 7. percent or division of money without a rounding rule -------------------------------------------------------

_MONEY_WORD = re.compile(
    r"сумм\w+|комисси\w+|кэшбэк\w*|кешбэк\w*|вознагражд\w+|платеж\w*|платёж\w*|стоимост\w+|штраф\w*|пен[ия]\b|неустойк\w+|процентн\w+\s+ставк\w+"
    r"|годов\w+\s+ставк\w+|тенге|₸|KZT|\bcashback\b|\bfee\b|\bamount\b|\bcommission\b|\binterest\b|\bpremium\b|премия|взнос\w*|выплат\w+|остат\w+",
    re.I)
_PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|процент\w*)")
_NOT_MONEY_PCT = re.compile(r"\bof\s+(?:purchases|requests|operations|transactions|cases|customers|users)|\bno\s+later\s+than|\bseconds?\b|секунд|\bshare\s+of|доля\s+клиент|доступен|доступна|доступно|доступны|времени\b|доступност|обращени|конверси|точност|покрыт|\bSLA\b|uptime|availability|нагрузк|загрузк|успешн\w+\s+(?:запрос|операц)|ошибок", re.I)
_CONVERT = re.compile(
    r"по\s+курсу|конвертац\w+|пересч[её]т\w*\s+(?:по|в|суммы)|курс\w*\s+(?:покупки|продажи|обмена|конвертации|казначейства)|exchange\s+rate|\bconverted\b|\bconversion\s+rate\b",
    re.I)
_SPLIT = re.compile(r"пропорциональн\w+|поровну|равными\s+частями|на\s+равные\s+части|делится\s+на|распределяется\s+(?:между|на)", re.I)
_ROUNDING = re.compile(
    r"округл|rounding|truncat|\bfloor\b|\bceil|\bround(?:ed|s)?\b|до\s+(?:ближайш|целого|тиын|копеек|цента|центов|сотых|десятых|тысячных)"
    r"|с\s+точностью\s+до|знак\w*\s+после\s+запят|тиын|тийын|копе[ейк]|\bcents?\b|minor\s+units?|\btiyn\b|banker|отбрасыва|усечени|в\s+пользу\s+(?:клиента|банка)"
    r"|decimal\s+places?|\d\s+знак\w*\s+после|двух\s+знаков|наименьш\w+\s+(?:единиц|денежн)|жуықт|дөңгелект",
    re.I)


def rule_rounding_missing(ctx: Ctx, rule):
    if doc_has(ctx, _ROUNDING):
        return []
    hits = []
    for b, t in scan_blocks(ctx):
        for m in _PERCENT.finditer(t):
            loc = sentence_at(b, t, m.start())
            if _MONEY_WORD.search(loc) and not _NOT_MONEY_PCT.search(loc):
                hits.append((b.line_start, m.start(), b))
        for rx in (_CONVERT, _SPLIT):
            for m in rx.finditer(t):
                loc = sentence_at(b, t, m.start())
                if _MONEY_WORD.search(loc):
                    hits.append((b.line_start, m.start(), b))
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Расчёт денег без правила округления",
        problem=("Сумма считается как процент, доля или по курсу, но нигде не сказано, как округлять результат и до какой единицы "
                 "(до целого тенге, до тиына, в какую сторону). У 1 % от 1 234 ₸ получается 12,34 ₸; разработчик округлит вниз, "
                 "банк — вверх, а тестировщику нечем доказать, кто прав. На большом потоке копейки превращаются в разницу в учёте."),
        recommendation="Добавьте правило: «Суммы округляются до тиына по математическим правилам; при делении остаток отдаётся последней части» (или укажите своё).",
        question=ask("Как округлять рассчитанные суммы: до какой единицы и в какую сторону?",
                     ["До тиына, математически", "До целого тенге, вниз", "До целого тенге, вверх"]),
        severity="MAJOR",
    )


# --- 8. a number with no unit ----------------------------------------------------------------------------------------------

_AMOUNT_STRONG = (r"сумм\w*|комисси\w+|штраф\w*|стоимост\w+|баланс\w*|кэшбэк\w*|кешбэк\w*|взнос\w*|тариф\w*|плат[аы]\b|платё?ж\w*|"
                  r"amount|fee|price|cost|balance")
_AMOUNT_WEAK = r"лимит\w*|порог\w*|минимум\w*|максимум\w*|остат\w+|limit|threshold"
_MONEY_CTX = re.compile(r"сумм|перевод|списан|пополнен|снят|платёж|платеж|выдач|расход|оборот|покупк|операци\w+\s+на|amount|transfer|payment", re.I)
_COUNT_CTX = re.compile(r"попыт|запрос|символ|ошиб|раз\b|шт\b|количеств|цифр|знак|уровн|кодов|сообщени|звонк|карт\b|счет|счёт|устройств|attempt|request|character|digit", re.I)
_AMOUNT_NO_UNIT = re.compile(
    r"\b(?P<noun>" + _AMOUNT_STRONG + r"|" + _AMOUNT_WEAK + r")\b(?P<gap>(?:\s+[^\s\d—:=.;,()|]+){0,3}?)"
    r"\s*(?:[—:=-]\s*|\s)(?:не\s+более|не\s+менее|не\s+ниже|не\s+выше|не\s+превышает|составляет|равн\w+|в\s+размере|до|от|max\.?|min\.?|up\s+to|of|is)?\s*"
    r"(?P<nums>(?:" + _NUM + r")(?:\s*(?:до|по|–|—|-|and|to)\s*(?:" + _NUM + r"))?)(?P<tail>[^\n]{0,30})",
    re.I)
_CURRENCY_GLOBAL = re.compile(
    r"(?:все|любые|вс[её])\s+(?:денежные\s+)?(?:суммы|значения|лимиты|тарифы|комиссии)[^.]{0,60}(?:тенге|KZT|₸|тиын|tenge|currency)"
    r"|(?:суммы|amounts?|lim[ie]ts|values)\s+(?:указан\w+|задан\w+|are|is|in|expressed)[^.]{0,40}(?:тенге|KZT|₸|tenge|currency)"
    r"|валют\w*\s*(?:\||:|—|-)\s*(?:тенге|KZT|₸)|currency\s*(?:\||:|—|-)\s*(?:KZT|tenge)|all\s+(?:amounts|sums|limits)[^.]{0,40}(?:KZT|tenge)",
    re.I)
_DURATION_NO_UNIT = re.compile(
    r"(?:действует|действителен|действительн\w+|блокируется\s+на|блокировка\s+на|заблокирован\w*\s+на|ожидани\w+|пауза|\bчерез\b|\bкаждые\b|раз\s+в|"
    r"интервал\w*\s+между|длительност\w+|срок\s+(?:жизни|действия))\s*(?:—|-|:|=)?\s*(?P<num>\d+(?:[.,]\d+)?)(?=\s*(?:[.;,)](?:\s|$)|$))",
    re.I)
_SIZE_NO_UNIT = re.compile(
    r"(?:размер\w*|объ[её]м\w*)(?:\s+(?:файла|вложения|запроса|ответа|изображения|документа|тела|загрузки))\s*(?:—|-|:|=)?\s*"
    r"(?:не\s+более|не\s+превышает|до|не\s+менее|составляет)?\s*(?P<num>" + _NUM + r")(?=\s*(?:[.;,)](?:\s|$)|$))",
    re.I)


def rule_value_without_unit(ctx: Ctx, rule):
    amount_global = doc_has(ctx, _CURRENCY_GLOBAL)
    hits = []
    kinds = set()
    for b, t in scan_blocks(ctx):
        if not amount_global and not any(_CUR.search(h) for h in b.header):
            for m in _AMOUNT_NO_UNIT.finditer(t):
                loc = sentence_at(b, t, m.start())
                if _CUR.search(loc) or "%" in loc or "процент" in loc.lower():
                    continue
                noun = m.group("noun").lower()
                weak = re.fullmatch(_AMOUNT_WEAK, noun, re.I) is not None
                gap = m.group("gap") or ""
                if weak and not _MONEY_CTX.search(gap):
                    continue
                if _COUNT_CTX.search(gap):
                    continue
                tail = (m.group("tail") or "").lstrip()
                if re.match(r"(?:включительно\s*)?(?:$|[.;,)|])", tail) and re.match(r"(?:включительно\s*)?(?:$|[.;)|]|,\s)", tail):
                    nums = [x for x in re.findall(_NUM, m.group("nums"))]
                    if any((_norm_num(x) or 0) >= 10 for x in nums):  # a lone 1, 2, 3 is a count, not an amount
                        hits.append((b.line_start, m.start(), b))
                        kinds.add("amount")
        for m in _DURATION_NO_UNIT.finditer(t):
            if _NO_UNIT.search(t) or re.search(r"\b(?:раз|шт)\b", sentence_at(b, t, m.start())):
                continue
            hits.append((b.line_start, m.start(), b))
            kinds.add("time")
        for m in _SIZE_NO_UNIT.finditer(t):
            hits.append((b.line_start, m.start(), b))
            kinds.add("size")
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    parts = []
    if "amount" in kinds:
        parts.append("суммы без валюты")
    if "time" in kinds:
        parts.append("сроки без единицы времени")
    if "size" in kinds:
        parts.append("размеры без единицы объёма")
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Число без единицы измерения: " + ", ".join(parts),
        problem=("Рядом с числом не написано, в чём оно измеряется: " + ", ".join(parts) + ". "
                 "100 000 может быть тенге, тиынами или долларами, 15 — секундами или минутами, 5 — килобайтами или мегабайтами; "
                 "разница в сотни и тысячи раз. Разработчик заложит одно, а тестировщик проверит другое."),
        recommendation="Допишите единицу рядом с числом («100 000 ₸», «15 минут», «5 МБ») или добавьте общее правило: «Все суммы — в тенге».",
        question=ask("В каких единицах задано значение: «" + short(_first_text(ctx, hits), 70) + "»?",
                     ["Тенге", "Секунды", "Минуты", "Мегабайты"]),
    )


def _first_text(ctx: Ctx, hits) -> str:
    b, pos = hits[0][2], hits[0][1]
    return ctx.q_sentence(b, pos).text


# --- 9. a configurable number with no step ----------------------------------------------------------------------------------

_SETTER = re.compile(
    r"(?:клиент|пользовател\w+|администратор\w*|оператор\w*|продавец|мерчант|кассир|аналитик|владелец|держатель\s+карты)\s+(?:может\s+|вправе\s+)?"
    r"(?:задаёт|задает|задать|выбирает|выбрать|указывает|указать|вводит|ввести|устанавливает|установить|настраивает|настроить|меняет|изменить)"
    r"|(?:настраивается|задаётся|задается|устанавливается|выбирается|указывается|вводится|конфигурируется|регулируется)"
    r"|(?:user|admin\w*|customer|merchant|operator)\s+(?:can\s+|may\s+)?(?:set|choose|select|enter|configure|specif\w+)|is\s+(?:configurable|set\s+by)"
    r"|(?:клиент|пайдаланушы)\s+(?:таңдайды|енгізеді|белгілейді)",
    re.I)
_RANGE = re.compile(r"(?:\bот\b|\bfrom\b|\bbetween\b)\s+(?P<a>" + _NUM + r")\s*(?:₸|тенге|KZT|%|[а-яa-z]+)?\s*(?:\bдо\b|\bto\b|\band\b|\bпо\b)\s+(?P<b>" + _NUM + r")(?![:.]\d)(?!\s*(?:числа|-?го))", re.I)
_STEP = re.compile(
    r"шаг\w*|кратн\w+|целое|целым|целых|целого|целые|integer|\bwhole\b|\bstep\b|multiples?\s+of|increment|дискретн|с\s+точностью|\d+\s+знак\w*\s+после"
    r"|decimal|только\s+значения|одно\s+из\s+значений|из\s+списка|из\s+перечн|из\s+справочника|тиын|копе[ейк]|десятичн|бүтін|қадам|\bselect\w*\s+from\s+list|list\s+of\s+values|\bchoose\s+from\b",
    re.I)
_NOT_QUANTITY = re.compile(r"возраст|дат[аыу]\b|год[аы]?\b|лет\b|\bчасов\b|час[аов]?\b\s*(?:дня|ночи)|:\d\d", re.I)


def rule_step_missing(ctx: Ctx, rule):
    hits = []
    for b, t in scan_blocks(ctx):
        for m in _RANGE.finditer(t):
            a, c = _norm_num(m.group("a")), _norm_num(m.group("b"))
            if a is None or c is None or a >= c:
                continue
            loc = sentence_at(b, t, m.start())
            if not _SETTER.search(loc):
                continue
            if _NOT_QUANTITY.search(t[max(0, m.start() - 20): m.end() + 12]):
                continue
            if _STEP.search(_neighborhood(ctx, b)):
                continue
            hits.append((b.line_start, m.start(), b))
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Значение задаётся в диапазоне, но не сказано, с каким шагом",
        problem=("Пользователь или администратор выбирает число внутри диапазона («от X до Y»), но не сказано, какие значения допустимы: любые, "
                 "только целые, с копейками или кратные какому-то шагу (например, 100 ₸). Разработчик сделает поле для любых чисел, "
                 "а тестировщик не поймёт, какое значение 'между' считать ошибкой."),
        recommendation="Допишите шаг или допустимые значения: «целое число», «кратно 100», «с точностью до 0,01», «из списка 5, 10, 15».",
        question=ask("Какие значения допустимы внутри диапазона: любые, только целые или с определённым шагом?",
                     ["Только целые числа", "С точностью до тиына", "Кратно определённому шагу — указать"]),
    )


# --- 10. the same threshold with conflicting comparison words ---------------------------------------------------------

_OPS = (
    r"не\s+менее|не\s+ниже|не\s+меньше|минимум|начиная\s+с|≥|>=|at\s+least|\bminimum\b|"           # ge
    r"не\s+более|не\s+выше|не\s+больше|не\s+свыше|не\s+превышает(?:\s+значени\w+)?|максимум|≤|<=|at\s+most|not\s+more\s+than|no\s+more\s+than|\bmaximum\b|"  # le
    r"(?<!не\s)более|свыше|(?<!не\s)выше|(?<!не\s)больше|превышает|превышающ\w+|>|more\s+than|\bover\b|\babove\b|exceeds?|greater\s+than|"  # gt
    r"(?<!не\s)менее|(?<!не\s)ниже|(?<!не\s)меньше|<|less\s+than|\bbelow\b|\bunder\b|fewer\s+than|"  # lt
    r"\bот\b|\bfrom\b|"  # ge
    r"\bдо\b|\bup\s+to\b|\buntil\b"  # upto
)
_THRESH = re.compile(r"(?P<op>" + _OPS + r")\s*(?:чем\s+)?(?P<num>" + _NUM + r")(?P<tail>(?:\s*(?:₸|%|[A-Za-zА-Яа-яЁё]+))?(?:\s+[A-Za-zА-Яа-яЁё]+)?)", re.I)
_GE = re.compile(r"не\s+менее|не\s+ниже|не\s+меньше|минимум|начиная\s+с|≥|>=|at\s+least|minimum|^от$|^from$", re.I)
_LE = re.compile(r"не\s+более|не\s+выше|не\s+больше|не\s+свыше|не\s+превышает|максимум|≤|<=|at\s+most|not\s+more|no\s+more|maximum", re.I)
_GT = re.compile(r"^(?:более|свыше|выше|больше|превышает|превышающ\w+|>|more\s+than|over|above|exceeds?|greater\s+than)$", re.I)
_LT = re.compile(r"^(?:менее|ниже|меньше|<|less\s+than|below|under|fewer\s+than)$", re.I)
_UPTO = re.compile(r"^(?:до|up\s+to|until)$", re.I)
_INCL_WORD = re.compile(r"включительно|inclusive|и\s+(?:более|выше|менее|ниже)|or\s+(?:more|less)", re.I)
_STOP_STEMS = {"клиен", "систе", "долже", "может", "должн", "требо", "пользо", "случа", "значе", "после", "перед", "более", "менее", "только", "также", "будет",
               "быть", "когда", "если", "иначе", "которы", "котор", "запре", "допус", "в", "услов", "прило", "прави", "порог"}
_UNIT_CLASS = [
    ("KZT", re.compile(r"^(?:₸|тенге|тг|kzt)$", re.I)),
    ("%", re.compile(r"^(?:%|процент\w*)$", re.I)),
    ("day", re.compile(r"^(?:дн\w*|календарн\w*|рабоч\w*|сут\w*|days?)$", re.I)),
    ("month", re.compile(r"^(?:месяц\w*|мес|months?)$", re.I)),
    ("year", re.compile(r"^(?:лет|год\w*|years?)$", re.I)),
    ("hour", re.compile(r"^(?:час\w*|ч|hours?)$", re.I)),
    ("min", re.compile(r"^(?:мин\w*|minutes?)$", re.I)),
    ("sec", re.compile(r"^(?:сек\w*|с|seconds?)$", re.I)),
]


def _unit_of(tail: str) -> str:
    toks = re.findall(r"₸|%|[A-Za-zА-Яа-яЁё]+", tail)
    for tok in toks[:2]:
        for name, rx in _UNIT_CLASS:
            if rx.match(tok):
                return name
    return toks[0].lower()[:5] if toks and len(toks[0]) > 3 else ""


def _kind(op: str, inclusive: bool) -> str:
    o = re.sub(r"\s+", " ", op.strip().lower())
    if _GT.match(o):
        return "ge" if inclusive else "gt"
    if _LT.match(o):
        return "le" if inclusive else "lt"
    if _UPTO.match(o):
        return "le" if inclusive else "upto"
    if _LE.match(o):
        return "le"
    if _GE.match(o):
        return "ge"
    return ""


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[а-яёa-z]{5,}", text.lower())} - _STOP_STEMS


def rule_threshold_conflict(ctx: Ctx, rule):
    from ._common import sentence_span
    occ: dict[tuple[float, str], list[tuple]] = {}
    for b in ctx.doc.blocks:
        if b.kind in ("code", "heading"):
            continue
        t = visible(b.text)
        for m in _THRESH.finditer(t):
            val = _norm_num(m.group("num"))
            if val is None:
                continue
            after = t[m.end("num"): m.end("num") + 3]
            if re.match(r"[:.]\d", after) or re.match(r"\s*-?(?:го|е)\b|\s+числа", t[m.end("num"): m.end("num") + 8]):
                continue  # a clock time, a date or a day of month
            inclusive = bool(_INCL_WORD.search(t[m.end("num"): m.end("num") + 40]))
            kind = _kind(m.group("op"), inclusive)
            if not kind:
                continue
            s, e = sentence_span(b, m.start())
            unit = _unit_of(m.group("tail") or "")
            occ.setdefault((val, unit), []).append((kind, b, m.start(), t[s:e], inclusive))
    out = []
    for (val, unit), items in sorted(occ.items(), key=lambda kv: kv[1][0][1].line_start):
        pair = None
        for k1, k2 in (("gt", "ge"), ("lt", "le"), ("upto", "le")):
            a = [x for x in items if x[0] == k1]
            c = [x for x in items if x[0] == k2]
            for x in a:
                for y in c:
                    if x[1] is y[1] and abs(x[2] - y[2]) < 80:
                        continue  # one sentence with both words is a range, not two rules
                    if (k1, k2) == ("upto", "le") and not (re.search(r"включительно", y[3], re.I) or re.search(r"не\s+более|at most|no more", y[3], re.I)):
                        continue
                    shared = _stems(x[3]) & _stems(y[3])
                    if not shared or x[3] == y[3]:
                        continue
                    pair = (k1, k2, x, y)
                    break
                if pair:
                    break
            if pair:
                break
        if not pair:
            continue
        k1, k2, x, y = pair
        words = {"gt": "«более»", "ge": "«от» или «не менее»", "lt": "«менее»", "le": "«не более» или «включительно»", "upto": "«до»"}
        shown = f"{int(val) if val == int(val) else val}"
        num = shown if val < 1000 else f"{int(val):,}".replace(",", " ")
        example = ""
        if k1 in ("gt", "lt"):  # strict against inclusive: exactly at the border one rule holds and the other does not
            example = (f" Пример: при значении ровно {num} правило «{short(x[3], 50)}» не действует, "
                       f"а правило «{short(y[3], 50)}» действует.")
        fs = finding(
            rule, [ctx.q_sentence(x[1], x[2]), ctx.q_sentence(y[1], y[2])],
            title=f"Граница {num} описана по-разному: {words[k1]} и {words[k2]}",
            problem=(f"Число {num} служит границей в двух местах, но с разным смыслом: в одном месте — {words[k1]}, в другом — {words[k2]}. "
                     f"Значение ровно {num} в одном месте попадает внутрь, в другом остаётся снаружи. "
                     "Разработчик реализует одно, тестировщик проверит другое, и клиент на границе получит разное поведение." + example),
            recommendation=f"Решите, входит ли ровно {num} в диапазон, и перепишите оба места одинаково: «не менее {num}» / «менее {num}» или «не более {num}» / «более {num}».",
            question=ask(f"Входит ли значение ровно {num} в границу — «{short(x[3], 60)}» или «{short(y[3], 60)}»?",
                         ["Входит (включительно)", "Не входит (строго больше или меньше)"]),
            severity="MAJOR",
        )
        out.append(fs)
    return out


# --- 11. a limit with no behaviour at the limit ------------------------------------------------------------------------

_LIMIT = re.compile(
    r"(?:не\s+более|максимум|не\s+свыше|не\s+превышает|лимит\w*(?:\s+\w+){0,2}?\s*(?:—|-|:)?)\s*(?P<num>\d+)\s*(?P<noun>попыт\w+|запрос\w*|неверн\w+\s+(?:ввод\w*|попыт\w+)|ошиб\w+|обращени\w+|смс|sms|отправок|attempts?|requests?)"
    r"|(?:not\s+more\s+than|no\s+more\s+than|at\s+most|up\s+to|maximum\s+of)\s+(?P<num2>\d+)\s+(?P<noun2>attempts?|requests?|retries)",
    re.I)
_BEHAVIOUR = re.compile(
    r"блокир\w+|заблок\w+|отклон\w+|отказ\w*|ошибк\w+|сообщени\w+|возвраща\w+|не\s+принима\w+|не\s+создаётся|не\s+создается|не\s+выполня\w+|не\s+позвол\w+|нельзя"
    r"|обреза\w+|усека\w+|запрещ\w+|ограничива\w+|недоступ\w+|превыш\w+|исчерп\w+|достижени\w+|\b4\d\d\b|\b429\b|reject\w*|\bblock\w*|\bdeni\w+|\block\w*|\berror\b|exceed\w*|refus\w+"
    r"|тоқтат|бұғатта|қате|қабылданбайды",
    re.I)


def rule_limit_no_behaviour(ctx: Ctx, rule):
    sentences_all = _all_sentences(ctx)
    hits = []
    for b, t in scan_blocks(ctx):
        for m in _LIMIT.finditer(t):
            noun = (m.group("noun") or m.group("noun2") or "").lower()
            stem = re.sub(r"\s+", " ", noun)[:5]
            if "неверн" in noun:
                stem = "неверн"
            near = _neighborhood(ctx, b)
            loc = sentence_at(b, t, m.start())
            if _BEHAVIOUR.search(loc) or _BEHAVIOUR.search(near):
                continue
            if any(_BEHAVIOUR.search(s) and stem in s.lower() for s in sentences_all if s != loc):
                continue
            hits.append((b.line_start, m.start(), b))
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Лимит задан, но не сказано, что происходит при его превышении",
        problem=("Есть ограничение («не более N попыток» или «не более N запросов»), но нигде не сказано, что делает система, когда лимит исчерпан: "
                 "показывает ошибку, блокирует на время, отклоняет операцию или молча обрезает. Разработчик придумает своё поведение, "
                 "а тестировщик не сможет описать проверку для (N+1)-го случая."),
        recommendation="Допишите результат при превышении: код и текст ошибки, срок блокировки, что видит клиент и как лимит снимается.",
        question=ask("Что происходит с N+1-й попыткой или запросом: ошибка, блокировка на время, отказ?",
                     ["Отказ с сообщением об ошибке", "Блокировка на время — указать срок", "Операция обрезается до лимита"]),
    )


def _all_sentences(ctx: Ctx) -> list[str]:
    cache = getattr(ctx, "_value_sentences", None)
    if cache is not None:
        return cache
    out = []
    for b in ctx.doc.blocks:
        if b.kind in ("code", "heading"):
            continue
        t = visible(b.text)
        out.extend(x for x in re.split(r"(?<=[.!?])\s+|\s\|\s", t) if x)
    try:
        ctx._value_sentences = out
    except Exception:
        pass
    return out


VALUE_RULES = {
    "rounding-missing": rule_rounding_missing,
    "value-without-unit": rule_value_without_unit,
    "step-missing": rule_step_missing,
    "threshold-conflict": rule_threshold_conflict,
    "limit-no-behaviour": rule_limit_no_behaviour,
}
