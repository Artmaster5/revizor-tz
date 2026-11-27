"""Defects made of several parts: a check-list is answered as one finding that names every missing part.

Covers refunds that change a counted value, the error body shape, error codes against endpoints, access codes,
ownership checks, list endpoints, notifications, closed value lists and parameter tables.
Every rule is silent when the document answers the question anywhere (a general statement counts).
One finding per rule per document; quotes are the first places where the question arises.
"""
from __future__ import annotations

import json
import re

from ..model import Block
from ._common import NAME_COL, TYPE_COL, Ctx, ask, column, finding, visible
from .api_rules import _governing_status

MAX_QUOTES = 3


def _text(ctx: Ctx) -> str:
    return visible(ctx.doc.text)


def _prose_blocks(ctx: Ctx):
    """Blocks where a requirement may be stated: no code, no headings, no change history."""
    for b in ctx.doc.blocks:
        if b.kind in ("code", "heading"):
            continue
        yield b, visible(b.text)


def _missing_list(items: list[str]) -> str:
    return "; ".join(items)


# --- 1. refund-checklist ---------------------------------------------------------------------------------

_REFUND = re.compile(
    r"возврат\w*\s+(?:товар|покупк|платеж|платёж|оплат|средств|денег|суммы\s+покупки)|(?:вернул\w*|возвращ\w+|вернут\w+)\s+(?:товар|покупк)|"
    r"покупк\w*[^.]{0,40}(?:возвращ\w+|вернул\w*|отмен\w+|сторнир\w+)|отмен\w+\s+(?:покупк|платеж|платёж)\w*|сторнир\w+|"
    r"возврат\w*\s+(?:уменьш|увеличив|снижа|пересчит|вычита|не\s+учитыва)\w*|(?:уменьш|вычита)\w+\s+[^.]{0,40}возврат\w*|"
    r"\brefund\w*|\bchargeback|\breversal|(?:purchases?|payments?|transactions?)\s+(?:that\s+)?(?:was|were|is|are|been)?\s*(?:returned|reversed|refunded|cancell?ed)|"
    r"\breturned\s+(?:or\s+reversed\s+)?(?:purchases?|goods)", re.I)
_COUNTED = re.compile(
    r"кэшбэк\w*|кешбэк\w*|cash-?back|бонус\w*|\bbonus\w*|баллы?\b|баллов|\bpoints?\b|лимит\w*|\blimits?\b|накоплен\w*|"
    r"сумм\w+\s+(?:расходов|трат|покупок)|\bрасход\w*|потраченн\w*|\bspend(?:ing)?\b|оборот\w*|\bturnover\b|\bquota\b|квот\w*", re.I)
_REFUND_PARTS = (
    ("частичный возврат (только часть суммы покупки)",
     re.compile(r"частичн|\bpartial|часть\s+(?:суммы|покупки|стоимости)|(?:не\s+)?полн\w+\s+(?:суммы|возврат)", re.I)),
    ("возврат в другом периоде, чем покупка (что с уже закрытым периодом)",
     re.compile(r"(?:друг\w+|следующ\w+|прошл\w+|закрыт\w+|предыдущ\w+)\s+(?:месяц\w*|период\w*|квартал\w*)|"
                r"(?:месяц\w*|период\w*)\s+(?:исходной\s+покупки|покупки|возврата|начисления)|в\s+месяце\s+покупки|"
                r"\b(?:later|another|different|previous|closed)\s+(?:month|period)|\bafter\s+the\s+(?:month|period)", re.I)),
    ("связь возврата с исходной покупкой",
     re.compile(r"исходн\w+\s+(?:покупк|операци|транзакци|платеж|платёж|оплат)\w*|оригинальн\w+\s+(?:покупк|операци)\w*|"
                r"привяз\w+\s+(?:к|по)\s+(?:покупк|исходн|операци)|к\s+(?:той\s+же|этой)\s+покупке|"
                r"\boriginal\s+(?:purchase|transaction|payment|operation)|\blinked\s+to\s+the\s+(?:purchase|original)", re.I)),
    ("отмена авторизации как отдельный случай, не равный возврату",
     re.compile(r"отмен\w+\s+авторизаци|авторизаци\w+\s+(?:отмен|сняти|снята|аннулир)|сняти\w+\s+(?:блокировк|холд)|"
                r"разблокировк\w+\s+(?:сумм|средств)|\bхолд\b|\bauthori[sz]ation\s+(?:reversal|void|release)|\bvoid(?:ed|s)?\b|"
                r"\breversal\s+of\s+(?:the\s+)?authori[sz]ation", re.I)),
    ("что будет, если значение после возврата уйдёт ниже нуля",
     re.compile(r"отрицательн|ниже\s+нул|меньше\s+нул|\bв\s+минус|уйд\w+\s+в\s+минус|не\s+(?:может|должн\w+)\s+быть\s+(?:меньше|ниже)\s+(?:0|нул)|"
                r"\bnegative\b|below\s+zero|less\s+than\s+zero|не\s+опуска\w+\s+ниже", re.I)),
)


