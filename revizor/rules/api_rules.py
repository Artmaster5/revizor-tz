"""REST API rules: fire only when the document describes an HTTP API."""
from __future__ import annotations

import re

from ..model import Block
from ._common import (
    METHOD_RE, NAME_COL, TYPE_COL, Ctx, Table, ask, cell_name, column, finding, fmt_list, path_tokens, short, visible,
)

_MUTATION_TOKENS = {
    "save", "create", "update", "delete", "remove", "set", "add", "edit", "change", "modify", "cancel", "submit",
    "reset", "insert", "register", "approve", "confirm", "block", "unblock", "activate", "deactivate", "enable",
    "disable", "transfer", "pay", "upload", "replace", "put", "post", "patch", "write", "store", "assign", "revoke",
}
_MUTATION_WORDS = re.compile(
    r"\b(?:сохранени[еяю]|сохран(?:ить|яет|ять|яется|яются)|создани[еяю]|созда(?:ть|[её]т|ются)|изменени[еяю]|"
    r"измен(?:ить|яет|ять|яется)|обновлени[еяю]|обнов(?:ить|ляет|лять|ляется)|удалени[еяю]|удал(?:ить|яет|ять|яется)|"
    r"замен(?:а|у|ы|ить|яет|ять|яется)|добавлени[еяю]|добав(?:ить|ляет|лять)|установк[аиу]|установ(?:ить|ливает)|"
    r"отмен(?:а|у|ить|яет)|списани[еяю]|списыва\w*|блокировк[аиу]|заблокир\w*|"
    r"saves?|saving|creates?|creating|updates?|updating|deletes?|deleting|removes?|modif(?:y|ies)|changes the)\b",
    re.I,
)
_PATH = re.compile(r"(?<![\w.$])(/[A-Za-z{][\w\-{}.:]*(?:/[\w\-{}.:]*)*)|https?://[^\s\"'`]+|\$\w+(/[\w\-{}./:]+)")
_CURL_GET = re.compile(r"\bcurl\b[^\n]*?(?:\s-G\b|\s--get\b|\s-X\s*GET\b|\s--request\s+GET\b)", re.I)
_GET_PATH = re.compile(r"\bGET\s+((?:https?://|/)[^\s\"'`|]+)")


def _paths(text: str) -> list[str]:
    out = []
    for m in _PATH.finditer(text):
        p = m.group(0)
        if p not in out:
            out.append(p)
    return out


def _table_of(ctx: Ctx, b: Block) -> Table | None:
    for t in ctx.tables:
        if b in t.rows:
            return t
    return None


def _evidence(ctx: Ctx, blocks: list[Block]) -> list[tuple[Block, str]]:
    ev = []
    for b in blocks:
        t = visible(b.text)
        for p in _paths(t):
            toks = path_tokens(p)
            hit = next((tok for tok in toks if tok in _MUTATION_TOKENS), None)
            if hit:
                ev.append((b, p))
                break
        else:
            m = _MUTATION_WORDS.search(t)
            if m:
                ev.append((b, m.group(0)))
    return ev


