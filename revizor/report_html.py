"""Markdown report -> one self-contained HTML file (no scripts, no network, no external fonts).

    python -m revizor.report_html INPUT.md [-o OUTPUT.html]

The converter knows the subset of Markdown the report uses: headings, paragraphs, bold / italic / code,
fenced code, links, raw ``<a id>`` anchors, blockquotes, nested lists, pipe tables with alignment, rules,
``<details>`` / ``<summary>``. Everything else degrades to readable text and never raises.

Safety: the report quotes the reviewed document, which is untrusted. All HTML in the source is escaped,
except a short whitelist the report writer itself emits (``<a id="…"></a>``, ``<details>``, ``<summary>``,
``<br>``, ``<sub>``, ``<sup>``). Links keep only the schemes ``#``, ``http:``, ``https:``, ``mailto:``.
The output is deterministic: the same input gives the same bytes.
"""
from __future__ import annotations

import argparse
import html as _html
import re
import sys
from pathlib import Path

_OPEN, _CLOSE = "\ue000", "\ue001"      # private-use brackets around a stash index
_STASH_RE = re.compile(_OPEN + r"(\d+)" + _CLOSE)

_ID = r"[\w.:\-]{1,80}"
_ANCHOR_INLINE = re.compile(r'<a id="(' + _ID + r')"></a>')
_ANCHOR_LINE = re.compile(r'^\s*(?:<a id="' + _ID + r'"></a>\s*)+$')
_BR = re.compile(r"<br\s*/?>", re.I)
_SUBSUP = re.compile(r"<(sub|sup)>(.+?)</\1>", re.S)
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*([^\s`]*)[^`]*$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_HEADING_EMPTY = re.compile(r"^ {0,3}(#{1,6})[ \t]*$")
_HR = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_LIST = re.compile(r"^( *)([-*+]|\d{1,9}[.)])(?:[ \t]+(.*)|[ \t]*)$")
_QUOTE = re.compile(r"^ {0,3}>[ ]?(.*)$")
_TABLE_SEP = re.compile(r"^ *\|? *:?-+:? *(?:\| *:?-+:? *)*\|? *$")
_DETAILS = re.compile(r"^\s*<(/?)details>\s*$", re.I)
_SUMMARY = re.compile(r"^\s*<summary>(.*)</summary>\s*$", re.I)
_SEV = re.compile(r"(?<![\w\-])(CRITICAL|MAJOR|MINOR)(?![\w\-])")
_CARD = re.compile(r"^\*\*([A-Za-zА-Яа-яЁё]{1,4}-\d+)(?:[ ]·|\.\*\*|\*\*)")
_BOLD = re.compile(r"(\*\*|__)(?=[^\s*_])(.+?)(?<=[^\s*_])\1")
_ITAL_STAR = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])")
_ITAL_UND = re.compile(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])")
_STRIKE = re.compile(r"~~(?=[^\s~])(.+?)(?<=[^\s~])~~")
_LINK = re.compile(r'\[([^\[\]]*)\]\(\s*<?([^()\s<>]*)>?(?:\s+"[^"]*")?\s*\)')
_URL_OK = re.compile(r"^(#|https?:|mailto:)", re.I)


# ---------------------------------------------------------------- inline