def rule_refund_checklist(ctx: Ctx, rule):
    hits = []
    for b, t in _prose_blocks(ctx):
        r = _REFUND.search(t)
        c = _COUNTED.search(t)
        if r and c and abs(r.start() - c.start()) <= 160:
            hits.append((b, r.start()))
    if not hits:
        return []
    text = _text(ctx)
    missing = [what for what, rx in _REFUND_PARTS if not rx.search(text)]
    if len(missing) < 3:
        return []
    quotes = [ctx.q_sentence(b, p) for b, p in hits[:MAX_QUOTES]]
    return [finding(
        rule, quotes,
        title="Возврат меняет считаемую величину, а правила возврата описаны не полностью",
        problem=("Возврат или отмена покупки меняет величину, которую считает система (кэшбэк, бонусы, лимит, расходы). "
                 "В документе не описано: " + _missing_list(missing) + ". Без этого разработчик и тестировщик по-разному "
                 "поймут, что делать с уже начисленным или потраченным, и величина разойдётся с реальными деньгами клиента."),
        recommendation=("Допишите правила возврата: " + _missing_list(missing) + ". Для каждого случая — как меняется "
                        "считаемая величина и что происходит, если она уходит ниже нуля."),
        question=ask("Как возврат покупки меняет эту величину в каждом из перечисленных случаев?",
                     ["Описать все случаи в требованиях", "Принять правило «возврат пересчитывает величину по исходной покупке»",
                      "Возвраты не влияют на величину — записать это явно"]),
        severity="MAJOR" if len(missing) >= 4 else "MINOR",
    )]


# --- 2. error-body-shapes ----------------------------------------------------------------------------------

_STRONG_ERR_KEYS = {"error", "errors", "errorcode", "error_code", "errormessage", "error_message", "errordescription", "error_description"}
_JSON_INLINE = re.compile(r"\{[^{}\n]*\"[^{}\n]*\}")


def _signature(obj) -> frozenset[str]:
    keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(str(k))
            if str(k).lower() in ("error", "errors") and isinstance(v, dict):
                keys.update(f"{k}.{x}" for x in v)
    return frozenset(keys)


def _error_examples(ctx: Ctx):
    out = []
    for i, b in enumerate(ctx.doc.blocks):
        cands = []
        if b.kind == "code":
            try:
                cands.append((json.loads(b.text), 0))
            except ValueError:
                pass
        else:
            t = visible(b.text)
            for m in _JSON_INLINE.finditer(t):
                try:
                    cands.append((json.loads(m.group(0)), m.start()))
                except ValueError:
                    pass
        for obj, pos in cands:
            if not isinstance(obj, dict) or not obj:
                continue
            strong = any(str(k).lower() in _STRONG_ERR_KEYS for k in obj)
            status = ""
            if b.kind == "code":
                status, _ = _governing_status(ctx, i)
            elif re.match(r"\s*(?:[-*]\s*)?`?(?:HTTP\s*)?([45]\d\d)\b", visible(b.text)):
                status = re.match(r"\s*(?:[-*]\s*)?`?(?:HTTP\s*)?([45]\d\d)\b", visible(b.text)).group(1)
            if strong or status[:1] in ("4", "5"):
                if status[:1] in ("2", "3"):
                    continue  # a success body
                out.append((b, pos, _signature(obj)))
    return out


