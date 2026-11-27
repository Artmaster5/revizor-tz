"""General checks a senior analyst always makes: repeats and time-outs, percentages and one-time actions, data storage,
numbering, priorities, external calls, audit of settings, measurement periods, languages.

Every rule here is silent when the document answers the question anywhere (a general statement counts).
One finding per rule per document; the quotes are the first places where the question arises.
"""
from __future__ import annotations

import re

from ._common import Ctx, ask, finding, short, visible
from .time_rules import emit, doc_has, scan_blocks, sentence_at
from .value_rules import _MONEY_WORD, _ROUNDING, _neighborhood, rule_rounding_missing

_DIGIT = re.compile(r"\d")
_NUMWORD = r"(?:один|одн\w+|дв[аеу]\w*|тр[иёе]\w*|четыр\w+|пят\w+|шест\w+|сем\w+|восем\w+|девят\w+|десят\w+)"


def _all_text(ctx: Ctx) -> str:
    return visible(ctx.doc.text)


# --- 1. a repeat without count or interval -------------------------------------------------------------------------

_RETRY = re.compile(
    r"повторя\w+|повторит\b|повторно\s+(?:отправ|пытает|списыв|вызыв|запрашив|передаёт|передает)\w*|пытается\s+(?:ещё\s+раз|снова)|автоматическ\w+\s+повтор\w*|"
    r"\bretr(?:y|ies|ied)\b|\bresend\w*|\bre-?send\w*|автоповтор\w*|ретрай\w*|повторн\w+\s+попытк\w+\s+(?:отправк|списани|доставк|вызов|запрос)\w*",
    re.I)
_RETRY_ACTOR = re.compile(
    r"систем|сервис|шлюз|банк|сервер|процессинг|платформ|уведомлен|смс|sms|push|письм|отправк|запрос|вызов|списани|webhook|callback|доставк|"
    r"system|service|gateway|server|notification|request|delivery|платёж|платеж", re.I)
_USER_RETRY = re.compile(r"клиент\w*\s+(?:повторно|может\s+повтор)|пользовател\w+\s+(?:повторно|может\s+повтор)|user\s+(?:may|can)\s+retry|customer", re.I)
_COUNT = re.compile(
    r"\d+\s*(?:раз|раза|попыт\w+|повтор\w+|times|attempts?|retries)|не\s+более\s+(?:\d+|" + _NUMWORD + r")\s*(?:раз|попыт|повтор)|"
    r"(?:до|максимум|не\s+более|up\s+to|max\w*|at\s+most)\s+\d+|трижды|дважды|однократно|попыт\w+\s*[—:=-]\s*\d|\d+\s*(?:повтор|ретрай|retr)\w*|"
    r"(?:один|два|три|четыре|пять)\s+(?:раз|раза|попыт)",
    re.I)
_INTERVAL = re.compile(
    r"интервал\w*|\bв\s+\d{1,2}:\d{2}|того\s+же\s+дня|на\s+следующ\w+\s+(?:день|сутки)|через\s+(?:каждые\s+)?(?:\d+|" + _NUMWORD + r")?\s*(?:сек|мин|час|мс|минут|секунд|s\b|ms\b|min|sec|hour)|каждые\s+\d+|"
    r"с\s+(?:паузой|задержкой|периодом)|экспоненциальн\w+|backoff|\bevery\s+\d+|\binterval|\bdelay\b|\bapart\b|\bafter\s+\d+\s*(?:s|sec|min|ms|seconds|minutes|hours)",
    re.I)


def rule_retry_unbounded(ctx: Ctx, rule):
    text_sents = _sentences_all(ctx)
    for s in text_sents:  # a general policy anywhere in the document silences the rule
        if _RETRY.search(s) and _COUNT.search(s) and _INTERVAL.search(s):
            return []
    hits = []
    missing: set[str] = set()
    for b, t in scan_blocks(ctx):
        for m in _RETRY.finditer(t):
            loc = sentence_at(b, t, m.start())
            if not _RETRY_ACTOR.search(loc) or _USER_RETRY.search(loc):
                continue
            near = _neighborhood(ctx, b)
            has_count = bool(_COUNT.search(loc) or _COUNT.search(near))
            has_int = bool(_INTERVAL.search(loc) or _INTERVAL.search(near))
            if has_count and has_int:
                continue
            hits.append((b.line_start, m.start(), b))
            if not has_count:
                missing.add("сколько раз повторять")
            if not has_int:
                missing.add("через какое время")
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    parts = " и ".join(p for p in ("сколько раз повторять", "через какое время") if p in missing)
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Повтор без числа попыток или без интервала",
        problem=(f"Система должна повторить действие (отправку, запрос, списание), но не сказано, {parts}. Без числа попыток повтор может "
                 "идти бесконечно и забить очередь или заспамить клиента, без интервала — повторы пойдут подряд и только усилят сбой. "
                 "Тестировщику не с чем сравнить результат, а разработчик выберет числа сам."),
        recommendation="Допишите правило повтора: число попыток, интервал между ними (лучше с нарастанием) и что происходит после последней неудачной попытки.",
        question=ask("Сколько раз и с каким интервалом система повторяет действие и что происходит после последней попытки?",
                     ["3 попытки с интервалом 1, 5 и 15 минут", "Повтор до успеха, но не дольше суток", "Один повтор, затем ошибка"]),
    )


# --- 2. a time-out with no outcome ---------------------------------------------------------------------------------

_TIMEOUT = re.compile(r"тайм-?аут\w*|time-?out\w*|время\s+ожидания|не\s+дожида\w+|ожидани\w+\s+ответа|тайм-?аут", re.I)
_OUTCOME = re.compile(
    r"отклон\w+|отмен\w+|ошибк\w+|блокир\w+|закрыва\w+|закрыт\w*|возвращ\w+|считается|переход\w+|повтор\w+|откат\w*|статус\w*|показыва\w+|сообщени\w+|"
    r"завершае\w+|прерыва\w+|не\s+выполня\w+|аннулир\w+|истека\w+|сбрасыва\w+|уведомля\w+|предлага\w+|\b[45]\d\d\b|"
    r"\berror\b|reject\w*|\bfail\w*|cancel\w*|\bretr\w+|fallback|abort\w*|expire\w*|\bstatus\b|return\w*|\bclos\w+|queue\w*|"
    r"\bmark\w*|\bshow\w*|display\w*|\bdecline\w*|treated|considered|\bunknown\b|неизвестн\w+",
    re.I)


