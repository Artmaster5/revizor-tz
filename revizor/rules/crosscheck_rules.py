"""Rules that compare places of one document with each other: value sets, examples against tables, numbers against rules.

Each rule is silent unless it can name both sides of the disagreement, with a quote for each.
One grouped finding per rule per document (a value set, a parameter and an arithmetic slip each get their own finding, at most three); up to five quotes.
"""
from __future__ import annotations

import json
import re

from ._common import NAME_COL, TYPE_COL, Ctx, ask, cell_name, column, finding, fmt_list, short, visible
from .api_rules import _norm_type

MAX_QUOTES = 5
_NUM = r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_NUM_RE = re.compile(r"(?<![\d.,])(?:" + _NUM + r")(?![\d]|[.,]\d)")


def parse_num(s: str) -> float | None:
    """Number with spaces as thousands separators and comma or dot as decimal point; None when unclear."""
    s = re.sub(r"[   ]", "", s.strip())
    if not re.fullmatch(r"\d+(?:[.,]\d+)?", s):
        return None
    if re.fullmatch(r"[1-9]\d{0,2}[.,]\d{3}", s):
        return None  # «1.000» may be one thousand or one: do not guess
    return float(s.replace(",", "."))


def section_label(b) -> str:
    sec = (b.section or "").split(" › ")[-1].strip()
    return f"в разделе «{short(sec, 50)}»" if sec else "в начале документа"


def _place(label: str, loc: bool = False) -> str:
    """«в разделе «X»» -> «раздел «X»» (or the prepositional form for «в …»)."""
    if label.startswith("в разделе "):
        return ("разделе " if loc else "раздел ") + label[len("в разделе "):]
    return "начале документа" if loc else "начало документа"


def _emit(ctx: Ctx, rule, quotes, *, title: str, problem: str, recommendation: str, question, severity=None):
    uniq = []
    for q in quotes:
        if q is not None and not any(u.text == q.text and u.line_start == q.line_start for u in uniq):
            uniq.append(q)
    if not uniq:
        return []
    return [finding(rule, uniq[:MAX_QUOTES], title=title, problem=problem, recommendation=recommendation,
                    question=question, severity=severity)]


def _split_sentences(text: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for part in re.split(r"(?<=[.!?;])\s+(?=[A-ZА-ЯЁ«\"(0-9-])", text):
        k = text.find(part, pos)
        k = max(k, 0)
        out.append((k, part))
        pos = k + len(part)
    return out


def _code_line_quote(ctx: Ctx, b, needle: str):
    """Quote of the line of a code block that holds ``needle`` (or the whole block when it is not found)."""
    t = b.text
    k = t.find(needle)
    if k < 0:
        return ctx.q_block(b)
    s = t.rfind("\n", 0, k) + 1
    e = t.find("\n", k)
    e = len(t) if e < 0 else e
    return ctx.q_block(b, s, e)


# --- 1. one value set, different members -------------------------------------------------------------------------

_KW = (
    r"(?P<kw>статус\w*(?:\s+(?P<q7>(?!принима|може|долж|являет|есть|бывает|вход|задаёт|задает)[а-яё]{4,}(?:а|я|ы|и|ов|ей|ых)\b))?|status(?:es)?|(?:ролей|роли|роль)(?:\s+(?P<q8>(?!принима|може|долж|являет|есть|бывает|вход)[а-яё]{4,}(?:а|я|ы|и|ов|ей|ых)\b))?|roles?|"
    r"тип\w*\s+(?P<q1>операци\w+|транзакци\w+|платеж\w+|платёж\w+|документ\w+|уведомлен\w+|событи\w+|заявк\w+|запрос\w+|клиент\w+|вклад\w+|счёт\w*|счет\w*|карт\w+)|"
    r"(?P<q2>operation|transaction|payment|document|event|notification|account|card)\s+types?|"
    r"код\w*\s+(?P<q3>ошиб\w+|отказ\w+|причин\w+|результат\w+)|(?P<q4>error|reason)\s+codes?|"
    r"причин\w+\s+(?P<q5>отказ\w+|блокировк\w+|закрыти\w+)|способ\w*\s+(?P<q6>оплат\w+|доставк\w+)|"
    r"состояни\w+|канал\w+|channels?)"
)
_KW_RE = re.compile(r"(?<![\w-])" + _KW + r"(?![\w])", re.I)
_CANON = [
    (re.compile(r"^(?:статус|status)", re.I), "статус"),
    (re.compile(r"^(?:рол|role)", re.I), "роль"),
    (re.compile(r"^(?:тип|\w+\s+types?)", re.I), "тип"),
    (re.compile(r"^(?:код|\w+\s+codes?)", re.I), "код"),
    (re.compile(r"^причин", re.I), "причина"),
    (re.compile(r"^способ", re.I), "способ"),
    (re.compile(r"^(?:состояни)", re.I), "статус"),
    (re.compile(r"^(?:канал|channel)", re.I), "канал"),
]
_ITEM = (r"(?:`[^`\n]{1,30}`|«[^»\n]{1,30}»|\"[^\"\n]{1,30}\"|“[^”\n]{1,30}”|'[^'\n]{1,30}'"
         r"|(?<![\w`])[A-Z][A-Z0-9_]{1,30}(?![\w`])|(?<![\w`])[А-ЯЁ][А-ЯЁ0-9_]{2,30}(?![\w`]))")
_SEP = r"(?:\s*[,;/|]\s*|\s*,?\s+(?:или|or|и|and)\s+)"
_LIST_RE = re.compile(_ITEM + r"(?:" + _SEP + _ITEM + r")+")
_INTRO = re.compile(r"[:—–]|-\s|значени|принима|вариант|допустим|возможн|один\s+из|одно\s+из|перечень|списк|may\s+be|one\s+of|values?|allowed|possible|\(", re.I)


def _canon(kw: str) -> str:
    for rx, name in _CANON:
        if rx.match(kw.strip()):
            return name
    return ""


def _qual(m) -> str:
    for g in ("q1", "q2", "q3", "q4", "q5", "q6", "q7", "q8"):
        v = m.groupdict().get(g)
        if v:
            return re.sub(r"[^\wа-яё]", "", v.lower().replace("ё", "е"))[:5]
    return ""


def _sec_qual(b) -> str:
    """Qualifier from the heading of the block's section («Статусы заявки» -> «заявк»)."""
    sec = (b.section or "").split(" › ")[-1]
    m = _KW_RE.search(sec)
    return _qual(m) if m else ""


def _member(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().strip("`'\"«»“”*").strip()).lower().replace("ё", "е")


_SHOWN: dict[str, str] = {}


def _remember(raw: str) -> str:
    norm = _member(raw)
    _SHOWN.setdefault(norm, raw.strip().strip("`'\"«»“”").strip())
    return norm


def _split_items(chunk: str) -> list[str]:
    return [_remember(m.group(0)) for m in re.finditer(_ITEM, chunk)]


class ValueSet:
    def __init__(self, canon, qual, members, block, line, form, label):
        self.canon, self.qual, self.members = canon, qual, members
        self.block, self.line, self.form, self.label = block, line, form, label
        self.shown = {}


def _camel_split(s: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])|_", " ", s)


