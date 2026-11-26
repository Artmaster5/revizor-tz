"""Rules on time: clock time and time zone, period boundaries, kinds of days, month length, undefined periods.

Every rule here is silent when the document answers the question anywhere (a general statement counts).
One finding per rule per document; the quotes are the first places where the question arises.
"""
from __future__ import annotations

import re

from ._common import Ctx, ask, finding, sentence_span, visible

MAX_QUOTES = 5

_MONTHS = r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
_HIST_SECTION = re.compile(r"истори\w+\s+изменен|версии документа|change\s?log|revision|лист согласован", re.I)


def scan_blocks(ctx: Ctx):
    """Blocks where a rule may be stated: requirement blocks without code, headings and the change history."""
    for b in ctx.req_blocks:
        if b.kind in ("code", "heading"):
            continue
        if _HIST_SECTION.search(b.section or ""):
            continue
        yield b, visible(b.text)


def sentence_at(b, text: str, pos: int) -> str:
    s, e = sentence_span(b, pos)
    return text[s:e]


def emit(ctx: Ctx, rule, hits: list, *, title: str, problem: str, recommendation: str, question,
         severity: str | None = None):
    """One grouped finding: up to MAX_QUOTES quotes, the rest is counted in the text."""
    quotes = []
    seen = set()
    for b, pos in hits:
        q = ctx.q_sentence(b, pos)
        key = (q.line_start, q.text)
        if key in seen:
            continue
        seen.add(key)
        quotes.append(q)
    if not quotes:
        return []
    more = len(quotes) - MAX_QUOTES
    shown = quotes[:MAX_QUOTES]
    if more > 0:
        problem += f" Таких мест в документе {len(quotes)}; показаны первые {MAX_QUOTES}, ещё {more} — по тому же вопросу."
    return [finding(rule, shown, title=title, problem=problem, recommendation=recommendation,
                    question=question, severity=severity)]


def doc_has(ctx: Ctx, pattern: re.Pattern) -> bool:
    return bool(pattern.search(visible(ctx.doc.text)))


# --- 1. clock time or date boundary without a time zone ---------------------------------------------

_CLOCK = re.compile(r"(?<![\d:.T\-/])(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?![\d:])")
_DATE_BOUND = re.compile(
    r"\b(?:в\s+)?полночь\b|\bполуночи\b"
    r"|до\s+конца\s+(?:рабочего\s+|операционного\s+|календарного\s+)?(?:дня|суток)"
    r"|на\s+следующий\s+(?:календарный\s+|рабочий\s+)?день|следующего\s+(?:календарного\s+|рабочего\s+)?дня"
    r"|предыдущ\w+\s+календарн\w+\s+дн\w+"
    r"|в\s+день\s+(?:покупки|оплаты|операции|списания|подачи|обращения|открытия|закрытия|создания|выдачи|получения|платежа|заявки)"
    r"|календарн(?:ый|ого|ым|ые|ых)\s+(?:день|дня|дню|сутки|суток)"
    r"|\bmidnight\b|\bnext\s+(?:calendar\s+|business\s+|working\s+)?day\b|\bend\s+of\s+(?:the\s+)?day\b"
    r"|\bon\s+the\s+day\s+of\b|\bcalendar\s+day\b"
    r"|келесі\s+күні|ертеңгі\s+күні|түн\s+ортасы|күн\s+соңына\s+дейін",
    re.I,
)
_TZ_WORDS = re.compile(
    r"\bUTC\b|\bGMT\b|\bMSK\b|\bМСК\b|Asia/\w+|часов\w+\s+пояс|тайм-?зон|time\s?zone|уақыт\s+белдеу"
    r"|по\s+времени\s+(?:Астан|Алматы|Москв|сервер|клиент|банк|пользовател|отправител|получател|мерчант)"
    r"|(?:Астан\w*|Алматы\w*|Almaty|Astana)\s+(?:врем\w*|time)|(?:врем\w*|time)\s+(?:Астан|Алматы|Almaty|Astana)"
    r"|(?:Астан\w*|Алматы\w*)\s+уақыт\w*"
    r"|(?:дат\w*|врем\w*|час\w*|месяц\w*|день|сутк\w*)[^.\n|]{0,40}\bв\s+(?:Алматы|Астане)"
    r"|(?:местн|серверн)\w+\s+врем\w+|врем\w+\s+(?:сервера|клиента|банка|пользователя|отправителя|получателя|мерчанта)"
    r"|(?:local|server|client)\s+time|жергілікті\s+уақыт|(?:сервер|клиент)\s+уақыты"
    r"|\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z\b|[+-]\d{2}:?\d{2}\b)|UTC\s?[+-]\s?\d|GMT\s?[+-]\s?\d|\(UTC",
    re.I,
)


