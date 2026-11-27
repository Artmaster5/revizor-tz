"""Rules on words with two readings and on one statement made in two places.

Each rule is silent when the document settles the question anywhere (a general statement counts) or when the
second reading is not even possible in the document (the rule needs both readings to be present).
One grouped finding per rule per document; the quotes are the first places where the question arises.
Several rules ask a question instead of claiming a defect: the document may mean either reading.
"""
from __future__ import annotations

import re

from ._common import Ctx, ask, finding, fmt_list, sentences, visible
from .time_rules import emit, scan_blocks, sentence_at

MAX_QUOTES = 5


def _texts(ctx: Ctx):
    """(block, visible text) of every block where a requirement may be stated."""
    return [(b, t) for b, t in scan_blocks(ctx)]


def _whole(ctx: Ctx) -> str:
    return "\n".join(t for _, t in _texts(ctx))


# --- 1. a period collects operations, but the document has several dated events of one operation --------------

_PERIOD_WORD = re.compile(
    r"календарн\w+\s+месяц\w*|в\s+месяц\b|за\s+месяц\b|в\s+течение\s+месяца|в\s+месяце\b|за\s+сутки|в\s+сутки|за\s+неделю|в\s+неделю|за\s+квартал|"
    r"в\s+течение\s+(?:суток|недели|дня)|per\s+(?:month|day|week)|calendar\s+month|(?:in|during)\s+a\s+(?:calendar\s+)?(?:month|day|week)|"
    r"бір\s+(?:ай|тәулік|апта)\w*\s+ішінде|айына|тәулігіне",
    re.I)
_COLLECT = re.compile(
    r"лимит\w*|не\s+более|не\s+свыше|не\s+превыша\w+|максимум|максимальн\w+|ограничен\w+|предел\w*|потолок|порог\w*|не\s+менее|накопленн\w+|нарастающ\w+|"
    r"cap(?:ped)?\b|limit|\bup\s+to\b|at\s+most|no\s+more\s+than|maximum|threshold|total|шектеу\w*|артық\s+емес",
    re.I)
_COLLECT_WHAT = re.compile(
    r"сумм\w+|количеств\w+|кэшбэк\w*|кешбэк\w*|начисл\w+|операци\w+|покупк\w+|платеж\w*|платёж\w*|транзакци\w+|трат\w+|расход\w+|перевод\w*|снят\w+|изъят\w+|"
    r"cashback|purchases?|operations?|transactions?|payments?|transfers?|withdraw\w*|amount|spend\w*|\b[\d ]+\s*(?:KZT|USD|EUR|RUB|₸|тенге)|\b\d+\s+(?:операци|раз)",
    re.I)
_EVENTS = {
    "покупка / совершение": re.compile(r"(?<!курс\s)(?<!курсы\s)(?<!курсу\s)\bпокупк\w+(?!\s+и\s+продаж)|совершени\w+\s+(?:операции|платежа|покупки)|\bpurchases?\b(?!\s+rate)", re.I),
    "авторизация": re.compile(r"авторизаци\w+\s+(?:операци|платеж|карт|покупк|транзакци|сумм)|(?:карточн|платёжн|платежн)\w+\s+авторизаци\w+|\b(?:card|payment|transaction)\s+authori[sz]ation|холдирова\w+|\bholds?\b|блокировк\w+\s+сумм|\bхолд\w*", re.I),
    "проведение": re.compile(r"\bпровед[её]н\w+|\bпроведени\w+|\bпроводк\w+|клиринг\w*|\bposting\b|\bposted\b|\bsettle\w*", re.I),
    "отправка": re.compile(r"\bотправк\w+|\bотправлен\w*|\bsent\b|\bsubmission\b", re.I),
}
# a sentence that says by which event an operation falls into a period
_PERIOD_NOUN = re.compile(r"месяц\w*|сутк\w+|день|дня|дн[её]м|недел\w+|период\w*|квартал\w*|month|\bday\b|\bweek\b|period|тәулік\w*", re.I)
_EVENT_DATE = re.compile(
    r"дат\w+\s+(?:провед\w+|покупк\w+|авторизаци\w+|списани\w+|отправк\w+|зачислени\w+|совершени\w+|операци\w+|транзакци\w+|платеж\w+)"
    r"|(?:момент\w*)\s+(?:провед\w+|покупк\w+|авторизаци\w+|списани\w+|отправк\w+|совершени\w+)"
    r"|(?:posting|purchase|authori[sz]ation|settlement|transaction|booking)\s+(?:date|time)|(?:date|time)\s+of\s+(?:the\s+)?(?:posting|purchase|authori[sz]ation|settlement|transaction)"
    r"|(?:провед\w+|покупк\w+|авторизаци\w+)\s+(?:операци\w+\s+)?(?:в|за|по)\s+(?:месяц|период|сутк)",
    re.I)
