"""Rules on terms: abbreviations nobody explains, one term defined twice, a condition word nobody defined.

Every rule is silent when the document answers the question anywhere (a definition, a glossary row, a
parenthesis right after the word). One finding per rule per document; the quotes are the first places
where the question arises (up to five).
"""
from __future__ import annotations

import re

from ._common import Ctx, ask, finding, fmt_list, visible

MAX_QUOTES = 5
_VOWELS_RU = set("АЕЁИОУЫЭЮЯ")
_VOWELS_EN = set("AEIOUY")

# --- shared text helpers --------------------------------------------------------------------------------------


def prose_blocks(ctx: Ctx):
    """Blocks with running text: everything except code samples."""
    for b in ctx.doc.blocks:
        if b.kind == "code":
            continue
        yield b, visible(b.text)


def _stem(word: str, n: int = 5) -> str:
    return re.sub(r"[^\wёЁ]", "", word.lower().replace("ё", "е"))[:n]


def _words(s: str) -> list[str]:
    return re.findall(r"[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]+", s)


def _quote(ctx: Ctx, b, pos: int):
    return ctx.q_sentence(b, pos)


def _emit(ctx: Ctx, rule, quotes, *, title: str, problem: str, recommendation: str, question, severity=None):
    uniq = []
    for q in quotes:
        if not any(u.text == q.text and u.line_start == q.line_start for u in uniq):
            uniq.append(q)
    if not uniq:
        return []
    shown = uniq[:MAX_QUOTES]
    return [finding(rule, shown, title=title, problem=problem, recommendation=recommendation,
                    question=question, severity=severity)]


# --- 1. abbreviation never explained ------------------------------------------------------------------------------