class _Inline:
    """Converts one chunk of Markdown text to HTML; ``stash`` keeps finished tags out of the way of later regexes."""

    def __init__(self) -> None:
        self.parts: list[str] = []

    def stash(self, s: str) -> str:
        self.parts.append(s)
        return f"{_OPEN}{len(self.parts) - 1}{_CLOSE}"

    def restore(self, s: str) -> str:
        for _ in range(12):                                   # stashed pieces may hold other stashes
            if _OPEN not in s:
                break
            s = _STASH_RE.sub(lambda m: self.parts[int(m.group(1))], s)
        return s.replace(_OPEN, "").replace(_CLOSE, "")

    def render(self, text: str) -> str:
        s = (text or "").replace(_OPEN, "\ufffd").replace(_CLOSE, "\ufffd")
        # 1. backslash escapes -> literal characters
        s = re.sub(r"\\([!-/:-@\[-`{-~])", lambda m: self.stash(_html.escape(m.group(1))), s)
        # 2. code spans
        s = re.sub(r"(`+)(.+?)(?<!`)\1(?!`)",
                   lambda m: self.stash("<code>" + _html.escape(m.group(2).strip()) + "</code>"), s)
        # 3. the short tag whitelist
        s = _ANCHOR_INLINE.sub(lambda m: self.stash(f'<a id="{m.group(1)}"></a>'), s)
        s = _BR.sub(lambda m: self.stash("<br>"), s)
        s = _SUBSUP.sub(lambda m: self.stash(f"<{m.group(1)}>") + m.group(2) + self.stash(f"</{m.group(1)}>"), s)
        # 4. everything else is text
        s = _html.escape(s, quote=False)
        # 5. links
        s = _LINK.sub(self._link, s)
        # 6. emphasis
        s = _BOLD.sub(lambda m: self.stash("<strong>") + m.group(2) + self.stash("</strong>"), s)
        s = _ITAL_STAR.sub(lambda m: self.stash("<em>") + m.group(1) + self.stash("</em>"), s)
        s = _ITAL_UND.sub(lambda m: self.stash("<em>") + m.group(1) + self.stash("</em>"), s)
        s = _STRIKE.sub(lambda m: self.stash("<del>") + m.group(1) + self.stash("</del>"), s)
        # 7. severity labels (text itself stays)
        s = _SEV.sub(lambda m: self.stash(f'<span class="sev sev-{m.group(1).lower()}">{m.group(1)}</span>'), s)
        return self.restore(s)

    def _link(self, m: re.Match) -> str:
        label, url = m.group(1), _html.unescape(m.group(2)).strip()
        if _URL_OK.match(url) and not re.search(r"[\x00-\x1f\x7f]", url):
            ext = not url.startswith("#")
            attrs = ' rel="noopener noreferrer"' if ext else ""
            href = _html.escape(url, quote=True)
            return self.stash(f'<a href="{href}"{attrs}>') + label + self.stash("</a>")
        return m.group(0)                                     # unsafe or relative target: plain text


def inline(text: str) -> str:
    return _Inline().render(text)


def _plain(html_s: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", "", html_s)).strip()


# ---------------------------------------------------------------- blocks

def _split_row(line: str) -> list[str]:
    s = line.strip()
    cells, cur, i = [], [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            cur.append(s[i:i + 2])
            i += 2
            continue
        if ch == "|":
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur))
    if s.startswith("|"):
        cells = cells[1:]
    if s.endswith("|") and not s.endswith("\\|") and cells:
        cells = cells[:-1]
    return [c.strip() for c in cells]


def _aligns(sep: str) -> list[str]:
    out = []
    for c in _split_row(sep):
        left, right = c.startswith(":"), c.endswith(":")
        out.append("center" if left and right else "right" if right else "left" if left else "")
    return out


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _starts_block(line: str) -> bool:
    """True when the line cannot be a lazy continuation of a paragraph / list item."""
    return bool(_FENCE.match(line) or _HEADING.match(line) or _HEADING_EMPTY.match(line) or _HR.match(line)
                or _QUOTE.match(line) or _ANCHOR_LINE.match(line) or _DETAILS.match(line)
                or _SUMMARY.match(line) or line.lstrip().startswith("|"))


class _Block:
    __slots__ = ("kind", "html", "level", "ids", "text", "sev")

    def __init__(self, kind: str, html: str = "", level: int = 0, ids: tuple = (), text: str = "", sev: str = ""):
        self.kind, self.html, self.level, self.ids, self.text, self.sev = kind, html, level, ids, text, sev