_TIE_LINK = re.compile(
    r"определя\w+|считае\w+|относ\w+|попада\w+|учитыва\w+|входит|входят|\bпо\b|\bна\b|determin\w+|counted|based|belong\w*|fall\w*|assign\w+|\bby\b|\bon\b|\bat\b", re.I)


def _has_tie(text: str) -> bool:
    """A sentence that says by which event an operation falls into a period."""
    for st in sentences(text):
        if _PERIOD_NOUN.search(st) and _EVENT_DATE.search(st) and _TIE_LINK.search(st):
            return True
    return False


def rule_period_event(ctx: Ctx, rule):
    texts = _texts(ctx)
    whole = "\n".join(t for _, t in texts)
    present = [name for name, rx in _EVENTS.items() if rx.search(whole)]
    if len(present) < 2 or ("покупка / совершение" not in present and "авторизация" not in present):
        return []
    if _has_tie(whole):
        return []
    hits = []
    for b, t in texts:
        for m in _PERIOD_WORD.finditer(t):
            loc = sentence_at(b, t, m.start())
            if not (_COLLECT.search(loc) and _COLLECT_WHAT.search(loc)):
                continue
            if _EVENT_DATE.search(loc) or any(rx.search(loc) for rx in _EVENTS.values()):
                continue  # the sentence itself names an event
            hits.append((b, m.start()))
    if not hits:
        return []
    events = fmt_list(present, 4)
    return emit(
        ctx, rule, hits,
        title="Период собирает операции, а в какой период попадает операция — не сказано",
        problem=(f"Лимиты, суммы или начисления заданы за период («месяц», «сутки», «неделя»). Но у одной операции в документе есть несколько дат "
                 f"({events}), и нигде не сказано, по какой из них операция относится к периоду. Операция, купленная 31-го и проведённая 1-го, "
                 "попадёт в разные месяцы у разработчика и у тестировщика; суммы, лимиты и начисления разойдутся."),
        recommendation="Добавьте одно правило: «Операция относится к месяцу (суткам) по дате проведения» — или назовите другое событие.",
        question=ask("По какому событию операция относится к периоду: покупка, авторизация, проведение или списание?",
                     ["По дате покупки", "По дате авторизации", "По дате проведения (списания)"]),
    )


# --- 2. a place name next to a time boundary: a time zone or a place? -----------------------------------------

_PLACE = (r"(?:Астан\w*|Алматы|Алмат\w+|Нур-?Султан\w*|Шымкент\w*|Караганд\w+|Москв\w+|Лондон\w*|Нью-?Йорк\w*|Казахстан\w*|России|"
          r"Astana|Almaty|Moscow|London|New\s+York|Kazakhstan)")
_PLACE_RX = re.compile(r"(?<![\w-])(?:в|по|для|of|in)\s+" + _PLACE + r"(?![\w-])|(?<![\w-])" + _PLACE + r"\s+(?:времен\w+|время)", re.I)
_BOUNDARY = re.compile(
    r"границ\w+\s+(?:дня|суток|месяца)|начал\w+\s+(?:дня|суток|месяца)|конец\s+(?:дня|суток|месяца)|\bполночь|00:00|23:59|"
    r"календарн\w+\s+(?:день|сутки|месяц)|\bсутк\w+|\bза\s+день\b|в\s+(?:этот|тот)\s+(?:же\s+)?день|\bдат\w+\s+(?:операции|покупки|платежа|списания|отправки)|"
    r"midnight|calendar\s+(?:day|month)|cut-?off|end\s+of\s+(?:the\s+)?day|"
    r"(?:месяц|сутки|день|дата)\w*\s+определя\w+|по\s+дате\s+(?:совершения|покупки|операции|проведения|платежа)|определя\w+\s+по\s+дате",
    re.I)
_ZONE_EXPLICIT = re.compile(
    r"\bUTC\b|\bGMT\b|\bMSK\b|Asia/\w+|часов\w+\s+пояс|тайм-?зон|time\s?zone|уақыт\s+белдеу|UTC\s?[+-]|\d{2}:\d{2}(?::\d{2})?(?:Z\b|[+-]\d{2}:?\d{2}\b)", re.I)