# Commonly known abbreviations: never reported. The list is deliberately generous.
KNOWN_ABBR = {
    # IT and protocols
    "API", "HTTP", "HTTPS", "JSON", "XML", "HTML", "CSS", "URL", "URI", "UUID", "GUID", "SQL", "REST", "SOAP", "GET", "POST", "PUT",
    "PATCH", "DELETE", "HEAD", "TCP", "UDP", "IP", "DNS", "SSL", "TLS", "SSH", "FTP", "SFTP", "SMTP", "VPN", "CPU", "RAM", "SSD",
    "HDD", "GPU", "OS", "IOS", "SDK", "IDE", "CLI", "GUI", "UI", "UX", "CRUD", "JWT", "OAUTH", "SSO", "MFA", "OTP", "TOTP", "HMAC",
    "SHA", "MD5", "AES", "RSA", "PDF", "CSV", "TXT", "PNG", "JPG", "JPEG", "GIF", "SVG", "ZIP", "DOCX", "XLSX", "UTF", "ASCII",
    "ISO", "IEEE", "GDPR", "SLA", "SLO", "RPO", "RTO", "RPS", "TPS", "QPS", "CI", "CD", "QA", "UAT", "MVP", "POC", "CRM", "ERP",
    "BI", "ETL", "DWH", "CDN", "WAF", "DDOS", "DOS", "XSS", "CSRF", "SQLI", "PII", "NFC", "QR", "SMS", "MMS", "USSD", "GPS", "ID",
    "PIN", "URL", "TBD", "FAQ", "KPI", "ROI", "OK", "NA", "UTC", "GMT", "MSK", "AI", "ML", "OCR", "PC", "USB", "WIFI", "LAN",
    "SOA", "ESB", "MQ", "BPMN", "UML", "ER", "DFD", "RACI", "SRS", "BRD", "FRD", "PRD", "TOR", "FR", "NFR", "REQ", "US", "AC",
    "JIRA", "CVV", "CVC", "KYC", "AML", "CFT", "IBAN", "BIC", "SWIFT", "MCC", "POS", "ATM", "EMV", "PCI", "DSS", "ACH", "SEPA",
    "FATCA", "CRS", "VISA", "NO", "TV", "EU", "USA", "UK", "USD", "EUR", "KZT", "RUB", "CNY", "GBP", "RUR", "UAH", "BYN", "CHF",
    "JPY", "TRY", "KGS", "UZS", "AMD", "AED", "ISIN", "CIS", "SMEV", "SBP", "MIR", "TIN", "SSN", "VAT", "HR", "PR", "IT", "PM",
    "PO", "BA", "SA", "DEV", "PROD", "TEST", "STAGE", "DB", "DBA", "SSN", "SMS", "PUSH", "APP", "WEB", "AND", "OR", "NOT", "ALL",
    "CEO", "CFO", "CTO", "COO", "CIO", "TZ", "ETA", "FIFO", "LIFO", "TTL", "CORS", "SAML", "LDAP", "AD", "IAM", "RBAC", "ACL",
    "MAC", "ARM", "LTE", "SIM", "ESIM", "IMEI", "IMSI", "MSISDN", "DOM", "SPA", "PWA", "SEO", "CTA", "GIT", "CVE", "EOL", "ASAP",
    "FYI", "RFC", "DTO", "ORM", "MVC", "SOLID", "DRY", "KISS", "YAGNI", "ACID", "CAP", "BASE", "LOB", "TCO", "OLAP", "OLTP",
    # Russian and Kazakh general and state abbreviations
    "ИИН", "БИН", "ФИО", "ИП", "ТОО", "АО", "ОАО", "ЗАО", "ООО", "ПАО", "РК", "РФ", "США", "ЕС", "СНГ", "ООН", "НДС", "ОКЭД", "КНП",
    "КБЕ", "БИК", "ИИК", "КПП", "ИНН", "ОГРН", "ОКПО", "СМС", "ТЗ", "ПО", "ИС", "ИТ", "ИБ", "БД", "ОС", "ЦБ", "НБ", "НБК", "ГК",
    "НК", "ТК", "УК", "КоАП", "ФЗ", "ГОСТ", "СТ", "РЦ", "ПДН", "ПДД", "МФО", "ВУЗ", "НИОКР", "ЭЦП", "ЭДО", "СУБД", "ПК", "ТЦ",
    "ЖК", "ЖШС", "АҚ", "ЖСН", "БСН", "ТАӘ", "ҚР", "ҚҚС", "ЕНПФ", "ЕДИНЫЙ", "ТО", "ОТК", "СЗ", "КП", "НТД", "ТУ", "ТТХ", "ЗП",
    "МРП", "МЗП", "ПИН", "КУ", "ЦОД", "ЦП", "ГБ", "МБ", "КБ", "ТБ", "СНИЛС", "ДМС", "ОСАГО", "КАСКО", "ЖКХ", "МСБ", "СМЭВ",
    "АБС", "ДБО", "JSC", "LLC", "LTD", "INC", "MB", "KB", "GB", "TB", "MS", "PAN", "BOM", "CRLF", "LF", "CR", "ДА", "НЕТ", "ВСЕ", "ИЛИ", "ИТОГО", "ЕСЛИ", "ОТ", "ДО", "НЕ", "ЧТО", "ТОЛЬКО",
}
_KNOWN_UP = {k.upper() for k in KNOWN_ABBR}

_ABBR = re.compile(r"(?<![\w/@$_.\-])([A-ZА-ЯЁӘҒҚҢӨҰҮҺІ]{2,6})(?![\w@_/\-]|\.\w)")
_ENUMISH_BEFORE = re.compile(r"[`\"'«“„]$")
_ENUMISH_AFTER = re.compile(r"^[`\"'»”]")
_BIG_LINE_CAPS = 0.6


def _vowels_ok(tok: str) -> bool:
    """True for a token that looks like an abbreviation rather than a capitalised word."""
    if re.fullmatch(r"[A-Z]+", tok):
        v = sum(c in _VOWELS_EN for c in tok)
        return v == 0 or (len(tok) <= 3 and v <= 1)  # SENT, TERM, DRAFT are words, not abbreviations
    return sum(c in _VOWELS_RU for c in tok) <= 1


def _line_is_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8:
        return False
    return sum(c.isupper() for c in letters) / len(letters) >= _BIG_LINE_CAPS