def collect_sets(ctx: Ctx) -> list[ValueSet]:
    _SHOWN.clear()
    sets: list[ValueSet] = []
    doc = ctx.doc
    # tables: a column whose header (or the section title) names the set, with all values different
    for t in ctx.tables:
        rows = [r for r in t.rows if not r.is_empty]
        if len(rows) < 3:
            continue
        sec = rows[0].section.split(" › ")[-1] if rows[0].section else ""
        for ci, h in enumerate(t.header):
            m = _KW_RE.search(h)
            by_heading = False
            if not m and re.fullmatch(r"\s*(?:код|code|значение|value|название|name)\s*", h, re.I):
                m = _KW_RE.search(sec)
                by_heading = bool(m)
            if not m or (not by_heading and not re.match(r"^\W*(?:" + _KW + r")", h, re.I)):
                continue
            canon = _canon(m.group("kw"))
            if not canon:
                continue
            vals = []
            for r in rows:
                if ci >= len(r.cells):
                    continue
                v = r.cells[ci].strip()
                if not v or len(v) > 40 or len(v.split()) > 3 or not re.match(r"[A-Za-zА-Яа-яЁё`«\"“']", v):
                    vals = []
                    break
                vals.append(_remember(v))
            if len(vals) < 3 or len(set(vals)) != len(vals):
                continue
            if all(re.fullmatch(r"(?:да|нет|yes|no|true|false)", v) for v in vals):
                continue
            sets.append(ValueSet(canon, _qual(m) or _sec_qual(rows[0]), set(vals), rows[0], rows[0].line_start, "table", section_label(rows[0])))
        # a parameter table: `status | string | ACTIVE, CLOSED`
        nc = column(t.header, NAME_COL)
        if nc >= 0:
            for r in rows:
                if nc >= len(r.cells):
                    continue
                name = _camel_split(cell_name(r.cells[nc]))
                m = _KW_RE.fullmatch(name.strip()) or _KW_RE.search(name)
                if not m or not re.fullmatch(r"\W*[\w .]{1,30}", name):
                    continue
                canon = _canon(m.group("kw"))
                if not canon:
                    continue
                for ci, c in enumerate(r.cells):
                    if ci == nc:
                        continue
                    lm = _LIST_RE.fullmatch(c.strip().rstrip("."))
                    if lm:
                        mem = set(_split_items(lm.group(0)))
                        if len(mem) >= 2:
                            sets.append(ValueSet(canon, _qual(m), mem, r, r.line_start, "param", section_label(r)))
    # running text: «статусы: A, B, C» and lists under a lead line
    for b in doc.blocks:
        if b.kind in ("code", "heading", "table_row"):
            continue
        text = visible(b.text)
        for km in _KW_RE.finditer(text):
            tail = text[km.end(): km.end() + 220]
            lm = _LIST_RE.search(tail)
            if not lm:
                continue
            gap = tail[:lm.start()]
            if len(gap) > 70 or re.search(r"[.!?]\s", gap) or not _INTRO.search(gap):
                continue
            # the list must not continue into a longer chain «A → B → C»
            after = tail[lm.end(): lm.end() + 4]
            if re.match(r"\s*(?:→|->)", after):
                continue
            canon = _canon(km.group("kw"))
            mem = set(_split_items(lm.group(0)))
            if canon and len(mem) >= 3:
                sets.append(ValueSet(canon, _qual(km) or _sec_qual(b), mem, b, b.line_start, "text", section_label(b)))
                sets[-1].pos = km.start()
    return sets


def _ex_values(ctx: Ctx):
    """(canon, qual, value, block, needle) for JSON members whose key names a set («status»: "X")."""
    out = []
    for b in ctx.doc.blocks:
        if b.kind != "code":
            continue
        for m in re.finditer(r"\"([A-Za-z_][\w]*)\"\s*:\s*\"([A-Za-z_][A-Za-z0-9_]*)\"", b.text):
            km = _KW_RE.fullmatch(_camel_split(m.group(1)).strip()) or _KW_RE.search(_camel_split(m.group(1)))
            if km and _canon(km.group("kw")):
                out.append((_canon(km.group("kw")), _qual(km), _member(m.group(2)), b, m.group(0), m.group(2)))
    return out


def _fmt_members(mem) -> str:
    return ", ".join(_SHOWN.get(m, m) for m in sorted(mem)[:8]) + ("…" if len(mem) > 8 else "")