def rule_time_zone_missing(ctx: Ctx, rule):
    if doc_has(ctx, _TZ_WORDS):
        return []
    hits = []
    for b, t in scan_blocks(ctx):
        for rx in (_CLOCK, _DATE_BOUND):
            for m in rx.finditer(t):
                hits.append((b.line_start, m.start(), b))
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Время или граница дня задана без часового пояса",
        problem=("В требованиях есть привязка к времени суток или к границе дня («в 00:00», «на следующий день», «в день покупки»), "
                 "но нигде не сказано, по какому часовому поясу это считается. Клиент, сервер и банк могут жить в разных поясах, "
                 "поэтому разработчик и тестировщик выберут разное, а граница суток сдвинется на несколько часов."),
        recommendation="Добавьте одно общее правило: «Все даты и время — по времени Астаны (UTC+5)» или укажите пояс в каждом месте.",
        question=ask("По какому часовому поясу считаются время и границы суток в документе?",
                     ["Время Астаны (UTC+5)", "UTC", "Местное время клиента"]),
    )


# --- 2. a period boundary without «включительно / не включая» ---------------------------------------------

_INCL_LOCAL = re.compile(
    r"включительно|не\s+включая|\bвключая\b|исключая|не\s+считая|\bсчитая\b|в\s+том\s+числе|не\s+входит|\bвходит\b"
    r"|включается|не\s+учитывается|\bучитывается|считается\s+(?:первым|последним)|перв\w+\s+день|inclusive|exclusive"
    r"|\bincluding\b|\bexcluding\b|қоса|қоспағанда",
    re.I,
)
_INCL_GLOBAL = re.compile(
    r"(?:границ\w+|начал\w+\s+и\s+конец|дат\w+\s+(?:начала|окончания))[^.]{0,50}(?:включ|входят|включают)"
    r"|(?:отсч[её]т\w*|отсчитыва\w+|исчисля\w+)[^.]{0,40}(?:с\s+даты|со\s+дня|с\s+дня|с\s+момента|со\s+следующего|начинается)"
    r"|день\s+(?:события|покупки|оплаты|операции|подачи|начала|открытия)[^.]{0,30}(?:не\s+)?(?:входит|считается|учитывается|является)"
    r"|(?:period|deadline)s?\s+(?:are|is)\s+(?:counted|inclusive)|counting\s+(?:starts|begins)",
    re.I,
)
_PERIOD = [
    re.compile(r"\bс\s+\d{1,2}(?:-?(?:го|е|ого))?\s+по\s+\d{1,2}(?:-?(?:е|го|ое|ого))?\s+(?:числ\w+|" + _MONTHS + r")", re.I),
    re.compile(r"\bс\s+\d{1,2}\.\d{1,2}\.\d{2,4}\s+по\s+\d{1,2}\.\d{1,2}\.\d{2,4}", re.I),
    re.compile(r"\bдо\s+\d{1,2}(?:-?(?:го|е))?\s+числа", re.I),
    re.compile(r"\b(?:в\s+течение|в\s+срок\s+не\s+более|не\s+позднее\s+чем\s+через|не\s+позднее\s+чем\s+за)\s+(?:\d+|[а-яё]+)\s*(?:\(\w+\)\s*)?"
               r"(?:календарн\w+\s+|рабоч\w+\s+|банковск\w+\s+)?(?:дн(?:ей|я|ь)|суток|сутки|недел\w+)\s+(?:с|со|после|от)\s+\w+", re.I),
    re.compile(r"\bwithin\s+\d+\s+(?:calendar\s+|business\s+|working\s+)?(?:days?|weeks?)\s+(?:of|from|after)\s+\w+", re.I),
]


