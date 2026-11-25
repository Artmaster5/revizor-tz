"""Text → ordered structural blocks (headings, paragraphs, list items, table rows, code).

Two parsers: Markdown (also used for .docx and .html, which the loader converts to Markdown)
and plain text (txt, PDF). Line numbers are 1-based and refer to ``SourceDoc.lines``.
"""
from __future__ import annotations

import json
import re

from .model import Block, SourceDoc

# --- invisible characters ----------------------------------------------------

INVISIBLE_CHARS = (
    "​‌‍‎‏"
    "‪‫‬‭‮"
    "⁠⁡⁢⁣⁤"
    "﻿"
)
_INVISIBLE_RE = re.compile("[" + INVISIBLE_CHARS + "]")

# --- document identifiers -----------------------------------------------------

_ID_HEAD = re.compile(r"^\s*(?:[-*+•]\s+)?(?:\*\*|__)?\s*")
_ID_LETTERS = re.compile(r"([A-Za-zА-Яа-яЁё]{1,6})([-.]?)( ?)(\d{1,4}(?:\.\d{1,4})*)")
_ID_NUMERIC = re.compile(r"(\d{1,3}(?:\.\d{1,3})+)\.?(?=\s+[A-ZА-ЯЁ«\"*_]|\s*$|\)|\*\*)")
_ID_TAIL = re.compile(r"^(?:\*\*|__)?(?:[.):\]]|\s|$|—|–)")
_NOT_ID_WORDS = {
    "HTTP", "HTTPS", "RFC", "ISO", "IEC", "IEEE", "ГОСТ", "GOST", "KZT", "USD", "EUR", "RUB", "API",
    "SLA", "OK", "PDF", "JSON", "XML", "UTF", "SHA", "TLS", "SSL", "AES", "RSA", "MD", "COVID", "ТОО",
    "АО", "ИП", "PCI", "DSS", "ID", "IP", "TCP", "UDP", "SQL", "WIN", "IOS", "MAC",
}


def detect_doc_id(text: str) -> str:
    """Identifier written at the very start of a block: «Ф-1», «FR-12», «НФТ 3», «REQ.07», «1.2.3»."""
    if not text:
        return ""
    s = _ID_HEAD.sub("", _INVISIBLE_RE.sub("", text), count=1)
    m = _ID_LETTERS.match(s)
    if m:
        letters, sep, space, num = m.groups()
        rest = s[m.end():]
        if _ID_TAIL.match(rest) and letters[0].isupper():
            caps = letters.upper() == letters
            ok = False
            if sep == "-":
                ok = letters.upper() not in _NOT_ID_WORDS
            elif sep == ".":
                ok = caps and letters not in _NOT_ID_WORDS
            elif space:
                ok = caps and len(letters) >= 2 and letters not in _NOT_ID_WORDS
            else:
                ok = caps and letters not in _NOT_ID_WORDS
            if ok:
                return f"{letters}{sep}{space}{num}"
    m = _ID_NUMERIC.match(s)
    if m:
        return m.group(1)
    return ""


# --- inline cleaning ---------------------------------------------------------

_HTML_TAG = re.compile(
    r"</?(?:b|i|u|em|strong|br|span|p|div|sup|sub|code|s|del|mark|font|small|big)\b[^>]*>", re.I
)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BOLD = re.compile(r"(?<![\d*])(?:\*\*|__)(?=\S)|(?<=\S)(?:\*\*|__)(?![\d*])")
_ITALIC = re.compile(r"(?<![\w*])\*(?=[^\s*])|(?<=[^\s*])\*(?![\w*])")
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>])")
# A masked value («4539 14** **** 6467», «123456******»): digits and asterisks, groups joined by spaces or hyphens,
# with at least four asterisks, that is not emphasis around a number («**200**»). Its asterisks are protected from the
# emphasis clean-up, so the value is shown exactly as masked.
_MASK_RUN = re.compile(r"(?<![\w*])[\d*]+(?:[ \-][\d*]+)*(?![\w*])")
_EMPH_NUMBER = re.compile(r"(\*+)\d+\1")
_STAR_GUARD = "\ue000"