def rule_city_zone(ctx: Ctx, rule):
    texts = _texts(ctx)
    whole = "\n".join(t for _, t in texts)
    if _ZONE_EXPLICIT.search(whole):
        return []
    hits = []
    for b, t in texts:
        for m in _PLACE_RX.finditer(t):
            loc = sentence_at(b, t, m.start())
            if not _BOUNDARY.search(loc):
                continue
            if re.search(r"\bпо\s+времени\b|\bвремя\s+(?:Астан|Алмат)|(?:Астан|Алмат)\w*\s+(?:время|времени)|\bwhere\b|\bместо\s+совершения", loc, re.I):
                continue  # «по времени Астаны»: the zone is named by the wording itself
            hits.append((b, m.start()))
    if not hits:
        return []
    return emit(
        ctx, rule, hits,
        title="Город рядом с границей дня или месяца: это часовой пояс или место операции?",
        problem=("Рядом с границей суток, месяца или датой операции стоит название города или страны («дата операции в Алматы»). Это можно прочитать двумя способами: "
                 "как часовой пояс, по которому считается граница дня, или как место, где совершена операция. Часового пояса (UTC+5, «время Астаны») в документе нигде нет. "
                 "Для операции за границей или клиента в другом поясе разработчик и тестировщик получат разные даты."),
        recommendation="Назовите пояс прямо: «граница суток — 00:00 по времени Астаны (UTC+5)»; если имеется в виду место совершения, так и напишите.",
        question=ask("Город в этом правиле — это часовой пояс (по какому считается дата) или место совершения операции?",
                     ["Часовой пояс: время Астаны (UTC+5)", "Место совершения операции"]),
    )


# --- 3. «return to the previous state» without a list of what is restored ----------------------------------------

_REVERT = re.compile(
    r"(?:возвраща\w+|вернуть\w*|вернут\w+|возврат\w*|восстанавлива\w+|восстановить|восстановлен\w+)\s+(?:\w+\s+){0,2}?(?:в\s+)?(?:прежн\w+|исходн\w+|предыдущ\w+|первоначальн\w+)\s+(?:состояни\w+|значени\w+|вид\w*|статус\w*|положени\w+|режим\w*)"
    r"|(?:прежн\w+|исходн\w+|предыдущ\w+|первоначальн\w+)\s+(?:состояни\w+|значени\w+|статус\w*)\s+(?:восстанавлива\w+|возвращает\w+)"
    r"|\bоткат\w*|\bоткатыва\w+|\bоткатить\b|отмен\w+\s+(?:всех\s+|всё\s+|все\s+)?(?:изменени\w+|внесённ\w+\s+изменени\w+)|отменя\w+\s+(?:все\s+)?изменени\w+"
    r"|\brollback\b|\brolls?\s+back\b|\brolled\s+back\b|\brolling\s+back\b|\brevert\w*\b|restore\w*\s+(?:the\s+)?(?:previous|original|prior)\s+state|undo\s+(?:all\s+)?changes"
    r"|бұрынғы\s+күйіне|бастапқы\s+күйіне|өзгерістерді\s+болдырмау",
    re.I)
_REVERT_LIST = re.compile(
    r"а\s+именно|в\s+том\s+числе|включая|то\s+есть|т\.\s?е\.|:\s*$|:\s|namely|including|i\.e\.|в\s+частности"
    r"|(?:лимит|статус|баланс|остаток|сумм|бонус|кэшбэк|баллы|счётчик|комисси|блокировк|данные|настройк)\w*\s*(?:,|и)\s*(?:лимит|статус|баланс|остаток|сумм|бонус|кэшбэк|баллы|счётчик|комисси|блокировк|данные|настройк)",
    re.I)


def rule_revert_no_list(ctx: Ctx, rule):
    blocks = ctx.doc.blocks
    index = {b.bid: k for k, b in enumerate(blocks)}
    hits = []
    for b, t in _texts(ctx):
        for m in _REVERT.finditer(t):
            loc = sentence_at(b, t, m.start())
            if _REVERT_LIST.search(loc):
                continue
            k = index.get(b.bid)
            nb = blocks[k + 1] if k is not None and k + 1 < len(blocks) else None
            if nb is not None and nb.kind in ("list_item", "table_row", "code") and nb.line_start - b.line_end <= 2 and b.kind == "paragraph":
                continue  # a list follows the sentence
            if m.group(0).lower().startswith("откат") and re.search(r"транзакци\w+\s+(?:БД|базы)|\bтранзакци\w+\s+баз|database\s+transaction", loc, re.I):
                continue
            hits.append((b, m.start()))
    if not hits:
        return []
    return emit(
        ctx, rule, hits,
        title="«Вернуть прежнее состояние» без перечня того, что именно возвращается",
        problem=("Требование говорит «вернуть в прежнее состояние», «откатить» или «отменить изменения», но не перечисляет, что именно возвращается: "
                 "статус, сумма, лимит, начисленные бонусы, комиссия, блокировка, уведомления. Одни вернут только статус, другие — и деньги, и счётчики; "
                 "то, что уже отправлено клиенту, вернуть нельзя совсем."),
        recommendation="Допишите список: «возвращаются: статус заявки, сумма на счёте, лимит, бонусы; не возвращаются: отправленные уведомления».",
        question=ask("Что именно должно вернуться в прежнее состояние, а что остаётся как есть?",
                     ["Только статус", "Статус, деньги и счётчики (лимиты, бонусы)", "Всё, кроме отправленных уведомлений"]),
    )