def rule_get_mutation(ctx: Ctx, rule):
    if not ctx.is_api:
        return []
    contexts: list[tuple[Block, list[Block]]] = []
    seen_tables: set[int] = set()
    for b in ctx.doc.blocks:
        t = visible(b.text)
        is_get = False
        if b.kind == "table_row" and any(c.strip().upper() == "GET" or c.strip().upper().startswith("GET ") for c in b.cells):
            is_get = True
        elif _GET_PATH.search(t) or _CURL_GET.search(t):
            is_get = True
        if not is_get:
            continue
        if b.kind == "table_row":
            tbl = _table_of(ctx, b)
            if tbl is not None and len(tbl.header) == 2:
                key = id(tbl)
                if key in seen_tables:
                    continue
                seen_tables.add(key)
                method_rows = [r for r in tbl.rows
                               if any(c.strip().upper() == "GET" for c in r.cells) or _CURL_GET.search(r.text)]
                contexts.append((method_rows[0] if method_rows else b, tbl.rows))
                continue
        contexts.append((b, [b]))
    out = []
    for method_block, blocks in contexts:
        ev = _evidence(ctx, blocks)
        if not ev:
            continue
        quotes = [ctx.q_block(method_block)]
        for eb, _ in ev[:3]:
            if eb is not method_block:
                quotes.append(ctx.q_block(eb))
        what = fmt_list(["«" + short(w, 40) + "»" for _, w in ev], 3)
        out.append(finding(
            rule, quotes,
            title="Метод GET используется для изменения данных",
            problem=(f"Запрос описан как GET, но по пути или описанию он сохраняет или меняет данные ({what}). "
                     "По стандарту HTTP (RFC 9110, п. 9.2.1) GET — безопасный метод, он только читает. Браузеры, прокси и "
                     "кэши вправе повторять и кэшировать такие запросы, а параметры попадают в адресную строку и журналы: "
                     "изменение может выполниться повторно или без ведома клиента."),
            recommendation=("Используйте PUT или PATCH для изменения настройки (POST — для создания новой записи) и передавайте "
                            "параметры в теле запроса в формате JSON; GET оставьте только для чтения."),
            question=ask("Какой HTTP-метод использовать для этого изменения?",
                         ["PUT — полная замена настройки", "PATCH — изменение отдельных полей", "POST — создание новой записи"],
                         "architect"),
        ))
    return out


# --- error-in-success ---------------------------------------------------------------------

_FAIL_BODY = re.compile(
    r'"(?:result|status|state|outcome|resultCode|result_code)"\s*:\s*"(?:error|fail|failed|failure|declined|rejected|err)"|'
    r'"success"\s*:\s*false|"ok"\s*:\s*false|"(?:error|errors|error_code|errorCode|errorMessage|error_message)"\s*:\s*(?!null|\[\s*\]|""|\{\s*\})\S|'
    r'"status"\s*:\s*"?[45]\d\d\b',
    re.I,
)
# a code at the start of a label: «200.», «**404** —», «201 Created — …»; never an amount like «200 000 тенге»
_LABEL_CODE = re.compile(r"^\s*(?:HTTP\s*)?([1-5]\d\d)(?!\d)(?!\s\d{3}\b)(?!\s*(?:тенге|₸|тиын|руб|%|мс|ms|сек|с\b|мин))")
_CTX_CODE = re.compile(r"\b(?:HTTP|код\w*|статус\w*|status|code|ответ\w*)\s*:?\s*([1-5]\d\d)\b", re.I)
_HTTP_LINE = re.compile(r"HTTP/\d(?:\.\d)?\s+([1-5]\d\d)")


def _status_of_label(text: str) -> str:
    m = _LABEL_CODE.match(text)
    if m:
        return m.group(1)
    m = _CTX_CODE.search(text)
    return m.group(1) if m else ""


def _governing_status(ctx: Ctx, idx: int) -> tuple[str, Block | None]:
    blocks = ctx.doc.blocks
    code_block = blocks[idx]
    m = _HTTP_LINE.search(code_block.text)
    if m:
        return m.group(1), code_block
    k = idx - 1
    while k >= 0:
        b = blocks[k]
        if b.section != code_block.section and b.kind != "heading":
            return "", None
        if b.kind == "code":
            k -= 1
            continue
        if b.kind == "heading":
            st = _status_of_label(b.text)
            return (st, b) if st else ("", None)
        st = _status_of_label(visible(b.text))
        if st:
            return st, b
        if b.kind == "paragraph" and len(b.text.split()) <= 2:
            k -= 1
            continue
        return "", None
    return "", None


