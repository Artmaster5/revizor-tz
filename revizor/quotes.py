"""Quote verification: every quote in the report must exist in the source text.

The document is normalised once with an index map (normalised position → original offset),
so a confirmed quote is always printed as the ORIGINAL source span.
"""
from __future__ import annotations

import bisect
import difflib
import re

from .model import Block, Quote, Requirement, SourceDoc
from .structure import INVISIBLE_CHARS, is_table_separator

_QUOTE_CHARS = set("«»“”„\"'’‘‚‹›")
_DASH_CHARS = set("—–‑‒−-")
_SPACE_CHARS = set(" \t            　\r\f\v")
_INVISIBLE = set(INVISIBLE_CHARS)
_LINE_PREFIX = re.compile(r"^\s*(?:>\s*)*(?:#{1,6}\s+|(?:[-*+•]|\d{1,3}[.)])\s+)?")
_ELLIPSIS = re.compile(r"\s*(?:\[\s*(?:\.\.\.|…)\s*\]|…|\.{3,})\s*")
_NUM = re.compile(r"\d+(?:[  ]\d{3})*(?:[.,]\d+)?")

MIN_CHARS = 12
MIN_WORDS = 3
FUZZY_RATIO = 0.90
ELLIPSIS_PART = 8
ELLIPSIS_GAP = 600


def _norm_map(text: str) -> tuple[str, list[int]]:
    out: list[str] = []
    idx: list[int] = []
    pos = 0

    def space(at: int) -> None:
        if out and out[-1] != " ":
            out.append(" ")
            idx.append(at)

    for line in text.split("\n"):
        if is_table_separator(line):
            space(pos)
            pos += len(line) + 1
            continue
        m = _LINE_PREFIX.match(line)
        start = m.end() if m else 0
        n = len(line)
        k = start
        while k < n:
            c = line[k]
            at = pos + k
            if c in "*`" or c in _INVISIBLE:
                k += 1
                continue
            if c == "_" and ((k + 1 < n and line[k + 1] == "_") or (k > 0 and line[k - 1] == "_")):
                k += 1
                continue
            if c == "\\" and k + 1 < n and not line[k + 1].isalnum():
                k += 1
                continue
            if c == "|" or c in _SPACE_CHARS or c.isspace():
                space(at)
            elif c in _QUOTE_CHARS:
                out.append('"')
                idx.append(at)
            elif c in _DASH_CHARS:
                out.append("-")
                idx.append(at)
            elif c in "ёЁ":
                out.append("е")
                idx.append(at)
            else:
                for lc in c.lower():
                    out.append(lc)
                    idx.append(at)
            k += 1
        space(pos + n)
        pos += n + 1
    # trim leading/trailing spaces
    while out and out[0] == " ":
        out.pop(0)
        idx.pop(0)
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def normalize(text: str) -> str:
    """Matching form: no markup, unified quotes/dashes/spaces, ё→е, lower case."""
    return _norm_map(text or "")[0]


def _query_form(text: str) -> str:
    q = normalize(text)
    return q.strip(" .,;:!?\"").strip()


def _numbers(s: str) -> list[str]:
    return [re.sub(r"[  ]", "", n).replace(",", ".") for n in _NUM.findall(s)]