def rule_set_members_differ(ctx: Ctx, rule):
    sets = collect_sets(ctx)
    out = []
    reported: set = set()
    for i, a in enumerate(sets):
        for b2 in sets[i + 1:]:
            if a.canon != b2.canon or (a.qual and b2.qual and a.qual != b2.qual):
                continue
            if a.block.bid == b2.block.bid and a.form != "param":
                continue
            shared = a.members & b2.members
            if len(shared) < 2:
                continue
            only_a, only_b = a.members - b2.members, b2.members - a.members
            full = {"table", "text"}
            if only_a and only_b:
                pass
            elif (only_a or only_b) and a.form in full and b2.form in full and len(a.members) >= 3 and len(b2.members) >= 3 \
                    and len(only_a | only_b) <= 2:
                pass
            else:
                continue
            key = (a.canon, frozenset(a.members), frozenset(b2.members))
            if key in reported:
                continue
            reported.add(key)
            out.append((a, b2, only_a, only_b))
    findings = []
    seen_pairs = set()
    for a, b2, only_a, only_b in out:
        pk = (a.canon, id(a))
        if pk in seen_pairs:
            continue
        seen_pairs.add(pk)
        qa = ctx.q_sentence(a.block, getattr(a, "pos", 0)) if a.form == "text" else ctx.q_block(a.block)
        qb = ctx.q_sentence(b2.block, getattr(b2, "pos", 0)) if b2.form == "text" else ctx.q_block(b2.block)
        what = {"статус": "статусов", "роль": "ролей", "тип": "типов", "код": "кодов", "причина": "причин",
                "способ": "способов", "канал": "каналов"}.get(a.canon, "значений")
        diff = []
        if only_a:
            diff.append(f"только в первом: {_fmt_members(only_a)}")
        if only_b:
            diff.append(f"только во втором: {_fmt_members(only_b)}")
        findings += _emit(
            ctx, rule, [qa, qb],
            title=f"Перечень {what} в двух местах разный: {_place(a.label)} и {_place(b2.label)}",
            problem=(f"Один и тот же перечень {what} записан дважды, и состав отличается. В {_place(a.label, True)}: {_fmt_members(a.members)}; "
                     f"в {_place(b2.label, True)}: {_fmt_members(b2.members)} ({'; '.join(diff)}). Разработчик не знает, какое значение настоящее, "
                     "и тестировщик не знает, какое проверять."),
            recommendation="Оставьте перечень в одном месте и дайте на него ссылку, а в остальных местах сверьте состав.",
            question=ask("Какой перечень верный?", ["Первый", "Второй", "Объединить"], "analyst"),
            severity="MAJOR",
        )
    # an example value that the full list of the same set does not have
    exs = _ex_values(ctx)
    for s in sets:
        if s.form not in ("table", "text") or len(s.members) < 3:
            continue
        if not all(re.fullmatch(r"[a-z0-9_]+", m) for m in s.members):
            continue
        for canon, qual, val, b, needle, raw in exs:
            if canon != s.canon or (qual and s.qual and qual != s.qual) or not re.fullmatch(r"[A-Z][A-Z0-9_]+", raw):
                continue
            if val in s.members or any(val in o.members for o in sets if o.canon == s.canon):
                continue
            # one list for the whole document, and the value is named nowhere else: otherwise it may belong to another entity
            if len({frozenset(o.members) for o in sets if o.canon == s.canon}) > 1:
                continue
            if len(re.findall(r"(?<![\w])" + re.escape(raw) + r"(?![\w])", ctx.doc.text)) > 1:
                continue
            if len(s.members & {m for m in s.members}) < 3:
                continue
            findings += _emit(
                ctx, rule, [ctx.q_block(s.block) if s.form == "table" else ctx.q_sentence(s.block, getattr(s, "pos", 0)),
                            _code_line_quote(ctx, b, needle)],
                title=f"В примере значение {raw}, которого нет в перечне",
                problem=(f"{s.label[0].upper() + s.label[1:]} перечислено: {_fmt_members(s.members)}, а в примере "
                         f"встречается {raw}. Либо значение пропущено в перечне, либо пример неверен."),
                recommendation="Добавьте значение в перечень или исправьте пример.",
                question=ask(f"Существует ли значение {raw}?", ["Да — добавить в перечень", "Нет — исправить пример"], "analyst"),
                severity="MAJOR",
            )
            break
    return findings[:3]


# --- 2. fields: example against table ---------------------------------------------------------------------------------

_RESP = re.compile(r"ответ|response|успешн|successful|результат|\b[1-5]\d\d\b|статус\w*\s+ответ", re.I)
_ERR = re.compile(r"\b[45]\d\d\b|unsuccessful|ошибк|error|неуспешн|failure", re.I)
_REQ = re.compile(r"запрос|request|query|path|body|тело|header|заголов|параметр", re.I)
_MAND_COL = re.compile(r"^(?:mandatory|обязательн\w*|required|обяз\w*|req\.?)\b", re.I)
_JSON_START = re.compile(r"[\[{]")


def _direction(ctx: Ctx, b) -> str:
    """'req', 'resp' or 'err' for a block, from the headings above it and the line right above it."""
    heads = [h for h in ctx.doc.blocks if h.kind == "heading" and h.line_start < b.line_start]
    chain = [h.text for h in heads[-3:]][::-1]
    prev = ""
    idx = ctx.doc.blocks.index(b)
    for pb in reversed(ctx.doc.blocks[max(0, idx - 2):idx]):
        if pb.kind != "heading" and pb.kind != "code" and b.line_start - pb.line_end <= 2:
            prev = pb.text[-160:]
            break
    for text in chain[:1] + [prev] + chain[1:]:
        if not text:
            continue
        if _RESP.search(text):
            return "err" if _ERR.search(text) and not re.search(r"\b2\d\d\b", text) else "resp"
        if _REQ.search(text):
            return "req"
    return ""


def _jtype(v) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "null"


def _flatten(v, key: str | None, depth: int, out: list):
    if isinstance(v, dict):
        for k, x in v.items():
            out.append((k, depth, x))
            _flatten(x, k, depth + 1, out)
    elif isinstance(v, list):
        for x in v:
            _flatten(x, key, depth, out)


def json_examples(ctx: Ctx):
    """[(block, direction, [(key, depth, value)])] for code blocks holding valid JSON."""
    res = []
    dec = json.JSONDecoder()
    for b in ctx.doc.blocks:
        if b.kind != "code":
            continue
        t = b.text.strip()
        if not t or not _JSON_START.match(t):
            continue
        items: list = []
        pos, ok = 0, True
        while pos < len(t):
            while pos < len(t) and t[pos] in " \n\t\r,":
                pos += 1
            if pos >= len(t):
                break
            if t[pos] not in "{[":
                ok = pos > 0 and False
                break
            try:
                obj, end = dec.raw_decode(t, pos)
            except ValueError:
                ok = False
                break
            _flatten(obj, None, 0, items)
            pos = end
        if ok and items:
            res.append((b, _direction(ctx, b), items))
    return res


def _curl_names(ctx: Ctx):
    """Parameter names from curl --data 'a=1&b=2' and from URLs with ?a=1&b=2: [(block, name, value)]."""
    out = []
    for b in ctx.doc.blocks:
        if b.kind == "heading":
            continue
        t = b.text
        for m in re.finditer(r"(?:--data(?:-urlencode|-raw)?|-d)\s+['\"]([^'\"\s]+)['\"]", t):
            for kv in m.group(1).split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    if re.fullmatch(r"[A-Za-z_][\w.\[\]-]*", k):
                        out.append((b, k, v))
        for m in re.finditer(r"https?://\S*\?([^\s\"'`]+)|\$\w+/[\w/{}.-]*\?([^\s\"'`]+)|(?<=\s)/[\w/{}.-]*\?([^\s\"'`]+)", t):
            q = next(g for g in m.groups() if g)
            for kv in q.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    if re.fullmatch(r"[A-Za-z_][\w.\[\]-]*", k):
                        out.append((b, k, v))
    return out