def rule_error_body_shapes(ctx: Ctx, rule):
    ex = _error_examples(ctx)
    if len(ex) < 2:
        return []
    for k, (b1, p1, s1) in enumerate(ex):
        for b2, p2, s2 in ex[k + 1:]:
            if s1 == s2 or s1 <= s2 or s2 <= s1:
                continue  # same shape, or one only adds a field
            only1, only2 = sorted(s1 - s2), sorted(s2 - s1)
            q1 = ctx.q_sentence(b1, p1) if b1.kind != "code" else ctx.q_block(b1)
            q2 = ctx.q_sentence(b2, p2) if b2.kind != "code" else ctx.q_block(b2)
            return [finding(
                rule, [q1, q2],
                title="Тело ошибки в двух местах устроено по-разному",
                problem=(f"В одном примере ошибки есть поля {', '.join(only1)}, в другом — {', '.join(only2)}. "
                         "Клиентское приложение разбирает тело ошибки по одной схеме; при двух схемах оно сломается на одной из них, "
                         "а тестировщик не знает, какую проверять."),
                recommendation="Опишите одну схему тела ошибки (имена полей, какие обязательны) и приведите по ней все примеры.",
                question=ask("Какую схему тела ошибки считать верной?",
                             ["Схему из первого примера", "Схему из второго примера", "Новая единая схема — описать"], "architect"),
                severity="MAJOR",
            )]
    return []


# --- 3. error-code-unlinked ------------------------------------------------------------------------------------

_HTTP_CELL = re.compile(r"^\s*`?([45]\d\d)\b")
_WHEN_COL = re.compile(r"when|условие|когда|причин|reason|cause|описание|description|meaning|смысл|ситуаци|trigger", re.I)
_ERR_COL = re.compile(r"error|ошибк|errors|коды?\s+ошибок", re.I)
_EMPTYISH = re.compile(r"^\s*(?:[-—–.]*|tbd|tbc|n/?a|todo|уточнить|будет\s+позже)\s*$", re.I)


def rule_error_code_unlinked(ctx: Ctx, rule):
    if not ctx.is_api:
        return []
    declared_in_table: dict[str, Block] = {}
    empty_cond: list[tuple[str, Block]] = []
    used: dict[str, Block] = {}
    for t in ctx.tables:
        h = t.header
        wcol = next((k for k, x in enumerate(h) if _WHEN_COL.search(x)), -1)
        ecol = next((k for k, x in enumerate(h) if _ERR_COL.fullmatch(x.strip()) or _ERR_COL.match(x.strip()) and "ошибк" in x.lower()), -1)
        for b in t.rows:
            cells = b.cells
            if len(cells) != len(h):
                continue
            hc = next((k for k, c in enumerate(cells) if _HTTP_CELL.match(c) and re.fullmatch(r"`?[45]\d\d`?(?:\s+[A-Za-z ]+)?", c.strip())), -1)
            if hc >= 0 and wcol >= 0 and wcol != hc and not re.search(r"method|метод|path|путь|endpoint", " ".join(h), re.I):
                code = _HTTP_CELL.match(cells[hc]).group(1)
                declared_in_table.setdefault(code, b)
                if _EMPTYISH.match(visible(cells[wcol])):
                    empty_cond.append((code, b))
            if ecol >= 0:
                for code in re.findall(r"(?<!\d)([45]\d\d)(?!\d)", cells[ecol]):
                    used.setdefault(code, b)
    parts = []
    quotes = []
    if empty_cond:
        parts.append("для кодов " + ", ".join(sorted({c for c, _ in empty_cond})) + " в таблице ошибок не указано условие")
        quotes += [ctx.q_block(b) for _, b in empty_cond[:MAX_QUOTES]]
    if declared_in_table and used:
        no_use = sorted(set(declared_in_table) - set(used))
        no_decl = sorted(set(used) - set(declared_in_table))
        if no_use:
            parts.append("коды " + ", ".join(no_use) + " описаны в таблице ошибок, но ни один метод их не возвращает")
            quotes.append(ctx.q_block(declared_in_table[no_use[0]]))
        if no_decl:
            parts.append("коды " + ", ".join(no_decl) + " указаны у методов, но в таблице ошибок не описаны")
            quotes.append(used[no_decl[0]] and ctx.q_block(used[no_decl[0]]))
    if not parts:
        return []
    return [finding(
        rule, quotes[:MAX_QUOTES + 1],
        title="Коды ошибок и методы интерфейса не сходятся",
        problem=("В таблицах интерфейса видно: " + "; ".join(parts) + ". Код без условия разработчик вернёт по своему усмотрению, "
                 "а код без метода нельзя проверить: непонятно, какой запрос его вызывает."),
        recommendation="Для каждого кода укажите условие и список методов, которые его возвращают; лишние коды уберите, недостающие опишите.",
        question=ask("Какие коды ошибок и в каких случаях возвращает каждый метод?",
                     ["Дополнить таблицу ошибок и столбец ошибок у методов", "Убрать коды, которые ничем не вызываются"], "architect"),
    )]


# --- 4. api-access-codes -------------------------------------------------------------------------------------------

