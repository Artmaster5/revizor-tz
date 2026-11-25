"""Blocks → requirements and skipped blocks (heuristic, and assembly of a model's answer)."""
from __future__ import annotations

import re

from .model import REQ_KINDS, SKIP_CATEGORIES, Block, Requirement, Skipped, SourceDoc
from .structure import detect_doc_id

_META_TITLE = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s*)?(?:общие сведения|оглавление|содержание|история изменений|история версий|"
    r"лист изменений|лист согласования|согласование документа|согласующ|блок согласующ|ревью|"
    r"approval|approvals|revision|document history|change history|table of contents|contents)",
    re.I,
)
_OPTION_CELL = re.compile(
    r"^(?:\[\s?\]|☐|(?:да|нет|yes|no|не требуется|не применимо|n/?a)(?:\s*/\s*(?:да|нет|yes|no|не требуется|не применимо|n/?a))+)$",
    re.I,
)
PLACEHOLDER_ONLY = re.compile(
    r"^\s*(?:tbd|tba|todo|tbc|to be (?:defined|determined|done)|\?{2,}|\.{3,}|…|—|–|-|n/?a|"
    r"заполня(?:ется|ются)(?:\s+\S+){0,4}|будет (?:определено|заполнено|уточнено|добавлено|описано)(?:\s+\S+){0,4}|"
    r"(?:раздел\s+)?(?:не заполнен[аоы]?|в разработке|уточняется|уточнить|согласовать позже|to do)(?:\s+\S+){0,4}|"
    r"<[^<>]{1,40}>|\[[^\[\]]{1,40}\]|lorem ipsum.*|xxx+)\s*[.!]?\s*$",
    re.I,
)
_GLUE = re.compile(r"^(?:или|либо|или же|и|or|and|пример|например|example|e\.g\.)\s*:?$", re.I)


def is_placeholder_text(text: str) -> bool:
    return bool(text) and bool(PLACEHOLDER_ONLY.match(text.strip()))


def is_option_cell(text: str) -> bool:
    return bool(_OPTION_CELL.match(text.strip()))


def is_meta_section(section: str) -> bool:
    return any(_META_TITLE.match(part.strip()) for part in section.split(" › ") if part.strip())


_KIND_BY_SECTION = [
    (r"критери\w* при[её]мк|acceptance", "acceptance"),
    (r"нефункц|non-?functional|производительн|надёжн|надежн|безопасн", "nonfunctional"),
    (r"\bцел[иья]?\b|\bgoal|objective", "goal"),
    (r"логик|процесс|bpmn|sequence|сценари|process|flow|алгоритм", "process"),
    (r"параметр|ответ|статус|request|response|rest|api|endpoint|метод|query|path|body|headers?", "api"),
    (r"данн|data|модель данн", "data"),
    (r"интеграц|integration", "integration"),
    (r"\bui\b|ux|интерфейс|экран|дизайн|макет", "ui"),
    (r"функцион|functional", "functional"),
    (r"ограничен|constraint", "constraint"),
]


def guess_kind(block: Block) -> str:
    sec = block.section.lower()
    parts = [p for p in sec.split(" › ") if p]
    for part in reversed(parts):
        for pat, kind in _KIND_BY_SECTION:
            if re.search(pat, part):
                return kind
    return "functional" if block.doc_id else "other"


def _heading_ids(blocks: list[Block]) -> dict[str, str]:
    """Letter-style ID written in a heading is inherited by the blocks under it (not section numbers)."""
    out: dict[str, str] = {}
    current = ""
    cur_level = 0
    for b in blocks:
        if b.kind == "heading":
            hid = detect_doc_id(b.text)
            if hid and not re.match(r"^\d", hid):
                current, cur_level = hid, b.level
            elif current and b.level <= cur_level:
                current, cur_level = "", 0
            continue
        if current:
            out[b.bid] = current
    return out


_KV_LINE = re.compile(r"^\s*[\w«»\"() .-]{2,40}?:\s+\S")
_META_KEY = re.compile(r"^\s*(?:верси|дата|статус|автор|разработ|утверд|согласова|связанн|исполнител|заказчик|владел|"
                       r"version|date|status|author|owner|related|approved)", re.I)
_NUMBERED_TITLE = re.compile(r"^\s*\d{1,3}(?:\.\d{1,3})*\.?\s")


def _is_doc_details(b: Block) -> bool:
    """A paragraph of «Версия: 0.9 / Дата: … / Статус: …» lines — details about the document, not requirements."""
    lines = [ln for ln in b.raw.split("\n") if ln.strip()]
    if len(lines) < 2 or b.doc_id:
        return False
    kv = [ln for ln in lines if _KV_LINE.match(ln)]
    return len(kv) * 2 >= len(lines) and sum(1 for ln in kv if _META_KEY.match(ln)) >= 2