# --- 4. one quantity tied to «at the moment of purchase» and to «posted operations» ------------------------------

_AT_MOMENT = re.compile(
    r"(?:в|на)\s+момент\w*\s+(?:покупки|оплаты|совершени\w+|транзакции|платежа)|при\s+(?:покупке|оплате|совершении\s+(?:покупки|платежа|операции))"
    r"|at\s+the\s+(?:time|moment)\s+of\s+(?:purchase|payment|the\s+transaction)",
    re.I)
_POSTED = re.compile(
    r"провед[её]нн\w+|завершённ\w+|завершенн\w+|обработанн\w+|исполненн\w+|подтверждённ\w+|подтвержденн\w+|\bposted\b|\bsettled\b|\bcompleted\s+(?:operations|transactions|purchases)\b",
    re.I)
_QUANT = re.compile(
    r"лимит\w*|кэшбэк\w*|кешбэк\w*|начисл\w+|сумм\w+|остат\w+|баланс\w*|счётчик\w*|порог\w*|комисси\w+|бонус\w*|балл\w+|бюджет\w*|расход\w+|трат\w+|cashback|limit|balance|total|amount|bonus|points|budget|spend\w*",
    re.I)


def _stem(w: str) -> str:
    return w.lower().replace("ё", "е")[:5]


def rule_moment_vs_posted(ctx: Ctx, rule):
    a_hits, p_hits = [], []
    for b, t in _texts(ctx):
        for m in _AT_MOMENT.finditer(t):
            loc = sentence_at(b, t, m.start())
            if _QUANT.search(loc):
                a_hits.append((b, m.start(), loc))
        for m in _POSTED.finditer(t):
            loc = sentence_at(b, t, m.start())
            if _QUANT.search(loc):
                p_hits.append((b, m.start(), loc))
    if not a_hits or not p_hits:
        return []
    whole = _whole(ctx)
    if re.search(r"(?:провед\w+|завершённ\w+|обработанн\w+)[^.]{0,60}(?:момент|в\s+момент)|(?:момент|в\s+момент)[^.]{0,60}(?:провед\w+|завершённ\w+)|"
                 r"считается\s+(?:в\s+момент|по\s+дате)|окончательн\w+\s+(?:сумм|расч)", whole, re.I):
        return []
    pair = None
    for ab, ap, aloc in a_hits:
        for pb, pp, ploc in p_hits:
            if pb.bid == ab.bid and ploc == aloc:
                continue
            pair = (ab, ap, pb, pp)
            break
        if pair:
            break
    if not pair:
        return []
    ab, ap, pb, pp = pair
    return [finding(
        rule, [ctx.q_sentence(ab, ap), ctx.q_sentence(pb, pp)],
        title="Одна величина привязана и к «моменту покупки», и к «проведённым» операциям",
        problem=("Одна и та же величина (лимит, сумма, начисление, остаток) в одном месте считается в момент покупки, а в другом — по проведённым операциям. "
                 "Это разные множества: между покупкой и проведением проходят часы или дни, часть операций отменяется, сумма меняется. "
                 "В итоге система в момент покупки видит одно число, а при расчёте — другое, и клиент увидит расхождение."),
        recommendation="Выберите, по какому событию считается величина (покупка, авторизация или проведение), и опишите, что делать, если операцию потом отменили или изменили.",
        question=ask("Величина считается в момент покупки или только по проведённым операциям, и что будет с ней после отмены операции?",
                     ["Только по проведённым операциям", "В момент покупки, при отмене пересчитываем", "Предварительно в момент покупки, окончательно после проведения"]),
    )]


# --- 5. a sentence of one section restated in another section ------------------------------------------------------

_STOP = {"должен", "должна", "должны", "должно", "может", "могут", "будет", "будут", "быть", "также", "если", "когда", "после", "перед", "через",
         "этого", "этой", "этом", "который", "которая", "которые", "система", "системы", "клиент", "клиента"}


_NUM_WORDS = {"один": "1", "одна": "1", "одного": "1", "одной": "1", "два": "2", "две": "2", "двух": "2", "три": "3", "трех": "3", "трёх": "3",
              "четыре": "4", "четырех": "4", "четырёх": "4", "пять": "5", "пяти": "5", "шесть": "6", "шести": "6", "семь": "7", "семи": "7",
              "восемь": "8", "восьми": "8", "девять": "9", "девяти": "9", "десять": "10", "десяти": "10"}
_NUM_WORD_RX = re.compile(r"\b(" + "|".join(_NUM_WORDS) + r")\b", re.I)