def rule_timeout_no_outcome(ctx: Ctx, rule):
    sents = _sentences_all(ctx)
    hits = []
    for b, t in scan_blocks(ctx):
        for m in _TIMEOUT.finditer(t):
            loc = sentence_at(b, t, m.start())
            if _OUTCOME.search(loc) or _OUTCOME.search(_neighborhood(ctx, b)):
                continue
            hits.append((b.line_start, m.start(), b))
    if not hits:
        return []
    if any(_TIMEOUT.search(s) and _OUTCOME.search(s) for s in sents):  # an outcome stated for a time-out elsewhere
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Время ожидания задано, но не сказано, что происходит по его истечении",
        problem=("Есть тайм-аут (время ожидания ответа или действия), но нигде не сказано, что делает система, когда время вышло: "
                 "считает операцию неуспешной, повторяет, оставляет в неопределённом статусе или отменяет. Для денег это особенно важно: "
                 "после тайм-аута платёж мог пройти, а клиенту показана ошибка."),
        recommendation="Допишите результат по тайм-ауту: в какой статус переходит операция, что видит клиент, повторяется ли запрос и как проверить итог.",
        question=ask("Что происходит с операцией, когда время ожидания истекло?",
                     ["Операция отклоняется, клиент видит ошибку", "Операция переходит в статус «в обработке» и проверяется позже", "Запрос повторяется заданное число раз"]),
    )


# --- 3. a percentage without a base --------------------------------------------------------------------------------

_PCT = re.compile(r"(?<![\w.,])\d{1,3}(?:[.,]\d+)?\s*(?:%|процент\w*)")
_PCT_MONEY = re.compile(
    r"комисси\w+|кэшбэк\w*|кешбэк\w*|скидк\w+|штраф\w*|пен[яи]\b|неустойк\w+|вознагражд\w+|надбавк\w+|наценк\w+|взнос\w*|"
    r"\bfee\b|cashback|discount|commission|surcharge|markup|penalty", re.I)
_PCT_BASE = re.compile(
    r"\bот\s+\w+|\bof\s+(?:the\s+)?\w+|\bfrom\s+\w+|годов\w+|\bв\s+год\b|\bв\s+месяц\b|per\s+(?:annum|year|month)|p\.\s?a\.|\bAPR\b|"
    r"\bна\s+(?:сумму|остаток|баланс|покупк|операци)\w*|\bиз\s+(?:суммы|стоимост|остатк|покупк)\w*|\bк\s+(?:сумме|стоимости)\b|\bпо\s+(?:остатку|сумме)\b|"
    r"\bсверх\b|\bпри\s+(?:сумме|покупк)\w*|\bна\s+(?:каждый|каждую|все)\b|\bза\s+(?:каждый|каждую|каждое)\b|"
    r"\bсумм\w+\s+(?:покупки|операции|платежа|перевода|кредита|займа|вклада)|\bкаждо\w+\s+(?:покупк|операци|платеж|перевод)\w*|"
    r"\bamount\b|\bprice\b|\bpremium\b|\bpurchase\w*|\bbalance\b|\bsum\b|\bprincipal\b|\bloan\b|\bdeposit\b|премии|премию|\bстоимост\w+|\bпокупк\w+\b|\bна\s+\w+\s+(?:остаток|сумму)|\bnet\b|\bgross\b|\bна\s+(?:остаток|общую)\b|"
    r"\bот\s+суммы\b|\b(?:суммы|стоимости|остатка)\b",
    re.I)
_PCT_SKIP = re.compile(
    r"\bexample\b|пример\w*|\u2192|=>|доступн\w+|uptime|availability|точност|\bSLA\b|успешн|конверси|покрыт|\bNPS\b|\bCSAT\b|снижен\w+|рост\w*|увеличен\w+|"
    r"ошиб\w+|отказ\w*|\bчаст\w+|времени\b|нагрузк\w+|загрузк\w+|\bCPU\b|\bRAM\b|заполнен|прогресс|\bдоля\b|доли\b|\bshare\b|\bcoverage\b|прирост|\d\s*%\s*(?:клиент|пользовател)",
    re.I)


def rule_percent_base_missing(ctx: Ctx, rule):
    hits = []
    for b, t in scan_blocks(ctx):
        if any(re.search(r"%|процент|\bот\b|доля", h, re.I) for h in b.header) and b.kind == "table_row":
            # a column header may carry the base ("Ставка, % от суммы")
            if any(re.search(r"\bот\b|\bof\b|годов|от\s+\w+", h, re.I) for h in b.header):
                continue
        for m in _PCT.finditer(t):
            loc = sentence_at(b, t, m.start())
            if not _PCT_MONEY.search(loc) or _PCT_SKIP.search(loc) or _PCT_BASE.search(loc):
                continue
            near = _neighborhood(ctx, b)
            if _PCT_BASE.search(near) and not b.kind == "table_row":
                continue
            hits.append((b.line_start, m.start(), b))
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Процент задан без базы расчёта",
        problem=("Указан процент комиссии, скидки, кэшбэка или штрафа, но не сказано, от чего он считается: от суммы операции, от остатка, "
                 "от суммы с комиссией или без неё. 5 % от 10 000 ₸ и 5 % от 10 500 ₸ дают разные суммы, поэтому разработчик и тестировщик "
                 "получат разные цифры и не смогут проверить расчёт."),
        recommendation="Рядом с процентом допишите базу: «5 % от суммы покупки без комиссии» (и порядок, если процентов несколько).",
        question=ask("От какой суммы считается этот процент?",
                     ["От суммы операции", "От остатка на момент расчёта", "От суммы вместе с комиссией"]),
    )


# --- 4. «once» without a period or a condition for the next time ---------------------------------------------------

_ONCE = re.compile(
    r"однократн\w+|не\s+чаще\s+(?:одного\s+раза|раза)\b|только\s+один\s+раз|единожды|\bonly\s+once\b|\bonce\s+only\b|\bat\s+most\s+once\b",
    re.I)
_ONCE_SKIP = re.compile(r"одноразов\w+\s+(?:код|парол|ссылк|пин|ключ|токен|карт)|\bOTP\b|one[- ]time\s+(?:password|code|link|pin|token|passcode)", re.I)
_ONCE_PERIOD = re.compile(
    r"\bв\s+(?:сутки|день|неделю|месяц|год|квартал|полугодие|течение|рамках|период)\b|\bза\s+(?:сутки|день|неделю|месяц|год|квартал|период|сессию|жизнь)\b|"
    r"\bна\s+(?:одного|каждого|одну|каждую|один|каждый|клиента|карту|счёт|счет|договор|заявку|операцию)\b|\bдля\s+(?:каждого|каждой|одного|одной|нового|новых)\b|"
    r"\bкаждого\b|\bкаждый\b|\bкаждую\b|\bкаждой\b|\bна\s+(?:весь|всё|все)\b|\bв\s+\w+\s+(?:сутки|месяц|год)\b|"
    r"\bper\s+\w+|\beach\b|\bevery\b|\bfor\s+(?:each|every|a\s+new|new)\b|\bwithin\b|\bin\s+(?:a|the|one|any)\s+(?:day|week|month|year|period|session)\b|\bper\b|"
    r"\bпока\b|\bдо\s+(?:тех\s+пор|истечения|окончания|закрытия|конца)|\bпо\s+(?:истечении|окончании)|\bпосле\s+(?:сброса|закрытия|окончания|завершения|истечения|отмены)|"
    r"\bпри\s+(?:каждом|каждой|новом|повторном)\b|\bза\s+всё\s+время|\bза\s+весь\b|\bпервы\w+\s+(?:раз|покупк|операци|пополнени|вход)|\bпри\s+первом\b|"
    r"\bуникальн\w+|\bкак\s+только\b|\bпока\s+не\b|\bтолько\s+при\s+первом\b|\bна\s+весь\s+срок\b|\bзакрыт|\bзавершен|\bзавершён",
    re.I)