def _defined(tok: str, blocks: list[tuple]) -> bool:
    t = re.escape(tok)
    pats = [
        re.compile(r"\(\s*(?:далее\s*[—–-]?\s*)?[«\"“]?" + t + r"[»\"”]?\s*[),;]"),  # Full Name (X)
        re.compile(r"(?<![\w-])" + t + r"\s*\(\s*(?:[a-zа-яёA-ZА-ЯЁ][^()]{6,}|[A-ZА-ЯЁ][a-zа-яё]+)"),  # X (expansion)
        re.compile(r"(?<![\w-])" + t + r"\s*(?:[—–]|\s-\s)\s*(?:это\s+)?[a-zа-яёA-Za-zА-ЯЁ«\"]"),  # X — expansion
        re.compile(r"(?<![\w-])" + t + r"\s*:\s*[a-zа-яё]{3,}"),
        re.compile(r"(?<![\w-])" + t + r"\s+(?:это|означает|расшифровывается|обозначает|stands\s+for|means|is\s+short\s+for|деген)\b", re.I),
        re.compile(r"(?:далее|сокращённо|сокращенно|аббревиатур\w*|сокр\.|hereinafter|abbreviated|aka|a\.k\.a\.?)\s*[—–:,-]?\s*[«\"“]?" + t + r"\b", re.I),
        re.compile(r"(?<![\w-])" + t + r"\s*,\s*(?:то\s+есть|т\.\s?е\.|или)\s+", re.I),
        re.compile(r"[«\"“]" + t + r"[»\"”]\s*(?:[—–-]|означает|это)", re.I),
    ]
    for b, text in blocks:
        for p in pats:
            if p.search(text):
                return True
        if b.kind == "table_row" and b.cells:  # a glossary row: the first cell is the term, the next one says what it is
            if b.cells[0].strip().strip("*`«»\"") == tok and any(len(c.strip()) >= 3 for c in b.cells[1:]):
                return True
    return False


def rule_abbreviation_undefined(ctx: Ctx, rule):
    blocks = list(prose_blocks(ctx))
    id_prefixes = set()
    for b in ctx.doc.blocks:
        if b.doc_id:
            m = re.match(r"^([A-Za-zА-Яа-яЁё]+)", b.doc_id)
            if m:
                id_prefixes.add(m.group(1).upper())
    cand: dict[str, list] = {}
    bad: set[str] = set()
    for b, text in blocks:
        for m in _ABBR.finditer(text):
            tok = m.group(1)
            if tok in bad or tok.upper() in _KNOWN_UP or tok.upper() in id_prefixes:
                continue
            before, after = text[:m.start()], text[m.end():]
            if _ENUMISH_BEFORE.search(before) and _ENUMISH_AFTER.match(after):
                bad.add(tok)  # quoted: a value, not an abbreviation
                continue
            if re.match(r"^\s{0,2}[-–]?\s?\d", after) and re.match(r"^[-–]\d|^\d", after.lstrip(" ")) and after[:1] in "-–":
                bad.add(tok)  # an identifier prefix (ФТ-1, ЦН-12)
                continue
            if b.kind == "table_row" and any(c.strip() == tok for c in b.cells):
                bad.add(tok)  # a value in a table cell (status, enum)
                continue
            if b.kind == "heading" or _line_is_caps(text):
                continue
            if not _vowels_ok(tok):
                continue
            cand.setdefault(tok, []).append((b, m.start()))
    out = []
    for tok, uses in cand.items():
        if tok in bad or len(uses) < 2:
            continue
        if _defined(tok, blocks):
            continue
        out.append((uses[0][0].line_start, tok, uses))
    if not out:
        return []
    out.sort(key=lambda x: x[0])
    terms = [f"{tok} ({len(uses)} раз)" for _, tok, uses in out]
    names = [tok for _, tok, _ in out]
    quotes = [_quote(ctx, uses[0][0], uses[0][1]) for _, _, uses in out]
    return _emit(
        ctx, rule, quotes,
        title="Сокращения без расшифровки: " + fmt_list(names, 5),
        problem=("В тексте есть сокращения, которые встречаются несколько раз, но нигде не расшифрованы: "
                 + fmt_list(terms, 8) + ". Читатель из другой команды поймёт их по-своему или потратит время на выяснение, "
                 "а тестировщик не сможет проверить то, чего не понимает."),
        recommendation="Расшифруйте каждое сокращение при первом упоминании («КДН (коэффициент долговой нагрузки)») или добавьте раздел «Термины и сокращения».",
        question=ask("Что означают сокращения: " + fmt_list(names, 5) + "?", ["Расшифровать в тексте", "Добавить раздел «Термины»"]),
    )