def _content(sentence: str) -> list[str]:
    sentence = _NUM_WORD_RX.sub(lambda m: _NUM_WORDS[m.group(1).lower()], sentence)
    s = re.sub(r"^\s*(?:[A-ZА-ЯЁ]{1,4}[- ]?\d+[\w.-]*[.:)]?\s*)", "", sentence)
    toks = re.findall(r"[a-zа-яё]{4,}|\d+(?:[.,]\d+)?", s.lower().replace("ё", "е"))
    return [t if t[0].isdigit() else t[:6] for t in toks if t not in _STOP]


def rule_restated(ctx: Ctx, rule):
    items = []
    for b, t in _texts(ctx):
        if b.kind not in ("paragraph", "list_item"):
            continue
        for s in sentences(t):
            toks = _content(s)
            words = [x for x in toks if not x[0].isdigit()]
            if len(words) >= 7:
                pos = t.find(s)
                items.append((b, s, toks, set(words), max(pos, 0)))
    out = []
    used: set[tuple[str, str]] = set()
    for i, (b1, s1, t1, w1, p1) in enumerate(items):
        for b2, s2, t2, w2, p2 in items[i + 1:]:
            if (b1.section or "") == (b2.section or "") or ctx.rid_of.get(b1.bid) == ctx.rid_of.get(b2.bid):
                continue
            inter = len(w1 & w2)
            union = len(w1 | w2)
            if union == 0 or inter / union < 0.7 or inter < 6:
                continue
            if re.sub(r"\W+", " ", s1.lower()) == re.sub(r"\W+", " ", s2.lower()):
                continue  # an exact copy is the business of the rule «duplicate»
            key = (b1.bid, b2.bid)
            if key in used:
                continue
            used.add(key)
            differ = sorted(x for x in t1 if x[0].isdigit()) != sorted(x for x in t2 if x[0].isdigit())
            out.append(finding(
                rule, [ctx.q_sentence(b1, p1), ctx.q_sentence(b2, p2)],
                title=("Одно правило записано в двух разделах, и числа в нём разные" if differ else "Одно правило записано в двух разделах разными словами"),
                problem=("Два места документа в разных разделах говорят почти одно и то же"
                         + (", но числа или условия в них не совпадают — непонятно, какое верно. " if differ else ". ")
                         + "Какое из мест считать главным, не сказано; при правке одного второе забудут поправить, и требования разойдутся."),
                recommendation="Оставьте правило в одном разделе, а в другом сошлитесь на него по номеру; если мест два — назовите главное.",
                question=ask("Какое из двух мест считать главным источником правила?", ["Первое", "Второе", "Это разные случаи — уточнить, чем они отличаются"]),
                severity="MAJOR" if differ else "MINOR",
            ))
            if len(out) >= 3:
                return out
    return out


# --- 6. money words with two readings -----------------------------------------------------------------------------

_CUR_KINDS = {
    "тенге": re.compile(r"₸|тенге|\bтг\b|\bKZT\b|теңге|тиын", re.I),
    "доллары": re.compile(r"\$|\bUSD\b|долл\w+|dollars?", re.I),
    "евро": re.compile(r"€|\bEUR\b|евро|euros?", re.I),
    "рубли": re.compile(r"₽|\bRUB\b|руб\w*", re.I),
    "иная валюта": re.compile(r"\bCNY\b|юан\w+|\bGBP\b|\bTRY\b|\bAED\b|иностранн\w+\s+валют|\bforeign\s+currency", re.I),
}
_CONVERT = re.compile(r"конвертац\w+|по\s+курсу|курс\w*\s+(?:обмена|банка|конвертации|покупки|продажи)|в\s+валюте\s+(?:счёта|операции|карты)|exchange\s+rate|\bconversion\b", re.I)
_OPAMOUNT = re.compile(
    r"сумм\w+\s+(?:операци\w+|транзакци\w+|платеж\w+|платёж\w+|покупк\w+|перевод\w+)|(?:transaction|operation|purchase|payment)\s+amount", re.I)
_AMOUNT_CUR_OK = re.compile(
    r"в\s+(?:валюте|тенге|тг)\b|в\s+эквивалент\w+|\bKZT\b|\bUSD\b|\bEUR\b|\bRUB\b|₸|\$|€|₽|тенге|эквивалент\w*|\bin\s+(?:the\s+)?(?:account|operation|transaction|card)\s+currency|\bin\s+KZT\b|"
    r"валют\w+\s+(?:счёта|операции|карты)",
    re.I)
_AMOUNT_DEF = re.compile(r"сумм\w+\s+(?:операци\w+|транзакци\w+|платеж\w+|покупк\w+)\s*(?:—|-|:|это|означает|определяется|считается|равна|задаётся)|amount\s+(?:means|is\s+defined)", re.I)