_AUTH = re.compile(r"\bbearer\b|\boauth\b|\bjwt\b|\bmtls\b|mutual\s+tls|access[ _-]?token|api[- ]?key|токен\w*\s+доступа|"
                   r"заголов\w+\s+authorization|header\s+authorization|\bauthorization\s*:", re.I)
_C401 = re.compile(r"\b401\b|unauthori[sz]ed", re.I)
_C403 = re.compile(r"\b403\b|forbidden", re.I)
_C404 = re.compile(r"\b404\b|not[ _]found|не\s+найден", re.I)
_PATH_PARAM = re.compile(r"(?<![\w.$])/[\w\-/{}.:]*\{\w+\}[\w\-/{}.:]*")
_NO_AUTH_ENDPOINT = re.compile(r"authori[sz]ation\s+is\s+not\s+required|без\s+авторизации|авторизация\s+не\s+требуется", re.I)


def rule_api_access_codes(ctx: Ctx, rule):
    if not ctx.is_api:
        return []
    text = _text(ctx)
    auth_b = next((b for b in ctx.doc.blocks if b.kind != "code" and _AUTH.search(visible(b.text))), None)
    path_b = next((b for b in ctx.doc.blocks if b.kind != "code" and _PATH_PARAM.search(visible(b.text))
                   and re.search(r"\b(?:GET|POST|PUT|PATCH|DELETE)\b", visible(b.text) + " " + (ctx.heading_for(b).text if ctx.heading_for(b) else ""))), None)
    missing = []
    quotes = []
    if auth_b is not None and not _C401.search(text) and not _C403.search(text):
        missing.append("коды 401 и 403 (запрос без токена или с чужими правами)")
        m = _AUTH.search(visible(auth_b.text))
        quotes.append(ctx.q_sentence(auth_b, m.start()))
    elif auth_b is not None and not _C401.search(text):
        missing.append("код 401 (запрос без токена или с просроченным токеном)")
        quotes.append(ctx.q_sentence(auth_b, _AUTH.search(visible(auth_b.text)).start()))
    if path_b is not None and not _C404.search(text):
        missing.append("код 404 (объект с таким идентификатором не найден)")
        m = _PATH_PARAM.search(visible(path_b.text))
        quotes.append(ctx.q_sentence(path_b, m.start()) if m else ctx.q_block(path_b))
    if not missing:
        return []
    return [finding(
        rule, quotes,
        title="В интерфейсе не описаны ответы на отказ в доступе или на неизвестный объект",
        problem=("В описании интерфейса нет ответов на случаи отказа: " + _missing_list(missing) +
                 ". Клиентское приложение не знает, как отличить «нет доступа» от «нет объекта» и когда обновлять токен; "
                 "тестировщик не может написать проверку."),
        recommendation="Добавьте в таблицу ответов: " + _missing_list(missing) + " — с условием, при котором код возвращается, и телом ошибки.",
        question=ask("Какие коды возвращает интерфейс без токена, при чужих правах и для несуществующего объекта?",
                     ["401 без токена, 403 при чужих правах, 404 для несуществующего", "Описать другие коды — перечислить"], "architect"),
    )]


# --- 5. api-ownership ---------------------------------------------------------------------------------------------------

_OBJ_ID = re.compile(r"\{(\w*(?:card|account|client|customer|user|deposit|loan|contract|wallet|session|policy|payment|order|merchant|payout)\w*)\}", re.I)
_OBJ_ID_RU = re.compile(r"\b(?:card|account|client|customer|user|deposit|loan|contract|wallet|iin|pan)[_-]?id\b|\bиин\b", re.I)
_OWNERSHIP = re.compile(
    r"принадлеж|belongs?\s+to|owned\s+by|\bowner\b|\bчуж\w+|\banother\s+(?:partner|client|customer|user|merchant|account)|"
    r"\bother\s+(?:partners?|clients?|customers?|users?|merchants?)|только\s+(?:свои|собственн|по\s+своим)|\bown\s+(?:accounts?|cards?|data|objects?)|"
    r"(?:права|доступ)\s+(?:клиента|вызывающ|пользовател|на\s+(?:объект|карт|сч[её]т))|\bissued\s+to\b|выдан\w*\s+(?:этому|данному|клиент|сессии)|"
    r"привязан\w*\s+к\s+(?:клиент|сессии|пользовател)|\bbound\s+to\b|вызывающ\w+\s+(?:имеет|вправе)|caller\s+(?:owns|has\s+access|is\s+authori)|"
    r"has\s+access\s+to\s+the|\bIDOR\b|\bscope\b|\bтолько\s+(?:клиент|владел)", re.I)