# --- 2. one term defined twice, differently -------------------------------------------------------------------------

_DEF_PATTERNS = [
    # Term — это definition
    re.compile(r"^\W*(?P<t>[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ«\"][^.:;—–|]{1,50}?)\s*(?:[—–]|\s-\s)\s*это\s+(?P<d>.{8,})$", re.I | re.S),
    # Под X понимается / понимают definition
    re.compile(r"(?:^|[.;]\s+)Под\s+(?P<t>[«\"]?[^,.;—–]{2,50}?[»\"]?)\s+(?:в\s+(?:настоящем\s+документе|документе)\s+)?понима\w+\s*(?:[—–:-]\s*)?(?P<d>.{8,})$", re.I | re.S),
    # X считается/признаётся Y, если ... : the term is the whole subject
    re.compile(r"^\W*(?P<t>[«\"]?[A-ZА-ЯЁ][^.,;:—–]{2,40}?[»\"]?)\s+(?:означает|определяется\s+как|называется|трактуется\s+как)\s+(?P<d>.{8,})$", re.I | re.S),
]
_GLOSS_TERM_COL = re.compile(r"^(?:термин\w*|понятие|понятия|term|terms|сокращени\w*|аббревиатур\w*|терм[иі]н\w*|abbreviation)\b", re.I)
_GLOSS_DEF_COL = re.compile(r"^(?:определени\w*|расшифровк\w*|описани\w*|значение|definition|meaning|description|анықтама\w*|сипаттама\w*|пояснени\w*)\b", re.I)


def _term_key(term: str) -> str:
    term = re.sub(r"[«»\"“”*`]", "", term).strip().lower().replace("ё", "е")
    words = [w for w in _words(term) if len(w) > 1]
    if not words or len(words) > 4:
        return ""
    return " ".join(w[:5] for w in words)


def _def_tokens(d: str) -> set[str]:
    stop = {"это", "если", "когда", "или", "для", "что", "при", "как", "его", "она", "они", "все", "также", "которы", "котор"}
    return {w[:5] for w in _words(d.lower().replace("ё", "е")) if len(w) > 3 and w[:5] not in stop}


def _def_numbers(d: str) -> set[str]:
    nums = re.findall(r"\d[\d  .,]*\d|\d", d)
    return {re.sub(r"[ .,]", "", n) for n in nums}


_GLOSS_HEAD = re.compile(r"^\W*(?:\d+(?:\.\d+)*\.?\s*)?(?:термин|глоссар|определени|сокращени|понятия|terms?\b|glossary|definitions?|abbreviations?|терм[иі]н)", re.I)
_GLOSS_LINE = re.compile(r"^\W*(?:\d+(?:\.\d+)*\.?\s+)?(?P<t>[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ«\"][^.:;—–|]{1,50}?)\s*(?:[—–]|\s-\s)\s+(?P<d>\S.{8,})$", re.S)


def _glossary_blocks(ctx: Ctx) -> set[str]:
    """Ids of the blocks that stand in a terms section (a heading or a lead line «Термины:» right above them)."""
    out: set[str] = set()
    active = False
    since = 0
    for b in ctx.doc.blocks:
        if b.kind == "heading":
            active = bool(_GLOSS_HEAD.match(b.text))
            since = 0
            continue
        if b.kind == "paragraph" and _GLOSS_HEAD.match(b.text) and len(b.text) < 120:
            active, since = True, 0
            continue
        if active:
            if b.kind in ("list_item", "paragraph") and since < 40:
                out.add(b.bid)
                since += 1
            elif b.kind == "table_row":
                continue
            else:
                active = False
    return out