def _money_ambiguity(ctx: Ctx, rule, *, trigger, silent_local, silent_global, needs, title, problem, recommendation, question):
    texts = _texts(ctx)
    whole = "\n".join(t for _, t in texts)
    if not needs(whole):
        return []
    if silent_global is not None and silent_global.search(whole):
        return []
    hits = []
    for b, t in texts:
        for m in trigger.finditer(t):
            loc = sentence_at(b, t, m.start())
            if silent_local.search(loc):
                continue
            hits.append((b, m.start()))
    if not hits:
        return []
    return emit(ctx, rule, hits, title=title, problem=problem, recommendation=recommendation, question=question)


def rule_amount_currency(ctx: Ctx, rule):
    def needs(whole: str) -> bool:
        kinds = [k for k, rx in _CUR_KINDS.items() if rx.search(whole)]
        return len(kinds) >= 2 or (bool(_CONVERT.search(whole)) and len(kinds) >= 1)

    return _money_ambiguity(
        ctx, rule, trigger=_OPAMOUNT, silent_local=_AMOUNT_CUR_OK, silent_global=_AMOUNT_DEF, needs=needs,
        title="«Сумма операции» в документе с несколькими валютами: в какой валюте?",
        problem=("В документе есть и несколько валют (или конвертация), и «сумма операции» без указания валюты. Это можно прочитать как сумму в валюте операции, "
                 "в валюте счёта или в тенге после конвертации. Пороги и лимиты, сравниваемые с такой суммой, дадут разный результат для покупки за границей."),
        recommendation="Определите один раз: «Сумма операции — сумма в валюте счёта после конвертации» (или в валюте операции) и пишите так везде.",
        question=ask("В какой валюте берётся «сумма операции» для расчётов и лимитов?",
                     ["В валюте операции", "В валюте счёта (после конвертации)", "В тенге по курсу на дату операции"]),
    )


_BAL = re.compile(
    r"(?<![\w-])(?:остат(?:ок|ка|ку|ком|ке)\s+(?:на\s+)?(?:счёт\w*|карт\w+|средств|денег)|баланс\w*\s+(?:счёта|карты|клиента)|"
    r"(?:account|card)\s+balance|balance\s+(?:of|on)\s+the\s+(?:account|card))(?![\w-])", re.I)
_BAL_NEEDS = re.compile(
    r"холд\w*|заморо\w+|\bhold(?:s|ed)?\b|заблокированн\w+\s+(?:сумм|средств)|блокировк\w+\s+(?:сумм|средств|денег)|блокируют\w*\s+(?:сумм|средств)|"
    r"резервирован\w+|резервиру\w+\s+(?:сумм|средств)|авторизационн\w+\s+блок|\bblocked\s+(?:amount|funds)|authori[sz]ation\s+hold", re.I)
_BAL_LOCAL_OK = re.compile(
    r"доступн\w+|фактическ\w+|учётн\w+|учетн\w+|с\s+учётом|с\s+учетом|без\s+учёта|без\s+учета|\bavailable\b|\bcurrent\b|\bledger\b|\bactual\b|блокиров\w+|холд|заморо\w+|\bhold\b",
    re.I)
_BAL_DEF = re.compile(
    r"(?:доступн\w+\s+(?:остат\w+|баланс\w*|средств\w+)|фактическ\w+\s+(?:остат\w+|баланс\w*)|available\s+balance|ledger\s+balance)\s*(?:—|-|:|=|это|равн\w+|определяется|рассчитывается|включает|means|is)"
    r"|(?:остат\w+|баланс\w*)\s+(?:—|-|:)?\s*(?:это|равен|равна|определяется|рассчитывается|означает)[^.]{0,60}(?:блокиров\w+|холд|заморо\w+)"
    r"|(?:остат\w+|баланс\w*)[^.]{0,50}(?:включа\w+|учитыва\w+|не\s+учитыва\w+|уменьша\w+|с\s+учётом|без\s+учёта)[^.]{0,30}(?:блокиров\w+|холд|заморо\w+|авторизац)",
    re.I)


def rule_balance_hold(ctx: Ctx, rule):
    return _money_ambiguity(
        ctx, rule, trigger=_BAL, silent_local=_BAL_LOCAL_OK, silent_global=_BAL_DEF, needs=lambda w: bool(_BAL_NEEDS.search(w)),
        title="«Остаток» или «баланс» в документе с блокировками: с блокировками или без?",
        problem=("В документе есть блокировки (холды, авторизации, зарезервированные суммы), и рядом — «остаток» или «баланс» без уточнения. "
                 "Остаток на счёте и сумма, которую клиент может потратить, — разные числа: между ними блокированные суммы. "
                 "Проверка «хватает ли денег» по одному и по другому даст разный ответ."),
        recommendation="Назовите величины отдельно: «фактический остаток» (с блокировками) и «доступный остаток» (без блокированных сумм) — и укажите, какой берётся в каждом правиле.",
        question=ask("«Остаток» в этих правилах — полный остаток на счёте или доступный к использованию (за вычетом блокировок)?",
                     ["Доступный (за вычетом блокировок)", "Фактический (с блокировками)"]),
    )