def rule_error_in_success(ctx: Ctx, rule):
    if not ctx.is_api:
        return []
    out = []
    for i, b in enumerate(ctx.doc.blocks):
        if b.kind != "code":
            continue
        m = _FAIL_BODY.search(b.text)
        if not m:
            continue
        status, label = _governing_status(ctx, i)
        if not status.startswith("2"):
            continue
        quotes = ([ctx.q_block(label)] if label is not None and label is not b else []) + [ctx.q_block(b)]
        sig = short(m.group(0), 30)
        out.append(finding(
            rule, quotes,
            title=f"Ошибка возвращается с успешным кодом {status}",
            problem=(f"В описании ответа {status} приведён пример с признаком ошибки ({sig}). Клиентское приложение, "
                     f"балансировщики и мониторинг по коду {status} решат, что операция прошла успешно, и ошибка останется "
                     "незамеченной; тестировщик не знает, какой ответ считать правильным."),
            recommendation=("Для ошибок используйте коды 4xx/5xx (например, 400 или 422 при недопустимом значении) с телом ошибки, "
                            "а в ответ 2xx включайте только успешный результат."),
            question=ask("Какой код возвращать, когда операция отклонена?",
                         ["400 Bad Request", "422 Unprocessable Content", "Оставить 200 и описать признак ошибки в контракте (не рекомендуется)"],
                         "architect"),
        ))
    return out


# --- status-mismatch -------------------------------------------------------------------------

REASON = {
    "201": r"created", "202": r"accepted", "204": r"no content", "301": r"moved permanently", "302": r"found",
    "304": r"not modified", "400": r"bad request", "401": r"unauthori[sz]ed", "403": r"forbidden", "404": r"not found",
    "405": r"method not allowed", "409": r"conflict", "410": r"gone", "415": r"unsupported media type",
    "422": r"unprocessable", "429": r"too many requests", "500": r"internal server error", "502": r"bad gateway",
    "503": r"service unavailable", "504": r"gateway time-?out",
}
STD_NAME = {"200": "OK", "201": "Created", "202": "Accepted", "204": "No Content", "400": "Bad Request",
            "401": "Unauthorized", "403": "Forbidden", "404": "Not Found", "409": "Conflict", "422": "Unprocessable Content",
            "429": "Too Many Requests", "500": "Internal Server Error", "502": "Bad Gateway", "503": "Service Unavailable",
            "504": "Gateway Timeout"}
_NOT_AMOUNT = r"(?![  ]?\d)(?!\s*(?:₸|тенге|руб|\$|%|мс|ms|сек|с\b|s\b|мин|min|раз|times|штук|шт|символ|знак|char|byte|байт|kb|кб|мб|mb))"
_RETURN = re.compile(
    r"(?:возвраща\w*|верн[её]т\w*|вернуть|отда[её]т\w*|отдавать|return(?:s|ed)?|respond(?:s|ed)?\s+with|ответ\w*\s+(?:с\s+)?(?:кодом|статусом))\s+"
    r"(?:(?:http[- ]?)?(?:код\w*|статус\w*|status|code)\s+)?(?:http\s+)?([1-5]\d\d)\b" + _NOT_AMOUNT,
    re.I,
)
_SUCCESS_SENT = re.compile(r"успешн|неуспешн|\bsuccess|\bunsuccess|\bfailure|\bошибочн", re.I)
_CODE_CTX_WORD = re.compile(r"код|результат|статус|ответ|\bcode|\bstatus|\bhttp|\bresponse", re.I)
_ANY_CODE = re.compile(r"(?<![\d.,])([1-5]\d\d)\b" + _NOT_AMOUNT)