def collect_definitions(ctx: Ctx):
    defs = []  # (key, term, definition, block, pos)
    gloss = _glossary_blocks(ctx)
    for b, text in prose_blocks(ctx):
        if b.kind == "heading":
            continue
        if b.kind == "table_row" and b.header and len(b.cells) >= 2:
            tc = next((k for k, h in enumerate(b.header) if _GLOSS_TERM_COL.match(h.strip())), -1)
            dc = next((k for k, h in enumerate(b.header) if _GLOSS_DEF_COL.match(h.strip())), -1)
            if tc >= 0 and dc >= 0 and tc != dc and len(b.cells) > max(tc, dc):
                term, d = b.cells[tc].strip(), b.cells[dc].strip()
                key = _term_key(term)
                if key and d:
                    defs.append((key, term, d, b, 0))
            continue
        if b.bid in gloss:
            m = _GLOSS_LINE.match(text)
            if m:
                key = _term_key(m.group("t"))
                if key:
                    defs.append((key, m.group("t").strip(" «»\""), m.group("d").strip(), b, 0))
                continue
        for s0, sent in _sentence_list(text):
            for rx in _DEF_PATTERNS:
                m = rx.search(sent)
                if m:
                    key = _term_key(m.group("t"))
                    if key:
                        defs.append((key, m.group("t").strip(" «»\""), m.group("d").strip(), b, s0))
                    break
    return defs


def _sentence_list(text: str):
    """(offset, sentence) pairs; also splits list-like glossary lines at semicolons."""
    out = []
    pos = 0
    for part in re.split(r"(?<=[.!?;])\s+(?=[A-ZА-ЯЁ«\"])", text):
        k = text.find(part, pos)
        out.append((max(k, 0), part))
        pos = max(k, 0) + len(part)
    return out


def rule_term_defined_twice(ctx: Ctx, rule):
    by_key: dict[str, list] = {}
    for d in collect_definitions(ctx):
        by_key.setdefault(d[0], []).append(d)
    out = []
    for key, items in by_key.items():
        if len(items) < 2:
            continue
        a = items[0]
        for b2 in items[1:]:
            if b2[3].bid == a[3].bid and b2[4] == a[4]:
                continue
            na, nb = _def_numbers(a[2]), _def_numbers(b2[2])
            ta, tb = _def_tokens(a[2]), _def_tokens(b2[2])
            differ = bool(na and nb and na != nb)
            if not differ and na != nb and (na or nb) and len(ta) >= 4 and len(tb) >= 4:
                union = ta | tb
                differ = len(ta & tb) / len(union) < 0.5 if union else False
            if not differ and len(ta) >= 4 and len(tb) >= 4 and not (ta & tb):
                differ = True
            if differ:
                out.append((a, b2))
                break
    if not out:
        return []
    quotes = []
    names = []
    for a, b2 in out:
        names.append(a[1])
        quotes.append(ctx.q_sentence(a[3], a[4]) if a[4] else ctx.q_block(a[3]))
        quotes.append(ctx.q_sentence(b2[3], b2[4]) if b2[4] else ctx.q_block(b2[3]))
    return _emit(
        ctx, rule, quotes,
        title="Термин определён в двух местах по-разному: " + fmt_list([f"«{n}»" for n in names], 3),
        problem=("Один и тот же термин объясняется в двух местах разными словами или с разными числами: "
                 + fmt_list([f"«{n}»" for n in names], 4) + ". Разработчик выберет то определение, которое прочитал первым, "
                 "а тестировщик — то, что прочитал вторым."),
        recommendation="Оставьте одно определение в разделе «Термины» и замените остальные ссылкой на него.",
        question=ask(f"Какое определение термина «{names[0]}» верное?", ["Первое", "Второе", "Объединить в одно"]),
    )


# --- 3. a condition word with no definition ---------------------------------------------------------------------------

# evaluative or state adjectives (stems); wording-only words such as «достаточный» belong to the vague-word rule
_ADJ_STEM_ONLY = (
    "активн", "неактивн", "стабильн", "нестабильн", "подозрительн", "крупн", "значительн", "существенн", "регулярн",
    "нестандартн", "нетипичн", "необычн", "приоритетн", "проблемн", "благонад[её]жн", "платёжеспособн", "платежеспособн",
    "лояльн", "аномальн", "рискованн", "высокорисков\\w*",
)
_ENDING = r"(?:ый|ий|ая|яя|ое|ее|ые|ие|ого|его|ой|ей|ую|юю|ым|им|ыми|ими|ых|их|ому|ему|ом|ем)"
_ADJ_RU = re.compile(
    r"(?<![\w-])(?P<adj>(?:" + "|".join(_ADJ_STEM_ONLY) + r")" + _ENDING + r")\s+(?P<noun>[а-яё]{3,})",
    re.I)