class _Doc:
    def __init__(self, source: str) -> None:
        self.reserved = set(_ANCHOR_INLINE.findall(source))
        self.used: set[str] = set()
        self.toc: list[tuple[str, str]] = []
        self.quote_seen = False
        self.h1: str | None = None
        self.details_open = 0

    # -- parsing ---------------------------------------------------
    def parse(self, lines: list[str], tight: bool = False, top: bool = False) -> list[_Block]:
        out: list[_Block] = []
        i, n = 0, len(lines)
        while i < n:
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            m = _FENCE.match(line)
            if m:
                fence, i = m.group(1), i + 1
                body = []
                while i < n and not (lines[i].strip().startswith(fence[0] * len(fence))
                                     and not lines[i].strip().strip(fence[0])):
                    body.append(lines[i])
                    i += 1
                i += 1
                out.append(_Block("code", "<pre><code>" + _html.escape("\n".join(body)) + "</code></pre>"))
                continue
            if _ANCHOR_LINE.match(line):
                out.append(_Block("anchor", ids=tuple(_ANCHOR_INLINE.findall(line))))
                i += 1
                continue
            m = _DETAILS.match(line)
            if m:
                if m.group(1):
                    if self.details_open > 0 and not tight:
                        self.details_open -= 1
                        out.append(_Block("raw", "</details>"))
                else:
                    self.details_open += 1
                    out.append(_Block("raw", "<details>"))
                i += 1
                continue
            m = _SUMMARY.match(line)
            if m:
                out.append(_Block("raw", "<summary>" + inline(m.group(1)) + "</summary>"))
                i += 1
                continue
            m = _HEADING.match(line) or _HEADING_EMPTY.match(line)
            if m:
                lvl, txt = len(m.group(1)), (m.group(2) if m.re is _HEADING else "")
                out.append(_Block("heading", inline(txt), level=lvl))
                i += 1
                continue
            if _HR.match(line):
                out.append(_Block("hr", "<hr>"))
                i += 1
                continue
            if line.lstrip().startswith("|") and i + 1 < n and _TABLE_SEP.match(lines[i + 1]) and "-" in lines[i + 1]:
                blk, i = self._table(lines, i)
                out.append(blk)
                continue
            if _QUOTE.match(line):
                blk, i = self._quote(lines, i, top)
                out.append(blk)
                continue
            m = _LIST.match(line)
            if m and _indent(line) < 4 + (0 if top else 8):
                blk, i = self._list(lines, i)
                out.append(blk)
                continue
            # paragraph
            buf = [line]
            i += 1
            while i < n and lines[i].strip() and not _starts_block(lines[i]) \
                    and not (_LIST.match(lines[i]) and _indent(lines[i]) < 4):
                buf.append(lines[i])
                i += 1
            out.append(self._paragraph(buf, tight))
        return out

    def _paragraph(self, buf: list[str], tight: bool) -> _Block:
        parts = []
        for k, ln in enumerate(buf):
            hard = ln.endswith("  ") or ln.endswith("\\")
            ln = ln.strip()
            if ln.endswith("\\") and not ln.endswith("\\\\"):
                ln = ln[:-1]
            parts.append(inline(ln))
        body = "<br>\n".join(parts)
        first = buf[0].strip()
        card = _CARD.match(first)
        sev = ""
        if card:
            sm = _SEV.search(first)
            sev = sm.group(1).lower() if sm else ""
        html = body if tight else f"<p>{body}</p>"
        return _Block("p", html, text=first if card else "", sev=sev)

    def _table(self, lines: list[str], i: int) -> tuple[_Block, int]:
        head = _split_row(lines[i])
        al = _aligns(lines[i + 1])
        i += 2
        rows = []
        while i < len(lines) and lines[i].strip() and "|" in lines[i] and not _starts_block_non_table(lines[i]):
            rows.append(_split_row(lines[i]))
            i += 1
        ncol = max([len(head), len(al)] + [len(r) for r in rows])
        head += [""] * (ncol - len(head))
        al += [""] * (ncol - len(al))

        def cell(tag: str, txt: str, a: str) -> str:
            cls = f' class="a-{a}"' if a else ""
            return f"<{tag}{cls}>{inline(txt)}</{tag}>"

        h = "<tr>" + "".join(cell("th", c, al[k]) for k, c in enumerate(head)) + "</tr>"
        b = "\n".join("<tr>" + "".join(cell("td", c, al[k]) for k, c in enumerate(r + [""] * (ncol - len(r))))
                      + "</tr>" for r in rows)
        html = (f'<div class="tablewrap"><table><thead>{h}</thead>'
                f'<tbody>\n{b}\n</tbody></table></div>')
        return _Block("table", html), i

    def _quote(self, lines: list[str], i: int, top: bool) -> tuple[_Block, int]:
        buf = []
        while i < len(lines):
            m = _QUOTE.match(lines[i])
            if m:
                buf.append(m.group(1))
            elif lines[i].strip() and buf and buf[-1].strip() and not _starts_block(lines[i]) \
                    and not _LIST.match(lines[i]):
                buf.append(lines[i].strip())                   # lazy continuation
            else:
                break
            i += 1
        inner = self.parse(buf)
        html = self.assemble(inner, top=False)
        cls = ""
        if top and not self.quote_seen:
            cls = ' class="verdict"'
        if top:
            self.quote_seen = True
        return _Block("quote", f"<blockquote{cls}>\n{html}\n</blockquote>"), i

    def _list(self, lines: list[str], i: int) -> tuple[_Block, int]:
        first = _LIST.match(lines[i])
        base = len(first.group(1))
        ordered = first.group(2)[0].isdigit()
        start = int(first.group(2)[:-1]) if ordered else 1
        items: list[list[str]] = []
        loose = False
        n = len(lines)
        while i < n:
            line = lines[i]
            m = _LIST.match(line)
            if m and len(m.group(1)) <= base + 1:
                if m.group(2)[0].isdigit() != ordered and items:
                    break                                      # another kind of list starts here
                off = len(m.group(1)) + len(m.group(2)) + 1
                items.append([(m.group(3) or "")])
                items[-1].append(off)                          # type: ignore[arg-type]
                i += 1
                continue
            if not items:
                break
            if not line.strip():
                j = i
                while j < n and not lines[j].strip():
                    j += 1
                if j >= n:
                    i = j
                    break
                nm = _LIST.match(lines[j])
                if nm and len(nm.group(1)) <= base + 1 and nm.group(2)[0].isdigit() == ordered:
                    loose = True
                elif _indent(lines[j]) <= base + 1:
                    break
                items[-1].extend([""] * (j - i))               # type: ignore[arg-type]
                i = j
                continue
            if _indent(line) > base + 1:
                off = items[-1][1]                              # type: ignore[index]
                items[-1].append(line[min(_indent(line), off):])
                i += 1
                continue
            if not _starts_block(line):                         # lazy continuation
                items[-1].append(line.strip())
                i += 1
                continue
            break
        lis = []
        for it in items:
            body = [it[0]] + [x for x in it[2:] if isinstance(x, str)]
            blocks = self.parse(body, tight=not loose)
            lis.append("<li>" + self.assemble(blocks, top=False, joiner="") + "</li>")
        tag = "ol" if ordered else "ul"
        st = f' start="{start}"' if ordered and start != 1 else ""
        return _Block("list", f"<{tag}{st}>\n" + "\n".join(lis) + f"\n</{tag}>"), i

    # -- assembling ------------------------------------------------
    def _claim(self, ids) -> list[str]:
        out = []
        for x in ids:
            if x not in self.used:
                self.used.add(x)
                out.append(x)
        return out

    def _gen_id(self) -> str:
        k = len(self.toc) + 1
        while f"sec-{k}" in self.reserved or f"sec-{k}" in self.used:
            k += 1
        return f"sec-{k}"

    def assemble(self, blocks: list[_Block], top: bool, joiner: str = "\n") -> str:
        out: list[str] = []
        pending: list[str] = []
        k, n = 0, len(blocks)

        def anchors() -> str:
            ids = self._claim(pending)
            pending.clear()
            return "".join(f'<a id="{x}"></a>' for x in ids)

        while k < n:
            b = blocks[k]
            if b.kind == "anchor":
                pending.extend(b.ids)
                k += 1
                continue
            if b.kind == "heading":
                ids = self._claim(pending)
                pending.clear()
                hid = ids[0] if ids else None
                extra = "".join(f'<a id="{x}"></a>' for x in ids[1:])
                if top and b.level == 2:
                    if hid is None:
                        hid = self._gen_id()
                        self.used.add(hid)
                    self.toc.append((hid, _plain(b.html)))
                if top and b.level == 1 and self.h1 is None:
                    self.h1 = _plain(b.html)
                idattr = f' id="{hid}"' if hid else ""
                out.append(f"<h{b.level}{idattr}>{extra}{b.html}</h{b.level}>")
                k += 1
                continue
            if b.kind == "p" and b.text:                        # a finding / question card
                head = anchors()
                sev = f" card-{b.sev}" if b.sev else ""
                members = [b.html]
                k += 1
                while k < n and blocks[k].kind in ("p", "list") and not blocks[k].text:
                    members.append(blocks[k].html)
                    k += 1
                out.append(f'<div class="card{sev}">{head}\n' + "\n".join(members) + "\n</div>")
                continue
            out.append(anchors() + b.html)
            k += 1
        if pending:
            out.append(anchors())
        return joiner.join(out)