_OPDATE = re.compile(r"(?<![\w-])дат\w+\s+(?:операци\w+|транзакци\w+|платеж\w+|платёж\w+|совершени\w+\s+операци\w+)|(?:transaction|operation|payment)\s+date", re.I)
_OPDATE_AUTH = _EVENTS["авторизация"]
_OPDATE_POST = re.compile(r"провед[её]н\w+|провод\w+|\bposting\b|\bposted\b|клиринг\w*|списани\w+|\bsettle\w*", re.I)
_OPDATE_LOCAL_OK = re.compile(r"авторизаци\w+|провед\w+|списани\w+|совершени\w+|покупк\w+|\bauthori\w+|\bposting\b|\bposted\b|\bsettle\w*|\bpurchase\b", re.I)
_OPDATE_DEF = re.compile(
    r"дат\w+\s+(?:операци\w+|транзакци\w+|платеж\w+)\s*(?:—|-|:|=|это|означает|определяется|считается|равна)|(?:transaction|operation)\s+date\s*(?:—|-|:|=|is|means)", re.I)


def rule_operation_date(ctx: Ctx, rule):
    return _money_ambiguity(
        ctx, rule, trigger=_OPDATE, silent_local=_OPDATE_LOCAL_OK, silent_global=_OPDATE_DEF,
        needs=lambda w: bool(_OPDATE_AUTH.search(w)) and bool(_OPDATE_POST.search(w)),
        title="«Дата операции» в документе, где у операции есть авторизация и проведение",
        problem=("В документе у операции есть и авторизация, и проведение (списание) — это разные даты, иногда через несколько дней. "
                 "«Дата операции» без уточнения может означать любую из них. От выбора зависят сроки, периоды, отчёты и выписки."),
        recommendation="Определите один раз: «Дата операции — дата авторизации (или проведения)» — и пользуйтесь только этим словом.",
        question=ask("«Дата операции» — это дата авторизации или дата проведения?", ["Дата авторизации", "Дата проведения (списания)"]),
    )


# --- 7. a limit on an amount: with the fee or without it ---------------------------------------------------------------

_AMOUNT_LIMIT = re.compile(
    r"(?:минимальн\w+|максимальн\w+|предельн\w+|лимит\w*|не\s+более|не\s+менее|не\s+превыша\w+|свыше|до)\s+[^.\n|]{0,50}?сумм\w+\s+(?:перевод\w+|платеж\w+|платёж\w+|операци\w+|пополнени\w+|снят\w+|выплат\w+)"
    r"|сумм\w+\s+(?:перевод\w+|платеж\w+|платёж\w+|операци\w+|пополнени\w+|снят\w+)[^.\n|]{0,50}?(?:не\s+более|не\s+менее|не\s+превыша\w+|не\s+может\s+(?:превышать|быть\s+меньше)|лимит|минимум|максимум)"
    r"|(?:minimum|maximum|limit)\s+[^.\n|]{0,40}?(?:transfer|payment|transaction)\s+amount|(?:transfer|payment|transaction)\s+amount[^.\n|]{0,40}?(?:at\s+most|at\s+least|exceed|limit)",
    re.I)
_FEE_WORD = re.compile(r"комисси\w+|\bfee\b|\bfees\b|\bcommission\b", re.I)
_FEE_LOCAL = re.compile(r"комисси\w+|\bfee\b|\bcommission\b", re.I)
_FEE_DEF = re.compile(
    r"(?:с\s+учётом|с\s+учетом|включая|без\s+учёта|без\s+учета|не\s+включая|не\s+учитывая|не\s+включает|включает|не\s+входит|входит|плюс|сверх)\s+комисси\w+|комисси\w+\s+(?:не\s+)?(?:входит|включается|учитывается|включена|включен|не\s+включена|сверх|взимается\s+сверх|удерживается\s+сверх)"
    r"|(?:including|excluding|plus|inclusive\s+of|exclusive\s+of|net\s+of)\s+(?:the\s+)?fees?|fees?\s+(?:is|are)\s+(?:not\s+)?(?:included|excluded)|сумма\s+с\s+комиссией|сумма\s+без\s+комиссии",
    re.I)


def rule_fee_inclusion(ctx: Ctx, rule):
    return _money_ambiguity(
        ctx, rule, trigger=_AMOUNT_LIMIT, silent_local=_FEE_LOCAL, silent_global=_FEE_DEF, needs=lambda w: bool(_FEE_WORD.search(w)),
        title="Лимит на сумму операции при наличии комиссии: сумма с комиссией или без?",
        problem=("Для суммы перевода или платежа заданы границы (минимум, максимум, лимит), и в документе же есть комиссия. Не сказано, входит ли комиссия в проверяемую сумму. "
                 "Перевод на предельную сумму плюс комиссия либо пройдёт, либо будет отклонён — зависит от прочтения, и граница сдвигается на размер комиссии."),
        recommendation="Допишите: «Лимит проверяется по сумме перевода без комиссии» (или «с учётом комиссии») и повторите это в каждом правиле о границах.",
        question=ask("Лимит проверяется по сумме перевода без комиссии или вместе с комиссией?", ["Без комиссии", "Вместе с комиссией"]),
    )


