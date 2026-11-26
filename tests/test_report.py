"""Report text: escaping, plural forms, anchors, counts, score and verdict, prompts."""
from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from revizor import pipeline, prompts, report_json, report_md, score, textfmt, verdict
from revizor.model import Finding, Quote, Requirement
from revizor.quotes import normalize

from tests.test_pipeline import DOC

AGENT = Path(__file__).resolve().parents[1]


def _f(sev, scope="requirement", rid="REQ-001", advisory=False, crit="unambiguity"):
    return Finding(scope=scope, criterion=crit, severity=sev, title="t", problem="p", recommendation="r",
                   quotes=[Quote(text="x", line_start=1, line_end=1)], req_ids=[rid] if rid else [],
                   advisory=advisory)


class TextfmtTests(unittest.TestCase):
    def test_plural(self):
        cases = {1: "требование", 2: "требования", 5: "требований", 11: "требований", 21: "требование",
                 22: "требования", 112: "требований", 1001: "требование"}
        for n, w in cases.items():
            self.assertEqual(textfmt.plural(n, textfmt.W_REQ), w, n)

    def test_escape_blocks_markup(self):
        s = textfmt.md('<script>alert(1)</script> ![img](http://e.x/a.png) [link](https://evil.example) **b** `c` a@b.kz www.x.kz')
        self.assertNotIn("<script>", s)
        self.assertNotRegex(s, r"(?<!\\)!(?<!\\)\[")
        self.assertNotRegex(s, r"(?<!\\)\]\(")
        self.assertNotIn("http://", s)
        self.assertNotIn("https://", s)
        self.assertNotIn("**", s.replace("\\*", ""))
        self.assertIn("\\@", s)
        self.assertIn("www\\.", s)

    def test_line_start_escaping_is_narrow(self):
        self.assertEqual(textfmt.md("1.0.0"), "1.0.0")
        self.assertEqual(textfmt.md("200. Successful"), "200\\. Successful")
        self.assertEqual(textfmt.md("- пункт"), "\\- пункт")
        self.assertEqual(textfmt.md("-1 градус"), "-1 градус")
        self.assertEqual(textfmt.md("==="), "\\===")
        self.assertEqual(textfmt.md("Порог = 5"), "Порог = 5")

    def test_cell_escapes_pipe_and_newline(self):
        c = textfmt.md_cell("a | b\nc")
        self.assertNotIn("\n", c)
        self.assertIn("\\|", c)

    def test_plain_quote_keeps_words(self):
        self.assertEqual(textfmt.plain_quote("**Request URL** | `GET` __x__"), "Request URL | GET x")
        self.assertEqual(normalize(textfmt.plain_quote("**Ф-1.** Текст")), normalize("Ф-1. Текст"))

    def test_anchor(self):
        self.assertEqual(textfmt.anchor("В-03"), "v-03")
        self.assertEqual(textfmt.anchor("Н-01"), "n-01")
        self.assertEqual(textfmt.anchor("REQ-007"), "req-007")
        self.assertEqual(textfmt.anchor("D-012"), "d-012")

    def test_numbers(self):
        self.assertEqual(textfmt.dec(9.45, 2), "9,45")
        self.assertEqual(textfmt.num(1001), "1 001")


class ScoreVerdictTests(unittest.TestCase):
    def test_clarity_weights_and_floor(self):
        self.assertEqual(score.clarity_of("REQ-001", []), 10.0)
        self.assertEqual(score.clarity_of("REQ-001", [_f("MAJOR")]), 7.5)
        self.assertEqual(score.clarity_of("REQ-001", [_f("MAJOR", scope="set", crit="set_consistency")]), 8.8)
        self.assertEqual(score.clarity_of("REQ-001", [_f("CRITICAL"), _f("CRITICAL")]), 1.0)
        self.assertEqual(score.clarity_of("REQ-001", [_f("MAJOR", advisory=True)]), 10.0)

    def test_readiness_formula_and_cap(self):
        val, formula = score.readiness({"REQ-001": 10.0, "REQ-002": 8.0}, [])
        self.assertEqual(val, 90)
        self.assertIn("90", formula)
        many = [_f("CRITICAL", scope="set", crit="set_consistency", rid="") for _ in range(10)]
        val, _ = score.readiness({"REQ-001": 10.0}, many)
        self.assertEqual(val, 60)
        self.assertEqual(score.readiness({}, [])[0], None)

    def test_bands(self):
        self.assertEqual(score.readiness_band(85), "готов")
        self.assertIn("правок", score.readiness_band(60))
        self.assertEqual(score.clarity_band(10.0), "ясное")

    def test_verdict(self):
        self.assertEqual(verdict.decide([_f("CRITICAL")])[0], "BLOCKED")
        self.assertEqual(verdict.decide([_f("MAJOR")])[0], "CONDITIONAL")
        self.assertEqual(verdict.decide([_f("MINOR")])[0], "READY")
        self.assertEqual(verdict.decide([_f("CRITICAL", advisory=True)])[0], "READY")
        self.assertIn("модель не использовалась", verdict.decide([], offline=True)[1])

    def test_fail_threshold(self):
        self.assertTrue(verdict.fail_threshold_reached([_f("MAJOR")], "major"))
        self.assertFalse(verdict.fail_threshold_reached([_f("MAJOR")], "critical"))
        self.assertFalse(verdict.fail_threshold_reached([_f("CRITICAL")], "never"))