def _described(ctx: Ctx) -> dict[str, list[Block]]:
    desc: dict[str, list[Block]] = {}
    for b in ctx.doc.blocks:
        if b.kind == "heading":
            m = _LABEL_CODE.match(b.text)
            if m:
                desc.setdefault(m.group(1), []).append(b)
        elif b.kind in ("paragraph", "list_item"):
            m = _LABEL_CODE.match(visible(b.text))
            if m:
                desc.setdefault(m.group(1), []).append(b)
        elif b.kind == "table_row":
            for h, c in zip(b.header, b.cells):
                m = _LABEL_CODE.match(c)
                if m and (re.search(r"код|code|status|статус|http", h, re.I) or c.strip() == m.group(1)):
                    desc.setdefault(m.group(1), []).append(b)
    return desc


def _status_section(ctx: Ctx, heading: Block) -> list[Block]:
    out = []
    started = False
    for b in ctx.doc.blocks:
        if b is heading:
            started = True
            continue
        if started:
            if b.kind == "heading" and b.level <= heading.level:
                break
            out.append(b)
    return out


def rule_status_mismatch(ctx: Ctx, rule):
    if not ctx.is_api:
        return []
    desc = _described(ctx)
    if not desc:
        return []
    out = []
    reported: set[str] = set()
    # (d) label of code X carries the standard reason phrase of another code Y
    for code, b in [(c, x) for c, xs in desc.items() for x in xs]:
        label = b.text if b.kind == "heading" else visible(b.text)[:80]
        own = REASON.get(code)
        if own and re.search(own, label, re.I):
            continue
        for other, rx in REASON.items():
            if other != code and re.search(r"\b" + rx + r"\b", label, re.I):
                out.append(finding(
                    rule, [ctx.q_block(b)],
                    title=f"Код {code} подписан названием другого кода («{STD_NAME.get(other, other)}» — это {other})",
                    problem=(f"В описании ответа {code} стоит название «{STD_NAME.get(other, other)}», которое по стандарту HTTP "
                             f"относится к коду {other}. Разработчик может вернуть {other}, а тестировщик будет ждать {code}."),
                    recommendation=(f"Исправьте подпись: {code} — {STD_NAME.get(code, 'стандартное название этого кода')}. "
                                    f"Если нужен ответ «{STD_NAME.get(other, other)}», используйте код {other} и опишите его отдельно."),
                    question=ask("Какой код должен возвращаться в этом случае?", [code, other], "architect"),
                ))
                break
    # (b) a description entry of code X says that another code Y is returned
    for code, h in [(c, x) for c, xs in desc.items() for x in xs]:
        if h.kind != "heading":
            continue
        for b in _status_section(ctx, h):
            if b.kind == "code":
                continue
            for m in _RETURN.finditer(visible(b.text)):
                y = m.group(1)
                if y == code:
                    continue
                key = f"{code}->{y}"
                if key in reported:
                    continue
                reported.add(key)
                out.append(finding(
                    rule, [ctx.q_block(h), ctx.q_sentence(b, m.start())],
                    title=f"В описании кода {code} сказано, что возвращается {y}",
                    problem=(f"Раздел описывает ответ {code}, но в нём же сказано, что в этом случае возвращается {y}. "
                             "Непонятно, какой код вернёт сервис: разработчик и тестировщик будут ожидать разное, "
                             "а клиентское приложение может неправильно показать ошибку."),
                    recommendation=(f"Оставьте для этого случая один код и опишите его. Если {code} больше не возвращается — "
                                    f"удалите его описание; если возвращается в другом случае — назовите этот случай."),
                    question=ask("Какой код возвращается в этом случае?", [code, y, "Оба — в разных случаях (описать какие)"],
                                 "architect"),
                ))
    # (a) a code is said to be returned but has no description; (c) called (un)successful but never described
    for b in ctx.doc.blocks:
        if b.kind in ("code", "heading"):
            continue
        t = visible(b.text)
        for m in _RETURN.finditer(t):
            y = m.group(1)
            if y in desc or y in reported:
                continue
            reported.add(y)
            out.append(_undescribed(ctx, rule, b, m.start(), y, "возвращается"))
        for s_start, sent in _sentences_with_pos(t):
            if not (_SUCCESS_SENT.search(sent) and _CODE_CTX_WORD.search(sent)):
                continue
            for m in _ANY_CODE.finditer(sent):
                y = m.group(1)
                if y in desc or y in reported:
                    continue
                reported.add(y)
                kind = "неуспешным" if re.search(r"неуспешн|unsuccess|failure|ошибочн", sent[m.end():] + sent[:m.start()], re.I) \
                    and not re.search(r"(?<!не)успешн|(?<!un)success", sent[m.end():m.end() + 30], re.I) else "успешным"
                out.append(_undescribed(ctx, rule, b, s_start + m.start(), y, f"назван {kind}"))
    return out