def _starts_block_non_table(line: str) -> bool:
    return bool(_FENCE.match(line) or _HEADING.match(line) or _QUOTE.match(line) or _HR.match(line))


# ---------------------------------------------------------------- page

_CSS = """
:root{--bg:#f6f7f9;--paper:#fff;--ink:#1f2933;--muted:#5f6b7a;--line:#d9dee5;--zebra:#f4f6f9;--link:#0b5cad;
--accent:#0f6b4f;--accent-soft:#e8f3ee;--code:#eef1f5;--crit:#b42318;--crit-bg:#fdecea;--maj:#9a5b00;--maj-bg:#fff2d6;
--min:#146c3a;--min-bg:#e3f5ea;--shadow:0 1px 2px rgba(16,24,40,.06),0 4px 14px rgba(16,24,40,.05)}
@media (prefers-color-scheme:dark){:root{--bg:#10151b;--paper:#171d25;--ink:#e6eaef;--muted:#9aa6b4;--line:#2d3744;
--zebra:#1b222b;--link:#7db4ff;--accent:#5fd0a2;--accent-soft:#16302a;--code:#222b36;--crit:#ff8a80;--crit-bg:#3b1d1b;
--maj:#ffc266;--maj-bg:#3a2d12;--min:#7ee2a6;--min-bg:#16301f;--shadow:none}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.65 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
.doc{max-width:920px;margin:0 auto;padding:28px 20px 56px}
.sheet{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:30px 34px 38px;box-shadow:var(--shadow)}
h1,h2,h3,h4,h5,h6{line-height:1.3;margin:1.7em 0 .55em;font-weight:650;color:var(--ink)}
h1{font-size:1.75rem;margin-top:0;letter-spacing:-.01em}
h2{font-size:1.38rem;padding-bottom:.3em;border-bottom:2px solid var(--line);margin-top:2.2em}
h3{font-size:1.12rem}h4{font-size:1rem}h5,h6{font-size:.95rem;color:var(--muted)}
h2,h3,h4,[id]{scroll-margin-top:64px}
p{margin:.65em 0}
a{color:var(--link);text-decoration:none;border-bottom:1px solid color-mix(in srgb,var(--link) 35%,transparent)}
a:hover{border-bottom-color:var(--link)}
a[id]:empty{border:0}
code{font:.88em ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--code);padding:.1em .38em;border-radius:4px;overflow-wrap:break-word}
pre{background:var(--code);padding:12px 14px;border-radius:8px;overflow:auto;line-height:1.45}
pre code{background:none;padding:0}
hr{border:0;border-top:1px solid var(--line);margin:1.8em 0}
ul,ol{margin:.5em 0;padding-left:1.45em}
li{margin:.22em 0}li>ul,li>ol{margin:.2em 0}
blockquote{margin:.9em 0;padding:.5em 1em;border-left:4px solid var(--line);background:var(--zebra);color:var(--ink);border-radius:0 8px 8px 0}
blockquote p{margin:.35em 0}
blockquote.verdict{border:1px solid var(--accent);border-left:6px solid var(--accent);background:var(--accent-soft);
padding:1em 1.3em;margin:1.1em 0 1.4em;font-size:1.04rem;border-radius:10px}
blockquote.verdict-blocked{border-color:var(--crit);background:var(--crit-bg)}
blockquote.verdict-conditional{border-color:var(--maj);background:var(--maj-bg)}
.tablewrap{overflow-x:auto;margin:1em 0;border:1px solid var(--line);border-radius:8px;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:.9rem;line-height:1.45}
th,td{border:1px solid var(--line);padding:6px 10px;vertical-align:top;text-align:left;overflow-wrap:break-word}
.tablewrap th:first-child,.tablewrap td:first-child{border-left:0}.tablewrap th:last-child,.tablewrap td:last-child{border-right:0}
thead th{background:var(--zebra);font-weight:650;border-top:0}
tbody tr:last-child td{border-bottom:0}
tbody tr:nth-child(even){background:var(--zebra)}
.a-right{text-align:right;font-variant-numeric:tabular-nums}.a-center{text-align:center}
.sev{display:inline-block;font:700 .72em/1.5 ui-monospace,Menlo,Consolas,monospace;letter-spacing:.03em;padding:0 .55em;border-radius:99px;
vertical-align:.08em;border:1px solid transparent;white-space:nowrap}
.sev-critical{color:var(--crit);background:var(--crit-bg);border-color:color-mix(in srgb,var(--crit) 35%,transparent)}
.sev-major{color:var(--maj);background:var(--maj-bg);border-color:color-mix(in srgb,var(--maj) 40%,transparent)}
.sev-minor{color:var(--min);background:var(--min-bg);border-color:color-mix(in srgb,var(--min) 35%,transparent)}
.card{margin:1.1em 0;padding:.7em 1.1em .8em;border:1px solid var(--line);border-left:5px solid var(--line);border-radius:8px;background:var(--paper)}
.card>p:first-of-type,.card>strong:first-child{margin-top:.2em}
.card-critical{border-left-color:var(--crit)}.card-major{border-left-color:var(--maj)}.card-minor{border-left-color:var(--min)}
details{margin:.8em 0;padding:.4em .9em;border:1px solid var(--line);border-radius:8px}
details>summary{cursor:pointer;font-weight:600}
nav.toc{position:sticky;top:0;z-index:20;background:var(--paper);border-bottom:1px solid var(--line);box-shadow:var(--shadow)}
nav.toc>details{max-width:920px;margin:0 auto;border:0;border-radius:0;padding:0 20px;position:relative}
nav.toc summary{padding:10px 0;color:var(--muted);font-size:.92rem;list-style:none}
nav.toc summary::-webkit-details-marker{display:none}
nav.toc summary::before{content:"\\2630";margin-right:.6em;color:var(--accent)}
nav.toc ol{position:absolute;left:20px;right:20px;top:100%;margin:0;padding:10px 14px 12px 36px;list-style:decimal;
background:var(--paper);border:1px solid var(--line);border-top:0;border-radius:0 0 10px 10px;box-shadow:var(--shadow);
max-height:70vh;overflow:auto;font-size:.93rem}
nav.toc li{margin:.18em 0}
footer{max-width:920px;margin:18px auto 0;padding:0 20px;color:var(--muted);font-size:.82rem;text-align:center}
@media (max-width:640px){body{font-size:15px}.doc{padding:14px 10px 40px}.sheet{padding:18px 14px 26px;border-radius:8px}
h1{font-size:1.4rem}h2{font-size:1.2rem}nav.toc>details{padding:0 10px}nav.toc ol{left:10px;right:10px}}
@page{size:A4;margin:14mm}
@media print{
html,body{background:#fff!important;color:#000!important;font-size:10.5pt;line-height:1.5}
.doc{max-width:none;padding:0}.sheet{border:0;box-shadow:none;padding:0;border-radius:0;background:#fff}
nav.toc,footer{display:none!important}
a{color:#000;border:0;text-decoration:none}
a[href^="http"]::after,a[href^="mailto"]::after{content:" (" attr(href) ")";font-size:.85em;word-break:break-all}
.tablewrap{overflow:visible;border:0}
table{font-size:8.6pt}thead{display:table-header-group}
tr,.card,blockquote,pre,.sev{break-inside:avoid;page-break-inside:avoid}
tbody tr:nth-child(even),thead th,blockquote,code,pre,.card,details{background:none!important}
blockquote,blockquote.verdict{border:1px solid #888;border-left:4px solid #444;border-radius:0}
.card{border:1px solid #bbb;border-left:4px solid #666;border-radius:0}
.sev{border-color:currentColor;background:none!important}
.sev-critical{color:#a00}.sev-major{color:#8a5200}.sev-minor{color:#0a5a2e}
h1,h2,h3,h4{break-after:avoid;page-break-after:avoid}
h2{border-bottom:1px solid #888;margin-top:1.6em}
[id]{scroll-margin-top:0}
}
"""