def rule_period_boundary(ctx: Ctx, rule):
    if doc_has(ctx, _INCL_GLOBAL):
        return []
    hits = []
    for b, t in scan_blocks(ctx):
        for rx in _PERIOD:
            for m in rx.finditer(t):
                local = sentence_at(b, t, m.start())
                if _INCL_LOCAL.search(local):
                    continue
                hits.append((b.line_start, m.start(), b))
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Не сказано, входит ли граничный день в срок или период",
        problem=("Срок или период задан границами («с 1 по 15 число», «до 5 числа», «в течение 30 дней с даты покупки»), "
                 "но не сказано, входит ли граничный день в срок. Если клиент пришёл в последний день, разработчик может принять заявку, "
                 "а тестировщик — ждать отказа; разница в один день меняет деньги и сроки."),
        recommendation="Допишите «включительно» или «не включая» либо добавьте общее правило: «День события в срок входит / не входит».",
        question=ask("Входит ли граничный день (или момент) в срок и период?",
                     ["Да, включительно", "Нет, не включая"]),
    )


# --- 3. «N дней» without the kind of days -------------------------------------------------------------

_NUM_WORDS = (r"(?:один|одного|два|двух|три|трёх|трех|четыре|четырёх|четырех|пять|шесть|семь|восемь|девять|десять|"
              r"пятнадцать|двадцать|тридцать|сорок|шестьдесят|девяносто)")
_DAYS_RU = re.compile(
    r"(?<![\d.,:])(?P<n>\d{1,3}|" + _NUM_WORDS + r")\s*(?:\(\w+\)\s*)?(?P<kind>(?:календарн|рабоч|банковск|операционн)\w+\s+)?"
    r"дн(?:ей|я|ь|ям|ях)\b(?!\.?\s*\()", re.I)
_DAYS_EN = re.compile(r"(?<![\d.,:])(?P<n>\d{1,3})[\s-]+(?P<kind>(?:calendar|business|working|banking)[\s-]+)?days?\b(?![\s-]*(?:old))", re.I)
_DAYS_KK = re.compile(r"(?<![\d.,:])(?P<n>\d{1,3})\s+(?P<kind>(?:күнтізбелік|жұмыс|банктік)\s+)?күн(?:і|ге|нен|ден|дік)?\b", re.I)
_DAY_DEADLINE = re.compile(
    r"в\s+течение|не\s+позднее|не\s+позже|\bчерез\b|срок\w*|по\s+истечении|спустя|не\s+более|не\s+менее|\bза\s+(?:\d+|\w+)\s+дн\w+\s+до"
    r"|\bwithin\b|no\s+later\s+than|not\s+later|\bafter\b|\bexpires?\b|\bin\s+\d+\s+days|\bwait\w*|\bретенци|хранится|хранятся"
    r"|\bішінде\b|\bдейін\b|кейін|мерзім",
    re.I,
)
# a deadline of a business action (not a countdown, a validity window or a retention period, which are calendar by nature)
_PROCESS = re.compile(
    r"возврат\w*|вернут\w*|вернёт|рассмотр\w+|обработ\w+|зачисл\w+|выплат\w+|ответ\w*|претензи\w+|заявк\w+|заявлени\w+|оформ\w+|обращени\w+|отмен\w+|"
    r"поставк\w+|доставк\w+|достав\w+|подач\w+|подать|подаётся|списан\w+|исполн\w+|выдач\w+|перевыпуск\w*|закры\w+|рассылк\w+|"
    r"refund\w*|review\w*|process\w*|respon\w+|settle\w*|deliver\w*|cancel\w*|submit\w*|complain\w*|dispute\w*|appeal\w*|claim\w*|payout\w*|payment\w*|"
    r"қайтару|қарау|өтініш",
    re.I)
_NOT_DEADLINE = re.compile(
    r"истека\w*|осталось|остаётся|до\s+окончания|до\s+истечения|хранит\w+|хранени\w+|удаля\w+|действует|действителен|срок\s+действия|"
    r"expires?|expiry|remaining|stored|retain\w*|retention|deleted|valid\s+for|жарамды|сақталады|\bпуш\w*|\bpush\b|уведомлени\w+\s+за|напомин\w+",
    re.I)