def _sentences_with_pos(t: str):
    pos = 0
    for part in re.split(r"(?<=[.!?])\s+", t):
        k = t.find(part, pos)
        yield (k if k >= 0 else pos), part
        pos = (k if k >= 0 else pos) + len(part)


def _undescribed(ctx, rule, b, pos, code, how):
    return finding(
        rule, [ctx.q_sentence(b, pos)],
        title=f"Код ответа {code} {how}, но не описан",
        problem=(f"В тексте код {code} {how}, но в описании ответов для него нет ни условия, ни примера тела ответа. "
                 "Разработчик не знает, когда его возвращать, а тестировщик — что проверять."),
        recommendation=f"Добавьте описание кода {code}: при каком условии он возвращается и пример тела ответа; или уберите упоминание.",
        question=ask(f"Возвращает ли сервис код {code}?", ["Да — добавить описание", "Нет — убрать упоминание"], "architect"),
    )


# --- param-type-mismatch -------------------------------------------------------------------------

_TYPE_SYNONYMS = [
    ("integer", r"^(?:int(?:eger)?(?:32|64)?|long|short|целое(?: число)?|целочисленн\w*)\b"),
    ("number", r"^(?:number|decimal|float|double|numeric|число|дробное|вещественн\w*|money|amount)\b"),
    ("string", r"^(?:string|str|строка|text|текст|varchar|char)\b"),
    ("boolean", r"^(?:bool(?:ean)?|логическ\w*|flag|флаг)\b"),
    ("object", r"^(?:object|объект|json)\b"),
    ("array", r"^(?:array|массив|list|список)\b"),
    ("date", r"^(?:date|дата)\b"),
    ("datetime", r"^(?:date-?time|timestamp|дата и время)\b"),
]


def _norm_type(s: str) -> str:
    s = cell_name(s).lower()
    for name, rx in _TYPE_SYNONYMS:
        if re.match(rx, s):
            return name
    return s


_AMOUNT_NAME = re.compile(r"amount|sum\b|price|cost|total|balance|fee\b|сумм|цена|стоимост|баланс|комисс|остаток", re.I)
_FLAG_NAME = re.compile(r"^(?:is|has|can|should|allow|enable|disable)(?:_|[A-Z])|(?i:enabled|disabled|active|flag|признак|флаг)")


def _type_example(name: str, types: list[str]) -> str:
    """Example of two readings of one value, built from the parameter itself."""
    norm = {_norm_type(t) for t in types}
    if _AMOUNT_NAME.search(name):
        return "число 50000 и строка \"50000\""
    if "boolean" in norm or _FLAG_NAME.search(name):
        return "значение true и строка \"true\""
    return "число 1 и строка \"1\""