def rule_once_without_period(ctx: Ctx, rule):
    hits = []
    for b, t in scan_blocks(ctx):
        for m in _ONCE.finditer(t):
            loc = sentence_at(b, t, m.start())
            if _ONCE_SKIP.search(loc) or _ONCE_PERIOD.search(loc):
                continue
            hits.append((b.line_start, m.start(), b))
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="«Один раз» без периода и без условия, когда можно снова",
        problem=("Действие разрешено «один раз» («однократно», «не чаще одного раза»), но не сказано, за какой срок или на что именно: "
                 "один раз в сутки, за всё время, на клиента, на карту, на договор. Не сказано и что позволяет повторить действие позже "
                 "(новый период, закрытие договора, отмена). Разработчик выберет границу сам, а тестировщик не сможет проверить второй раз."),
        recommendation="Допишите «один раз» полностью: «один раз на клиента за всё время» или «один раз в календарный месяц»; и что снимает ограничение.",
        question=ask("На какой срок и на что именно действует ограничение «один раз» и когда его можно снять?",
                     ["Один раз на клиента за всё время", "Один раз в календарный месяц", "Один раз на договор, до его закрытия"]),
    )


# --- 5. stored data without a retention period ---------------------------------------------------------------------

_STORE = re.compile(
    r"хран\w+|сохраня\w+|записыва\w+|архивир\w+|логир\w+|журналир\w+|фиксиру\w+\s+в\s+(?:журнал|лог|базе|хранилищ)|\bstor(?:e|es|ed|ing)\b|\bretain\w*|\barchiv\w+|\blogged\b|\blogs?\s+(?:the|every|all)",
    re.I)
_STORE_DATA = re.compile(
    r"скан\w*|фото\w*|изображени\w+|переписк\w+|анкет\w+|звонк\w+|персональн\w+|паспорт\w*|ИИН|биометр\w+|"
    r"\bimages?\b|\bphotos?\b|\bpersonal\b|passport|biometric|\bscans?\b|call\s+recordings?|аудиозапис\w+|запис\w+\s+разговор\w*",
    re.I)
_RETENTION = re.compile(
    r"срок\w*\s+хранени\w+|хран\w+\s+(?:\d+|" + _NUMWORD + r")\s*(?:лет|год|месяц|дн|недел|суток|часов)|хран\w+\s+не\s+(?:более|менее|дольше)|хран\w+\s+в\s+течение|"
    r"хран\w+\s+до\b|после\s+(?:закрытия|расторжения|окончания|завершения)[^.]{0,60}(?:удал|хран|уничтож)|удаля\w+\s+(?:через|спустя|по\s+истечении|автоматическ)|"
    r"подлежит\s+удален|уничтожени\w+|архивн\w+\s+срок|\bretention\b|\bretain\w*\s+(?:for|\d)|\bdeleted?\s+(?:after|within|automatically)|\bpurg\w+|\bTTL\b|"
    r"(?:in\s+)?storage\s+period|\bkept?\s+(?:for|until)|\bstored\s+(?:for|until)|\bremoved\s+after|\bexpire\w*\s+after|хранения\s+(?:данных|документов)|"
    r"сақтау\s+мерзім|(?:не\s+)?(?:более|менее|дольше)\s+(?:\d+|" + _NUMWORD + r")\s+(?:лет|года|год)\s+(?:после|с\s+момента)|"
    r"\d+\s*(?:лет|года|год|months?|years?)\s+(?:после|с\s+даты|с\s+момента|after|from)|срок\s+(?:жизни|годности)\s+(?:записи|данных|логов|файла)",
    re.I)
_STORE_SKIP = re.compile(r"не\s+хран\w+|не\s+сохраня\w+|не\s+записыва\w+|\bnot\s+stor\w+|\bnever\s+stor\w+|без\s+сохранения", re.I)


def rule_retention_missing(ctx: Ctx, rule):
    if doc_has(ctx, _RETENTION):
        return []
    hits = []
    for b, t in scan_blocks(ctx):
        for m in _STORE.finditer(t):
            loc = sentence_at(b, t, m.start())
            if _STORE_SKIP.search(loc) or not _STORE_DATA.search(loc):
                continue
            # a storage word used only for «сохранить» as a user action (save the form) is not a retention question
            if re.search(r"кнопк\w*\s+[«\"]?сохранить|нажима\w+\s+[«\"]?сохранить|\bсохранить\s+(?:изменения|черновик)|кэш\w*", loc, re.I):
                continue
            hits.append((b.line_start, m.start(), b))
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Данные хранятся, а срок хранения не назван",
        problem=("Система хранит данные клиента, документы или журнал событий, но нигде не сказано, как долго их держать и что с ними происходит потом. "
                 "Без срока данные копятся вечно: это риск по закону о персональных данных и по требованиям регулятора, растёт стоимость хранения, "
                 "а тестировщику нечего проверять в удалении или архивации."),
        recommendation="Допишите срок хранения для каждого вида данных (в годах или месяцах, с какого события считать) и что делается по его истечении: удаление или архив.",
        question=ask("Как долго хранятся эти данные и что с ними делается по истечении срока?",
                     ["Хранить 5 лет после закрытия договора, затем удалять", "Хранить 1 год, затем удалять", "Срок определяет служба безопасности — уточнить"]),
    )


# --- 6. card number, ИИН or phone shown or logged without masking ---------------------------------------------------

_SHOW = re.compile(
    r"отображ\w+|показыва\w+|показыв\w+|выводит\w*|выводятся|печата\w+|логир\w+|записыва\w+\s+в\s+(?:журнал|лог)|в\s+(?:журнал|лог)\w*|"
    r"\bdisplay\w*|\bshow\w*|\bprint\w*|\blog(?:s|ged|ging)?\b|\bwritten\s+to\s+(?:the\s+)?log|отправляется\s+в\s+(?:журнал|лог)|выдач\w+\s+на\s+экран|"
    r"присутству\w+\s+в\s+(?:ответе|логах|журнале)|возвраща\w+\s+в\s+ответе",
    re.I)
_PII = re.compile(
    r"номер\w*\s+(?:банковской\s+)?карт\w+|\bPAN\b|номер\w*\s+телефон\w+|телефон\w*\s+(?:клиента|пользователя|получателя|отправителя)|\bИИН\b|\bIIN\b|"
    r"card\s+number|phone\s+number|mobile\s+number|\bCVV\b|\bCVC\b|номер\w*\s+счёт\w+|номер\w*\s+счет\w+|\bIBAN\b|паспорт\w*\s+данн\w+",
    re.I)
