"""Small Russian text helpers shared by the renderers: plural forms, numbers, locations, escaping."""
from __future__ import annotations

import re

from .model import Quote

# --- plural forms ------------------------------------------------------------------


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """forms = ("требование", "требования", "требований")."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def count(n: int, forms: tuple[str, str, str]) -> str:
    return f"{num(n)} {plural(n, forms)}"


W_REQ = ("требование", "требования", "требований")
W_FINDING = ("замечание", "замечания", "замечаний")
W_DEFECT = ("дефект", "дефекта", "дефектов")
W_QUESTION = ("вопрос", "вопроса", "вопросов")
W_BLOCK = ("блок", "блока", "блоков")
W_CALL = ("обращение", "обращения", "обращений")
W_LINE = ("строка", "строки", "строк")
W_VALUE = ("значение", "значения", "значений")
W_OBSERVATION = ("наблюдение", "наблюдения", "наблюдений")
W_QUOTE = ("цитата", "цитаты", "цитат")
W_TOKEN = ("токен", "токена", "токенов")
W_RULE = ("правило", "правила", "правил")
W_PAGE = ("страница", "страницы", "страниц")
W_MINUTE = ("минута", "минуты", "минут")


def num(n: int | float) -> str:
    """12345 → «12 345» (non-breaking space), 7.5 → «7,5»."""
    if isinstance(n, float) and not n.is_integer():
        return f"{n:.1f}".replace(".", ",")
    s = f"{int(n):,}".replace(",", " ")
    return s


def dec(x: float, digits: int = 1) -> str:
    return f"{x:.{digits}f}".replace(".", ",")


def usd(x: float | None) -> str:
    if x is None:
        return "неизвестна"
    if x == 0:
        return "$0"
    if x < 0.01:
        return "$" + f"{x:.4f}"
    return "$" + f"{x:.2f}"


def size_text(n: int | None) -> str:
    if n is None:
        return "—"
    if n < 1024:
        return f"{n} байт"
    if n < 1024 * 1024:
        return dec(n / 1024) + " КБ"
    return dec(n / 1024 / 1024) + " МБ"


def duration_text(sec: float) -> str:
    if sec < 60:
        return dec(sec) + " с"
    m, s = divmod(int(round(sec)), 60)
    return f"{m} мин {s} с"


def minutes_text(total: int) -> str:
    h, m = divmod(int(total), 60)
    if h and m:
        return f"{h} ч {m} мин"
    if h:
        return f"{h} ч"
    return f"{m} мин"


def shorten(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    if len(s) <= n:
        return s
    cut = s[: n - 1]
    sp = cut.rfind(" ")
    if sp > n * 0.6:
        cut = cut[:sp]
    return cut.rstrip(" ,;:.—-") + "…"


def one_line(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# --- locations -----------------------------------------------------------------------


def lines_text(a: int, b: int) -> str:
    return f"строка {a}" if a == b or not b else f"строки {a}–{b}"


def location(line_start: int, line_end: int, page: int | None = None) -> str:
    if page:
        return f"стр. {page}"
    return lines_text(line_start, line_end)


def quote_location(q: Quote) -> str:
    return location(q.line_start, q.line_end, q.page)


def last_section(section: str) -> str:
    parts = [p.strip() for p in (section or "").split(" › ") if p.strip()]
    return parts[-1] if parts else ""


# --- anchors ---------------------------------------------------------------------------


def anchor(item_id: str) -> str:
    """«D-012» → «d-012», «REQ-007» → «req-007», «В-03» → «v-03», «Н-01» → «n-01»."""
    s = (item_id or "").strip()
    s = s.replace("В", "v").replace("в", "v").replace("Н", "n").replace("н", "n").replace("Т", "t").replace("т", "t")
    s = s.lower()
    return re.sub(r"[^a-z0-9\-]", "", s)


# --- Markdown escaping ---------------------------------------------------------------------

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|!~$&#{}])")
_URL_SCHEME = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]{1,15}):(?=//)")
_WWW = re.compile(r"(?i)\b(www)\.")
_EMAIL_AT = re.compile(r"@")
_LINE_SIGN = re.compile(r"^(\s*)([\-+])(?=\s)", re.M)          # list item «- x», «+ x»
_LINE_RULE = re.compile(r"^(\s*)([=\-])(?=[=\-]*\s*$)", re.M)    # setext underline / rule «===», «---»
_LINE_NUM = re.compile(r"^(\s*)(\d+)([.)])(?=\s|$)", re.M)      # ordered list «1. x»


def md(s: str) -> str:
    """Escape text that came from the document or from the model so it cannot form markup.

    Collapses line breaks (use ``md_lines`` to keep them). Breaks autolinks (scheme://, www., e-mail)."""
    return _md(one_line(s))


def _md(s: str) -> str:
    s = _MD_SPECIAL.sub(r"\\\1", s)
    s = _URL_SCHEME.sub(r"\1\\:", s)
    s = _WWW.sub(r"\1\\.", s)
    s = _EMAIL_AT.sub("\\@", s)
    s = _LINE_SIGN.sub(lambda m: m.group(1) + "\\" + m.group(2), s)
    s = _LINE_RULE.sub(lambda m: m.group(1) + "\\" + m.group(2), s)
    s = _LINE_NUM.sub(lambda m: m.group(1) + m.group(2) + "\\" + m.group(3), s)
    return s


# Emphasis is «**word…**»: the text inside starts and ends with a non-space that is not a mark, so a masked
# value such as «4539 14** **** 6467» is never taken for emphasis and keeps its asterisks.
_EMPH = re.compile(r"(\*\*|__)(?=[^\s*_])(.+?)(?<=[^\s*_])\1")


def plain_quote(s: str) -> str:
    """A document quote for display: drop the author's own Markdown emphasis and code marks.

    The text stays the same words in the same order («**Request URL**» → «Request URL»); escaping is still applied
    by ``md``/``md_cell``/``blockquote`` afterwards."""
    t = _EMPH.sub(r"\2", s or "")
    t = t.replace("`", "")
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", t)
    return t


def md_lines(s: str) -> list[str]:
    """Escaped lines (blank lines removed), for blockquotes."""
    out = []
    for ln in (s or "").split("\n"):
        ln = re.sub(r"[ \t ]+", " ", ln).strip()
        if ln:
            out.append(_md(ln))
    return out


def md_cell(s: str, limit: int | None = None) -> str:
    t = one_line(s)
    if limit:
        t = shorten(t, limit)
    return _md(t) if t else "—"


def blockquote(s: str) -> str:
    lines = md_lines(s)
    return "\n".join("> " + ln for ln in lines) if lines else "> —"


def unescape_md(s: str) -> str:
    """Inverse of ``md`` for tests: drop the escaping backslashes."""
    return re.sub(r"\\(.)", r"\1", s)
