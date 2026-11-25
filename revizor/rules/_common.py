"""Shared helpers for deterministic rules."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..model import Block, Finding, QuestionDraft, Quote, Requirement, Skipped, SourceDoc
from ..quotes import QuoteIndex
from ..structure import INVISIBLE_CHARS, detect_doc_id

_INVIS = re.compile("[" + INVISIBLE_CHARS + "]")


@dataclass
class Table:
    header: list[str]
    rows: list[Block]
    header_line: int  # 1-based line of the header row (0 if unknown)


@dataclass
class Ctx:
    doc: SourceDoc
    requirements: list[Requirement]
    skipped: list[Skipped]
    qi: QuoteIndex
    rid_of: dict[str, str] = field(default_factory=dict)
    req_blocks: list[Block] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    is_api: bool = False

    @classmethod
    def build(cls, doc: SourceDoc, requirements: list[Requirement], skipped: list[Skipped]) -> "Ctx":
        qi = QuoteIndex(doc, requirements)
        rid_of = {}
        for r in requirements:
            for b in r.block_ids:
                rid_of.setdefault(b, r.rid)
        req_blocks = [b for b in doc.blocks if b.bid in rid_of]
        ctx = cls(doc=doc, requirements=requirements, skipped=skipped, qi=qi, rid_of=rid_of, req_blocks=req_blocks)
        ctx.tables = group_tables(doc)
        ctx.is_api = describes_http_api(doc)
        return ctx

    # quotes ---------------------------------------------------------------
    def q_block(self, b: Block, start: int | None = None, end: int | None = None) -> Quote:
        return self.qi.from_block(b, start, end)

    def q_sentence(self, b: Block, pos: int) -> Quote:
        s, e = sentence_span(b, pos)
        return self.qi.from_block(b, s, e)

    def rids(self, blocks: list[Block]) -> list[str]:
        out = []
        for b in blocks:
            r = self.rid_of.get(b.bid)
            if r and r not in out:
                out.append(r)
        return out

    def heading_for(self, b: Block) -> Block | None:
        """The heading whose section the block belongs to."""
        best = None
        for h in self.doc.blocks:
            if h.line_start > b.line_start:
                break
            if h.kind == "heading":
                best = h
        return best


def finding(rule, quotes: list[Quote], *, title: str, problem: str, recommendation: str,
            question: QuestionDraft | None, req_ids: list[str] | None = None,
            severity: str | None = None) -> Finding:
    uniq: list[Quote] = []
    for q in quotes:
        if q is None:
            continue
        if any(u.text == q.text and u.line_start == q.line_start for u in uniq):
            continue
        uniq.append(q)
    rids = list(req_ids or [])
    for q in uniq:
        if q.rid and q.rid not in rids:
            rids.append(q.rid)
    return Finding(
        scope=rule.scope,
        criterion=rule.criterion,
        severity=severity or rule.severity,
        title=title,
        problem=problem,
        recommendation=recommendation,
        quotes=uniq,
        req_ids=rids,
        question=question,
        source=f"rule:{rule.id}",
        rule_ids=[rule.id],
        stage="rules",
    )


def ask(text: str, options: list[str], addressee: str = "analyst") -> QuestionDraft:
    return QuestionDraft(text=text, options=options[:4], addressee=addressee)


# --- text helpers ------------------------------------------------------------------

_ABBREV = re.compile(
    r"(?:\bт\.\s?[дпеч]|\bт\.\s?н|\bи\s?др|\bг|\bгг|\bруб|\bкоп|\bсм|\bп|\bпп|\bстр|\bнапр|\bрис|\bтабл|\bmax|\bmin|"
    r"\be\.g|\bi\.e|\betc|\bvs|\bmr|\bdr|\bNo|\bтел|\bул|\bд|\bкв|\bмлн|\bмлрд|\bтыс|\bпр|\bст)\.$",
    re.I,
)


def strip_id(text: str) -> str:
    did = detect_doc_id(text)
    if not did:
        return text
    k = text.find(did)
    if 0 <= k <= 4:
        return text[k + len(did):].lstrip(" .):—–-")
    return text


def sentences(text: str) -> list[str]:
    """Split into sentences, careful with abbreviations, decimals and leading identifiers."""
    t = strip_id(text.strip())
    out, cur = [], []
    tokens = re.split(r"(\s+)", t)
    for k, tok in enumerate(tokens):
        cur.append(tok)
        if tok and not tok.isspace() and re.search(r"[.!?]$", tok) and not _ABBREV.search(tok):
            nxt = next((x for x in tokens[k + 1:] if x and not x.isspace()), "")
            if nxt and re.match(r"[A-ZА-ЯЁ«\"(0-9]", nxt):
                out.append("".join(cur).strip())
                cur = []
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


def sentence_span(b: Block, pos: int) -> tuple[int, int]:
    """Span of the sentence (or table cell value) of block.text around position pos."""
    t = b.text
    lo, hi = 0, len(t)
    if b.kind == "table_row":
        lo = t.rfind(" | ", 0, pos)
        lo = 0 if lo < 0 else lo + 3
        hi = t.find(" | ", pos)
        hi = len(t) if hi < 0 else hi
        colon = t.find(": ", lo, pos)
        if colon >= 0:
            lo = colon + 2
    k = pos
    while k > lo:
        if t[k - 1] in ".!?" and k < len(t) and t[k] == " " and not _ABBREV.search(t[max(lo, k - 6):k]):
            break
        k -= 1
    start = k
    k = pos
    while k < hi:
        if t[k] in ".!?" and (k + 1 >= hi or t[k + 1] == " ") and not _ABBREV.search(t[max(lo, k - 5):k + 1]):
            k += 1
            break
        k += 1
    end = k
    return start + (len(t[start:end]) - len(t[start:end].lstrip())), end


def visible(text: str) -> str:
    return _INVIS.sub("", text)


def fmt_list(items: list[str], limit: int = 6) -> str:
    items = [i for i in items if i]
    if len(items) > limit:
        return ", ".join(items[:limit]) + f" и ещё {len(items) - limit}"
    return ", ".join(items)


def short(s: str, n: int = 60) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# --- tables --------------------------------------------------------------------------


def group_tables(doc: SourceDoc) -> list[Table]:
    tables: list[Table] = []
    prev: Block | None = None
    for b in doc.blocks:
        if b.kind != "table_row":
            prev = None
            continue
        # rows of one table may be separated only by blank lines (a PDF page break)
        if (prev is not None and prev.header == b.header and b.line_start > prev.line_end
                and all(not doc.lines[k - 1].strip() for k in range(prev.line_end + 1, b.line_start))):
            tables[-1].rows.append(b)
        else:
            hl = b.line_start - 2
            if not (1 <= hl <= len(doc.lines) and "|" in doc.lines[hl - 1]):
                hl = b.line_start - 1  # space-drawn table: the header is the line right above
                if not (1 <= hl <= len(doc.lines) and b.header and b.header[0] in doc.lines[hl - 1]):
                    hl = 0
            tables.append(Table(header=list(b.header), rows=[b], header_line=hl))
        prev = b
    return tables


NAME_COL = re.compile(r"^(?:parameter|param|параметр|поле|field|name|имя|атрибут|attribute|key|ключ|наименование параметра|название)\b", re.I)
TYPE_COL = re.compile(r"^(?:format|формат|type|тип|тип данных|data ?type)\b", re.I)


def column(header: list[str], pattern: re.Pattern) -> int:
    for k, h in enumerate(header):
        if pattern.match(h.strip()):
            return k
    return -1


def cell_name(s: str) -> str:
    return s.strip().strip("`'\"«»").strip()


# --- HTTP API detection -----------------------------------------------------------

METHOD_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b")
_STATUS_CTX = re.compile(
    r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*[1-5]\d\d\b|\b(?:HTTP|код\w*|статус\w*|status|code)\s*:?\s*[1-5]\d\d\b",
    re.I,
)
_PATH_RE = re.compile(r"(?<![\w.])/(?:[A-Za-z{][\w\-{}.:]*)(?:/[\w\-{}.:]+)+|https?://\S+")


def describes_http_api(doc: SourceDoc) -> bool:
    t = doc.text
    signals = 0
    signals += bool(METHOD_RE.search(t))
    signals += bool(_STATUS_CTX.search(t))
    signals += bool(_PATH_RE.search(t))
    return signals >= 2


def path_tokens(path: str) -> list[str]:
    toks = []
    for seg in re.split(r"[/?&=#.]", path):
        seg = re.sub(r"[{}]", "", seg)
        for part in re.split(r"[-_]", seg):
            toks += [w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", part)]
    return toks