def rule_param_type_mismatch(ctx: Ctx, rule):
    seen: dict[str, list[tuple[int, Block, str, str]]] = {}
    for ti, t in enumerate(ctx.tables):
        nc, tc = column(t.header, NAME_COL), column(t.header, TYPE_COL)
        if nc < 0 or tc < 0 or nc == tc:
            continue
        for r in t.rows:
            if r.is_empty or len(r.cells) <= max(nc, tc):
                continue
            name = cell_name(r.cells[nc])
            typ = r.cells[tc].strip()
            if not name or not typ or not re.match(r"^[\w.\-\[\]]+$", name):
                continue
            seen.setdefault(name.lower(), []).append((ti, r, typ, _norm_type(typ)))
    out = []
    for key, items in seen.items():
        tables = {i[0] for i in items}
        types = []
        for it in items:
            if it[3] not in [x[3] for x in types]:
                types.append(it)
        if len(tables) < 2 or len(types) < 2:
            continue
        a, b = types[0], types[1]
        name = cell_name(a[1].cells[column(ctx.tables[a[0]].header, NAME_COL)])
        out.append(finding(
            rule, [ctx.q_block(a[1]), ctx.q_block(b[1])],
            title=f"Параметр «{name}» описан с разными типами: {a[2]} и {b[2]}",
            problem=(f"В одной таблице параметр «{name}» имеет тип {a[2]}, в другой — {b[2]}. Клиент и сервер разберут значение "
                     f"по-разному: например, {_type_example(name, [a[2], b[2]])} по-разному проходят проверку и сравнение, "
                     "и интеграция сломается на первом же запросе."),
            recommendation=(f"Выберите для «{name}» один тип и формат во всех таблицах (для сумм и признаков обычно integer или boolean) "
                            "и исправьте остальные места."),
            question=ask(f"Какой тип у параметра «{name}»?", [a[2], b[2]], "architect"),
        ))
    return out


# --- content-type-without-body ------------------------------------------------------------------

_CT = re.compile(r"content-type", re.I)
_NO_BODY = re.compile(
    r"тело\s+(?:запроса\s+)?не\s+(?:использу\w*|переда\w*|требу\w*|нужно)|без\s+тела|тело\s+(?:запроса\s+)?отсутству\w*|"
    r"body\s+(?:is\s+)?(?:not\s+used|empty|not\s+required|unused)|no\s+(?:request\s+)?body",
    re.I,
)


def rule_content_type_without_body(ctx: Ctx, rule):
    if not ctx.is_api:
        return []
    out = []
    done = set()
    methods = set(METHOD_RE.findall(ctx.doc.text))
    for b in ctx.doc.blocks:
        if b.kind in ("code", "heading") or not _CT.search(b.text) or b.section in done:
            continue
        siblings = [x for x in ctx.doc.blocks if x.section == b.section and x is not b and x.kind != "heading"]
        nb = next((x for x in siblings if _NO_BODY.search(visible(x.text))), None)
        reason = ""
        if nb is not None:
            reason = "сказано, что тело запроса не используется"
        elif methods == {"GET"} and re.search(r"request|запрос", b.section, re.I):
            reason = "единственный метод — GET, у которого нет тела"
        if not reason:
            continue
        done.add(b.section)
        quotes = [ctx.q_block(b)] + ([ctx.q_block(nb)] if nb is not None else [])
        out.append(finding(
            rule, quotes,
            title="Указан Content-Type, хотя тела запроса нет",
            problem=(f"Для запроса задан заголовок Content-Type, но {reason}. Непонятно, нужно ли отправлять тело и в каком виде: "
                     "одни клиенты пошлют пустое тело с заголовком, другие — параметры в теле, третьи — без заголовка."),
            recommendation=("Если тело не нужно — уберите Content-Type из описания запроса. Если параметры должны идти в теле — "
                            "опишите тело (поля и типы) и используйте метод POST, PUT или PATCH."),
            question=ask("Передаются ли параметры в теле запроса?",
                         ["Нет — убрать Content-Type", "Да — описать тело и сменить метод"], "architect"),
        ))
    return out


# --- sensitive-in-query ----------------------------------------------------------------------------