_MASK = re.compile(
    r"маск\w+|\*{2,}|\bX{4}\b|•{2,}|последни\w+\s+(?:\d+|четыр\w+|" + _NUMWORD + r")\s*(?:цифр|знак|символ)|первы\w+\s+(?:\d+|шест\w+)\s+(?:и|\w+)\s+последни|"
    r"\bmask\w*|\btruncat\w*|\bredact\w*|\blast\s+(?:4|four)\s+digits|\bobfuscat\w*|скрыва\w+|скрыт\w+\s+(?:цифр|част)|не\s+(?:показыва|отображ|логир|выводит|записыва)\w+\s+(?:полн\w+|номер|ИИН|CVV)|"
    r"токенизац\w+|\btoken\w*|хеш\w*|\bhash\w*|шифр\w+|\bencrypt\w*|\bPCI\b",
    re.I)


def rule_masking_missing(ctx: Ctx, rule):
    if doc_has(ctx, _MASK):
        return []
    hits = []
    log_hit = False
    for b, t in scan_blocks(ctx):
        for m in _PII.finditer(t):
            loc = sentence_at(b, t, m.start())
            if not _SHOW.search(loc):
                continue
            hits.append((b.line_start, m.start(), b))
            if re.search(r"логир|журнал|\blog", loc, re.I):
                log_hit = True
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Номер карты, ИИН или телефон показываются или пишутся в журнал без маскирования",
        problem=("Документ требует показать или записать в журнал номер карты, ИИН или телефон, но нигде не сказано, показывать ли его целиком "
                 "или скрыть часть («**** 1234»). Полный номер на экране, в письме или в логе читают посторонние и сотрудники поддержки, "
                 "а правила платёжной безопасности (PCI DSS) разрешают целиком показывать номер карты только там, где это необходимо."),
        recommendation="Допишите правило маскирования: номер карты — первые 6 и последние 4 цифры (или только последние 4), ИИН и телефон — с замаскированной частью; в журналах — только в маскированном виде.",
        question=ask("Как показывать и записывать в журнал эти данные: целиком или с маскированием?",
                     ["Только последние 4 цифры карты, остальное скрыто", "Целиком на экране самого клиента, в журналах — маскированно", "Целиком везде"]),
        severity="MAJOR" if log_hit else "MINOR",
    )


# --- 7. numbering: the same number twice, a gap in a series ----------------------------------------------------------

_ID_PARTS = re.compile(r"^((?:[A-Za-zА-Яа-яЁё]{1,8})(?:[-_][A-Za-zА-Яа-яЁё]{1,8})*)[-_ ]?(\d{1,4})$")
_NOT_REQ_SECTION = re.compile(r"матриц|трасс|traceab|тест|test|прилож|appendix|истори|changelog|glossar|глоссар|перечень\s+рисков|риск", re.I)


def _numbered(ctx: Ctx):
    """(prefix, number, block) of requirement blocks that start with an identifier like «ТР-12»."""
    out = []
    for b in ctx.req_blocks:
        if not b.doc_id or _NOT_REQ_SECTION.search(b.section or ""):
            continue
        m = _ID_PARTS.match(b.doc_id.strip().rstrip(".:)"))
        if not m:
            continue
        out.append((m.group(1).upper(), int(m.group(2)), b))
    return out


def rule_req_id_clash(ctx: Ctx, rule):
    seen: dict[tuple[str, int], list] = {}
    for pre, num, b in _numbered(ctx):
        seen.setdefault((pre, num), []).append(b)
    clashes = []
    for (pre, num), blocks in seen.items():
        if len(blocks) < 2:
            continue
        texts = {re.sub(r"\W+", " ", visible(x.text).lower()).strip() for x in blocks}
        if len(texts) < 2:  # an exact copy is the job of the rule «duplicate»
            continue
        clashes.append((blocks[0].line_start, pre, num, blocks))
    if not clashes:
        return []
    clashes.sort(key=lambda x: x[0])
    quotes = []
    names = []
    for _, pre, num, blocks in clashes:
        names.append(blocks[0].doc_id)
        quotes += [ctx.q_block(blocks[0]), ctx.q_block(blocks[1])]
    return [finding(
        rule, quotes[:6],
        title=f"Один номер у двух разных требований: {short(', '.join(names), 60)}",
        problem=("Под одним номером стоят два разных требования. Ссылка на этот номер (в критериях приёмки, тестах, переписке) теряет смысл: "
                 "неясно, о каком из двух речь, а разработчик может реализовать только первое или только второе."),
        recommendation="Дайте второму требованию новый номер и поправьте ссылки на него.",
        question=ask(f"Какое из двух требований с номером {names[0]} верное и под каким номером записать второе?",
                     ["Второму присвоить новый номер", "Это одно требование — объединить"]),
        severity="MAJOR",
    )]


