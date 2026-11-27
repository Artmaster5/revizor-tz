"""revizor.report_html: Markdown report -> one self-contained, inert HTML page."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from revizor.report_html import render_html

# the child prints Russian: read it as UTF-8 on every system (Windows would decode with cp1252 otherwise)
UTF8 = {"encoding": "utf-8", "errors": "replace", "env": {**os.environ, "PYTHONIOENCODING": "utf-8"}}

ROOT = Path(__file__).resolve().parent.parent
VOID = {"meta", "br", "hr", "img", "link", "input"}


class Checker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.errors, self.ids, self.hrefs, self.tags, self.attrs = [], [], [], [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        d = dict(attrs)
        self.attrs += list(d)
        if "id" in d:
            self.ids.append(d["id"])
        if "href" in d:
            self.hrefs.append(d["href"])
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unbalanced </{tag}> at {self.stack[-3:]}")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


def check(html_text: str) -> Checker:
    c = Checker()
    c.feed(html_text)
    c.close()
    return c


def body(md: str) -> str:
    h = render_html(md)
    return h[h.index("<article"):h.index("</article>")]


class Constructs(unittest.TestCase):
    def test_headings_and_inline(self):
        b = body("# T\n\n## Раздел\n\n### Под\n\n#### Ещё\n\nТекст **жирный**, *курсив*, `код` и \\$5.")
        for tag in ("<h1>", "<h3>", "<h4>", "<strong>жирный</strong>", "<em>курсив</em>", "<code>код</code>", "$5"):
            self.assertIn(tag, b)
        self.assertIn("<h2", b)

    def test_fenced_code_escaped(self):
        b = body("```\n<b>x</b> & **y**\n```\n")
        self.assertIn("<pre><code>&lt;b&gt;x&lt;/b&gt; &amp; **y**</code></pre>", b)

    def test_blockquote_and_verdict_card(self):
        b = body("> **Итог:** плохо\n> вторая строка\n\nабзац\n\n> цитата *б*\n")
        self.assertEqual(b.count('<blockquote class="verdict">'), 1)
        self.assertEqual(b.count("<blockquote"), 2)
        self.assertIn("<em>б</em>", b)

    def test_nested_lists(self):
        b = body("- a\n  - b\n    - c\n- d\n\n1. x\n2. y\n")
        self.assertEqual(b.count("<ul>"), 3)
        self.assertIn("<ol>", b)
        self.assertEqual(len(re.findall("<li>", b)), 6)
        self.assertFalse(check(render_html("- a\n  - b\n    - c\n- d\n")).errors)

    def test_table_alignment_and_escaped_pipes(self):
        b = body("| A | B | C |\n| :--- | ---: | :---: |\n| a \\| b | `x` | 3 |\n")
        self.assertIn('<th class="a-left">A</th>', b)
        self.assertIn('<th class="a-right">B</th>', b)
        self.assertIn('<th class="a-center">C</th>', b)
        self.assertIn('<td class="a-left">a | b</td>', b)
        self.assertIn('<div class="tablewrap">', b)

    def test_hr_details_br_sub(self):
        b = body("---\n\n<details>\n<summary>Ещё</summary>\n\nвнутри\n\n</details>\n\nа<br>б H<sub>2</sub>O x<sup>2</sup>")
        for t in ("<hr>", "<details>", "<summary>Ещё</summary>", "</details>", "<br>", "<sub>2</sub>", "<sup>2</sup>"):
            self.assertIn(t, b)
        self.assertFalse(check(render_html("<details>\n<summary>S</summary>\nx\n")).errors)

    def test_severity_labels_keep_text(self):
        b = body("🔴 CRITICAL и MAJOR, MINOR; NOTCRITICAL")
        self.assertIn('<span class="sev sev-critical">CRITICAL</span>', b)
        self.assertIn('sev-major', b)
        self.assertIn('sev-minor', b)
        self.assertNotIn("NOT<span", b)

    def test_anchors_and_links_resolve(self):
        md = '# T\n\n[к](#s1) [х](#d-1)\n\n<a id="s1"></a>\n## Один\n\n<a id="d-1"></a>\n**D-1 · MINOR · Тема.** текст\n\n- пункт\n\n| a |\n|---|\n| <a id="t-1"></a>Т |\n\n[внеш](https://example.com/a_b_c)'
        c = check(render_html(md))
        self.assertEqual(c.errors, [])
        self.assertEqual(len(c.ids), len(set(c.ids)))
        for h in c.hrefs:
            if h.startswith("#"):
                self.assertIn(h[1:], c.ids)
        self.assertIn("s1", c.ids)
        self.assertIn('<a href="https://example.com/a_b_c" rel="noopener noreferrer">', render_html(md))
        self.assertIn('class="card card-minor"', render_html(md))

    def test_toc_for_h2(self):
        h = render_html("# T\n\n## Первый\n\nx\n\n## Второй\n")
        self.assertIn('<nav class="toc"', h)
        self.assertEqual(h.count("<li><a href=\"#"), 2)

    def test_unknown_constructs_never_raise(self):
        for md in ["", "\x00﻿", "| a |", "|\n|-|", "> ", "- ", "1.", "#", "```", "<details>", "</details>",
                   "**", "[", "[a](", " 5 ", "<summary>x", "A" * 5000 + "**" * 500]:
            check(render_html(md))
        self.assertTrue(render_html(None).startswith("<!DOCTYPE html>"))  # type: ignore[arg-type]


class Safety(unittest.TestCase):
    def test_script_inert(self):
        h = render_html("<script>alert(1)</script>\n\nтекст <script>x</script>\n\n| <script>y</script> |\n|---|\n| z |")
        self.assertEqual(h.lower().count("<script"), 1)   # только свой скрипт переключателя темы
        self.assertIn("&lt;script&gt;", h)

    def test_img_onerror_inert(self):
        h = render_html('<img src=x onerror=alert(1)>\n\n> <img src=x onerror="alert(1)">')
        self.assertNotIn("<img", h)
        self.assertIn("&lt;img", h)

    def test_javascript_links_inert(self):
        for url in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x", "//evil.example", "vbscript:x"):
            h = render_html(f"[x]({url})")
            self.assertNotIn("<a href", h)
        self.assertEqual(check(render_html("[x](javascript:alert(1))")).hrefs, [])

    def test_attribute_injection_in_anchor_and_link(self):
        h = render_html('<a id="x" onclick="evil()"></a> <a id="a&quot;b"></a> [t](https://e.com/"onmouseover="x)')
        c = check(h)                                                      # no element carries an event attribute
        self.assertEqual([a for a in c.attrs if a.startswith("on")], [])
        self.assertEqual([i for i in check(h).ids if i in ('x', 'a"b')], [])

    def test_no_script_in_page_chrome(self):
        self.assertEqual(render_html("# a").lower().count("<script"), 1)  # только переключатель темы


class Output(unittest.TestCase):
    def test_deterministic(self):
        md = "# T\n\n## A\n\n- x\n\n| a |\n|---|\n| 1 |\n"
        self.assertEqual(render_html(md), render_html(md))

    def test_title(self):
        self.assertIn("<title>Мой</title>", render_html("# T", title="Мой"))
        self.assertIn("<title>T</title>", render_html("# T"))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "r.md"
            src.write_text("# Привет\n", encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "revizor.report_html", str(src)], cwd=ROOT, capture_output=True, text=True, **UTF8)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Привет", (Path(d) / "r.html").read_text(encoding="utf-8"))
            r = subprocess.run([sys.executable, "-m", "revizor.report_html", str(Path(d) / "нет.md")], cwd=ROOT, capture_output=True, text=True, **UTF8)
            self.assertEqual(r.returncode, 2)
            self.assertIn("Не удалось", r.stderr)
            (Path(d) / "b.md").write_bytes(b"\x00\x01\xff\xfe")
            r = subprocess.run([sys.executable, "-m", "revizor.report_html", str(Path(d) / "b.md")], cwd=ROOT, capture_output=True, text=True, **UTF8)
            self.assertEqual(r.returncode, 2)


class Smoke(unittest.TestCase):
    def test_real_report(self):
        p = ROOT / "reports" / "report.md"
        if not p.exists():
            self.skipTest("reports/report.md отсутствует")
        src = p.read_text(encoding="utf-8")
        h = render_html(src)
        c = check(h)
        self.assertEqual(c.errors, [])
        self.assertEqual(c.stack, [])
        self.assertEqual(h.lower().count("<script"), 1)   # только свой скрипт переключателя темы
        self.assertEqual(len(c.ids), len(set(c.ids)))
        in_source = set(re.findall(r'<a id="([^"]+)"></a>', src))      # links the report itself leaves dangling are not ours
        missing = [x for x in c.hrefs if x.startswith("#") and x[1:] in in_source and x[1:] not in c.ids]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