def _is_statement(b: Block) -> bool:
    """A numbered full sentence («1.2. Цель доработки — снизить … на 30%.») — kept even inside a meta section."""
    return (b.kind in ("paragraph", "list_item") and bool(b.doc_id) and len(b.text.split()) >= 6
            and b.text.rstrip().endswith(".") and not _is_doc_details(b))


def _skip_reason(b: Block, first_heading: Block | None, single_title: bool,
                 title_area_end: int = 0) -> tuple[str, str] | None:
    if b.kind == "heading":
        return "heading", "заголовок раздела"
    if b.kind == "table_row" and b.is_empty:
        return "template", "пустая строка таблицы"
    if not b.text.strip():
        return "other", "пустой блок"
    if is_meta_section(b.section) and not _is_statement(b):
        return "meta", "служебный раздел документа (сведения, ревью, согласование)"
    if b.kind == "paragraph" and _is_doc_details(b):
        return "meta", "сведения о документе (версия, дата, статус)"
    if b.kind == "table_row":
        values = [c for c in b.cells[1:] if c.strip()] if len(b.cells) > 1 else [c for c in b.cells if c.strip()]
        if not values or all(is_option_cell(c) for c in values):
            return "template", "строка шаблона: значения не заполнены или оставлены варианты выбора"
    if b.kind != "code" and is_placeholder_text(b.text):
        return "placeholder", "заглушка вместо содержания"
    if b.raw.lstrip().startswith("<!--"):
        return "other", "скрытый комментарий (не виден читателю документа)"
    if first_heading is not None and single_title and b.section == first_heading.section and b.line_start > first_heading.line_start:
        return "explanation", "вступление под названием документа, до первого раздела"
    if b.line_start < title_area_end and not b.doc_id:
        return "explanation", "название и шапка документа, до первого нумерованного раздела"
    if first_heading is None or b.line_start < first_heading.line_start:
        if len(b.text) < 200 and not b.doc_id:
            return "explanation", "текст до первого заголовка"
    return None


def _groups(doc: SourceDoc, blocks: list[Block]):
    """Heuristic classification of the given blocks (in document order).

    Yields ("req", [blocks], kind) or ("skip", [block], (category, reason)).
    Code blocks and tiny connective paragraphs ("или", "404") join their neighbours.
    """
    all_blocks = doc.blocks
    headings = [b for b in all_blocks if b.kind == "heading"]
    first_heading = headings[0] if headings else None
    single_title = bool(first_heading and first_heading.level == 1
                        and sum(1 for h in headings if h.level == 1) == 1 and len(headings) > 1)
    pos = {b.bid: k for k, b in enumerate(all_blocks)}
    # plain documents often start with an unnumbered title ("ТЕХНИЧЕСКОЕ ЗАДАНИЕ") followed by numbered sections;
    # everything before the first numbered section is the title page
    numbered = [h for h in headings if _NUMBERED_TITLE.match(h.text)]
    title_area_end = 0
    before = [h for h in headings if numbered and h.line_start < numbered[0].line_start]
    if (len(numbered) >= 2 and len(before) == 1 and len(numbered) * 2 >= len(headings)
            and first_heading is not None and not _NUMBERED_TITLE.match(first_heading.text)):
        title_area_end = numbered[0].line_start
    out: list[list] = []
    for b in blocks:
        reason = _skip_reason(b, first_heading, single_title, title_area_end)
        if reason:
            out.append(["skip", [b], reason])
            continue
        prev = out[-1] if out else None
        adjacent = prev is not None and pos[prev[1][-1].bid] == pos[b.bid] - 1 and prev[1][-1].section == b.section
        tiny = b.kind == "paragraph" and (len(b.text.split()) <= 2 and len(b.text) <= 20)
        prev_last = prev[1][-1] if prev else None
        if adjacent and prev[0] == "req" and (
            (b.kind == "code" and prev_last.kind != "table_row")
            or (tiny and _GLUE.match(b.text) and prev_last.kind == "code")
            or (prev_last.kind == "paragraph" and len(prev_last.text.split()) <= 2
                and len(prev_last.text) <= 20 and len(prev[1]) == 1 and b.kind == "code")
            or (prev_last.kind == "paragraph" and _GLUE.match(prev_last.text or "") and b.kind == "code")
        ):
            prev[1].append(b)
            continue
        out.append(["req", [b], guess_kind(b)])
    return out


def _quote_of(blocks: list[Block]) -> str:
    return "\n".join(b.text for b in blocks if b.text)


def _requirement(blocks: list[Block], kind: str, doc_id: str, summary: str, origin: str) -> Requirement:
    return Requirement(
        rid="",
        doc_id=doc_id,
        section=blocks[0].section,
        kind=kind if kind in REQ_KINDS else "other",
        quote=_quote_of(blocks),
        summary=summary,
        block_ids=[b.bid for b in blocks],
        line_start=blocks[0].line_start,
        line_end=blocks[-1].line_end,
        page=blocks[0].page,
        origin=origin,
    )