def rule_req_id_gap(ctx: Ctx, rule):
    series: dict[str, dict[int, object]] = {}
    for pre, num, b in _numbered(ctx):
        series.setdefault(pre, {}).setdefault(num, b)
    hits = []
    for pre, nums in series.items():
        if len(nums) < 6:
            continue
        lo, hi = min(nums), max(nums)
        missing = [n for n in range(lo, hi + 1) if n not in nums]
        if not missing or len(missing) > max(2, len(nums) // 10):
            continue
        ordered = sorted(nums)
        # a gap counts only when the series is otherwise dense and goes in order of the document
        lines = [nums[n].line_start for n in ordered]
        if lines != sorted(lines):
            continue
        hits.append((pre, missing, nums))
    if not hits:
        return []
    quotes = []
    texts = []
    for pre, missing, nums in hits[:2]:
        for n in missing[:2]:
            before = max((k for k in nums if k < n), default=None)
            after = min((k for k in nums if k > n), default=None)
            if before is not None:
                quotes.append(ctx.q_block(nums[before]))
            if after is not None:
                quotes.append(ctx.q_block(nums[after]))
        texts.append(", ".join(f"{pre}-{n}" for n in missing[:5]))
    return [finding(
        rule, quotes[:6],
        title=f"В нумерации требований пропущено: {short('; '.join(texts), 60)}",
        problem=("В ряду номеров требований есть пропуск. Либо требование потеряно при правке, либо номер зарезервирован и это нигде не сказано. "
                 "Тогда ссылки на него (в тестах, критериях приёмки, переписке) ведут в никуда, а проверяющий не может убедиться, что документ полон."),
        recommendation="Верните пропущенное требование или запишите, что номер снят («ТР-5 — исключено»), чтобы ряд читался без разрывов.",
        question=ask(f"Что было под номером {texts[0].split(',')[0]}: требование потеряно или номер снят намеренно?",
                     ["Потеряно — восстановить", "Снято намеренно — записать это в документе"]),
    )]


# --- 8. priority on some requirements and not on others -------------------------------------------------------------

_PRIO_WORD = re.compile(
    r"приоритет\w*\s*[:—-]?\s*(?:высок|средн|низк|критич|обязат|\bP?[0-3]\b|must|should|could|high|medium|low)\w*|\bMUST\b|\bSHOULD\b|\bCOULD\b|\bMoSCoW\b|\[P[0-3]\]|\(P[0-3]\)|"
    r"\bP[0-3]\b|\bпри-?\s*[0-3]\b|priority\s*[:—-]?\s*(?:high|medium|low|critical|must|should|could|p?\d)|обязательн\w+\s+для\s+(?:MVP|первой|релиза)|\bMVP\b",
    re.I)
_PRIO_COL = re.compile(r"^\s*(?:приоритет|priority|prio|важность|MoSCoW|критичность)\b", re.I)


def rule_priority_mixed(ctx: Ctx, rule):
    marked, unmarked = [], []
    # tables with a priority column: empty cells are unmarked
    for tb in ctx.tables:
        k = next((i for i, h in enumerate(tb.header) if _PRIO_COL.match(h)), -1)
        if k < 0:
            continue
        for r in tb.rows:
            if r.bid not in ctx.rid_of:
                continue
            cell = r.cells[k].strip() if k < len(r.cells) else ""
            if cell and cell not in ("-", "—", "–", "?", "TBD"):
                marked.append(r)
            else:
                unmarked.append(r)
    if len(marked) < 3 or len(unmarked) < 1:
        # inline marks in list items and paragraphs
        mk, um = [], []
        for b in ctx.req_blocks:
            if b.kind not in ("list_item", "paragraph"):
                continue
            if _NOT_REQ_SECTION.search(b.section or "") or b.kind == "heading":
                continue
            sec_marked = bool(_PRIO_WORD.search(b.section or ""))
            if _PRIO_WORD.search(visible(b.text)) or sec_marked:
                mk.append(b)
            else:
                um.append(b)
        if len(mk) >= 3 and len(um) >= 1:
            marked, unmarked = mk, um
    if len(marked) < 3 or not unmarked:
        return []
    share = len(marked) / (len(marked) + len(unmarked))
    if share < 0.5:  # priority only on a few: probably a deliberate remark, not a scheme
        return []
    quotes = [ctx.q_block(unmarked[0]), ctx.q_block(marked[0])]
    if len(unmarked) > 1:
        quotes.insert(1, ctx.q_block(unmarked[1]))
    return [finding(
        rule, quotes[:3],
        title=f"Приоритет указан у {len(marked)} требований из {len(marked) + len(unmarked)}",
        problem=(f"Часть требований помечена приоритетом ({len(marked)}), у остальных ({len(unmarked)}) приоритета нет. Непонятно, что это значит: "
                 "они менее важны, не обязательны, или приоритет просто забыли. Команда не сможет решить, что делать первым и что резать при нехватке времени."),
        recommendation="Проставьте приоритет у всех требований по одной шкале (например, обязательно / желательно / позже) или уберите его вовсе; допишите, что значит отсутствие приоритета.",
        question=ask("Какой приоритет у требований, где он не указан?",
                     ["Считать обязательными", "Считать желательными", "Автор проставит приоритет везде"]),
    )]


# --- 9. a call to an external system with no behaviour when it does not answer ------------------------------------

_EXTERNAL = re.compile(
    r"внешн\w+\s+(?:систем|сервис|интерфейс|источник|API|провайдер)\w*|сторонн\w+\s+(?:систем|сервис|провайдер|организаци)\w*|кредитн\w+\s+бюро|\bКБ\b|\bГКБ\b|"
    r"запрашива\w+\s+(?:в|у|из)\s+(?:внешн|государственн|сторонн|систем|бюро|сервис|процессинг)\w*|обращ\w+\s+(?:в|к)\s+(?:внешн|государственн|сторонн|бюро|сервис[уа]?\s+\w+|процессинг)\w*|"
    r"\bпроцессинг\w*|платёжн\w+\s+шлюз\w*|платежн\w+\s+шлюз\w*|\bАИС\b|ГБД\s?ФЛ|\bСМЭВ\b|third[- ]party|external\s+(?:system|service|provider|API)|payment\s+gateway|credit\s+bureau|"
    r"вызыва\w+\s+(?:сервис|API|метод)\s+\w+|интеграци\w+\s+с\s+(?:внешн|сторонн)\w+", re.I)
_UNAVAILABLE = re.compile(
    r"недоступн\w+|не\s+отвечает|не\s+ответил\w*|не\s+получен\w*\s+ответ|нет\s+ответа|отсутстви\w+\s+ответа|тайм-?аут\w*|таймаут|сбо[йяе]\w*|\bотказ\w*\s+(?:сервис|систем|внешн)\w*|"
    r"ошибк\w+\s+(?:связи|интеграци|вызова|внешн|обращени|ответа)\w*|при\s+ошибке\s+(?:от|внешн|ответа|вызова)|"
    r"unavailable|not\s+respon\w+|no\s+response|time-?out|fallback|degrad\w+|circuit|retry|\bfail(?:s|ure|ed)?\b|\boutage|\bdown\b|не\s+ответит|не\s+доступ\w+|неуспешн\w+\s+ответ|"
    r"повтор\w*\s+(?:запрос|вызов|попытк)|очеред\w+|отложен\w+|резервн\w+\s+(?:канал|способ|режим)|ручн\w+\s+(?:обработк|режим|провер)|жауап\s+бермесе|қолжетімсіз",
    re.I)


def rule_external_no_answer(ctx: Ctx, rule):
    if ctx.is_api:  # an interface document is the field of api-reliability and status-mismatch
        return []
    if doc_has(ctx, _UNAVAILABLE):
        return []
    hits = []
    for b, t in scan_blocks(ctx):
        for m in _EXTERNAL.finditer(t):
            loc = sentence_at(b, t, m.start())
            if re.search(r"\bзапрос\w*|\bпроверк\w+|\bполуч\w+|\bпередае|\bотправ\w+|\bобраща\w+|\bвызыва\w+|\bсверя\w+|\bcall\w*|\brequest\w*|\bquery|\bsend\w*|\bcheck\w*|\bверифиц\w+|\bзапрашива\w+|\bрасчёт|\bрасчет", loc, re.I):
                hits.append((b.line_start, m.start(), b))
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Обращение к внешней системе без правила на случай, если она не отвечает",
        problem=("Процесс зависит от ответа внешней системы (бюро, процессинга, государственной базы, партнёра), но нигде в документе не сказано, "
                 "что делать, когда она недоступна, молчит или отвечает ошибкой. Такие сбои случаются регулярно, и без правила клиент зависнет "
                 "на полпути, а разработчик выберет поведение сам: отказать, подождать или одобрить без проверки."),
        recommendation="Допишите для каждого обращения: сколько ждём ответ, что показываем клиенту, повторяем ли запрос и можно ли продолжить без ответа.",
        question=ask("Что должна делать система, если внешний сервис не ответил или вернул ошибку?",
                     ["Отказать в операции и показать понятное сообщение", "Поставить операцию в очередь и повторить позже", "Передать заявку на ручную проверку"]),
    )


# --- 10. a setting changed by a person with no record of who and when -----------------------------------------------