def _guard_masks(s: str) -> str:
    def one(m: re.Match) -> str:
        t = m.group(0)
        if t.count("*") < 4 or _EMPH_NUMBER.fullmatch(t):
            return t
        return t.replace("*", _STAR_GUARD)
    return _MASK_RUN.sub(one, s)


def clean_inline(s: str) -> str:
    """Remove Markdown markup from one piece of text, keep the words."""
    s = _INVISIBLE_RE.sub("", s)
    s = _HTML_TAG.sub(" ", s)
    s = _IMAGE.sub(r"\1", s)
    s = _LINK.sub(r"\1", s)
    s = _guard_masks(s)
    s = _BOLD.sub("", s)
    s = _ITALIC.sub("", s)
    s = s.replace(_STAR_GUARD, "*")
    s = s.replace("`", "")
    s = _ESCAPE.sub(r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


# --- Markdown -----------------------------------------------------------------

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
_ATX = re.compile(r"^\s{0,3}(#{1,6})(?:\s+(.*?))?\s*#*\s*$")
_HR = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_SETEXT = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)*\|?\s*$")
_LIST = re.compile(r"^(\s*)([-*+•]|\d{1,3}[.)])\s+(.*)$")
_BOLD_LEAD = re.compile(r"^\s*(?:\*\*|__)\S")
_COMMENT_START = re.compile(r"^\s*<!--")


def split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, i = [], [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur))
    return [c.strip() for c in cells]


def is_table_separator(line: str) -> bool:
    return "-" in line and bool(_TABLE_SEP.match(line)) and ("|" in line or line.strip().startswith(":"))


def _starts_construct(lines: list[str], i: int) -> bool:
    line = lines[i]
    if not line.strip():
        return True
    if _FENCE.match(line) or _ATX.match(line) or _HR.match(line) or _COMMENT_START.match(line):
        return True
    if _LIST.match(line):
        return True
    if "|" in line and i + 1 < len(lines) and is_table_separator(lines[i + 1]):
        return True
    return False


_LEAD_SHAPE = re.compile(r"^\s*(?:\*\*|__)([^*_\n]{1,20}?)(?:\*\*|__)")
_CONNECTOR = re.compile(r"^(?:или|либо|or|и|а также)[\s:.,—-]*$", re.I)


def _lead_shape(raw: str) -> str:
    """Shape of a bold lead with a number: «**401.** Ответ…» -> «0.»; "" when there is none."""
    m = _LEAD_SHAPE.match(raw or "")
    if not m or not re.search(r"\d", m.group(1)):
        return ""
    return re.sub(r"\d+", "0", m.group(1)).strip()


class _Builder:
    def __init__(self, line_pages: list[int] | None):
        self.blocks: list[Block] = []
        self.stack: list[tuple[int, str]] = []
        self.line_pages = line_pages
        self.prev_lead = ""  # lead shape of the previous block when it was a paragraph
        # After a heading that holds only a code example, a paragraph with the same bold numbered lead as the
        # paragraphs before that heading («**401.** …» after «#### 200. …» + JSON) belongs to the parent section.
        self.resume: dict | None = None

    def section(self) -> str:
        return " › ".join(t for _, t in self.stack if t)

    def add(self, kind: str, text: str, raw_lines: list[str], start: int, end: int, **kw) -> Block:
        shape = _lead_shape(raw_lines[0]) if kind == "paragraph" and raw_lines else ""
        rs = self.resume
        if rs is not None and kind != "heading":
            if kind == "code":
                rs["code"] = True
            elif (kind == "paragraph" and rs["code"] and shape and shape == rs["shape"]):
                self.stack = rs["stack"]
                self.resume = None
            elif kind == "paragraph" and rs["code"] and _CONNECTOR.match(text.strip()):
                pass
            else:
                self.resume = None
        self.prev_lead = shape
        page = None
        if self.line_pages and 0 < start <= len(self.line_pages):
            page = self.line_pages[start - 1]
        b = Block(
            bid=f"B{len(self.blocks) + 1:03d}",
            kind=kind,
            text=text,
            raw="\n".join(raw_lines),
            section=kw.pop("section", None) or self.section(),
            line_start=start,
            line_end=end,
            page=page,
            **kw,
        )
        if not b.doc_id and kind != "code":
            b.doc_id = detect_doc_id(raw_lines[0] if raw_lines else text) if kind != "table_row" else ""
        self.blocks.append(b)
        return b

    def heading(self, level: int, title: str, raw: str, lineno: int) -> None:
        self.resume = None
        if self.prev_lead and self.stack:
            self.resume = {"stack": list(self.stack), "shape": self.prev_lead, "code": False}
        self.prev_lead = ""
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        self.stack.append((level, title))
        self.add("heading", title, [raw], lineno, lineno, level=level, section=self.section())