_LOOKBACK = re.compile(r"за\s+(?:последние|прошедшие|предыдущие|прошлые)|last\s+\d|past\s+\d|over\s+the\s+last|ақырғы|соңғы", re.I)
_DAYKIND_GLOBAL = re.compile(
    r"(?:все|любые|любой|везде|по\s+умолчанию|если\s+не\s+указано|если\s+не\s+сказано)[^.]{0,80}(?:календарн|рабоч|банковск)"
    r"|(?:дн\w+|сроки?)[^.]{0,40}(?:считаются|понимаются|означают|подразумеваются|исчисляются|указаны|—)[^.]{0,30}(?:календарн|рабоч|банковск)"
    r"|в\s+календарных\s+дн\w+|срок\w*\s+(?:исчисляется|считается)[^.]{0,30}(?:календарн|рабоч)"
    r"|all\s+(?:periods|deadlines|days|time\s+limits)[^.]{0,80}(?:calendar|business|working)"
    r"|\bdays?\s+(?:are|means?|are\s+counted\s+as)[^.]{0,30}(?:calendar|business|working)"
    r"|(?:calendar|business|working)\s+days?\s+(?:are|is|only|unless)",
    re.I,
)
_KIND_WORD = re.compile(r"календарн|рабоч|банковск|операционн|calendar|business|working|banking|күнтізбелік|жұмыс|банктік", re.I)


def rule_days_without_kind(ctx: Ctx, rule):
    full = visible(ctx.doc.text)
    if _DAYKIND_GLOBAL.search(full):
        return []
    # a number that has its kind somewhere in the document («30 календарных дней») is settled for its other mentions
    kinded = set()
    for rx in (_DAYS_RU, _DAYS_EN, _DAYS_KK):
        for m in rx.finditer(full):
            if m.group("kind"):
                kinded.add(m.group("n").lower())
    hits = []
    for b, t in scan_blocks(ctx):
        for rx in (_DAYS_RU, _DAYS_EN, _DAYS_KK):
            for m in rx.finditer(t):
                if m.group("kind"):
                    continue
                local = sentence_at(b, t, m.start())
                if _KIND_WORD.search(local) or _LOOKBACK.search(local) or m.group("n").lower() in kinded:
                    continue
                if _NOT_DEADLINE.search(local) or not _PROCESS.search(local):
                    continue
                before = t[max(0, m.start() - 45): m.end()]
                if not _DAY_DEADLINE.search(before + " " + t[m.end(): m.end() + 25]):
                    continue
                hits.append((b.line_start, m.start(), b))
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Срок в днях без указания, какие это дни",
        problem=("Срок задан числом дней («в течение 30 дней»), но не сказано, календарные это дни или рабочие, и в документе нет общего правила на этот счёт. "
                 "Срок «30 дней» календарных и рабочих отличается больше чем в полтора раза; разработчик и тестировщик посчитают дату по-разному."),
        recommendation="Допишите «календарных» или «рабочих» в каждом месте либо добавьте общее правило: «Дни в сроках — календарные, если не сказано иное».",
        question=ask("Какие дни считаются в сроках — календарные или рабочие?",
                     ["Календарные", "Рабочие", "Банковские (по календарю банка)"]),
    )


# --- 4. day 29–31 in a monthly schedule ------------------------------------------------------------------------

_MONTHLY = re.compile(
    r"ежемесячн\w+|раз\s+в\s+месяц|каждого\s+месяца|каждый\s+месяц|каждое\s+\d{1,2}(?:-?е)?\s+числ\w+|"
    r"(?:выбранн|указанн|заданн|определённ|определенн)\w*\s+(?:день|числ\w+)\s+месяца|monthly|every\s+month|ай\s+сайын",
    re.I)
_DAY_OF_MONTH_RANGE = re.compile(
    r"(?:день|числ\w+)\s+месяца[^.\n|;]{0,40}?(?:от\s+)?1\s*(?:до|по|[–—-])\s*(?P<hi>\d{1,2})\b"
    r"|(?:от\s+)?1\s*(?:до|по|[–—-])\s*(?P<hi2>\d{1,2})\s*(?:-?(?:го|е))?\s+числ\w+"
    r"|day\s+of\s+(?:the\s+)?month[^.\n|;]{0,40}?1\s*(?:to|[–—-])\s*(?P<hi3>\d{1,2})\b", re.I)
_DAY_29_31 = re.compile(r"(?<![\d.,:])(?:29|30|31)(?:-?(?:го|е|ое))?\s+числа\b", re.I)
_DAY_CHOSEN = re.compile(
    r"(?:выбранн|указанн|заданн|определённ|определенн|любо[ей])\w*\s+(?:день|числ\w+)\s+месяца|day\s+of\s+(?:the\s+)?month", re.I)