_SETTING = re.compile(
    r"(?:администратор\w*|оператор\w*|менеджер\w*|сотрудник\w*|модератор\w*|бухгалтер\w*|пользовател\w+|клиент\w*|admin\w*|operator\w*|manager\w*|employee\w*)"
    r"[^.\n]{0,60}?(?:может\s+|могут\s+|вправе\s+|имеет\s+право\s+|должен\s+|способен\s+|can\s+|may\s+)?(?:измен\w+|задаёт|задает|задают|настраива\w+|меняет|меняют|редактиру\w+|устанавлива\w+|включа\w+|отключа\w+|change\w*|set\w*|configur\w+|edit\w*|modif\w+|enable\w*|disable\w*)"
    r"[^.\n]{0,60}?(?:лимит\w*|тариф\w*|комисси\w+|порог\w*|настройк\w+|парамет\w+|ставк\w+|курс\w*|правил\w+|шаблон\w*|роль\s|роли\s|прав\w+|статус\s+доступа|limit\w*|tariff\w*|fee\w*|threshold\w*|settings?|parameters?|rates?|rules?|templates?|permission\w*)",
    re.I)
_PERSON_SET = re.compile(
    r"(?:администратор\w*|оператор\w*|менеджер\w*|сотрудник\w*|модератор\w*|бухгалтер\w*|admin\w*|operator\w*|manager\w*|employee\w*|back-?office)", re.I)
_SELF_SET = re.compile(r"(?:клиент\w*|пользовател\w+|user|customer)", re.I)
_AUDIT = re.compile(
    r"аудит\w*|журнал\w*\s+(?:измен|действи|аудит|событий|операций|админ)|кто\s+и\s+когда|кем\s+и\s+когда|история\s+измен\w+|протокол\w*\s+(?:измен|действи)|"
    r"логир\w+\s+(?:измен|действи|все\s+действи|операции\s+администратор)|фиксиру\w+\s+(?:автор|кто|кем|дату\s+и\s+(?:время|автор)|измен)|сохраня\w+\s+(?:автор|кто|кем|историю\s+измен)|"
    r"\baudit\w*|change\s+(?:log|history)|who\s+and\s+when|\bwho\s+changed|\bactivity\s+log|\bevent\s+log|\btrail\b|версионирован\w+|лог\w*\s+действий|\bчетыре\s+глаза|four[- ]eyes|maker[- ]checker|двойн\w+\s+подтвержд\w+",
    re.I)


def rule_setting_no_audit(ctx: Ctx, rule):
    if doc_has(ctx, _AUDIT):
        return []
    hits = []
    for b, t in scan_blocks(ctx):
        for m in _SETTING.finditer(t):
            loc = sentence_at(b, t, m.start())
            if not _PERSON_SET.search(loc):
                continue
            # a limit that the client sets for himself is the client's own setting; a record of that is not asked here
            subj = loc[: m.start() - 0] if False else ""
            if re.match(r"\s*(?:клиент|пользовател|user|customer)", m.group(0), re.I) and not _PERSON_SET.search(m.group(0)):
                continue
            hits.append((b.line_start, m.start(), b))
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Сотрудник меняет настройку, а запись «кто и когда изменил» не требуется",
        problem=("Администратор или оператор может менять лимиты, тарифы или правила, но нигде не сказано, что система записывает, кто, когда и что изменил. "
                 "Без такой записи нельзя разобрать жалобу клиента («вчера лимит был больше»), найти виновного при ошибке или злоупотреблении "
                 "и показать регулятору, кто управлял денежными параметрами."),
        recommendation="Добавьте требование: каждое изменение настройки записывается в журнал — кто, когда, старое и новое значение; журнал нельзя править.",
        question=ask("Должна ли система записывать, кто, когда и как менял эту настройку?",
                     ["Да: кто, когда, старое и новое значение, журнал нельзя править", "Да, но только кто и когда", "Нет, запись не нужна"]),
    )


# --- 11. availability, load or volume without a period of measurement ----------------------------------------------

_AVAIL = re.compile(
    r"(?:доступност\w+|готовност\w+|работоспособност\w+|availability|uptime|\bSLA\b|бесперебойн\w+)[^.\n|]{0,60}?\d{2,3}(?:[.,]\d+)?\s*%|"
    r"\d{2,3}(?:[.,]\d+)?\s*%\s*(?:доступност\w+|времени\s+(?:работы|доступност)|uptime|availability)|"
    r"(?:доступн\w+|работа\w+|available|operational)[^.\n|]{0,30}\d{2,3}(?:[.,]\d+)?\s*%\s*(?:времени|of\s+(?:the\s+)?time)",
    re.I)
_AVAIL_PERIOD = re.compile(
    r"(?:в|за)\s+(?:месяц|год|квартал|сутки|неделю|календарн\w+|отчётн\w+|отчетн\w+|любой|каждый|любом|каждом|расчётн\w+|расчетн\w+)|\bмесячн\w+|\bгодов\w+|\bквартальн\w+|\bсуточн\w+|"
    r"\bper\s+(?:month|year|quarter|day|week)|\b(?:monthly|yearly|quarterly|annual|annually|daily|weekly)\b|\b(?:in|over|within)\s+(?:a|any|each|the|every)?\s*(?:calendar\s+)?(?:month|year|quarter|day|week|period)|"
    r"\bпериод\w*\s+(?:измерени|расч[её]т|оценк)|\bпериод\s+(?:\d+|измерени)|\bза\s+\d+\s*(?:дн|месяц|час|сут|недел)|\bв\s+течение\s+(?:календарн|года|месяца|суток|квартала)|\bна\s+протяжени\w+",
    re.I)
_VOLUME = re.compile(
    r"(?:до|не\s+менее|не\s+более|минимум|максимум|выдерж\w+|обрабатыва\w+|поддержива\w+|рассчитан\w*\s+на|up\s+to|at\s+least|at\s+most|handle\w*|support\w*|process\w*)\s+"
    r"(?P<n>\d{1,3}(?:[   ]\d{3})+|\d+)\s*(?:тыс\.?\s*|млн\.?\s*|k\b|тысяч\s+|миллион\w*\s+)?"
    r"(?:одновременн\w+\s+)?(?:операци\w+|транзакци\w+|запрос\w+|пользовател\w+|клиент\w+|сообщени\w+|платеж\w+|платёж\w+|заявк\w+|подключени\w+|сесси\w+|"
    r"transactions?|requests?|users?|customers?|messages?|payments?|connections?|sessions?|operations?)",
    re.I)
_VOL_PERIOD = re.compile(
    r"(?:в|за|на)\s+(?:секунд|минут|час|сутк|день|дни|месяц|год|пик)\w*|/\s*(?:сек|с\b|мин|час|день|сутки|s\b|sec|min|hour|day|month)|\bRPS\b|\bTPS\b|\bQPS\b|\bper\s+(?:second|minute|hour|day|month|year)|"
    r"одновременн\w+|\bconcurrent\w*|simultaneous\w*|\bin\s+(?:a|one|any|each)\s+(?:second|minute|hour|day|month)|\b(?:daily|monthly|hourly|annual)\b|\bв\s+пиковой|\bпиков\w+|"
    r"\bв\s+секунду|\bв\s+минуту|\bв\s+день|\bв\s+месяц|\bв\s+год|\bнеделю|\bдень\b|\bсутки\b|\bчас\b|\bмесяц\b|\bгод\b|\bsecond\b|\bminute\b|\bhour\b|\bday\b|\bmonth\b|\byear\b|\bweek\b",
    re.I)