_VALID_RU = re.compile(r"(?<![\w-])(?P<adj>действующ\w{2,3})\s+(?P<noun>договор\w*|соглашени\w+|контракт\w*|тариф\w*|лицензи\w+|полис\w*|сертификат\w*|доверенност\w+|поручени\w+)", re.I)
_NEW_RU = re.compile(r"(?<![\w-])(?P<adj>(?:нов|постоянн)(?:ый|ого|ому|ым|ом|ая|ой|ую|ое|ые|ых|ыми))\s+(?P<noun>клиент\w*|пользовател\w+|заёмщик\w*|заемщик\w*|получател\w+|контрагент\w*|держател\w+|покупател\w+|партн[её]р\w*)", re.I)
_COND_LEAD = re.compile(
    r"(?:^|[\s(,;])(?:если|при|когда|только|для|в\s+случае|кроме|иметь|имеет|имеют|имеющ\w*|наличи[еия]|наличии|обладает|обладающ\w*|"
    r"являющ\w*|является|должен\s+быть|должны\s+быть)\s+(?:\S+\s+){0,2}$", re.I)
_ROLE_LEAD = re.compile(
    r"(?:клиент|держател|пользовател|заёмщик|заемщик|владел|заявител|получател|отправител|покупател|продав|вкладчик|мерчант)\w*\s+(?:с\s+|со\s+|имеющ\w+\s+)?$", re.I)
_DEF_MARK = re.compile(
    r"считается|считаются|считать|признаётся|признается|признаются|называется|называются|понимается|понимаются|определяется|"
    r"определяются|означает|—\s+это|–\s+это|\sэто\s|является\s+\w+,\s+если|то\s+есть|т\.\s?е\.|в\s+статусе|равн\w+|составляет|"
    r"критери\w+|признак\w*|условием|условия\s+(?:отнесения|признания)", re.I)

_ADJ_EN = re.compile(
    r"\b(?:if|when|for|only\s+if|where|with|has|have|having|an?|the|valid|each|every|any)\s+(?:an?\s+|the\s+)?"
    r"(?P<adj>active|inactive|stable|suspicious|large|regular|significant|frequent|unusual|"
    r"problematic|trusted|abnormal|risky)\s+(?:[A-Z]{2,5}\s+)?"
    r"(?P<noun>[a-z]{3,}(?:\s+(?:card|account|customer|client|transaction|device|user|payment|transfer)s?)?)\b", re.I)
_DEF_MARK_EN = re.compile(r"\b(?:is\s+considered|are\s+considered|is\s+defined|means|is\s+a|are\s+defined|defined\s+as|shall\s+mean|refers\s+to)\b", re.I)
_ADJ_KK = re.compile(r"(?<![\w-])(?P<adj>белсенді|тұрақты|күдікті|ірі|әрекет\s+етуші|жарамды)\s+(?P<noun>[а-яәғқңөұүһі]{3,})", re.I)
_DEF_MARK_KK = re.compile(r"деп\s+(?:саналады|танылады|есептеледі|аталады)|дегеніміз|анықтамасы", re.I)


def _phrase_defined(adj: str, noun: str, blocks: list[tuple], own: tuple, lang: str) -> bool:
    a = _stem(adj, 6)
    n = _stem(noun, 5)
    if lang == "en":
        mark = _DEF_MARK_EN
    elif lang == "kk":
        mark = _DEF_MARK_KK
    else:
        mark = _DEF_MARK
    near = re.compile(re.escape(adj[:max(4, len(adj) - 2)]) + r"\w*\s+" + re.escape(noun[:max(3, len(noun) - 2)]) + r"\w*\s*(?:\(|[—–]|,\s*т\.\s?е\.|,\s*то\s+есть|:)", re.I)
    for b, text in blocks:
        low = text.lower().replace("ё", "е")
        if a not in low:
            continue
        if b.kind == "table_row" and b.cells and len(b.cells) >= 2:
            c0 = b.cells[0].lower().replace("ё", "е")
            if a in c0 and (n[:4] in c0 or len(c0.split()) <= 1):
                return True
        for s0, sent in _sentence_list(text):
            ls = sent.lower().replace("ё", "е")
            if a not in ls:
                continue
            if (b.bid, s0) == own:
                continue
            mn = near.search(sent)
            if mn and not (_COND_LEAD.search(sent[:mn.start()]) or _ROLE_LEAD.search(sent[:mn.start()])):
                return True
            # a sentence about the adjective with a defining marker («Карта считается активной, если …»)
            if re.search(re.escape(a) + r"\w*", ls) and mark.search(sent) and n in ls and re.search(r"если|когда|if\b|case|егер", ls + " если"):
                return True
            if re.search(re.escape(a) + r"\w*", ls) and mark.search(sent) and re.search(r"(?:считается|считаются|признаётся|признается|признаются|называется|называются|понимается|определяется|—\s+это|is\s+considered|means|деп\s+саналады)", sent):
                return True
    return False