_NO_SUCH_DAY = re.compile(
    r"в\s+(?:коротк\w+\s+)?месяц\w+,?\s+(?:где|в\s+которых|в\s+котором)\s+(?:нет|отсутствует|не\s+бывает)"
    r"|если\s+(?:в\s+месяце\s+)?(?:выбранн\w+\s+|указанн\w+\s+|заданн\w+\s+)?(?:число|день|даты|дата)[^.]{0,40}(?:нет|отсутствует|не\s+существует)"
    r"|нет\s+(?:такого|выбранного|указанного)\s+(?:числа|дня)|не\s+существует\s+в\s+(?:этом\s+)?месяце|29\s+февраля|февраль"
    r"|последн\w+\s+(?:календарн\w+\s+)?(?:день|числ\w+)\s+(?:этого\s+|такого\s+|данного\s+|текущего\s+)?месяца[^.]{0,40}(?:если|вместо|переносит)"
    r"|(?:если|when|if)[^.]{0,50}(?:month|месяц)[^.]{0,40}(?:has\s+no|нет)|last\s+day\s+of\s+(?:the\s+)?(?:shorter\s+)?month\s+if",
    re.I)


_DAY_CAP = re.compile(
    r"(?:день|числ\w+)\s+месяца[^.\n|;]{0,20}?(?:от\s+)?1\s*(?:до|по|[–—-])\s*(?:2[5-8])\b|\b1\s*[–—-]\s*28\b"
    r"|day\s+of\s+(?:the\s+)?month[^.\n|;]{0,20}?1\s*(?:to|[–—-])\s*(?:2[5-8])\b", re.I)


def rule_month_end_day(ctx: Ctx, rule):
    if doc_has(ctx, _NO_SUCH_DAY):
        return []
    full = visible(ctx.doc.text)
    # the document itself limits the day of month to 28 or less: a «chosen day» can never fall into a short month
    capped = bool(_DAY_CAP.search(full))
    hits = []
    for b, t in scan_blocks(ctx):
        for m in _DAY_OF_MONTH_RANGE.finditer(t):
            hi = int(m.group("hi") or m.group("hi2") or m.group("hi3"))
            loc = sentence_at(b, t, m.start())
            if hi >= 29 and (_MONTHLY.search(loc) or re.search(r"ежемесячн|monthly|месяц", t)):
                hits.append((b.line_start, m.start(), b))
        for m in _DAY_29_31.finditer(t):
            loc = sentence_at(b, t, m.start())
            if _MONTHLY.search(loc):
                hits.append((b.line_start, m.start(), b))
        for m in _DAY_CHOSEN.finditer(t):
            loc = sentence_at(b, t, m.start())
            if _MONTHLY.search(loc) and not capped:
                hits.append((b.line_start, m.start(), b))
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Ежемесячное действие в выбранный день месяца: не сказано, что делать, если такого дня нет",
        problem=("Действие повторяется каждый месяц в выбранный день, и день может быть 29, 30 или 31. В феврале и в коротких месяцах такого числа нет. "
                 "В документе не сказано, что делать в этом случае: выполнить в последний день месяца, перенести на 1-е или пропустить. "
                 "Разработчик выберет сам, и клиент потеряет платёж или получит его не в тот день."),
        recommendation="Добавьте правило: «Если в месяце нет выбранного числа, действие выполняется в последний день месяца» (или ограничьте выбор днями 1–28).",
        question=ask("Что делать в месяце, где нет выбранного числа (29, 30, 31)?",
                     ["Выполнить в последний день месяца", "Перенести на 1-е число следующего месяца", "Ограничить выбор днями 1–28"]),
    )


# --- 5. «месяц» and «сутки» in limits without a definition ------------------------------------------------------

_MONTH_LIMIT = re.compile(
    r"(?:не\s+более|не\s+свыше|не\s+превышает|максимум|лимит\w*|не\s+менее|порог\w*|счётчик\w*|квот\w*|количество|число|сумм\w+)"
    r"[^.\n|;]{0,60}?(?:\bв\s+месяц\b|\bза\s+месяц\b|\bв\s+течение\s+месяца\b|\bв\s+месяце\b|\bper\s+month\b|\ba\s+month\b)"
    r"|ежемесячн\w+\s+(?:лимит|квот|ограничени|порог|счётчик)|monthly\s+(?:limit|cap|quota|counter)",
    re.I)