def field_tables(ctx: Ctx):
    """[(Table, direction, {name_lower: (row, type_norm, mandatory_text, name)})] for tables with a name column."""
    res = []
    for t in ctx.tables:
        nc = column(t.header, NAME_COL)
        if nc < 0 or not t.rows:
            continue
        # a general-data table «Поле | Значение» is not a field table
        if not any(re.match(r"(?:type|тип|format|формат|size|размер|logic|логика|description|описание|mandatory|обязат|required)", h.strip(), re.I)
                   for i, h in enumerate(t.header) if i != nc):
            continue
        tc = column(t.header, TYPE_COL)
        mc = next((i for i, h in enumerate(t.header) if _MAND_COL.match(h.strip())), -1)
        d = _direction(ctx, t.rows[0])
        if not d:
            continue
        names: dict = {}
        for r in t.rows:
            if r.is_empty or len(r.cells) <= nc:
                continue
            raw = cell_name(r.cells[nc])
            for nm in re.split(r"\s*,\s*", raw):
                nm = nm.strip().strip("`")
                if not re.fullmatch(r"[A-Za-z_][\w.\[\]-]*", nm):
                    continue
                typ = _norm_type(r.cells[tc]) if 0 <= tc < len(r.cells) and r.cells[tc].strip() else ""
                mand = r.cells[mc] if 0 <= mc < len(r.cells) else None
                leaf = re.split(r"[.\[\]]+", nm.rstrip("[]"))
                leaf = [x for x in leaf if x][-1].lower() if leaf else nm.lower()
                names[leaf] = (r, typ, mand, nm, "." in nm or "[" in nm)
        if names:
            res.append((t, d, names))
    return res


def _type_clash(table_type: str, jt: str, value) -> str:
    """Text for a disagreement between the table type and the type of the value in the example."""
    num = {"integer", "number"}
    if table_type in num and jt == "string":
        return "в таблице число, в примере строка в кавычках"
    if table_type in num and jt == "boolean":
        return "в таблице число, в примере логическое значение"
    if table_type == "string" and jt in num:
        return "в таблице строка, в примере число без кавычек"
    if table_type == "string" and jt == "boolean":
        return "в таблице строка, в примере логическое значение"
    if table_type == "boolean" and jt == "string":
        return "в таблице логический тип, в примере строка в кавычках"
    if table_type == "boolean" and jt in num:
        return "в таблице логический тип, в примере число"
    if table_type == "integer" and jt == "number" and isinstance(value, float) and value != int(value):
        return "в таблице целое, в примере дробное число"
    return ""


def rule_example_field_mismatch(ctx: Ctx, rule):
    tables = field_tables(ctx)
    if not tables:
        return []
    exs = json_examples(ctx)
    curl = _curl_names(ctx)
    problems = []  # (quote_example, quote_table, text)
    for bucket in ("req", "resp", "err"):
        tabs = [x for x in tables if x[1] == bucket]
        if not tabs:
            continue
        names: dict = {}
        every: dict = {}  # all rows of all tables of this kind that name the field
        has_nested = False
        for t, _, nm in tabs:
            for k, v in nm.items():
                names.setdefault(k, (t, v))
                every.setdefault(k, []).append(v)
                has_nested = has_nested or v[4]
        bex = [x for x in exs if x[1] == bucket]
        carried: set = set()
        # (a) a key of an example that no table of the same kind has; (c) a type that disagrees
        for b, _, items in bex:
            for key, depth, val in items:
                lk = key.lower()
                carried.add(lk)
                if lk not in names:
                    if depth > 0 and not has_nested:
                        continue
                    problems.append((_code_line_quote(ctx, b, f"\"{key}\""), None, bucket,
                                     f"поле «{key}» есть в примере, но нет в таблице полей"))
                    continue
                t, (row, typ, mand, nm, _) = names[lk]
                clashes = [_type_clash(v[1], _jtype(val), val) if v[1] else "" for v in every[lk]]
                clash = clashes[0] if all(clashes) else ""  # a field described twice is a clash only when no row fits
                if clash:
                    problems.append((_code_line_quote(ctx, b, f"\"{key}\""), ctx.q_block(row), bucket,
                                     f"у поля «{key}» тип расходится: {clash}"))
        if bucket == "req":
            for b, k, v in curl:
                if k.lower() not in names and not any(k.lower() in nm2 for nm2 in names):
                    problems.append((ctx.q_sentence(b, max(b.text.find(k + "="), 0)), None, bucket,
                                     f"параметр «{k}» есть в примере запроса, но нет в таблице параметров"))
                carried.add(k.lower())
        # (b) a mandatory field that no example carries
        if bex or (bucket == "req" and curl):
            for lk, (t, (row, typ, mand, nm, _)) in names.items():
                if lk in carried:
                    continue
                if any(v[2] is None or not re.match(r"^\W*(?:да|yes|y|true|обязательн\w*|required|\+)\W*$", v[2].strip(), re.I) for v in every[lk]) and mand is not None:
                    continue
                if re.search(r"\{" + re.escape(nm) + r"\}|/:" + re.escape(nm) + r"\b|-H\s+[\"']" + re.escape(nm) + r"\s*:", ctx.doc.text, re.I):
                    continue  # a path or header parameter: it travels outside the body
                if mand is not None:
                    if not re.match(r"^\W*(?:да|yes|y|true|обязательн\w*|required|\+)\W*$", mand.strip(), re.I):
                        continue
                else:
                    total = len(names)
                    if total < 3 or len([k for k in names if k in carried]) / total < 0.75 or total - len([k for k in names if k in carried]) > 2:
                        continue
                    if re.search(r"если|при\s|опционал|optional|только|условн|может|if\b|when\b", " ".join(row.cells), re.I):
                        continue
                ex_b = bex[0][0] if bex else curl[0][0]
                problems.append((ctx.q_block(row), _code_line_quote(ctx, ex_b, "{") if ex_b.kind == "code" else ctx.q_sentence(ex_b, 0), bucket,
                                 f"обязательное поле «{nm}» есть в таблице, но ни в одном примере его нет"))
    if not problems:
        return []
    # one finding per document; the first five disagreements, one quote per side
    quotes, lines = [], []
    seen = set()
    for qa, qb, bucket, text in problems:
        if text in seen:
            continue
        seen.add(text)
        lines.append(text)
        quotes += [qa, qb]
    shown = lines[:3]
    more = len(lines) - len(shown)
    return _emit(
        ctx, rule, quotes,
        title="Пример и таблица полей расходятся: " + fmt_list([re.search(r"«([^»]+)»", t).group(1) for t in lines], 4),
        problem=("Пример запроса или ответа и таблица полей описывают разное: " + "; ".join(shown)
                 + (f"; и ещё {more}" if more > 0 else "") + ". Разработчик не знает, чему верить: примеру или таблице, "
                 "а интеграция сломается на первом реальном запросе."),
        recommendation="Приведите пример и таблицу к одному виду: добавьте поле в таблицу или в пример, выровняйте тип.",
        question=ask("Что верно: пример или таблица полей?", ["Таблица, исправить пример", "Пример, исправить таблицу"], "architect"),
    )