def _parse_markdown(text: str, line_pages: list[int] | None) -> list[Block]:
    lines = text.split("\n")
    b = _Builder(line_pages)
    para: list[int] = []  # 0-based indexes of pending paragraph lines

    def flush() -> None:
        if not para:
            return
        raw = [lines[k] for k in para]
        txt = clean_inline(" ".join(r.strip() for r in raw))
        if txt:
            b.add("paragraph", txt, raw, para[0] + 1, para[-1] + 1)
        para.clear()

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            flush()
            i += 1
            continue
        fm = _FENCE.match(line)
        if fm:
            flush()
            fence = fm.group(1)
            j = i + 1
            while j < n:
                cm = re.match(r"^\s{0,3}(`{3,}|~{3,})\s*$", lines[j])
                if cm and cm.group(1)[0] == fence[0] and len(cm.group(1)) >= len(fence):
                    break
                j += 1
            end = min(j, n - 1)
            inner = lines[i + 1:j]
            code_text = _INVISIBLE_RE.sub("", "\n".join(inner)).strip("\n")
            b.add("code", code_text, lines[i:end + 1], i + 1, end + 1)
            i = end + 1
            continue
        if _COMMENT_START.match(line):
            flush()
            j = i
            while j < n and "-->" not in lines[j]:
                j += 1
            end = min(j, n - 1)
            raw = lines[i:end + 1]
            joined = "\n".join(raw)
            content = joined.split("<!--", 1)[1]
            content = content.split("-->", 1)[0]
            txt = re.sub(r"\s+", " ", _INVISIBLE_RE.sub("", content)).strip()
            b.add("paragraph", txt or "(пустой HTML-комментарий)", raw, i + 1, end + 1)
            i = end + 1
            continue
        am = _ATX.match(line)
        if am:
            flush()
            level = len(am.group(1))
            title = clean_inline(am.group(2) or "")
            b.heading(level, title, line, i + 1)
            i += 1
            continue
        if para and _SETEXT.match(line) and len(para) >= 1:
            ch = line.strip()[0]
            if ch == "=" or (ch == "-" and len(para) == 1):
                raw_lines = [lines[k] for k in para]
                title = clean_inline(" ".join(r.strip() for r in raw_lines))
                start = para[0] + 1
                para.clear()
                level = 1 if ch == "=" else 2
                b.resume = None
                b.prev_lead = ""
                while b.stack and b.stack[-1][0] >= level:
                    b.stack.pop()
                b.stack.append((level, title))
                b.add("heading", title, raw_lines + [line], start, i + 1, level=level, section=b.section())
                i += 1
                continue
        if _HR.match(line):
            flush()
            i += 1
            continue
        if "|" in line and i + 1 < n and is_table_separator(lines[i + 1]):
            flush()
            header = [clean_inline(c) for c in split_row(line)]
            header = [h if h else f"Столбец {k + 1}" for k, h in enumerate(header)]
            j = i + 2
            while j < n and lines[j].strip() and "|" in lines[j] and not _FENCE.match(lines[j]):
                cells = [clean_inline(c) for c in split_row(lines[j])]
                if len(cells) < len(header):
                    cells += [""] * (len(header) - len(cells))
                elif len(cells) > len(header):
                    extra = cells[len(header) - 1:]
                    cells = cells[: len(header) - 1] + [" | ".join(c for c in extra if c)]
                empty = not any(cells)
                txt = " | ".join(f"{h}: {v}" for h, v in zip(header, cells) if v)
                blk = b.add(
                    "table_row", txt, [lines[j]], j + 1, j + 1,
                    header=list(header), cells=cells, is_empty=empty,
                )
                if cells and not empty:
                    blk.doc_id = detect_doc_id(cells[0]) if detect_doc_id(cells[0]) == cells[0].rstrip(".") else ""
                j += 1
            i = j
            continue
        lm = _LIST.match(line)
        if lm:
            flush()
            item = [i]
            j = i + 1
            while j < n and lines[j].strip() and not _starts_construct(lines, j):
                item.append(j)
                j += 1
            raw = [lines[k] for k in item]
            body = [lm.group(3)] + [lines[k].strip() for k in item[1:]]
            txt = clean_inline(" ".join(body))
            if txt:
                b.add("list_item", txt, raw, i + 1, j)
            i = j
            continue
        # paragraph line; a bold lead or an identifier at the line start opens a new entry
        if para and (_BOLD_LEAD.match(line) or detect_doc_id(line)):
            flush()
        para.append(i)
        i += 1
    flush()
    return b.blocks