_DAY_LIMIT = re.compile(
    r"(?:не\s+более|не\s+свыше|не\s+превышает|максимум|лимит\w*|квот\w*|не\s+чаще)[^.\n|;]{0,60}?(?:\bв\s+сутки\b|\bза\s+сутки\b|\bв\s+день\b|\bза\s+день\b|\bв\s+течение\s+(?:суток|дня)\b|\bper\s+day\b|\ba\s+day\b)"
    r"|(?:суточн|дневн|ежедневн)\w+\s+(?:лимит|квот|ограничени)|daily\s+(?:limit|cap|quota)|тәуліктік\s+лимит|бір\s+тәулікте",
    re.I)
_MONTH_LOCAL_OK = re.compile(r"календарн|скольз|rolling|calendar|с\s+начала\s+месяца|с\s+1(?:-?(?:го|е))?\s+числа|\b30\s+(?:календарн\w+\s+)?дн|\b30\s+days"
                             r"|обнуля|сбрасыва|reset|күнтізбелік", re.I)
_DAY_LOCAL_OK = re.compile(r"календарн|скольз|rolling|calendar|24\s*(?:час|ч\b)|24\s+hours|00:00|обнуля|сбрасыва|reset|с\s+начала\s+(?:дня|суток)|күнтізбелік", re.I)
_MONTH_DEF = re.compile(
    r"календарн\w+\s+месяц|calendar\s+month|скользящ\w+\s+месяц|rolling\s+month"
    r"|месяц\w*[^.\n]{0,40}(?:определяется|считается|отсчитывается|исчисляется|determined|defined|counted|calculated)\s*(?:по|с|от|by|from)"
    r"|(?:месяц|календарн\w+\s+месяц|расч[её]тн\w+\s+месяц|отч[её]тн\w+\s+месяц)\s*(?:\||:)?\s*(?:—|-|это|считается|определяется|означает|отсчитывается|начинается)"
    r"|месяц\w*[^.]{0,30}(?:определяется|считается|отсчитывается|исчисляется)\s+(?:по|с|от)"
    r"|месяц\w*\s+(?:с|начинается\s+с)\s+1(?:-?(?:го|е))?\s+числа|счётчик\w*[^.]{0,40}(?:обнуля|сбрасыва)"
    r"|month\s+(?:is|means|starts|begins)|calendar\s+month\s*(?:—|-|:|means|is)|скользящ\w+\s+(?:месяц|окно)|rolling\s+(?:month|30)",
    re.I)
_DAY_DEF = re.compile(
    r"календарн\w+\s+(?:день|сутки|суток)|calendar\s+day|24[- ]hour\s+(?:period|window)|скользящ\w+\s+(?:сутки|24)"
    r"|(?:календарн\w+\s+(?:день|сутки)|сутки|операционн\w+\s+день)\s*(?:\||:)?\s*(?:—|-|это|считается|определяется|означает|отсчитывается|начинается)"
    r"|(?:день|сутки)\s+(?:определяется|считается|отсчитывается)\s+(?:по|с|от)|счётчик\w*[^.]{0,40}(?:обнуля|сбрасыва|обновля)"
    r"|лимит\w*[^.]{0,40}(?:обнуля|сбрасыва|обновля)|(?:сутки|day)\s+(?:is|means|starts|begins)|24[- ]hour|календарн\w+\s+день\s+—\s+с\s+00:00"
    r"|с\s+00:00(?::00)?\s+до\s+23:59",
    re.I)


def rule_period_undefined(ctx: Ctx, rule):
    month_hits, day_hits = [], []
    month_defined = doc_has(ctx, _MONTH_DEF)
    day_defined = doc_has(ctx, _DAY_DEF)
    for b, t in scan_blocks(ctx):
        if not month_defined:
            for m in _MONTH_LIMIT.finditer(t):
                loc = sentence_at(b, t, m.start())
                if re.search(r"\d", loc) and not _MONTH_LOCAL_OK.search(loc):
                    month_hits.append((b.line_start, m.start(), b))
        if not day_defined:
            for m in _DAY_LIMIT.finditer(t):
                loc = sentence_at(b, t, m.start())
                if re.search(r"\d", loc) and not _DAY_LOCAL_OK.search(loc):
                    day_hits.append((b.line_start, m.start(), b))
    if not month_hits and not day_hits:
        return []
    hits = sorted(month_hits + day_hits, key=lambda x: (x[0], x[1]))
    if month_hits and day_hits:
        what, short_what = "«месяц» и «сутки»", "месяц и сутки"
        q = "Как считаются месяц и сутки в лимитах: по календарю или как скользящий период?"
    elif month_hits:
        what, short_what = "«месяц»", "месяц"
        q = "Как считается месяц в лимитах: календарный месяц или 30 дней подряд, и с какого момента идёт счёт?"
    else:
        what, short_what = "«сутки»", "сутки"
        q = "Как считаются сутки в лимитах: календарный день с 00:00 или 24 часа от первой операции?"
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title=f"Лимит по периоду без определения: что такое {short_what}",
        problem=(f"Лимиты или счётчики заданы на период {what}, но нигде не сказано, как он считается. «Месяц» может быть календарным (с 1-го числа) "
                 "или скользящим (последние 30 дней); «сутки» — календарным днём или 24 часами от первой операции. "
                 "От этого зависит, когда счётчик обнуляется и пропустит ли система операцию."),
        recommendation="Определите период один раз: «Месяц — календарный, с 1-го числа 00:00 по последнее число» (или скользящие 30 дней) и когда обнуляется счётчик.",
        question=ask(q, ["Календарный период, счётчик обнуляется в начале периода", "Скользящий период (30 дней / 24 часа)"]),
    )