# --- 8. one actor under several names ---------------------------------------------------------------------------------

_ACTOR_GROUPS = [
    ("клиент", re.compile(r"\bклиент\w*", re.I)),
    ("пользователь", re.compile(r"\bпользовател\w+", re.I)),
    ("держатель карты", re.compile(r"\bдержател\w+\s+карт\w+", re.I)),
    ("заёмщик", re.compile(r"\bзаёмщик\w*|\bзаемщик\w*", re.I)),
    ("вкладчик", re.compile(r"\bвкладчик\w*", re.I)),
    ("плательщик", re.compile(r"\bплательщик\w*", re.I)),
    ("отправитель", re.compile(r"\bотправител\w+", re.I)),
    ("customer", re.compile(r"\bcustomers?\b", re.I)),
    ("user", re.compile(r"\busers?\b", re.I)),
    ("cardholder", re.compile(r"\bcardholders?\b", re.I)),
]
_ACTOR_SAME = re.compile(
    r"(?:клиент|пользовател\w+|держател\w+\s+карт\w+|customer|user|cardholder)\w*\s*(?:\(|—|-|–|,)\s*(?:далее|т\.\s?е\.|то\s+есть|или|он\s+же|also|aka|a\.k\.a)"
    r"|\(\s*далее\s*[—–-]?\s*(?:«|\")?(?:клиент|пользовател|держател|customer|user)|далее\s*[—–-]\s*(?:«|\")?(?:клиент|пользовател|держател)"
    r"|(?:клиент|пользовател\w+|держател\w+\s+карт\w+)[^.]{0,40}(?:один\s+и\s+тот\s+же|синоним|тождествен|то\s+же\s+самое|понимаются\s+как|понимается)"
    r"|(?:термин\w*|определени\w+|глоссари\w+)[^.]{0,80}(?:клиент|пользовател|держател)|(?:клиент|пользовател|держател)\w*\s*(?:=|—)\s*(?:клиент|пользовател|держател|физическ)",
    re.I)
_ROLE_SECTION = ("роль", "роли", "акторы", "actors", "glossary", "термин", "глоссар", "участник")


def rule_actor_names(ctx: Ctx, rule):
    whole = _whole(ctx)
    present = [(name, rx, len(rx.findall(whole))) for name, rx in _ACTOR_GROUPS[:7]]
    present = [x for x in present if x[2] >= 3]
    if len(present) < 2:
        present = [(name, rx, len(rx.findall(whole))) for name, rx in _ACTOR_GROUPS[7:]]
        present = [x for x in present if x[2] >= 3]
        if len(present) < 2:
            return []
    if _ACTOR_SAME.search(whole):
        return []
    # a document with a list of roles or a glossary names them on purpose
    if any(any(w in (b.section or "").lower() for w in _ROLE_SECTION) for b in ctx.doc.blocks if b.kind == "heading" or b.section):
        return []
    names = [p[0] for p in present]
    hits = []
    for name, rx, _ in present[:3]:
        for b, t in _texts(ctx):
            m = rx.search(t)
            if m:
                hits.append((b, m.start()))
                break
    return emit(
        ctx, rule, hits,
        title=f"Один участник назван по-разному: {fmt_list(names, 3)}",
        problem=(f"В требованиях действуют «{'», «'.join(names[:3])}», и нигде не сказано, один это человек или разные роли. "
                 "Если разные, у каждой должны быть свои права и правила; если один — слова надо свести к одному. Иначе тестировщик не знает, "
                 "проверять ли одно и то же правило для каждой «роли», а разработчик может дать им разные права."),
        recommendation="Допишите в начало: «Клиент, пользователь и держатель карты в документе означают одного и того же человека» или разведите роли и опишите права каждой.",
        question=ask(f"«{names[0]}» и «{names[1]}» — это один и тот же участник или разные роли?", ["Один и тот же участник", "Разные роли с разными правами"]),
    )


AMBIGUITY_RULES = {
    "period-event": rule_period_event,
    "city-or-zone": rule_city_zone,
    "revert-no-list": rule_revert_no_list,
    "moment-vs-posted": rule_moment_vs_posted,
    "restated-rule": rule_restated,
    "amount-currency": rule_amount_currency,
    "balance-hold": rule_balance_hold,
    "operation-date": rule_operation_date,
    "fee-inclusion": rule_fee_inclusion,
    "actor-names": rule_actor_names,
}