# --- plain text (txt, PDF) ----------------------------------------------------

# numbered items go up to 99: «200. Ответ …» is a status code label, not the 200th list item
_PLAIN_LIST = re.compile(r"^\s*(?:[-•*–·]|\d{1,2}[.)]|[a-zа-я]\))\s+\S")
_MULTI_NUM = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3})+)\.?\s+(\S.*)$")
_SINGLE_NUM = re.compile(r"^\s*(\d{1,3})[.)]\s+(\S.*)$")
_SENT_END = re.compile(r"[.!?;:]\s*$")


_GAP = re.compile(r"\S(?:\s{2,})\S")


def _is_caps_heading(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return (len(letters) >= 4 and all(c.isupper() for c in letters) and len(s.split()) <= 12
            and not _GAP.search(s))


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _plain_cells(line: str) -> list[tuple[int, str]]:
    """Cells of a space-aligned table line: runs of text separated by two or more spaces."""
    line = line.replace("\t", "    ")
    return [(m.start(), m.group(0)) for m in re.finditer(r"\S+(?: \S+)*", line)]


def _plain_table_at(lines: list[str], i: int, line_pages: list[int] | None = None):
    """A space-drawn table starting at line i (plain text, or PDF extracted in layout mode).

    Header: 3–8 short cells separated by 2+ spaces (or 2 cells separated by a wide gap with exactly aligned rows).
    Rows follow without a blank line; a table may continue across a PDF page break.
    Returns (header cells [(pos, text)], end index, rows [(first line, last line, cells)]) or None.
    """
    s0 = lines[i].strip()
    head = _plain_cells(lines[i])
    if _SENT_END.search(s0) or any(len(t.split()) > 5 for _, t in head):
        return None
    if not (3 <= len(head) <= 8 or len(head) == 2):
        return None
    starts = [p for p, _ in head]
    if len(head) == 2:
        gap = head[1][0] - (head[0][0] + len(head[0][1]))
        if gap < 4 or any(len(t.split()) > 3 for _, t in head):
            return None

    tol = 2  # columns may shift a little on a new PDF page (layout is computed per page)

    def cells_of(line: str) -> list[tuple[int, str]] | None:
        cs = _plain_cells(line)
        if not cs or not all(min(abs(p - s_) for s_ in starts) <= tol for p, _ in cs):
            return None
        return cs

    def page(k: int) -> int:
        return line_pages[k] if line_pages and k < len(line_pages) else 0

    j = i + 1
    body: list[int] = []
    while j < len(lines):
        if lines[j].strip():
            if cells_of(lines[j]) is None:
                break
            body.append(j)
            j += 1
            continue
        # blank line: the table goes on only across a page break, with a full row on the next page
        k = j
        while k < len(lines) and not lines[k].strip():
            k += 1
        if body and k < len(lines) and line_pages and page(k) != page(body[-1]):
            # columns may shift on the new page; its first lines may be a wrapped first cell,
            # so a full row must follow soon
            tol = 6
            ahead = []
            for q in range(k, min(k + 5, len(lines))):
                if not lines[q].strip() or cells_of(lines[q]) is None:
                    break
                ahead.append(len(cells_of(lines[q]) or []))
            if any(n_ >= 2 for n_ in ahead):
                j = k
                continue
            tol = 2
        break
    multi = sum(1 for k in body if len(cells_of(lines[k]) or []) >= 2)
    if len(head) == 2:
        exact = [k for k in body if len(_plain_cells(lines[k])) == 2 and abs(_plain_cells(lines[k])[1][0] - starts[1]) <= 1]
        if len(exact) < 2:
            return None
    elif len(body) < 2 or (multi < 2 and multi != 0):
        # header-only table (PDF of an unfilled form): short capitalised cells, then a wide gap
        if not body and len(head) >= 3 and all(t[:1].isupper() and len(t.split()) <= 4 for _, t in head):
            gap_ok = all(not lines[k].strip() for k in range(i + 1, min(i + 3, len(lines))))
            prev_blank = i == 0 or not lines[i - 1].strip()
            if gap_ok and prev_blank and i + 2 < len(lines):
                return head, i + 1, [(i, i, [""] * len(head))]
        return None
    # group lines into rows: a line starts a new row unless it continues the previous one
    rows: list[tuple[int, int, list[str]]] = []
    for k in body:
        vals = [""] * len(head)
        for p, t in cells_of(lines[k]) or []:
            c = min(range(len(starts)), key=lambda c_: abs(starts[c_] - p))
            vals[c] = (vals[c] + " " + t).strip()
        cont = False
        if rows and rows[-1][1] == k - 1:
            prev = rows[-1][2]
            if not vals[0]:
                cont = True  # wrapped text in another column
            elif vals[0][:1].islower():
                fills_gap = any(v and not prev[c] for c, v in enumerate(vals) if c > 0)
                only_first = not any(vals[1:])
                cont = only_first or fills_gap
        if cont:
            f, _, prev = rows[-1]
            rows[-1] = (f, k, [(a + " " + v).strip() if v else a for a, v in zip(prev, vals)])
        else:
            rows.append((k, k, vals))
    return head, j, rows


def _plain_heading(lines: list[str], i: int) -> tuple[int, str] | None:
    s = lines[i].strip()
    if not s or len(s) > 120 or _SENT_END.search(s) or s.endswith(","):
        return None
    words = s.split()
    prev_blank = i == 0 or not lines[i - 1].strip()
    next_blank = i + 1 >= len(lines) or not lines[i + 1].strip()
    m = _MULTI_NUM.match(s)
    if m and len(words) <= 13 and re.match(r"[A-ZА-ЯЁ«\"]", m.group(2)):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        # a wrapped numbered paragraph ("1.1. Модуль выполняет …" + indented continuation) is not a heading
        if nxt.strip() and (_indent(nxt) > _indent(lines[i]) or len(words) > 8):
            return None
        return m.group(1).count(".") + 1, s
    m = _SINGLE_NUM.match(s)
    if m:
        num = int(m.group(1))
        if len(words) > 9 or not re.match(r"[A-ZА-ЯЁ«\"]", m.group(2)):
            return None
        # a numbered line inside a numbered sequence is a list item, never a heading
        for k in (i - 1, i + 1, i - 2, i + 2):
            if 0 <= k < len(lines):
                mk = _SINGLE_NUM.match(lines[k].strip())
                if mk and abs(int(mk.group(1)) - num) == 1:
                    return None
        if prev_blank and next_blank:
            return 1, s
        return None
    if _is_caps_heading(s):
        return 1, s
    if len(words) <= 8 and prev_blank and next_blank and s[0].isupper() and not _PLAIN_LIST.match(s):
        return 6, s
    return None


def _json_end(lines: list[str], i: int) -> int | None:
    """Index of the last line of a JSON object/array starting at line i, or None if it does not look like JSON."""
    depth = 0
    in_str = False
    esc = False
    for k in range(i, min(i + 200, len(lines))):
        if not lines[k].strip():
            return None
        for ch in lines[k]:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth < 0:
                    return None
        if depth == 0:
            body = "".join(lines[i:k + 1]).strip()
            return k if body[-1:] in "}]" and ('":' in body or body.startswith("[")) else None
    return None


def _parse_plain(text: str, line_pages: list[int] | None, headings: dict[int, int] | None = None) -> list[Block]:
    lines = text.split("\n")
    b = _Builder(line_pages)
    cur: list[int] = []
    cur_kind = "paragraph"

    def flush() -> None:
        nonlocal cur_kind
        if cur:
            raw = [lines[k] for k in cur]
            first = raw[0].strip()
            if cur_kind == "list_item":
                first = re.sub(r"^(?:[-•*–·]|\d{1,2}[.)]|[a-zа-я]\))\s+", "", first)
            txt = clean_inline(" ".join([first] + [r.strip() for r in raw[1:]]))
            if txt:
                b.add(cur_kind, txt, raw, cur[0] + 1, cur[-1] + 1)
        cur.clear()
        cur_kind = "paragraph"

    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            flush()
            i += 1
            continue
        tb = _plain_table_at(lines, i, line_pages)
        if tb:
            flush()
            head, end, rows = tb
            header = [clean_inline(t) for _, t in head]
            for f, l_, vals in rows:
                cells = [clean_inline(v) for v in vals]
                empty = not any(cells)
                txt = " | ".join(f"{h}: {v}" for h, v in zip(header, cells) if v)
                blk = b.add("table_row", txt, lines[f:l_ + 1], f + 1, l_ + 1, header=list(header), cells=cells,
                            is_empty=empty)
                did = detect_doc_id(cells[0]) if cells and cells[0] else ""
                blk.doc_id = did if did and did == cells[0].rstrip(".") else ""
            i = end
            continue
        i += 1
        if headings and i in headings and len(s) <= 150:
            flush()
            b.heading(headings[i], clean_inline(s), line, i)
            continue
        h = _plain_heading(lines, i - 1)
        if h:
            flush()
            b.heading(h[0], clean_inline(h[1]), line, i)
            continue
        if s[:1] in "{[":
            end_json = _json_end(lines, i - 1)
            if end_json is not None:
                flush()
                raw = lines[i - 1:end_json + 1]
                b.add("code", "\n".join(r.rstrip() for r in raw), raw, i, end_json + 1)
                i = end_json + 1
                continue
        if _PLAIN_LIST.match(s):
            flush()
            cur_kind = "list_item"
            cur.append(i - 1)
            continue
        if cur:
            prev = lines[cur[-1]].strip()
            new_sentence_line = _SENT_END.search(prev) and re.match(r"[A-ZА-ЯЁ0-9«\"(]", s)
            if new_sentence_line or detect_doc_id(s):
                flush()
        cur.append(i - 1)
    flush()
    return b.blocks


# --- public API ---------------------------------------------------------------


def looks_like_markdown(text: str) -> bool:
    lines = text.split("\n")
    score = 0
    for k, ln in enumerate(lines):
        if re.match(r"^#{1,6}\s+\S", ln) or _FENCE.match(ln):
            score += 1
        elif is_table_separator(ln) and k > 0 and "|" in lines[k - 1]:
            score += 2
        if score >= 2:
            return True
    return False


def parse_blocks(text: str, fmt: str, line_pages: list[int] | None = None,
                 headings: dict[int, int] | None = None) -> list[Block]:
    """Split a document into blocks. ``headings`` (PDF only): {1-based line: level} of lines set in a large font."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if fmt in ("md", "markdown", "docx", "html") or (fmt == "txt" and looks_like_markdown(text)):
        return _parse_markdown(text, line_pages)
    return _parse_plain(text, line_pages, headings)


def numbered_text(doc: SourceDoc) -> str:
    return "\n".join(f"{n:>4}| {line}" for n, line in enumerate(doc.lines, 1))


def blocks_for_prompt(doc: SourceDoc) -> str:
    out = []
    for b in doc.blocks:
        out.append(json.dumps({"id": b.bid, "kind": b.kind, "section": b.section, "text": b.text}, ensure_ascii=False))
    return "\n".join(out)