def _skipped(blocks: list[Block], category: str, reason: str, origin: str) -> Skipped:
    return Skipped(
        block_ids=[b.bid for b in blocks],
        category=category if category in SKIP_CATEGORIES else "other",
        reason=reason,
        section=blocks[0].section,
        line_start=blocks[0].line_start,
        line_end=blocks[-1].line_end,
        text=blocks[0].text or blocks[0].raw.strip(),
        origin=origin,
    )


def _number(doc: SourceDoc, reqs: list[Requirement], skipped: list[Skipped]):
    pos = {b.bid: k for k, b in enumerate(doc.blocks)}
    reqs.sort(key=lambda r: pos.get(r.block_ids[0], 10**9))
    skipped.sort(key=lambda s: pos.get(s.block_ids[0], 10**9))
    for k, r in enumerate(reqs, 1):
        r.rid = f"REQ-{k:03d}"
    return reqs, skipped


def _classify(doc: SourceDoc, blocks: list[Block], origin: str = "heuristic"):
    inherited = _heading_ids(doc.blocks)
    reqs, skipped = [], []
    for g in _groups(doc, blocks):
        if g[0] == "skip":
            skipped.append(_skipped(g[1], g[2][0], g[2][1], origin))
        else:
            did = next((b.doc_id for b in g[1] if b.doc_id), "") or inherited.get(g[1][0].bid, "")
            reqs.append(_requirement(g[1], g[2], did, "", origin))
    return reqs, skipped


def heuristic_slice(doc: SourceDoc) -> tuple[list[Requirement], list[Skipped]]:
    reqs, skipped = _classify(doc, list(doc.blocks))
    return _number(doc, reqs, skipped)


def _runs(ids: list[str], pos: dict[str, int]) -> list[list[str]]:
    ids = sorted(ids, key=lambda b: pos[b])
    runs: list[list[str]] = []
    for bid in ids:
        if runs and pos[bid] == pos[runs[-1][-1]] + 1:
            runs[-1].append(bid)
        else:
            runs.append([bid])
    return runs


def assemble(doc: SourceDoc, req_items: list[dict], skipped_items: list[dict], *, origin: str = "model"):
    by_id = {b.bid: b for b in doc.blocks}
    pos = {b.bid: k for k, b in enumerate(doc.blocks)}
    used: set[str] = set()
    reqs: list[Requirement] = []
    skipped: list[Skipped] = []

    def take(item: dict) -> list[list[str]]:
        ids = item.get("block_ids") if isinstance(item, dict) else None
        if isinstance(ids, str):
            ids = [ids]
        if not isinstance(ids, list):
            return []
        fresh = []
        for bid in ids:
            bid = str(bid).strip()
            if bid in by_id and bid not in used and bid not in fresh:
                fresh.append(bid)
        used.update(fresh)
        return _runs(fresh, pos) if fresh else []

    for item in req_items or []:
        for run in take(item):
            blocks = [by_id[b] for b in run]
            did = str(item.get("doc_id") or "").strip() or next((b.doc_id for b in blocks if b.doc_id), "")
            reqs.append(_requirement(blocks, str(item.get("kind") or "other"), did,
                                     str(item.get("summary") or "").strip(), origin))
    for item in skipped_items or []:
        for run in take(item):
            blocks = [by_id[b] for b in run]
            skipped.append(_skipped(blocks, str(item.get("category") or "other"),
                                    str(item.get("reason") or "").strip(), origin))
    missing = [b.bid for b in doc.blocks if b.bid not in used]
    reqs, skipped = _number(doc, reqs, skipped)
    return reqs, skipped, missing


def fill_missing(doc: SourceDoc, requirements: list[Requirement], skipped: list[Skipped], missing_ids: list[str]):
    by_id = {b.bid: b for b in doc.blocks}
    blocks = [by_id[b] for b in missing_ids if b in by_id]
    pos = {b.bid: k for k, b in enumerate(doc.blocks)}
    blocks.sort(key=lambda b: pos[b.bid])
    # classify each adjacent run separately so groups never jump over classified blocks
    new_r: list[Requirement] = []
    new_s: list[Skipped] = []
    run: list[Block] = []
    for b in blocks + [None]:  # type: ignore[list-item]
        if b is not None and (not run or pos[b.bid] == pos[run[-1].bid] + 1):
            run.append(b)
            continue
        if run:
            r, s = _classify(doc, run)
            new_r += r
            new_s += s
        run = [b] if b is not None else []
    reqs = list(requirements) + new_r
    skips = list(skipped) + new_s
    return _number(doc, reqs, skips)