# --- 3. the same named parameter with different numbers -----------------------------------------------------------------

_UNITS = [  # (regex, class, factor)
    (r"₸|тенге|тг\b|kzt", "kzt", 1), (r"%|процент\w*", "pct", 1),
    (r"мс\b|миллисекунд\w*|ms\b", "time", 0.001), (r"секунд\w*|сек\b|с\b|sec\w*", "time", 1),
    (r"минут\w*|мин\b|min\w*", "time", 60), (r"час\w*|ч\b|hours?", "time", 3600),
    (r"дн\w*|день|сутк\w*|суток|days?", "time", 86400), (r"недел\w+|weeks?", "time", 604800),
    (r"месяц\w*|мес\b|months?", "month", 1), (r"лет\b|год\w*|years?", "month", 12),
    (r"раз\w*|попыт\w+|шт\b|штук\w*|times|attempts?", "count", 1),
    (r"символ\w*|знак\w*|цифр\w*|characters?|digits?", "chars", 1),
    (r"мб|mb\b|мегабайт\w*", "mb", 1), (r"кб|kb\b", "mb", 1 / 1024),
]
_UNIT_RE = [(re.compile(r"^\s*(?:" + p + r")", re.I), c, f) for p, c, f in _UNITS]
_HEAD_NOUN = re.compile(
    r"срок\w*|лимит\w*|сумм\w*|врем\w+|количеств\w+|числ\w+|размер\w*|длин\w+|порог\w*|комисси\w+|ставк\w+|процент\w*|частот\w+|"
    r"интервал\w*|период\w*|длительност\w+|задержк\w+|тайм-?аут\w*|попыт\w+|кэшбэк\w*|штраф\w*|пен[яи]\b|"
    r"limit|timeout|amount|fee|rate|period|interval|attempts|size|length|delay|ttl", re.I)
_STOP = {"в", "на", "для", "по", "и", "а", "но", "не", "с", "со", "у", "к", "от", "до", "из", "за", "при", "или", "the", "of", "a", "an", "is", "to"}
_BOUNDARY = re.compile(r"(?:^|[,;(]|\s)(?:если|когда|при|для|что|чтобы|который|которая|которое|причём|причем|where|when|if|that|which|while)\s", re.I)
_SKIP_SENT = re.compile(r"для\s|если|при\s|когда|кроме|иначе|исключ|по\s+умолчанию|default|if\s|when\s|for\s|except|unless|тариф|уровн|категори|сегмент|статус\w*\s+клиент|пример|например|e\.g", re.I)
_CW = (r"(?:составляет|равн\w+|не\s+более|не\s+менее|не\s+превыша\w+|не\s+должн\w+\s+превыша\w+|не\s+выше|не\s+ниже|до|от|"
       r"максимум|минимум|at\s+most|at\s+least|up\s+to|max|min|is|equals?)")
_CONN = re.compile(
    r"(?P<name>[^\d—–:=]{3,90}?)\s*(?P<conn>(?:[—–:=]\s*(?:" + _CW + r"\b\s*)?|\b" + _CW + r"\b\s*))"
    r"(?P<num>" + _NUM + r")\s*(?P<unit>[^\s\d.,;:)]{0,14}(?:\s[а-яё]{2,10})?)", re.I)


def _op_of(conn: str) -> str:
    c = conn.lower()
    if re.search(r"не\s+более|превыша|не\s+выше|до\b|максим|at\s+most|up\s+to|\bmax", c):
        return "max"
    if re.search(r"не\s+менее|не\s+ниже|минимум|от\b|at\s+least|\bmin", c):
        return "min"
    return "eq"


def _name_key(name: str) -> tuple:
    words = [w.lower().replace("ё", "е") for w in re.findall(r"[A-Za-zА-Яа-яЁё]+", name)]
    words = [w for w in words if w not in _STOP and len(w) > 1]
    return tuple(w[:5] for w in words)


def _param_mentions(ctx: Ctx):
    """[(key, op, value_in_base, unit_class, value_text, block, pos, name_text)] for «<named parameter> — <number> <unit>»."""
    out = []
    for b in ctx.doc.blocks:
        if b.kind in ("code", "heading"):
            continue
        if b.section and re.search(r"истори\w+\s+изменен|change\s?log", b.section, re.I):
            continue
        text = visible(b.text)
        pieces = []
        if b.kind == "table_row":
            if len(b.cells) == 2 and b.cells[0].strip() and b.cells[1].strip() and len(b.header) == 2:
                pieces.append((0, f"{b.cells[0].strip()} — {b.cells[1].strip()}"))
            else:
                continue
        else:
            pieces = _split_sentences(text)
        for s0, sent in pieces:
            if _SKIP_SENT.search(sent):
                continue
            for m in _CONN.finditer(sent):
                pre = sent[:m.start()].rstrip()
                if pre and not re.search(r"(?:[,;(]|\bи|\bа|\bно|[.!?])$", pre):
                    continue  # the name must start a clause, otherwise it is the tail of another phrase
                name = m.group("name")
                name = re.split(r"[.;,]\s|\(", name)[-1]
                bm = list(_BOUNDARY.finditer(name))
                if bm:
                    name = name[bm[-1].end():]
                if not _HEAD_NOUN.search(name):
                    continue
                key = _name_key(name)
                if len(key) < 2 or len(key) > 5:
                    continue
                v = parse_num(m.group("num"))
                if v is None:
                    continue
                unit = m.group("unit")
                cls, fac = "", 1.0
                for rx, c, f in _UNIT_RE:
                    if rx.match(unit):
                        cls, fac = c, f
                        break
                if not cls:
                    continue
                op = _op_of(m.group("conn"))
                out.append((key, op, v * fac, cls, f"{m.group('num').strip()} {unit.strip()}".strip(), b, s0 + m.start(),
                            " ".join(name.split()[-4:]).strip(" :—–,").lower()))
    return out