_SENSITIVE_TOKENS = {
    "card", "pan", "account", "iban", "phone", "msisdn", "token", "password", "passwd", "pwd", "pin", "cvv", "cvc",
    "cvv2", "iin", "inn", "snils", "passport", "ssn", "secret", "email", "apikey", "cardnumber", "accountnumber",
}
_SENSITIVE_DESC = re.compile(r"номер\w*\s+(?:карт|сч[её]т|телефон)|\bиин\b|паспорт|токен|парол|идентификатор\w*\s+карт", re.I)
_QUERY_SECTION = re.compile(r"query|строк\w*\s+запроса|параметры\s+url|url[- ]параметр", re.I)
_URL_QUERY = re.compile(r"[?&]([A-Za-z_][\w.\-]*)=")
_CURL_DATA = re.compile(r"(?:--data(?:-urlencode|-raw)?|-d)\s+['\"]?([^'\"\s]+)")


def _sensitive_name(name: str) -> bool:
    toks = path_tokens(name)
    joined = "".join(toks)
    return any(t in _SENSITIVE_TOKENS for t in toks) or joined in _SENSITIVE_TOKENS or "apikey" in joined


def rule_sensitive_in_query(ctx: Ctx, rule):
    if not ctx.is_api:
        return []
    hits: list[tuple[str, Block]] = []

    def add(name: str, b: Block) -> None:
        if name and name.lower() not in [h[0].lower() for h in hits]:
            hits.append((name, b))

    for t in ctx.tables:
        sec = t.rows[0].section.split(" › ")[-1] if t.rows else ""
        if not _QUERY_SECTION.search(sec):
            continue
        nc = column(t.header, NAME_COL)
        if nc < 0:
            continue
        for r in t.rows:
            if r.is_empty or len(r.cells) <= nc:
                continue
            name = cell_name(r.cells[nc])
            desc = " ".join(r.cells)
            if _sensitive_name(name) or _SENSITIVE_DESC.search(desc):
                add(name, r)
    for b in ctx.doc.blocks:
        t = b.text if b.kind == "code" else visible(b.text)
        for m in _URL_QUERY.finditer(t):
            if _sensitive_name(m.group(1)):
                add(m.group(1), b)
        if _CURL_GET.search(t):
            for m in _CURL_DATA.finditer(t):
                for pair in m.group(1).split("&"):
                    name = pair.split("=", 1)[0]
                    if re.match(r"^[A-Za-z_][\w.\-]*$", name) and _sensitive_name(name):
                        add(name, b)
    if not hits:
        return []
    names = [h[0] for h in hits]
    quotes = []
    for _, b in hits:
        if len(quotes) >= 3:
            break
        q = ctx.q_block(b)
        if all(q.line_start != x.line_start for x in quotes):
            quotes.append(q)
    return [finding(
        rule, quotes,
        title="Чувствительные данные передаются в адресной строке запроса",
        problem=(f"Параметры {fmt_list(['«' + n + '»' for n in names])} передаются в строке запроса (query). Адрес запроса целиком "
                 "попадает в журналы веб-серверов и прокси, историю браузера и системы мониторинга — идентификаторы карт, счетов, "
                 "телефонов и токены окажутся там в открытом виде. Это противоречит требованиям к защите платёжных и персональных "
                 "данных (PCI DSS)."),
        recommendation=("Передавайте такие значения в теле запроса (POST/PUT/PATCH), а токены — только в заголовке Authorization; "
                        "вместо номера карты используйте внутренний непрозрачный идентификатор и запретите запись этих значений в журналы."),
        question=ask("Как передавать идентификатор карты и другие чувствительные значения?",
                     ["В теле запроса", "В пути в виде внутреннего непрозрачного идентификатора", "Оставить в query (обосновать для ИБ)"],
                     "security"),
    )]


API_RULES = {
    "get-mutation": rule_get_mutation,
    "error-in-success": rule_error_in_success,
    "status-mismatch": rule_status_mismatch,
    "param-type-mismatch": rule_param_type_mismatch,
    "content-type-without-body": rule_content_type_without_body,
    "sensitive-in-query": rule_sensitive_in_query,
}