class QuoteIndex:
    def __init__(self, doc: SourceDoc, requirements: list[Requirement] | None = None):
        self.doc = doc
        self.text = doc.text
        self.norm, self.idx = _norm_map(doc.text)
        self.line_off: list[int] = []
        off = 0
        for ln in doc.lines:
            self.line_off.append(off)
            off += len(ln) + 1
        self.blocks = list(doc.blocks)
        self._block_norm: dict[str, tuple[int, int]] = {}
        for b in self.blocks:
            s, e = self._block_offsets(b)
            self._block_norm[b.bid] = (bisect.bisect_left(self.idx, s), bisect.bisect_left(self.idx, e))
        self.rid_of: dict[str, str] = {}
        if requirements:
            self.set_requirements(requirements)

    # --- bookkeeping -----------------------------------------------------------

    def set_requirements(self, requirements: list[Requirement]) -> None:
        self.rid_of = {}
        for r in requirements:
            for bid in r.block_ids:
                self.rid_of.setdefault(bid, r.rid)

    def _block_offsets(self, b: Block) -> tuple[int, int]:
        ls = max(1, min(b.line_start, len(self.line_off)))
        le = max(ls, min(b.line_end, len(self.line_off)))
        return self.line_off[ls - 1], self.line_off[le - 1] + len(self.doc.lines[le - 1])

    def _line_of(self, off: int) -> int:
        return max(1, bisect.bisect_right(self.line_off, off))

    def _balance(self, s: int, e: int) -> tuple[int, int]:
        """Widen a span so that emphasis markers it cuts through are kept in pairs («**Ф-1.** …», not «Ф-1.** …»)."""
        t = self.text
        for mk in ("**", "__", "`"):
            if t[s:e].count(mk) % 2 == 1:
                if t[max(0, s - len(mk)):s] == mk:
                    s -= len(mk)
                elif t[e:e + len(mk)] == mk:
                    e += len(mk)
        return s, e

    def _make(self, s: int, e: int, prefer_rid: str = "", exact: bool = True) -> Quote:
        e = max(e, s + 1)
        s, e = self._balance(s, e)
        ls, le = self._line_of(s), self._line_of(e - 1)
        overlapping = [b for b in self.blocks if b.line_start <= le and b.line_end >= ls]
        section = ""
        if overlapping:
            section = overlapping[0].section
        else:
            after = [b for b in self.blocks if b.line_start > le]
            before = [b for b in self.blocks if b.line_end < ls]
            if after:
                section = after[0].section
            elif before:
                section = before[-1].section
        rids = [self.rid_of[b.bid] for b in overlapping if b.bid in self.rid_of]
        rid = prefer_rid if prefer_rid and prefer_rid in rids else (rids[0] if rids else "")
        page = None
        if self.doc.line_pages and ls - 1 < len(self.doc.line_pages):
            page = self.doc.line_pages[ls - 1]
        return Quote(text=self.text[s:e], line_start=ls, line_end=le, page=page, section=section, rid=rid, exact=exact)

    def _span(self, ns: int, ne: int) -> tuple[int, int]:
        return self.idx[ns], self.idx[ne - 1] + 1

    def _rid_ranges(self, rid: str) -> list[tuple[int, int]]:
        return [self._block_norm[b] for b, r in self.rid_of.items() if r == rid and b in self._block_norm]

    # --- number-safe exact search ------------------------------------------

    def _boundary_ok(self, ns: int, ne: int) -> bool:
        t = self.norm
        if ne < len(t) and t[ne - 1].isdigit():
            nxt = t[ne:ne + 5]
            if nxt[:1].isdigit() or re.match(r" \d{3}(?!\d)", nxt) or re.match(r"[.,]\d", nxt):
                return False
        if ns > 0 and t[ns].isdigit():
            prev = t[max(0, ns - 2):ns]
            if prev[-1:].isdigit() or re.match(r"\d[.,]$", prev):
                return False
            if prev.endswith(" ") and re.match(r"\d{3}(?!\d)", t[ns:ns + 4]) and len(prev) == 2 and prev[0].isdigit():
                return False
        return True

    def _occurrences(self, q: str, lo: int = 0, hi: int | None = None) -> list[int]:
        hi = len(self.norm) if hi is None else hi
        res, start = [], lo
        while True:
            k = self.norm.find(q, start, hi)
            if k < 0:
                return res
            if self._boundary_ok(k, k + len(q)):
                res.append(k)
            start = k + 1

    def _pick(self, occ: list[int], length: int, prefer_rid: str) -> int:
        if prefer_rid:
            for lo, hi in self._rid_ranges(prefer_rid):
                for k in occ:
                    if lo <= k and k + length <= hi:
                        return k
        return occ[0]

    # --- public ------------------------------------------------------------------

    def find(self, quote: str, *, prefer_rid: str = "") -> Quote | None:
        if not quote or not quote.strip():
            return None
        parts = [p for p in _ELLIPSIS.split(quote) if p.strip()]
        if len(parts) > 1:
            return self._find_ellipsis(parts, prefer_rid)
        q = _query_form(quote)
        if not q:
            return None
        if len(q) < MIN_CHARS and len(q.split()) < MIN_WORDS:
            return self._find_short(q, prefer_rid)
        occ = self._occurrences(q)
        if occ:
            k = self._pick(occ, len(q), prefer_rid)
            s, e = self._span(k, k + len(q))
            return self._make(s, e, prefer_rid)
        return self._find_fuzzy(q, prefer_rid)

    def _find_short(self, q: str, prefer_rid: str) -> Quote | None:
        """A short quote counts only when it is a whole block or a whole table cell."""
        cands: list[tuple[int, int]] = []
        for b in self.blocks:
            lo, hi = self._block_norm[b.bid]
            pieces = [b.text] + (b.cells if b.kind == "table_row" else [])
            if any(_query_form(p) == q for p in pieces if p):
                cands.extend((k, b.bid) for k in self._occurrences(q, lo, hi))  # type: ignore[misc]
        if not cands:
            return None
        if prefer_rid:
            for k, bid in cands:
                if self.rid_of.get(bid) == prefer_rid:
                    s, e = self._span(k, k + len(q))
                    return self._make(s, e, prefer_rid)
        k = cands[0][0]
        s, e = self._span(k, k + len(q))
        return self._make(s, e, prefer_rid)

    def _find_ellipsis(self, parts: list[str], prefer_rid: str) -> Quote | None:
        qs = [_query_form(p) for p in parts]
        qs = [q for q in qs if len(q) >= ELLIPSIS_PART]
        if not qs:
            return None
        for first in self._occurrences(qs[0]):
            pos = first + len(qs[0])
            ok = True
            for q in qs[1:]:
                occ = self._occurrences(q, pos, min(len(self.norm), pos + ELLIPSIS_GAP + len(q)))
                if not occ:
                    ok = False
                    break
                pos = occ[0] + len(q)
            if ok:
                s, e = self._span(first, pos)
                return self._make(s, e, prefer_rid)
        return None

    def _find_fuzzy(self, q: str, prefer_rid: str) -> Quote | None:
        if len(q) < MIN_CHARS:
            return None
        qwords = set(q.split())
        qnums = _numbers(q)
        best: tuple[float, int, int] | None = None
        for b in self.blocks:
            lo, hi = self._block_norm[b.bid]
            bn = self.norm[lo:hi]
            if not bn:
                continue
            bwords = set(bn.split())
            if len(qwords & bwords) < 0.5 * len(qwords):
                continue
            bnums = set(_numbers(bn))
            if any(n not in bnums for n in qnums):
                continue
            sm = difflib.SequenceMatcher(None, q, bn, autojunk=False)
            mb = [m for m in sm.get_matching_blocks() if m.size >= 3]
            if not mb:
                continue
            ws, we = mb[0].b, mb[-1].b + mb[-1].size
            # widen to word boundaries
            while ws > 0 and bn[ws - 1] != " ":
                ws -= 1
            while we < len(bn) and bn[we] != " ":
                we += 1
            window = bn[ws:we]
            ratio = difflib.SequenceMatcher(None, q, window, autojunk=False).ratio()
            if ratio >= FUZZY_RATIO and (best is None or ratio > best[0]):
                best = (ratio, lo + ws, lo + we)
        if best is None:
            return None
        ns, ne = best[1], best[2]
        while ne > ns and self.norm[ne - 1] in " .,;:":
            ne -= 1
        s, e = self._span(ns, ne)
        return self._make(s, e, prefer_rid, exact=False)

    def from_lines(self, line_start: int, line_end: int | None = None) -> Quote:
        """Exact quote of whole source lines (markup stripped at the edges)."""
        le = line_end or line_start
        s = self.line_off[line_start - 1]
        e = self.line_off[le - 1] + len(self.doc.lines[le - 1])
        s, e = self._trim(s, e)
        return self._make(s, e)

    def _trim(self, s: int, e: int) -> tuple[int, int]:
        t = self.text
        first_line_end = t.find("\n", s)
        first_line_end = e if first_line_end < 0 or first_line_end > e else first_line_end
        m = re.match(r"\s*(?:>\s*)*(?:#{1,6}\s+|(?:[-*+•]|\d{1,3}[.)])\s+|\|\s*|<!--\s*)?", t[s:first_line_end])
        if m:
            s += m.end()
        while e > s and (t[e - 1].isspace() or t[e - 1] == "|" or t[e - 1] == "#"):
            e -= 1
        if t[s:e].endswith("-->"):
            e -= 3
            while e > s and t[e - 1].isspace():
                e -= 1
        if e <= s:
            return s, max(e, s + 1)
        return s, e

    def from_block(self, block: Block, start: int | None = None, end: int | None = None) -> Quote:
        bs, be = self._block_offsets(block)
        if block.kind == "code" and block.line_end - block.line_start >= 2:
            bs = self.line_off[block.line_start]  # skip the opening fence
            last = block.line_end - 1
            if re.match(r"^\s{0,3}(`{3,}|~{3,})\s*$", self.doc.lines[last]):
                be = self.line_off[last - 1] + len(self.doc.lines[last - 1])
        if start is not None or end is not None:
            sub = block.text[start or 0: end if end is not None else len(block.text)]
            q = _query_form(sub)
            if q:
                lo, hi = self._block_norm.get(block.bid, (0, len(self.norm)))
                occ = self._occurrences(q, lo, hi) or [k for k in [self.norm.find(q, lo, hi)] if k >= 0]
                if occ:
                    s, e = self._span(occ[0], occ[0] + len(q))
                    return self._make(s, e, self.rid_of.get(block.bid, ""))
        s, e = self._trim(bs, be)
        return self._make(s, e, self.rid_of.get(block.bid, ""))