def rule_parameter_two_values(ctx: Ctx, rule):
    ms = _param_mentions(ctx)
    by: dict = {}
    for m in ms:
        by.setdefault((m[0], m[1], m[3]), []).append(m)
    findings = []
    for (key, op, cls), items in by.items():
        vals = {round(i[2], 9) for i in items}
        if len(vals) < 2:
            continue
        a = items[0]
        c = next(i for i in items if round(i[2], 9) != round(a[2], 9))
        if a[5].bid == c[5].bid and abs(a[6] - c[6]) < 5:
            continue
        nm_text = a[7]
        example = ""
        na, nc = parse_num(a[4].split()[0]) if a[4] else None, parse_num(c[4].split()[0]) if c[4] else None
        ua, uc = " ".join(a[4].split()[1:]), " ".join(c[4].split()[1:])
        if na is not None and nc is not None and ua == uc and na != nc:
            lo, hi = sorted((na, nc))
            unit = f" {ua}" if ua else ""
            if a[1] == "max":
                mid = (lo + hi) / 2
                example = (f" Пример: в {_place(section_label(a[5]), True)} {a[4]}, в {_place(section_label(c[5]), True)} {c[4]}: "
                           f"значение {fmt_num(mid)}{unit} пройдёт по большему пределу и не пройдёт по меньшему.")
            elif a[1] == "min":
                mid = (lo + hi) / 2
                example = (f" Пример: значение {fmt_num(mid)}{unit} подходит под меньший минимум ({fmt_num(lo)}{unit}) "
                           f"и не подходит под больший ({fmt_num(hi)}{unit}).")
            else:
                example = f" Пример: проверка на значении {a[4]} верна по одному месту и неверна по другому ({c[4]})."
        findings += _emit(
            ctx, rule, [ctx.q_sentence(a[5], a[6]), ctx.q_sentence(c[5], c[6])],
            title=f"«{nm_text}» названо двумя разными числами: {a[4]} и {c[4]}",
            problem=(f"Один и тот же параметр «{nm_text}» в одном месте равен {a[4]} ({section_label(a[5])}), "
                     f"в другом — {c[4]} ({section_label(c[5])}). Разработчик заложит одно число, тестировщик проверит другое." + example),
            recommendation="Оставьте значение в одном месте и дайте на него ссылку, а в остальных местах исправьте число.",
            question=ask(f"Чему равен параметр «{nm_text}»?", [a[4], c[4]], "analyst"),
            severity="MAJOR",
        )
        if len(findings) >= 3:
            break
    return findings


# --- 4. recompute explicit arithmetic -------------------------------------------------------------------------------

_UNIT_AFTER = r"(?:\s*(?:₸|тг\b|тенге|KZT|USD|EUR|RUB|₽|\$|€|%|мс|сек\w*|мин\w*|шт\b|дн\w*|тиын))?"
_OPERAND = r"(?P<n@>" + _NUM + r")" + _UNIT_AFTER
_OPS = r"(?P<o@>\+|−|×|\*|÷|/|(?<=\s)[x×хХ](?=\s)|(?<=\s)[-–—](?=\s))"
_ARITH = re.compile(
    r"(?<![\w.,/-])" + _OPERAND.replace("@", "1") + r"\s*" + _OPS.replace("@", "1") + r"\s*" + _OPERAND.replace("@", "2")
    + r"(?:\s*" + _OPS.replace("@", "2") + r"\s*" + _OPERAND.replace("@", "3") + r")?"
    + r"\s*(?:=|→|равно|получается|итого|даёт|дает)\s*(?P<res>" + _NUM + r")" + _UNIT_AFTER + r"(?![\w.,]\d)")
_PCT_OF = re.compile(
    r"(?<![\w.,])(?P<p>\d+(?:[.,]\d+)?)\s*%\s*(?:от|of)\s*(?P<s>" + _NUM + r")" + _UNIT_AFTER
    + r"\s*(?:=|—|–|→|составляет|равн\w+|это|даёт|дает|получается|is|equals)\s*(?P<res>" + _NUM + r")(?![\w.,]\d)", re.I)
_APPROX = re.compile(r"≈|~|около|примерно|приблизительно|округл|approx|rounded|roughly|порядка", re.I)


def _decimals(s: str) -> int:
    m = re.search(r"[.,](\d+)$", s.strip())
    return len(m.group(1)) if m else 0


def _consistent(calc: float, stated: str, r: float) -> bool:
    d = _decimals(stated)
    f = 10 ** d
    import math
    cands = {round(calc * f + 1e-9) / f, math.floor(calc * f + 1e-9) / f, math.ceil(calc * f - 1e-9) / f}
    return any(abs(c - r) < 0.5 / f for c in cands)


def _eval(nums: list[float], ops: list[str]) -> float | None:
    vals, o = list(nums), list(ops)
    # multiplication and division first
    k = 0
    while k < len(o):
        if o[k] in "*÷/":
            try:
                vals[k] = vals[k] * vals[k + 1] if o[k] == "*" else vals[k] / vals[k + 1]
            except ZeroDivisionError:
                return None
            del vals[k + 1], o[k]
        else:
            k += 1
    res = vals[0]
    for op, v in zip(o, vals[1:]):
        res = res + v if op == "+" else res - v
    return res


def _norm_op(s: str) -> str:
    if s in "+":
        return "+"
    if s in "−-–—":
        return "-"
    if s in "×*xхХ":
        return "*"
    return "/"