def rule_api_ownership(ctx: Ctx, rule):
    if not ctx.is_api:
        return []
    text = _text(ctx)
    if not _AUTH.search(text) or _OWNERSHIP.search(text):
        return []
    hit = None
    for b in ctx.doc.blocks:
        if b.kind == "code":
            continue
        t = visible(b.text)
        head = ctx.heading_for(b)
        ctxt = t + " " + (head.text if head is not None and head is not b else "")
        if not re.search(r"\b(?:GET|POST|PUT|PATCH|DELETE)\b", ctxt):
            continue
        m = _OBJ_ID.search(ctxt) or _OBJ_ID_RU.search(ctxt)
        if m:
            hit = (b, t, m)
            break
    if hit is None:
        return []
    b, t, m = hit
    q = ctx.q_block(b) if b.kind in ("heading", "table_row") else ctx.q_sentence(b, 0)
    return [finding(
        rule, [q],
        title="Не сказано, что проверяется право вызывающего на объект",
        problem=("Метод читает или меняет данные по идентификатору (карты, счёта, клиента, заявки), но нигде не сказано, что сервис проверяет, "
                 "что этот объект принадлежит вызывающему. Если проверки нет, подставив чужой идентификатор, можно прочитать или изменить чужие "
                 "данные клиента."),
        recommendation=("Допишите правило: сервис сверяет владельца объекта с вызывающим по токену и для чужого объекта возвращает один и тот же ответ "
                        "(например, 404), чтобы не раскрывать, что объект существует."),
        question=ask("Проверяет ли сервис, что объект принадлежит вызывающему, и что возвращается для чужого объекта?",
                     ["Да: сверка по токену, для чужого объекта — 404", "Да: для чужого объекта — 403", "Проверка выполняется во внешней системе — указать, в какой"],
                     "security"),
        severity="MAJOR",
    )]


# --- 6. list-no-order-limit --------------------------------------------------------------------------------------------------

_GET_LIST = re.compile(r"\bGET\s+(`?)(/[\w\-/{}.:$]*?[A-Za-z]{2,}s?)(?:\?[^\s`|]*)?\1(?=[\s`|]|$)")
_LISTISH = re.compile(r"список|перечень|\blists?\b|\bhistory\b|истори|выписк|\barray\b|коллекци|\bcollection\b|все\s+\w+|\ball\s+\w+|\bpurchase\s+list\b|"
                      r"\btransactions\b|операции", re.I)
_ORDER = re.compile(r"сортиров|упорядоч|\border(?:ed)?\s+by\b|\bsort\w*|\bsorted\b|от\s+(?:новых|старых|нов\w+|стар\w+)|новые\s+(?:сначала|первыми)|"
                    r"\bnewest\b|\boldest\b|\bdesc\b|\basc\b|в\s+порядке|по\s+(?:убыв|возраст)", re.I)
_SIZE = re.compile(r"\bpage[_ ]?size\b|\bper[_ ]?page\b|\blimit\b|\boffset\b|\bcursor\b|размер\s+страницы|пагинац|pagination|\bpaginat|постранич|"
                   r"не\s+более\s+\d+\s+(?:записей|элементов|строк|позиций|items|records|results)|up\s+to\s+\d+\s+(?:items|records|results)|"
                   r"максимум\s+\d+\s+(?:записей|элементов|строк)|\bpage\b|\bстраниц", re.I)


def _endpoint_scope(ctx: Ctx, b: Block) -> str:
    """Text of the block, and for a heading-style endpoint the paragraphs under it up to the next heading."""
    if b.kind != "heading":
        return visible(b.text)
    out = [b.text]
    started = False
    for x in ctx.doc.blocks:
        if x is b:
            started = True
            continue
        if started:
            if x.kind == "heading":
                break
            out.append(visible(x.text))
    return "\n".join(out)