# --- 6. relative period words without a definition -----------------------------------------------------------------

_REL_PERIOD = re.compile(
    r"\b(?:закрыт\w+|отч[её]тн\w+|расч[её]тн\w+|прошл\w+|предыдущ\w+|текущ\w+)\s+(?:отч[её]тн\w+\s+|расч[её]тн\w+\s+)?период\w*\b"
    r"|\b(?:закрыт\w+|отч[её]тн\w+|расч[её]тн\w+)\s+месяц\w*\b"
    r"|\b(?:previous|prior|current|closed|reporting|last)\s+(?:billing\s+|reporting\s+)?period\b",
    re.I)
_REL_LOCAL_OK = re.compile(r"\bс\s+\d|\bпо\s+\d|\d{2}\.\d{2}\.\d{2,4}|календарн|\bза\s+\d+\s+(?:мес|дн|час)|\(|calendar|\bfrom\s+\d|\bto\s+\d", re.I)
_REL_DEF = re.compile(
    r"(?:закрыт\w+|отч[её]тн\w+|расч[её]тн\w+|прошл\w+|предыдущ\w+|текущ\w+)\s+(?:отч[её]тн\w+\s+|расч[её]тн\w+\s+)?(?:период|месяц)\w*\s*(?:\||:)?\s*"
    r"(?:—|-|:|это|считается|определяется|означает|начинается|заканчивается|равен|is|means)"
    r"|(?:период|месяц)\w*\s+(?:считается|определяется)\s+(?:закрыт|отч[её]тн)|(?:period|month)\s+(?:is|are)\s+(?:considered\s+)?closed"
    r"|(?:closed|reporting|billing|previous|current)\s+period\s*(?:—|-|:|means|is)",
    re.I)


def rule_relative_period(ctx: Ctx, rule):
    if doc_has(ctx, _REL_DEF):
        return []
    hits = []
    words = []
    for b, t in scan_blocks(ctx):
        for m in _REL_PERIOD.finditer(t):
            loc = sentence_at(b, t, m.start())
            if _REL_LOCAL_OK.search(loc):
                continue
            hits.append((b.line_start, m.start(), b))
            words.append(m.group(0).lower())
    if not hits:
        return []
    order = sorted(range(len(hits)), key=lambda k: (hits[k][0], hits[k][1]))
    hits = [hits[k] for k in order]
    words = [words[k] for k in order]
    first = words[0]
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title=f"Период назван словами, но не определён: «{first}»",
        problem=(f"В правилах используется «{first}», но нигде не сказано, какие даты он охватывает и когда считается закрытым. "
                 "Без определения разработчик возьмёт календарный месяц, а бухгалтерия — свой расчётный период; отчёты и расчёты разойдутся."),
        recommendation="Определите период: с какой по какую дату, когда он считается закрытым и что делать с операциями, пришедшими после закрытия.",
        question=ask(f"Что именно означает «{first}»: с какой по какую дату и когда он закрывается?",
                     ["Календарный месяц", "Расчётный период по календарю банка"]),
    )


TIME_RULES = {
    "time-zone-missing": rule_time_zone_missing,
    "period-boundary": rule_period_boundary,
    "days-without-kind": rule_days_without_kind,
    "month-end-day": rule_month_end_day,
    "period-undefined": rule_period_undefined,
    "relative-period": rule_relative_period,
}