def rule_example_arithmetic(ctx: Ctx, rule):
    bad = []  # (block, pos, text, calc, stated)
    for b in ctx.doc.blocks:
        if b.kind in ("code", "heading"):
            continue
        text = visible(b.text)
        for s0, sent in _split_sentences(text) if b.kind != "table_row" else [(0, text)]:
            if b.kind == "table_row":
                parts = [(text.find(c), c) for c in b.cells if c.strip()]
            else:
                parts = [(0, sent)]
            for p0, part in parts:
                if _APPROX.search(part):
                    continue
                for m in _PCT_OF.finditer(part):
                    p, s, r = parse_num(m.group("p")), parse_num(m.group("s")), parse_num(m.group("res"))
                    if None in (p, s, r):
                        continue
                    calc = p * s / 100
                    if not _consistent(calc, m.group("res"), r):
                        bad.append((b, s0 + max(p0, 0) + m.start(), m.group(0), calc, m.group("res"), r))
                for m in _ARITH.finditer(part):
                    pieces = [m.group("n1"), m.group("n2"), m.group("n3")]
                    ops = [m.group("o1"), m.group("o2")]
                    nums = [parse_num(x) for x in pieces if x]
                    if None in nums or any(x is None for x in nums):
                        continue
                    opsn = [_norm_op(o) for o in ops if o]
                    if len(opsn) != len(nums) - 1:
                        continue
                    ctxt = part[m.start():m.end()]
                    # percent operands mean a share: «S × 5% = R»
                    nums2 = list(nums)
                    for i, x in enumerate(pieces[:len(nums)]):
                        if re.search(re.escape(x) + r"\s*%", ctxt):
                            nums2[i] = nums[i] / 100
                    calc = _eval(nums2, opsn)
                    r = parse_num(m.group("res"))
                    if calc is None or r is None:
                        continue
                    if "%" in ctxt and not _consistent(calc, m.group("res"), r):
                        # a result in percent may itself be a share: try both readings
                        if _consistent(calc * 100, m.group("res"), r):
                            continue
                    if not _consistent(calc, m.group("res"), r):
                        bad.append((b, s0 + max(p0, 0) + m.start(), m.group(0), calc, m.group("res"), r))
    # an «Итого» row
    for t in ctx.tables:
        rows = [r for r in t.rows if not r.is_empty]
        for i, r in enumerate(rows):
            if not r.cells or not re.match(r"^\W*(?:итого|всего|total|барлығы|жиын)\b", r.cells[0].strip(), re.I) or i < 2:
                continue
            for ci in range(1, len(r.cells)):
                tot = r.cells[ci]
                tm = re.fullmatch(r"\s*(" + _NUM + r")\s*(?:₸|тг|тенге|KZT|%|USD|EUR)?\s*", tot)
                if not tm:
                    continue
                prev = []
                for pr in reversed(rows[:i]):
                    if re.match(r"^\W*(?:итого|всего|total|подытог|промежуточн)", pr.cells[0].strip(), re.I) if pr.cells else True:
                        break
                    cm = re.fullmatch(r"\s*(" + _NUM + r")\s*(?:₸|тг|тенге|KZT|%|USD|EUR)?\s*", pr.cells[ci]) if ci < len(pr.cells) else None
                    if not cm:
                        prev = []
                        break
                    prev.append(parse_num(cm.group(1)))
                if len(prev) < 2 or None in prev:
                    continue
                stated = parse_num(tm.group(1))
                if stated is None:
                    continue
                calc = sum(prev)
                if not _consistent(calc, tm.group(1), stated):
                    bad.append((r, 0, f"{r.cells[0].strip()}: {tm.group(1)}", calc, tm.group(1), stated))
    if not bad:
        return []
    out = []
    for b, pos, expr, calc, stated, r in bad[:3]:
        calc_txt = (f"{calc:.2f}".rstrip("0").rstrip(".")).replace(".", ",")
        q = ctx.q_block(b) if b.kind == "table_row" else ctx.q_sentence(b, pos)
        lhs = "сумма строк выше" if b.kind == "table_row" else re.split(r"\s*(?:=|→|равно|получается|итого|даёт|дает|—|–|составляет)\s*", expr)[0].strip()
        recount = f"Пересчёт: {lhs} = {calc_txt}, в примере {stated}."
        out += _emit(
            ctx, rule, [q],
            title=f"Пример посчитан неверно: в тексте {stated}, по расчёту {calc_txt}",
            problem=(f"В примере «{short(expr, 70)}» указан результат {stated}, а пересчёт даёт {calc_txt}. "
                     "Либо ошибка в числах примера, либо в формуле, и разработчик с тестировщиком возьмут разные результаты. "
                     + recount),
            recommendation="Пересчитайте пример и поправьте числа или формулу; по этому примеру будут писать проверку.",
            question=ask(f"Какой результат верный: {stated} или {calc_txt}?", [stated, calc_txt], "analyst"),
            severity="MAJOR",
        )
    return out


# --- 5. an example that breaks the range or the format of the same parameter -----------------------------------------

_RANGE_CELL = re.compile(r"^\W*(?:от\s+)?(?P<a>" + _NUM + r")\s*(?:[–—-]|до|to)\s*(?P<b>" + _NUM + r")\W*(?:[a-zа-яё₸%]{0,12})\W*$", re.I)
_ENUM_CELL = re.compile(r"^\W*(?P<l>[\w.-]{1,20}(?:\s*(?:/|,|\||или|or)\s*[\w.-]{1,20})+)\W*$", re.I)
_DATE_MASKS = [
    (re.compile(r"YYYY-MM-DD(?:T|\s)HH:mm(?::ss)?(?:\S*)", re.I), r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
    (re.compile(r"YYYY-MM-DD|ГГГГ-ММ-ДД", re.I), r"\d{4}-\d{2}-\d{2}"),
    (re.compile(r"DD\.MM\.YYYY|ДД\.ММ\.ГГГГ", re.I), r"\d{2}\.\d{2}\.\d{4}"),
    (re.compile(r"DD/MM/YYYY|ДД/ММ/ГГГГ|MM/DD/YYYY", re.I), r"\d{2}/\d{2}/\d{4}"),
]
_ANY_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\S*)?$|^\d{2}\.\d{2}\.\d{4}$|^\d{2}/\d{2}/\d{4}$")