def rule_list_no_order_limit(ctx: Ctx, rule):
    if not ctx.is_api:
        return []
    text = _text(ctx)
    hits = []
    for b in ctx.doc.blocks:
        if b.kind == "code":
            continue
        t = visible(b.text)
        m = _GET_LIST.search(t)
        if not m:
            continue
        path = m.group(2)
        if re.search(r"\}$", path):
            continue  # one object by identifier, not a list
        scope = _endpoint_scope(ctx, b)
        if not _LISTISH.search(scope):
            continue
        if _ORDER.search(scope) or _SIZE.search(scope):
            continue
        hits.append((b, m.start(), path))
    if not hits:
        return []
    if _SIZE.search(text) and _ORDER.search(text):
        return []  # the document states both somewhere (a general rule for all lists)
    quotes = [ctx.q_block(b) if b.kind in ("heading", "table_row") else ctx.q_sentence(b, p) for b, p, _ in hits[:MAX_QUOTES]]
    paths = ", ".join(sorted({p for _, _, p in hits})[:4])
    return [finding(
        rule, quotes,
        title="Метод возвращает список без порядка и ограничения размера",
        problem=(f"Методы {paths} возвращают список записей, но не сказано, в каком порядке они идут и сколько записей приходит за один ответ. "
                 "Без порядка две страницы могут пересечься или пропустить записи, а без предела размера большой список перегрузит клиент и сервер; "
                 "тестировщик не знает, чего ждать."),
        recommendation="Укажите порядок (например, по дате, от новых к старым), размер страницы по умолчанию и максимум, а также способ получить следующую страницу.",
        question=ask("В каком порядке и по сколько записей возвращает список метод?",
                     ["От новых к старым, 20 записей по умолчанию, максимум 100", "Другой порядок и размер — указать"], "architect"),
    )]


# --- 7. notification-checklist ------------------------------------------------------------------------------------------------------

_CHANNEL = re.compile(r"\bpush\b|пуш|push-?уведомлен\w*|\bsms\b|\bсмс\b|e-?mail|электронн\w+\s+почт|\bписьм\w+|уведомлен\w+|\bnotification|мессенджер|whatsapp", re.I)
_SEND_VERB = re.compile(r"отправ|приход\w+|получа\w+|направл|присыл|уведомля\w+|высыл|оповещ|\bsent?\b|\bsends?\b|\bnotif\w+\s+(?:is|are|will)|\bdeliver", re.I)
_NOTIF_PARTS = (
    ("текст и содержимое уведомления",
     re.compile(r"текст\w*\s+(?:уведомлен|сообщени|push|пуш|смс|sms|письма)|шаблон|\btemplate|содержани\w+\s+(?:уведомлен|сообщени)|"
                r"\bmessage\s+text|в\s+(?:уведомлении|сообщении|push|смс|sms)\s*(?::|указ|будет|приходит|показан|содерж)|"
                r"(?:уведомлени|сообщени)\w+\s+(?:содерж|включа|показыва)|(?:push|пуш|sms|смс)\s*:\s*\w+|"
                r"(?:уведомлен|push|пуш|смс|sms)\w*\s+(?:приход\w+|с)\s+(?:с\s+)?(?:сумм|остатк|текст)|в\s+уведомлении", re.I)),
    ("язык уведомления",
     re.compile(r"\bязык|\bязыке|\blanguage|\blocale|\bлокализ|на\s+(?:русском|казахском|английском)|русск\w+\s+и\s+казахск|казахск\w+\s+и\s+русск|на\s+языке", re.I)),
    ("что делать, если уведомление не доставлено",
     re.compile(r"не\s+(?:удалось\s+)?(?:доставлен|доставить|доставл|отправлен|отправить|дошл)|недоставл|ошибк\w+\s+(?:доставки|отправки)|"
                r"\bfail\w*\s+(?:to\s+)?(?:deliver|send)|delivery\s+(?:fail|error|status|receipt)|резервн\w+\s+канал|\bfallback\b|"
                r"статус\w*\s+доставки|\bbounce|канал\w*\s+недоступ|при\s+недоступности\s+(?:push|канал|sms|смс|сервис)|"
                r"если\s+(?:push|пуш|sms|смс|e-?mail|сервис\s+уведомлен)\w*\s+(?:недоступ|не\s+работа)|повторн\w+\s+попытк", re.I)),
    ("защита от повторной отправки",
     re.compile(r"повторн\w+\s+(?:отправ|уведомлен|push|пуш|смс|sms)|не\s+чаще|не\s+более\s+(?:одного|\d+)\s+(?:раз|уведомлен|push|смс|sms|сообщени)|"
                r"дубл|не\s+повторя|антиспам|частот\w+\s+(?:отправки|уведомлен)|\brepeat|\bduplicat|deduplic|\brate[- ]limit|только\s+один\s+раз|"
                r"однократн|один\s+раз|раз\s+в\s+(?:сутки|день|час)", re.I)),
    ("время суток, когда уведомления можно отправлять",
     re.compile(r"ночн|тих\w+\s+час|\bquiet\s+hours|do\s+not\s+disturb|не\s+(?:раньше|позже|ночью)|время\s+отправки|\bв\s+течение\s+дня|"
                r"\bс\s+\d{1,2}[:.]\d\d\s+до\s+\d{1,2}[:.]\d\d|\bрабоч\w+\s+(?:час|врем)|\bдн[её]м\b|часов\w+\s+пояс", re.I)),
    ("как клиент отключает уведомления",
     re.compile(r"отключ\w+|выключ\w+|отказ\w*\s+от\s+(?:уведомлен|рассылк|получени)|настро\w+\s+уведомлен|настройк\w+\s+уведомлен|\bopt[- ]?(?:out|in)\b|"
                r"\bunsubscribe|согласи\w+\s+на\s+(?:получени|рассылк|уведомлен)|в\s+настройках|управл\w+\s+уведомлен|\bmute\b|\bsettings\b", re.I)),
)