def render_html(markdown_text: str, title: str | None = None, theme: str | None = None) -> str:
    """Return a complete, self-contained HTML page for the Markdown report.

    ``theme="light"`` pins the light colours (used for screenshots); by default the page follows the system."""
    try:
        text = markdown_text if isinstance(markdown_text, str) else ("" if markdown_text is None else str(markdown_text))
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "").expandtabs(4)
        doc = _Doc(text)
        blocks = doc.parse(text.split("\n"), top=True)
        body = doc.assemble(blocks, top=True)
        while doc.details_open > 0:
            body += "\n</details>"
            doc.details_open -= 1
    except Exception as e:                                      # never raise: degrade to preformatted text
        body = "<pre>" + _html.escape(str(markdown_text)) + "</pre>"
        doc = _Doc("")
        doc.h1 = None
    css = _CSS if theme != "light" else _CSS.replace("@media (prefers-color-scheme:dark){", "@media not all{")
    scheme = "light dark" if theme != "light" else "light"
    page_title = title or doc.h1 or "Отчёт Ревизора ТЗ"
    toc = ""
    if len(doc.toc) >= 2:
        items = "\n".join(f'<li><a href="#{i}">{_html.escape(t)}</a></li>' for i, t in doc.toc)
        toc = (f'<nav class="toc" aria-label="Содержание"><details><summary>Содержание</summary>'
               f'<ol>\n{items}\n</ol></details></nav>\n')
    return (
        '<!DOCTYPE html>\n<html lang="ru">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:\">\n"
        '<meta name="color-scheme" content="' + scheme + '">\n'
        f"<title>{_html.escape(page_title)}</title>\n<style>{css}</style>\n</head>\n<body>\n{toc}"
        f'<main class="doc"><article class="sheet">\n{body}\n</article></main>\n'
        "<footer>Отчёт сформирован программой «Ревизор ТЗ». Решение остаётся за человеком.</footer>\n"
        "</body>\n</html>\n"
    )


# ---------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m revizor.report_html",
                                 description="Превращает отчёт Markdown в один самодостаточный HTML-файл.")
    ap.add_argument("input", help="файл отчёта .md")
    ap.add_argument("-o", "--output", help="куда записать (по умолчанию рядом, с расширением .html)")
    a = ap.parse_args(argv)
    src = Path(a.input)
    try:
        raw = src.read_bytes()
    except OSError:
        print(f"Не удалось открыть файл «{src}»: проверьте путь.", file=sys.stderr)
        return 2
    try:
        if b"\x00" in raw:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "binary")
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        print(f"Файл «{src}» не похож на текстовый отчёт (ожидается Markdown в кодировке UTF-8).", file=sys.stderr)
        return 2
    out = Path(a.output) if a.output else src.with_suffix(".html")
    try:
        out.write_text(render_html(text), encoding="utf-8", newline="\n")
    except OSError as e:
        print(f"Не удалось записать «{out}»: {e.strerror or e}.", file=sys.stderr)
        return 2
    print(f"Готово: {out}")
    return 0


if __name__ == "__main__":
    from .console import prepare_console

    prepare_console()
    sys.exit(main())