def rule_measure_period_missing(ctx: Ctx, rule):
    hits = []
    kinds = set()
    for b, t in scan_blocks(ctx):
        for m in _AVAIL.finditer(t):
            loc = sentence_at(b, t, m.start())
            if _AVAIL_PERIOD.search(loc) or _AVAIL_PERIOD.search(_neighborhood(ctx, b)):
                continue
            if b.kind == "table_row" and any(_AVAIL_PERIOD.search(h) for h in b.header):
                continue
            hits.append((b.line_start, m.start(), b))
            kinds.add("avail")
    if not hits:
        return []
    # a general line about the period of measurement anywhere in the document answers the question
    if re.search(r"период\w*\s+(?:измерени|расч[её]т\s+доступност|оценк)\w*|измеря\w+\s+(?:за|в\s+течение|по)\s+(?:календарн|месяц|год|квартал|сутк)|measured\s+(?:over|per|monthly|annually)|measurement\s+(?:period|window)",
                 _all_text(ctx), re.I):
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Доступность в процентах без периода измерения",
        problem=("Требование к доступности задано в процентах («не менее 99,9 %»), но не сказано, за какой период она измеряется: за месяц, квартал или год. "
                 "99,9 % за год допускают почти 9 часов простоя подряд, 99,9 % за месяц — около 43 минут. Без периода (и без правила, "
                 "какие простои не считаются, например плановые работы) показатель нельзя проверить."),
        recommendation="Допишите период и способ измерения: «не менее 99,9 % в календарный месяц, плановые работы не считаются».",
        question=ask("За какой период измеряется доступность и учитываются ли плановые работы?",
                     ["За календарный месяц, плановые работы не считаются", "За календарный год", "За календарный месяц, считаются все простои"]),
    )


def _volume_rule_unused():  # kept out of the registry: «volume without period» fires on correct texts too often
    return _VOLUME, _VOL_PERIOD


# --- 12. user-facing text with no word about the language -----------------------------------------------------------

_USER_TEXT = re.compile(
    r"текст\w*\s+(?:сообщени|уведомлени|ошибк|письм|SMS|смс|push|пуш)\w*|сообщени\w+\s+об\s+ошибк\w+|(?:отправля\w+|направля\w+|получа\w+)\s+(?:клиенту\s+)?(?:SMS|смс|push|пуш|письм\w+|уведомлени\w+|e-?mail)|"
    r"(?:SMS|смс|push|пуш)[- ]?(?:уведомлени\w*|сообщени\w*)|уведомлени\w+\s+клиент\w+|клиент\w*\s+(?:видит|получает)\s+(?:сообщени|уведомлени|письм|SMS|смс|push|пуш)\w*|"
    r"(?:error|notification|confirmation)\s+(?:message|text)s?|\b(?:SMS|push)\s+(?:message|notification|text)s?|sends?\s+(?:an?\s+)?(?:SMS|push|e-?mail|notification)|"
    r"(?:показыва\w+|выводит\w*|отображ\w+)\s+(?:сообщени|подсказк|предупреждени)\w*|текст\w*\s+(?:оферт|соглаши|договор|ответа)\w*",
    re.I)
_LANG = re.compile(
    r"язык\w*|\bрусск\w+|\bказахск\w+|\bанглийск\w+|на\s+русском|на\s+казахском|\bRU\b|\bKK\b|\bKZ\b|\bEN\b|\bkk[-_]KZ\b|\bru[-_]RU\b|локализ\w+|мультиязыч\w+|многоязыч\w+|"
    r"\blanguages?\b|\blocale\w*|\blocali[sz]\w+|\bEnglish\b|\bRussian\b|\bKazakh\b|\bi18n\b|\bl10n\b|\bAccept-Language\b|қазақ\w*\s+тіл|орыс\w*\s+тіл|тіл\w*|на\s+двух\s+языках|двуязыч\w+|"
    r"языков\w+\s+(?:настройк|версии)",
    re.I)


def rule_language_missing(ctx: Ctx, rule):
    if doc_has(ctx, _LANG):
        return []
    hits = []
    for b, t in scan_blocks(ctx):
        for m in _USER_TEXT.finditer(t):
            hits.append((b.line_start, m.start(), b))
    # the rule looks for a document that gives user-facing texts as a theme: at least two places, one with the literal text
    if len({h[0] for h in hits}) < 2:
        return []
    if not any(re.search(r"(?:push|пуш|SMS|смс|сообщени\w*|уведомлени\w*|текст\w*|подсказк\w*)\s*[:—-]?\s*[«\"][^»\"]{6,}|текст\w*\s+(?:ошибк|сообщени|уведомлени)", b.text, re.I) for _, _, b in hits):
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Тексты для клиента есть, а язык не назван",
        problem=("В требованиях есть тексты, которые видит клиент (сообщения об ошибках, SMS, push, письма), но нигде не сказано, на каком языке или языках они пишутся "
                 "и как выбирается язык: по настройке приложения, по языку клиента, всегда русский. Для банка в Казахстане это обычный вопрос: "
                 "тексты нужны на казахском и русском, а переводы и сроки на них нужно закладывать заранее."),
        recommendation="Допишите языки (например, казахский, русский, английский), как выбирается язык текста и на каком языке текст, если перевода нет.",
        question=ask("На каких языках показываются клиенту сообщения и уведомления и как выбирается язык?",
                     ["Казахский и русский, язык по настройке клиента", "Только русский", "Казахский, русский и английский"]),
    )


# --- 13. a user story without its purpose; a story without acceptance criteria ------------------------------------

_STORY = re.compile(
    r"(?:^|(?<=[.!?]\s))(?:Как|Будучи)\s+[^,.\n«»]{2,70},\s*(?:я\s+)?(?:хочу|хотел\w*\s+бы|могу|должен|должна|нужно|необходимо|мне\s+нужн\w+)"
    r"|\bAs\s+(?:an?|the)\s+[^,.\n]{2,70},\s*I\s+(?:want|need|would\s+like|can|should|must|wish)"
    r"|[^.\n]{2,70}\s+ретінде,?\s*[^.\n]{0,160}?(?:қалаймын|болуы\s+керек|қажет)",
    re.I)
_PURPOSE = re.compile(
    r"\bчтобы\b|\bчтоб\b|для\s+того\b|\bради\b|с\s+целью|\bso\s+that\b|\bin\s+order\s+to\b|\bso\s+I\s+can\b|"
    r"үшін|мақсатында|\bchtoby\b",
    re.I)


def _stories(ctx: Ctx):
    out = []
    for b in ctx.doc.blocks:
        if b.kind in ("heading", "code"):
            continue
        t = visible(b.text)
        m = _STORY.search(t)
        if m:
            out.append((b, t, m))
    return out