_SPECIFIC_CHANNEL = re.compile(r"\bpush\b|пуш|\bsms\b|\bсмс\b|e-?mail|электронн\w+\s+почт|письм\w+|уведомлен\w+|\bnotification", re.I)
_NOT_A_SENDING = re.compile(r"сервис\w*\s+уведомлен|модул\w+\s+уведомлен|раздел\w*\s+[«\"]?уведомлен|→|->|\bnotification\s+(?:service|module)", re.I)


def rule_notification_checklist(ctx: Ctx, rule):
    hits = []
    for b, t in _prose_blocks(ctx):
        if b.kind == "table_row" and not re.search(r"push|пуш|sms|смс|e-?mail|уведомлен", t, re.I):
            continue
        pos = 0
        for sent in re.split(r"(?<=[.!?])\s+", t):
            k = t.find(sent, pos)
            pos = (k if k >= 0 else pos) + len(sent)
            if _NOT_A_SENDING.search(sent):
                continue
            m = _SPECIFIC_CHANNEL.search(sent)
            if m and _SEND_VERB.search(sent):
                hits.append((b, max(k, 0) + m.start()))
                break
    if len(hits) < 3:
        return []
    text = _text(ctx)
    missing = [what for what, rx in _NOTIF_PARTS if not rx.search(text)]
    if len(missing) < 4:
        return []
    quotes = [ctx.q_sentence(b, p) for b, p in hits[:MAX_QUOTES]]
    return [finding(
        rule, quotes,
        title="Уведомления клиенту описаны без нескольких обязательных частей",
        problem=("В документе есть требования об отправке уведомлений, но не описано: " + _missing_list(missing) + ". Без этого каждая команда "
                 "решит по-своему: клиент получит дубли или ночью, на непонятном языке, а если уведомление не дошло — никто об этом не узнает."),
        recommendation="Для уведомлений добавьте: " + _missing_list(missing) + ". Достаточно одного общего раздела «Уведомления» для всех каналов.",
        question=ask("Какие общие правила действуют для всех уведомлений клиенту (текст, язык, сбой доставки, повторы, время, отключение)?",
                     ["Описать общие правила в отдельном разделе", "Принять стандарт банка и сослаться на него", "Правила отличаются по уведомлениям — описать каждое"]),
    )]


# --- 8. enum-no-values --------------------------------------------------------------------------------------------------------------

_ONE_OF = re.compile(
    r"одно\s+из\s+(?:допустимых\s+|возможных\s+|следующих\s+|заданных\s+|разрешённых\s+|разрешенных\s+|фиксированных\s+|предопределённых\s+)?значени\w+|"
    r"(?:один|одну)\s+из\s+допустимых\s+(?:значений|вариантов)|"
    r"\b(?:one|any)\s+of\s+(?:the\s+)?(?:allowed|following|valid|possible|supported|predefined|permitted)\s+values|"
    r"\bone\s+of\s+the\s+(?:allowed|following|valid|possible|supported)\s+(?:values|options|types|statuses)|"
    r"(?:мәндердің|мәндерінің)\s+бірі", re.I)
_LISTED = re.compile(r"`[^`]+`.*`[^`]+`|«[^»]+»[^«]*«[^»]+»|\"[^\"]+\".*\"[^\"]+\"|\b[A-Z][A-Z_]{2,}\b[^.\n]{0,40}\b[A-Z][A-Z_]{2,}\b")