def rule_condition_undefined(ctx: Ctx, rule):
    blocks = [(b, t) for b, t in prose_blocks(ctx)]
    found: dict[str, tuple] = {}
    counted: dict[str, int] = {}
    for b, text in blocks:
        if b.kind == "heading" or b.section and re.search(r"истори\w+\s+изменен|термин|глоссар|определени|сокращени|abbrev|glossar", b.section, re.I):
            continue
        if b.kind == "table_row" and b.header and any(_GLOSS_TERM_COL.match(h.strip()) for h in b.header):
            continue
        for s0, sent in _sentence_list(text):
            cands = []
            for m in list(_ADJ_RU.finditer(sent)) + list(_NEW_RU.finditer(sent)) + list(_VALID_RU.finditer(sent)):
                cands.append((m.start(), m.group("adj"), m.group("noun"), "ru"))
            for m in _ADJ_EN.finditer(sent):
                cands.append((m.start("adj"), m.group("adj"), m.group("noun"), "en"))
            for m in _ADJ_KK.finditer(sent):
                cands.append((m.start("adj"), m.group("adj"), m.group("noun"), "kk"))
            for pos, adj, noun, lang in cands:
                lead = sent[:pos]
                if re.search(r"(?:^|\s)(?:и|или|and|or|а\s+также)\s+$", lead):
                    continue  # «новых и действующих клиентов» names everybody, it is not a condition
                if lang == "ru" and not (_COND_LEAD.search(lead) or _ROLE_LEAD.search(lead)):
                    continue
                if lang == "en" and not re.search(r"\b(?:if|when|for|only|where|with|has|have|having|by|of)\s+(?:an?\s+|the\s+)?(?:\S+\s+){0,1}$", lead, re.I):
                    continue
                key = lang + ":" + _stem(adj, 6) + " " + _stem(noun, 4)
                counted[key] = counted.get(key, 0) + 1
                found.setdefault(key, (b, s0 + pos, adj, noun, lang, (b.bid, s0)))
    out = []
    for key, (b, pos, adj, noun, lang, own) in found.items():
        if _phrase_defined(adj, noun, blocks, own, lang):
            continue
        out.append((b.line_start, pos, f"{adj} {noun}".lower(), b))
    if not out:
        return []
    out.sort(key=lambda x: (x[0], x[1]))
    phrases = [p for _, _, p, _ in out]
    quotes = [ctx.q_sentence(b, pos) for _, pos, _, b in out]
    quoted = [f"«{p}»" for p in phrases]
    return _emit(
        ctx, rule, quotes,
        title="Условие зависит от неопределённого понятия: " + fmt_list(quoted, 3),
        problem=("Правило срабатывает или нет в зависимости от свойства, которое в документе нигде не определено: "
                 + fmt_list(quoted, 6) + ". Каждый поймёт его по-своему: разработчик заложит одно условие, "
                 "тестировщик проверит другое, а клиент увидит третье."),
        recommendation=("Для каждого понятия добавьте проверяемое определение в разделе «Термины» "
                        "(например: «Карта считается активной, если по ней была операция за последние 90 дней»)."),
        question=ask(f"Как в документе определяется понятие «{phrases[0]}»?", ["Дать определение с числами", "Сослаться на справочник или статус в системе"]),
    )


GLOSSARY_RULES = {
    "abbreviation-undefined": rule_abbreviation_undefined,
    "term-defined-twice": rule_term_defined_twice,
    "condition-undefined": rule_condition_undefined,
}