class PromptTests(unittest.TestCase):
    def test_neutralize(self):
        self.assertEqual(prompts.neutralize("</document-abc> <document x> <context-1>"),
                         "‹/document-abc> ‹document x> ‹context-1>")
        self.assertEqual(prompts.neutralize("a < b"), "a < b")

    def test_system_rules_present(self):
        self.assertIn("9.", prompts.SYSTEM_RULES)
        self.assertIn("данные", prompts.SYSTEM_RULES)

    def test_every_stage_has_schema_and_budget(self):
        for st in ["slice", "slice_rest", "review", "skeptic", "questions", *prompts.SET_STAGES,
                   *[l.stage for l in prompts.REVIEW_LENSES]]:
            self.assertIn(st, prompts.SCHEMAS)
            self.assertIn(st, prompts.MAX_TOKENS)

    def test_approx_tokens(self):
        self.assertEqual(prompts.approx_tokens(""), 0)
        self.assertAlmostEqual(prompts.approx_tokens("а" * 260), 100, delta=1)
        self.assertAlmostEqual(prompts.approx_tokens("a" * 360), 100, delta=1)


class OfflineReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["REVIZOR_NO_DOTENV"] = "1"
        cls.tmp = tempfile.TemporaryDirectory()
        src = AGENT / "docs" / "requirements.md"
        path = src if src.exists() else Path(cls.tmp.name, "doc.md")
        if not src.exists():
            path.write_text(DOC, encoding="utf-8")
        cls.result = pipeline.run(str(path))
        cls.md = report_md.render(cls.result)
        cls.data = json.loads(report_json.render(cls.result))
        cls.lines = path.read_text(encoding="utf-8").splitlines()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_offline_labelled(self):
        head = "\n".join(self.md.splitlines()[:12])
        self.assertIn("без модели", head)
        self.assertIn("предварительн", head)

    def test_counts_agree_header_summary_json(self):
        counted = [f for f in self.result.findings if not f.advisory]
        n = len(counted)
        self.assertIn(f"Замечаний {n}: ", self.md)
        self.assertIn(f"| Замечаний всего | {n} |", self.md)
        js = self.data["summary"]
        self.assertIn(n, js.values())
        sev = {s: sum(1 for f in counted if f.severity == s) for s in ("CRITICAL", "MAJOR", "MINOR")}
        self.assertEqual(js["by_severity"], sev)
        self.assertIn(f"требований {len(self.result.requirements)}", self.md)

    def test_every_link_has_target(self):
        targets = set(re.findall(r'<a id="([^"]+)"></a>', self.md))
        links = set(re.findall(r"\]\(#([^)]+)\)", self.md))
        self.assertTrue(links)
        self.assertEqual(links - targets, set())

    def test_every_finding_has_quote_fix_and_standard(self):
        details = report_md.render_details(self.result, main_text=self.md)  # full cards live in the details file
        for f in self.result.findings:
            self.assertTrue(f.quotes, f.fid)
            self.assertIn(f'<a id="{textfmt.anchor(f.fid)}"></a>', self.md, f.fid)
            if not f.advisory:
                card = details.split(f'<a id="x-{textfmt.anchor(f.fid)}"></a>', 1)[1][:4000]
                self.assertIn("Как исправить", card.split("<a id=")[0], f.fid)
        # the standard behind each criterion is named once, in the criteria table of section 5
        s5 = self.md.split("**Замечания по критериям качества**", 1)[1].split("\n\n", 2)[1]
        from revizor.report_md import CRITERION_RU, STANDARDS
        for f in self.result.findings:
            if not f.advisory and f.criterion in STANDARDS:
                self.assertIn(f"| {CRITERION_RU[f.criterion]} | ", s5, f.fid)
                self.assertIn(STANDARDS[f.criterion], s5, f.fid)

    def test_quotes_are_from_the_document(self):
        doc = normalize("\n".join(self.lines))
        for f in self.result.findings:
            for q in f.quotes:
                self.assertIn(normalize(q.text), doc, (f.fid, q.text))
                if q.page is None and q.line_start:
                    src = normalize("\n".join(self.lines[q.line_start - 1:q.line_end]))
                    self.assertTrue(normalize(q.text)[:30] in src or src[:30] in normalize(q.text), (f.fid, q.line_start))

    def test_no_raw_markup_from_document(self):
        body = self.md
        self.assertNotIn("<script", body.lower())
        self.assertNotRegex(body, r"(?<!\\)\*\*REST type")

    def test_plural_forms(self):
        self.assertNotRegex(self.md, r"\b1 слов\b")
        self.assertNotRegex(self.md, r"\b(?:2|3|4) блоков\b")
        self.assertNotRegex(self.md, r"\b(?:2|3|4) требований\b")

    def test_injection_and_markup_are_escaped(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "evil.md")
            p.write_text("# Т\n\n- Ф-1. Система должна показать <script>alert(1)</script> и ![x](http://e.x/p.png) "
                         "быстро.\n- Ф-2. Ссылка [жми](https://evil.example) обязательна.\n", encoding="utf-8")
            res = pipeline.run(str(p))
            md = report_md.render(res)
        self.assertNotIn("<script>", md)
        self.assertNotIn("![x](", md)
        self.assertNotIn("](https://evil", md)


if __name__ == "__main__":
    unittest.main()