def rule_enum_no_values(ctx: Ctx, rule):
    out = []
    blocks = ctx.doc.blocks
    for i, b in enumerate(blocks):
        if b.kind in ("code", "heading"):
            continue
        t = visible(b.text)
        for m in _ONE_OF.finditer(t):
            rest = t[m.end():m.end() + 160]
            if re.match(r"\s*(?::|—|–|-\s|\()", rest) or _LISTED.search(t[max(0, m.start() - 80):m.end() + 160]):
                continue
            if re.match(r"\s*(?:из\s+)?(?:справочник|списка|перечня|таблицы|раздела|приложения|of\s+the\s+(?:dictionary|reference|table)|see)", rest, re.I):
                continue
            if re.search(r"(?:см\.|смотри|see|согласно|по\s+справочнику|reference|таблиц\w+\s+\d|раздел\w*\s+\d|приложени)", t[m.start():m.end() + 160], re.I):
                continue
            nxt = blocks[i + 1] if i + 1 < len(blocks) else None
            if nxt is not None and nxt.kind in ("list_item", "table_row", "code"):
                continue
            if t.rstrip().endswith(":"):
                continue
            # the value list may sit in the type / rules column of the same table row
            if b.kind == "table_row" and re.search(r"[`«\"][^`»\"]+[`»\"]", t[:m.start()] + t[m.end():]) and _LISTED.search(t):
                continue
            out.append(ctx.q_sentence(b, m.start()))
            break
    if not out:
        return []
    return [finding(
        rule, out[:MAX_QUOTES],
        title="«Одно из значений» без перечня значений",
        problem=("Поле принимает «одно из значений», но список значений не приведён и нет ссылки на справочник. Разработчик не знает, какие значения "
                 "принимать и отвергать, а тестировщик не может проверить граничные и недопустимые значения."),
        recommendation="Перечислите допустимые значения (с кодом и смыслом каждого) или дайте точную ссылку на справочник и его версию.",
        question=ask("Какие значения допустимы для этого поля?", ["Перечислить значения здесь", "Дать ссылку на справочник с номером раздела"]),
    )]


# --- 9. param-table-no-required ----------------------------------------------------------------------------------------------------------------

_REQ_COL = re.compile(r"обязат|required|mandatory|optional|необязат|presence|наличие|\breq\b|\bm/o\b|\bo/m\b", re.I)
_REQ_WORD = re.compile(r"обязательн|необязательн|\brequired\b|\boptional\b|\bmandatory\b", re.I)
_REQUESTISH = re.compile(r"request|запрос|\bbody\b|тело|parameters?|параметр|query|вход", re.I)
_RESPONSEISH = re.compile(r"response|ответ|объект|\bobject\b|result|результат|webhook|callback", re.I)


def rule_param_table_no_required(ctx: Ctx, rule):
    if not ctx.is_api:
        return []
    bad = []
    for t in ctx.tables:
        h = t.header
        if column(h, NAME_COL) < 0 or column(h, TYPE_COL) < 0:
            continue
        if any(_REQ_COL.search(x) for x in h):
            continue
        if any(_REQ_WORD.search(visible(b.text)) for b in t.rows):
            continue
        first = t.rows[0]
        before = ""
        k = ctx.doc.blocks.index(first) - 1
        if k >= 0 and ctx.doc.blocks[k].kind != "table_row":
            before = visible(ctx.doc.blocks[k].text)
        head = ctx.heading_for(first)
        label = before + " " + (head.text if head is not None else "")
        if not _REQUESTISH.search(label) or _RESPONSEISH.search(before):
            continue
        bad.append(first)
    if not bad:
        return []
    return [finding(
        rule, [ctx.q_block(b) for b in bad[:MAX_QUOTES]],
        title="В таблице параметров запроса нет признака «обязательный»",
        problem=("В таблице параметров запроса не указано, какие поля обязательны, а какие можно не передавать, и в строках об этом тоже ничего нет. "
                 "Разработчик клиента не знает, что обязан передать, а тестировщик — когда ждать ошибку о пропущенном поле."),
        recommendation="Добавьте столбец «Обязательный» (да / нет) и для необязательных полей — значение по умолчанию.",
        question=ask("Какие поля запроса обязательны?", ["Добавить столбец «Обязательный» в таблицу", "Все поля обязательны — записать это явно"], "architect"),
    )]


COMPOSITE_RULES = {
    "refund-checklist": rule_refund_checklist,
    "error-body-shapes": rule_error_body_shapes,
    "error-code-unlinked": rule_error_code_unlinked,
    "api-access-codes": rule_api_access_codes,
    "api-ownership": rule_api_ownership,
    "list-no-order-limit": rule_list_no_order_limit,
    "notification-checklist": rule_notification_checklist,
    "enum-no-values": rule_enum_no_values,
    "param-table-no-required": rule_param_table_no_required,
}