def rule_example_out_of_range(ctx: Ctx, rule):
    tables = field_tables(ctx)
    if not tables:
        return []
    exs = json_examples(ctx)
    curl = _curl_names(ctx)
    values = []  # (bucket, name_lower, value, block, needle)
    for b, d, items in exs:
        for key, depth, val in items:
            if isinstance(val, (str, int, float)) and not isinstance(val, bool):
                values.append((d, key.lower(), val, b, f"\"{key}\""))
    for b, k, v in curl:
        values.append(("req", k.lower(), v, b, k + "="))
    # constraints per (kind of message, parameter name): every row of every table that names the parameter
    cand: dict = {}
    for t, d, names in tables:
        nc = column(t.header, NAME_COL)
        for lk, (row, typ, mand, nm, nested) in names.items():
            checks = []
            for ci, cell in enumerate(row.cells):
                h = t.header[ci] if ci < len(t.header) else ""
                if ci == nc or not cell.strip() or TYPE_COL.match(h.strip()):
                    continue
                if re.match(r"(?:size|размер)", h.strip(), re.I):
                    rm = _RANGE_CELL.match(cell)
                    if typ == "string" and rm:
                        checks.append(("len", parse_num(rm.group("a")), parse_num(rm.group("b"))))
                    continue
                if typ in ("integer", "number"):
                    rm = _RANGE_CELL.match(cell)
                    if rm:
                        checks.append(("range", parse_num(rm.group("a")), parse_num(rm.group("b"))))
                        continue
                em = _ENUM_CELL.match(cell)
                if em and not re.search(r"да\s*/\s*нет", cell, re.I) and _norm_type(cell) not in ("string", "integer", "number", "boolean", "object", "array", "date", "datetime"):
                    items = [x.strip() for x in re.split(r"\s*(?:/|,|\||\bили\b|\bor\b)\s*", em.group("l")) if x.strip()]
                    if 2 <= len(items) <= 12 and all(len(x) <= 20 for x in items):
                        checks.append(("enum", items, None))
                        continue
                for rx, pat in _DATE_MASKS:
                    mm = rx.search(cell)
                    if mm:
                        checks.append(("date", pat, mm.group(0)))
                        break
            cand.setdefault((d, lk), []).append((row, nm, checks))
    bad = []
    for vd, vname, val, vb, needle in values:
        rows = cand.get((vd, vname))
        if not rows:
            continue
        sval = str(val)
        first_msg = None
        accepted = False
        for row, nm, checks in rows:
            row_msg = None
            for kind, a, b3 in checks:
                msg = ""
                if kind == "range" and a is not None and b3 is not None:
                    n = parse_num(sval) if isinstance(val, str) else float(val)
                    if n is not None and not (a <= n <= b3):
                        msg = f"значение {sval} вне допустимого диапазона {fmt_num(a)}–{fmt_num(b3)}"
                elif kind == "len" and a is not None and b3 is not None:
                    if isinstance(val, str) and not (a <= len(val) <= b3):
                        msg = f"длина значения «{val}» вне диапазона {fmt_num(a)}–{fmt_num(b3)}"
                elif kind == "enum":
                    if sval not in a and sval.lower() not in [x.lower() for x in a]:
                        # a value that the document names elsewhere belongs to some other list
                        if len(re.findall(r"(?<![\w])" + re.escape(sval) + r"(?![\w])", ctx.doc.text)) > 1:
                            continue
                        msg = f"значения {sval} нет среди допустимых: {', '.join(a)}"
                elif kind == "date":
                    if isinstance(val, str) and _ANY_DATE.match(val.strip()) and not re.fullmatch(a, val.strip()):
                        msg = f"дата {val} записана не в формате {b3}"
                if msg:
                    row_msg = row_msg or (row, nm, msg)
            if row_msg is None:
                accepted = True
                break
            first_msg = first_msg or row_msg
        if not accepted and first_msg:
            bad.append((vb, needle, first_msg[0], first_msg[1], first_msg[2]))
    if not bad:
        return []
    quotes, lines = [], []
    seen = set()
    for vb, needle, row, nm, msg in bad:
        if (nm, msg) in seen:
            continue
        seen.add((nm, msg))
        lines.append(f"«{nm}»: {msg}")
        quotes += [_code_line_quote(ctx, vb, needle) if vb.kind == "code" else ctx.q_sentence(vb, max(vb.text.find(needle), 0)), ctx.q_block(row)]
    return _emit(
        ctx, rule, quotes,
        title="Пример не подходит под описание параметра: " + fmt_list([l.split(":")[0] for l in lines], 3),
        problem=("Значение в примере нарушает то, что сказано про этот же параметр в таблице: " + "; ".join(lines[:3])
                 + ". Пример может оказаться единственным, что прочтёт разработчик, и он повторит ошибку."),
        recommendation="Исправьте пример так, чтобы он укладывался в диапазон и формат, либо поправьте описание параметра.",
        question=ask("Что верно: пример или описание параметра?", ["Описание, исправить пример", "Пример, исправить описание"], "analyst"),
    )


def fmt_num(x: float) -> str:
    return f"{int(x):,}".replace(",", " ") if x == int(x) else str(x).replace(".", ",")


# --- 6. requirements nobody covers with a criterion -----------------------------------------------------------------------

_CRIT_SEC = re.compile(r"при[её]мк|acceptance|критери|тестир|test|проверк", re.I)
_TRACE_COL = re.compile(r"требован|requirement|req\b|покрыва|covers|ссылк|трасс|trace|источник|основан", re.I)


def rule_requirement_without_criterion(ctx: Ctx, rule):
    doc = ctx.doc
    crit_blocks = [b for b in doc.blocks if b.kind != "heading" and (_CRIT_SEC.search(b.section or "") or
                   (b.kind == "table_row" and any(_TRACE_COL.search(h) for h in b.header) and any(re.search(r"критер|expected|ожидаем|test|тест|действи", h, re.I) for h in b.header)))]
    if not crit_blocks:
        return []
    crit_ids = {b.bid for b in crit_blocks}
    req = []
    for b in doc.blocks:
        if b.bid in crit_ids or not b.doc_id or b.kind == "heading" or b.bid not in ctx.rid_of:
            continue
        req.append(b)
    ids = {b.doc_id.rstrip(".") for b in req}
    if len(ids) < 4:
        return []
    prefixes = {re.match(r"^[^\d]+", i).group(0) for i in ids if re.match(r"^[^\d]+", i)}
    if not prefixes:
        return []
    ref_rx = re.compile(r"(?<![\w-])(" + "|".join(re.escape(p) for p in sorted(prefixes, key=len, reverse=True)) + r")(\d+(?:\.\d+)*)(?:\s*[–-]\s*(?:\1)?(\d+))?")
    referenced: set = set()
    for b in crit_blocks:
        for m in ref_rx.finditer(visible(b.text)):
            pre, n1, n2 = m.group(1), m.group(2), m.group(3)
            referenced.add(pre + n1)
            if n2 and n1.isdigit() and n2.isdigit() and int(n2) > int(n1) and int(n2) - int(n1) < 60:
                for k in range(int(n1), int(n2) + 1):
                    referenced.add(pre + str(k).zfill(len(n1)))
    live = {i for i in ids if i in referenced or re.sub(r"[а-яa-z]$", "", i) in referenced}
    if len(live) < 3 or len(live) / len(ids) < 0.3:
        return []  # the criteria do not link to requirement numbers: nothing to compare
    missing = [b for b in req if b.doc_id.rstrip(".") not in live and re.sub(r"[а-яa-z]$", "", b.doc_id.rstrip(".")) not in live]
    if not missing:
        return []
    names = [b.doc_id.rstrip(".") for b in missing]
    return _emit(
        ctx, rule, [ctx.q_block(b) for b in missing[:MAX_QUOTES]],
        title="Требования без критерия приёмки: " + fmt_list(names, 5),
        problem=(f"Критерии приёмки в документе ссылаются на номера требований ({len(live)} из {len(ids)}), но на эти требования "
                 f"ссылок нет: {fmt_list(names, 8)}. Для них нечем подтвердить, что они выполнены."),
        recommendation="Добавьте для каждого требования проверяемый критерий со ссылкой на его номер или уберите требование, которое проверять не нужно.",
        question=ask(f"Какой критерий приёмки у требования {names[0]}?", ["Добавить критерий", "Проверка не нужна"], "qa"),
    )


CROSSCHECK_RULES = {
    "set-members-differ": rule_set_members_differ,
    "example-field-mismatch": rule_example_field_mismatch,
    "parameter-two-values": rule_parameter_two_values,
    "example-arithmetic": rule_example_arithmetic,
    "example-out-of-range": rule_example_out_of_range,
    "requirement-without-criterion": rule_requirement_without_criterion,
}