def rule_story_no_purpose(ctx: Ctx, rule):
    hits = []
    for b, t, m in _stories(ctx):
        # the purpose may stand in the next line of the same story
        nxt = _neighborhood(ctx, b) if not _PURPOSE.search(t) else t
        if _PURPOSE.search(t[m.start():]) or (b.kind != "table_row" and _PURPOSE.match(visible(nxt[len(t):]).strip())):
            continue
        hits.append((b.line_start, m.start(), b))
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="История пользователя без цели («чтобы …»)",
        problem=("История записана как «Как <роль>, я хочу …», но без части «чтобы …». Цель — то, ради чего нужна функция: по ней проверяют, "
                 "что решение вообще помогает, выбирают между вариантами и отсекают лишнее. Без цели разработчик строит то, что написано, "
                 "а не то, что нужно, и тестировщику не с чем сверить результат."),
        recommendation="Допишите цель к каждой истории: «Как кассир, я хочу …, чтобы …».",
        question=ask("Какую цель преследует этот пользователь: ради чего ему нужна эта функция?", ["Автор допишет цель к истории"]),
    )


_CRITERIA_HEAD = re.compile(r"критери\w+\s+(?:при[её]мк|принятия|готовности)|acceptance\s+criteria|\bAC\b|қабылдау\s+критерий|Дано\b[^.\n]*Когда|\bGiven\b[^.\n]*\bwhen\b", re.I)


def rule_story_no_criteria(ctx: Ctx, rule):
    stories = _stories(ctx)
    if len(stories) < 4:
        return []
    blocks = ctx.doc.blocks
    index = {b.bid: k for k, b in enumerate(blocks)}
    story_ids = {b.bid for b, _, _ in stories}
    with_c, without = [], []
    for b, t, m in stories:
        k = index[b.bid]
        has = False
        for nb in blocks[k + 1:]:
            if nb.bid in story_ids:
                break
            if nb.kind == "heading":
                has = bool(_CRITERIA_HEAD.search(nb.text))
                if not has and nb.line_start - b.line_end > 0:
                    break
                continue
            if _CRITERIA_HEAD.search(visible(nb.text)):
                has = True
                break
        # criteria may also stand in the same block as the story (a table row or a long paragraph)
        has = has or bool(_CRITERIA_HEAD.search(t))
        (with_c if has else without).append(b)
    if len(with_c) < 3 or not without:
        return []
    names = [b.doc_id or short(visible(b.text), 30) for b in without]
    return [finding(
        rule, [ctx.q_block(b) for b in without[:5]],
        title=f"История без критериев приёмки: {short(', '.join(names), 60)}",
        problem=(f"У {len(with_c)} историй документа есть критерии приёмки, а у {len(without)} — нет: {short(', '.join(names), 100)}. "
                 "Без критериев нечем подтвердить, что история выполнена: тестировщик напишет проверки по догадке, а заказчик примет работу по ощущению."),
        recommendation="Допишите критерии приёмки каждой истории: 2–5 проверяемых условий с числами и ожидаемым результатом.",
        question=ask(f"Какие критерии приёмки у истории «{short(names[0], 50)}»?", ["Автор допишет критерии"], "qa"),
    )]


# --- 15. money calculation with no rounding rule (interest, instalment, recalculation) ------------------------------

_CALC = re.compile(
    r"начисля\w+\s+(?:проценты|процентов|вознагражд)|проценты\s+начисля\w+|начислени\w+\s+(?:процент|вознагражд)\w*|рассчитыва\w+\s+(?:как|по\s+формуле|исходя)|по\s+формуле|"
    r"аннуитет\w*|пересч[её]т\w*\s+(?:график|платеж|платёж|сумм|остатк|процент)\w*|пересчитыва\w+\s+(?:график|платеж|платёж|сумм|остат|процент)\w*|"
    r"\baccru\w+|\binterest\b|\bamorti[sz]\w+|\bprorat\w+|\bpro[- ]rata|за\s+фактическ\w+\s+количеств\w+\s+дн|на\s+количество\s+дней|"
    r"умножа\w+\s+на|делит\w+\s+на|делени\w+\s+на|умножени\w+",
    re.I)


def rule_calc_rounding_missing(ctx: Ctx, rule):
    if doc_has(ctx, _ROUNDING):
        return []
    if rule_rounding_missing(ctx, rule):  # the older rule already speaks about this document
        return []
    hits = []
    for b, t in scan_blocks(ctx):
        for m in _CALC.finditer(t):
            loc = sentence_at(b, t, m.start())
            if _MONEY_WORD.search(loc) or re.search(r"платеж|платёж|процент\w*|\bпроценты\b|график", loc, re.I):
                hits.append((b.line_start, m.start(), b))
    if not hits:
        return []
    hits.sort(key=lambda x: (x[0], x[1]))
    return emit(
        ctx, rule, [(b, p) for _, p, b in hits],
        title="Расчёт процентов или платежа без правила округления",
        problem=("Документ требует рассчитать проценты, платёж, переплату или пересчитать график, но нигде не сказано, как округлять результат: до тиына или до целого тенге, "
                 "на каждом шаге или только в конце, вверх или по правилам арифметики. У ежемесячного платежа по кредиту копейки в каждом месяце "
                 "складываются в заметную разницу, и два реализованных расчёта разойдутся."),
        recommendation="Добавьте правило: «Суммы округляются до тиына по математическим правилам, только в конце расчёта; остаток переносится в последний платёж» (или своё).",
        question=ask("Как округлять проценты и платежи: до какой единицы, в какую сторону и на каком шаге расчёта?",
                     ["До тиына, математически, в конце расчёта", "До целого тенге, вверх, остаток в последний платёж"]),
        severity="MAJOR",
    )


# --- helpers ---------------------------------------------------------------------------------------------------------


def _sentences_all(ctx: Ctx) -> list[str]:
    cache = getattr(ctx, "_general_sentences", None)
    if cache is not None:
        return cache
    out = []
    for b in ctx.doc.blocks:
        if b.kind in ("code", "heading"):
            continue
        t = visible(b.text)
        out.extend(x for x in re.split(r"(?<=[.!?])\s+|\s\|\s", t) if x)
    try:
        ctx._general_sentences = out
    except Exception:
        pass
    return out


GENERAL_RULES = {
    "retry-unbounded": rule_retry_unbounded,
    "timeout-no-outcome": rule_timeout_no_outcome,
    "percent-base-missing": rule_percent_base_missing,
    "once-without-period": rule_once_without_period,
    "retention-missing": rule_retention_missing,
    "masking-missing": rule_masking_missing,
    "req-id-clash": rule_req_id_clash,
    "req-id-gap": rule_req_id_gap,
    "priority-mixed": rule_priority_mixed,
    "external-no-answer": rule_external_no_answer,
    "setting-no-audit": rule_setting_no_audit,
    "measure-period-missing": rule_measure_period_missing,
    "language-missing": rule_language_missing,
    "story-no-purpose": rule_story_no_purpose,
    "story-no-criteria": rule_story_no_criteria,
    "calc-rounding-missing": rule_calc_rounding_missing,
}
