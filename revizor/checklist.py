"""An unfilled review checklist inside the document: detection by code, answers by the model (or «unknown").

Detection is generic and needs no model: a run of at least ``MIN_RUN`` consecutive table rows or list items / lines
that each carry an UNSET mark — both options still present («Да / Нет», «Yes / No»), an empty checkbox («[ ]», «☐»)
or an empty cell under a mark column («Отметка», «Да/Нет», «Done»). The answers are a suggestion for the reviewer:
they never change findings, indices or the verdict.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .model import CHECKLIST_VERDICTS, ChecklistItem, SourceDoc
from .slicer import is_option_cell

MIN_RUN = 3
CHUNK = 25
OFFLINE_BASIS = "без модели не оценивается"
MISSING_BASIS = "модель не дала ответа на этот пункт"

_OPT = r"(?:да|нет|yes|no|не требуется|не применимо|n/?a)"
# A line that ends with the full set of options: «Пункт — Да / Нет», «Item: Yes / No / N/A».
_TRAILING_OPTIONS = re.compile(
    r"^(?P<text>.*?\w.*?)(?:[\s:—–\-|]+" + _OPT + r"(?:\s*/\s*" + _OPT + r")+)+\s*\|?\s*$", re.I)
# A line that starts with an empty checkbox: «[ ] Пункт», «☐ Пункт».
_LEADING_BOX = re.compile(r"^(?:\[\s?\]|☐|□)\s*(?P<text>\S.*)$")
_LIST_MARK = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
# A table column that holds the mark itself (its cell is left empty in an unfilled checklist).
_MARK_HEAD = re.compile(r"^(?:отметка|отметки|да\s*/\s*нет|yes\s*/\s*no|выполнено|✓|✔|☐|\[\s?\]|done|check(?:ed)?|"
                        r"ok)$", re.I)
_LETTERS = re.compile(r"[^\W\d_]", re.U)


def _has_words(s: str) -> bool:
    return len(_LETTERS.findall(s or "")) >= 2


def _line_item(line: str) -> str:
    """The item text of one text line that carries an unset mark, or ""."""
    t = _LIST_MARK.sub("", line or "", count=1).strip()
    m = _LEADING_BOX.match(t)
    if m and _has_words(m.group("text")):
        return m.group("text").strip()
    m = _TRAILING_OPTIONS.match(t)
    if m:
        text = m.group("text").strip().rstrip(":—–-|").strip()
        if _has_words(text):
            return text
    return ""


def _row_item(block) -> str:
    """The item text of a table row that carries an unset mark, or ""."""
    cells = list(block.cells or [])
    marked = any(c.strip() and is_option_cell(c) for c in cells)
    if not marked:
        header = list(block.header or [])
        marked = any(_MARK_HEAD.match((h or "").strip()) and k < len(cells) and not cells[k].strip()
                     for k, h in enumerate(header))
    if not marked:
        return ""
    for c in cells:
        c = c.strip()
        if c and not is_option_cell(c) and _has_words(c) and not _MARK_HEAD.match(c):
            return c
    return ""


def detect(doc: SourceDoc) -> list[ChecklistItem]:
    """Items of every run of ≥ MIN_RUN consecutive unset marks, numbered in document order. Never raises."""
    runs: list[list[tuple[str, int, Any]]] = []
    cur: list[tuple[str, int, Any]] = []

    def close() -> None:
        nonlocal cur
        if len(cur) >= MIN_RUN:
            runs.append(cur)
        cur = []

    pages = doc.line_pages or []

    def page_of(line: int):
        return pages[line - 1] if 0 < line <= len(pages) else None

    for b in doc.blocks:
        if b.kind == "table_row":
            text = _row_item(b)
            if text:
                cur.append((text, b.line_start, b.page if b.page else page_of(b.line_start)))
            else:
                close()
        elif b.kind in ("list_item", "paragraph"):
            raw_lines = (b.raw or b.text or "").split("\n")
            if b.kind == "list_item" and len(raw_lines) > 1:
                # a list item with continuation lines: judge the item as a whole, by its first line
                raw_lines = [b.text]
            for k, ln in enumerate(raw_lines):
                text = _line_item(ln)
                if text:
                    line = b.line_start + k
                    cur.append((text, line, page_of(line) or b.page))
                elif ln.strip():
                    close()
        else:
            close()
    close()
    out: list[ChecklistItem] = []
    for run in runs:
        for text, line, page in run:
            out.append(ChecklistItem(number=len(out) + 1, text=text, line=line, page=page))
    return out


def chunks(items: list[ChecklistItem], size: int = CHUNK) -> list[list[ChecklistItem]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def payload(items: list[ChecklistItem]) -> str:
    """One JSON line per item: number, line, text (document data, shown to the model as data)."""
    return "\n".join(json.dumps({"number": it.number, "line": it.line, "text": it.text}, ensure_ascii=False)
                     for it in items)


def fill_offline(items: list[ChecklistItem], basis: str = OFFLINE_BASIS) -> None:
    for it in items:
        it.verdict, it.basis, it.findings, it.by_model = "unknown", basis, [], False


def apply_model(ans: dict, items: list[ChecklistItem], known_fids: set[str], audit: list[dict] | None = None) -> int:
    """Apply the model's answers to ``items`` (in place). Unknown finding ids are removed; an item the model did not
    return gets «unknown». Returns the number of items answered by the model."""
    by_num = {it.number: it for it in items}
    seen: set[int] = set()
    removed = 0
    for x in (ans or {}).get("items") or []:
        if not isinstance(x, dict):
            continue
        try:
            n = int(x.get("number"))
        except (TypeError, ValueError):
            continue
        it = by_num.get(n)
        if it is None or n in seen:
            continue
        seen.add(n)
        v = str(x.get("verdict") or "unknown").strip().lower()
        it.verdict = v if v in CHECKLIST_VERDICTS else "unknown"
        it.basis = " ".join(str(x.get("basis") or "").split())[:400]
        ids = []
        for f in x.get("findings") or []:
            f = str(f).strip()
            if f in known_fids and f not in ids:
                ids.append(f)
            elif f:
                removed += 1
        it.findings = ids
        it.by_model = True
    for it in items:
        if it.number not in seen:
            it.verdict, it.basis, it.findings, it.by_model = "unknown", MISSING_BASIS, [], False
    if audit is not None and removed:
        audit.append({"type": "decision", "what": "checklist_unknown_ids_removed", "count": removed})
    return len(seen)


def counts(items: list[ChecklistItem]) -> dict[str, int]:
    return {v: sum(1 for it in items if it.verdict == v) for v in CHECKLIST_VERDICTS}
