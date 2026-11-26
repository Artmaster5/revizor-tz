"""Wave 5A: the report never contradicts itself, first screen, length, merges, exports, re-render."""
from __future__ import annotations

import copy
import csv
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from revizor import consistency, exports, merge, pipeline, report_json, report_md
from revizor.model import CriterionVerdict, Finding, Quote, Strength

from tests import test_pipeline as tp


_PINNED: list = []


def setUpModule() -> None:
    _PINNED.extend(tp.pin_policy())


def tearDownModule() -> None:
    tp.unpin_policy(_PINNED)

AGENT = Path(__file__).resolve().parent.parent
FIXTURES = AGENT / "tests" / "fixtures"


def _offline(path: Path):
    return pipeline.run(str(path), backend=None)


class _Fake(tp._Base):
    """A full run on the small test document with the scripted model: reviews, strengths, questions are present."""

    def base(self):
        res, _ = self.run_fake()
        self.assertEqual(consistency.check_consistency(res), [], "the starting result must be consistent")
        return res

    def problems(self, res):
        return consistency.check_consistency(res)


# --- task 1: seven invariants, each pinned on a result changed by hand -----------------------------------------------

class InvariantLevelAndNote(_Fake):
    def test_trail_must_end_on_the_card_level(self):
        res = self.base()
        f = next(f for f in res.findings if f.severity == "MAJOR" and not f.advisory)
        f.level_trail = [{"by": "model", "level": "MAJOR"}, {"by": "skeptic", "level": "MINOR", "why": "мелочь"}]
        self.assertTrue(any("запись об уровне" in p for p in self.problems(res)))

    def test_note_without_trail_must_match_the_level(self):
        res = self.base()
        f = next(f for f in res.findings if f.severity == "MAJOR" and not f.advisory)
        f.level_trail = []
        f.status_reason = "уровень изменён перепроверкой: MAJOR → MINOR"
        self.assertTrue(any("примечание говорит MINOR" in p for p in self.problems(res)))
        consistency.settle(res)  # rebuilds the trail from the note and the current level
        self.assertEqual(res.findings[[x.fid for x in res.findings].index(f.fid)].level_trail[-1]["level"], "MAJOR")
        self.assertEqual(self.problems(res), [])

    def test_merge_takes_the_highest_level_after_the_recheck_and_says_so(self):
        a = Finding(scope="requirement", criterion="completeness", severity="MINOR", title="a", problem="p",
                    recommendation="r", fid="D-001", source="model")
        merge.note_level(a, "skeptic", "MINOR", "перепроверка понизила")
        b = Finding(scope="requirement", criterion="completeness", severity="MAJOR", title="b", problem="p",
                    recommendation="r", fid="D-002", source="model")
        merge.absorb(a, b)
        self.assertEqual(a.severity, "MAJOR")
        self.assertEqual(a.level_trail[-1]["level"], "MAJOR")
        self.assertEqual(a.level_trail[-1]["by"], "merge")
        self.assertEqual(a.level_trail[-1]["with"], "D-002")
        self.assertIn("по старшему из объединённых", consistency.trail_line(a))
        self.assertEqual(a.also, ["b"])


class InvariantMarks(_Fake):
    def test_check_without_link_and_link_without_warning(self):
        res = self.base()
        rv = next(rv for rv in res.reviews if rv.rid == "REQ-003")
        rv.criteria["feasibility"] = CriterionVerdict("issue", "")
        self.assertTrue(any("REQ-003: feasibility с ⚠️ без связанного замечания" in p for p in self.problems(res)))
        consistency.settle(res)
        self.assertEqual(self.problems(res), [])

    def test_set_finding_marks_the_requirements_it_names(self):
        res = self.base()
        f = next(f for f in res.findings if f.criterion == "set_completeness")
        rv = next(rv for rv in res.reviews if rv.rid == f.req_ids[0])
        rv.criteria["completeness"] = CriterionVerdict("ok", "")
        self.assertTrue(any("completeness без ⚠️" in p for p in self.problems(res)))
        consistency.settle(res)
        self.assertEqual(rv.criteria["completeness"].status, "issue")
        self.assertEqual(self.problems(res), [])

    def test_matrix_and_card_use_the_same_marks(self):
        res = self.base()
        md = report_md.render(res)
        by_crit, _t = consistency.marks_of("REQ-004", res.findings)
        fid = by_crit["completeness"][0].fid
        s3 = md.split('<a id="s3"></a>', 1)[1]
        row = next(ln for ln in s3.splitlines() if ln.startswith("| [REQ-004](#req-004)"))
        self.assertIn(f"[⚠️](#{fid.lower()})", row)
        det = report_md.render_details(res)  # the requirement block lives in the details file
        card = det.split('<a id="x-req-004"></a>', 1)[1].split("<a id=", 1)[0]
        self.assertIn(f"[{fid}](#x-{fid.lower()})", card)


class InvariantClarity(_Fake):
    def test_clarity_follows_the_findings(self):
        res = self.base()
        res.reviews[0].clarity = 1.0
        self.assertTrue(any("ясность 1.0" in p for p in self.problems(res)))
        consistency.settle(res)
        self.assertEqual(self.problems(res), [])

    def test_band_word_only_with_linked_findings(self):
        res = self.base()
        md = report_md.render_details(res)
        clean = md.split('<a id="x-s3-clean"></a>', 1)[1].split('<a id="x-part-b">', 1)[0]
        for word in ("использовать нельзя", "нужна доработка", "есть замечания"):
            self.assertNotIn(word, clean)


class InvariantStrengths(_Fake):
    def test_strength_on_a_requirement_with_a_major_finding_is_dropped(self):
        res = self.base()
        res.strengths.append(Strength("Срок отправки задан хорошо", ["REQ-001"]))
        self.assertTrue(any("Сильная сторона" in p for p in self.problems(res)))
        consistency.settle(res)
        self.assertNotIn("Срок отправки задан хорошо", [s.text for s in res.strengths])
        self.assertTrue(any(a.get("what") == "strengths_dropped" or a.get("type") == "strengths_dropped"
                            for a in res.audit))
        self.assertEqual(self.problems(res), [])

    def test_strength_quoting_a_finding_is_dropped(self):
        res = self.base()
        q = next(f for f in res.findings if f.severity == "MINOR" and f.quotes and f.scope == "requirement").quotes[0]
        res.strengths.append(Strength(f"Хорошо сказано: «{q.text}»", []))
        self.assertTrue(consistency.strength_conflict(res.strengths[-1].text, [], res))

    def test_nothing_left_says_so_in_one_line(self):
        res = self.base()
        res.strengths = []
        md = report_md.render(res)
        self.assertIn("Агент не нашёл мест, которые можно назвать хорошими без оговорок", md)

    def test_prompt_asks_for_places_without_findings(self):
        from revizor import prompts
        src = Path(prompts.__file__).read_text(encoding="utf-8")
        self.assertIn("Хорошим называй только место, о котором нет ни замечания, ни вопроса.", src)


class InvariantHeadEqualsBody(_Fake):
    def test_counts_and_blockers_in_the_head(self):
        res = self.base()
        md = report_md.render(res)
        t = consistency.tally(res.findings)
        self.assertIn(f"блокеров {t['CRITICAL']} · существенных {t['MAJOR']} · мелких {t['MINOR']}", md)
        self.assertEqual(json.loads(report_json.render(res))["summary"]["by_severity"],
                         {k: t[k] for k in ("CRITICAL", "MAJOR", "MINOR")})

    def test_a_changed_head_is_caught(self):
        res = self.base()
        md = report_md.render(res).replace("блокеров 0 · ", "блокеров 2 · ", 1)
        self.assertIn("Числа в начале отчёта не совпадают с замечаниями", consistency.check_consistency(res, md))

    def test_blocker_line_names_the_reason_in_words(self):
        res = _offline(AGENT / "docs" / "requirements.md")
        md = report_md.render(res)
        head = md.split('<a id="s1"></a>', 1)[0]
        crit = consistency.blockers(res.findings)
        self.assertTrue(crit)
        lines = re.findall(r"^\d+\. \[(D-\d+)\].*$", head, re.M)
        self.assertEqual(lines[:len(crit)], [f.fid for f in crit])
        for f in crit:
            line = next(ln for ln in head.splitlines() if ln.startswith(tuple(f"{i}. [{f.fid}]" for i in range(1, 30))))
            self.assertIn(consistency.BLOCKER_SHORT[f.blocker_reason], line)

    def test_verdict_follows_the_blockers(self):
        res = self.base()
        res.verdict = "READY"
        self.assertTrue(any(p.startswith("Вердикт READY") for p in self.problems(res)))
        consistency.settle(res)
        self.assertEqual(self.problems(res), [])


class InvariantQuestions(_Fake):
    def test_blocking_priority_only_with_an_open_critical(self):
        res = self.base()
        q = res.questions[0]
        q.priority = "blocker"
        self.assertTrue(any(p.startswith(f"{q.qid}: приоритет blocker") for p in self.problems(res)))
        consistency.settle(res)
        self.assertNotEqual(q.priority, "blocker")
        self.assertEqual(self.problems(res), [])


class InvariantLinks(_Fake):
    def test_link_to_a_finding_without_a_card_becomes_text(self):
        md = "См. [D-034](#d-034) и [D-001](#d-001).\n\n<a id=\"d-001\"></a>карточка"
        fixed = consistency.fix_links(md)
        self.assertIn("См. D-034 и [D-001](#d-001).", fixed)

    def test_dangling_link_is_reported(self):
        res = self.base()
        md = report_md.render(res) + "\n[D-099](#d-099)\n"
        self.assertTrue(any("d-099" in p for p in consistency.check_consistency(res, md)))


class OfflineDocumentsAreConsistent(unittest.TestCase):
    def test_docs_and_every_fixture(self):
        paths = [AGENT / "docs" / "requirements.md"] + sorted(p for p in FIXTURES.iterdir() if p.is_file())
        self.assertGreater(len(paths), 3)
        for p in paths:
            with self.subTest(p.name):
                res = _offline(p)
                self.assertEqual(consistency.check_consistency(res), [], p.name)
                self.assertFalse(any("противоречить себе" in w for w in res.run.warnings), p.name)


# --- task 0: re-render from JSON -------------------------------------------------------------------------------------

class RoundTrip(unittest.TestCase):
    def test_offline_json_back_to_the_same_markdown(self):
        res = _offline(AGENT / "docs" / "requirements.md")
        direct = report_md.render(res)
        loaded = report_json.from_data(json.loads(report_json.render(res)))
        self.assertEqual(report_md.render(loaded), direct)
        consistency.settle(loaded)  # settling an already settled result changes nothing
        self.assertEqual(report_md.render(loaded), direct)

    def test_older_file_with_missing_fields_loads(self):
        res = _offline(AGENT / "docs" / "requirements.md")
        data = json.loads(report_json.render(res))
        for f in data["findings"]:
            f.pop("level_trail", None)
            f.pop("also", None)
        data["run"].pop("model_version", None)
        loaded = report_json.from_data(data)
        self.assertEqual(len(loaded.findings), len(res.findings))
        self.assertEqual(loaded.run.model_version, "")
        with self.assertRaises(ValueError):
            report_json.from_data({"findings": []})

    def test_tool_writes_report_and_side_files(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("rerender", AGENT / "tools" / "rerender.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        res = _offline(AGENT / "docs" / "requirements.md")
        with tempfile.TemporaryDirectory() as t:
            src = Path(t) / "r.json"
            src.write_text(report_json.render(res), encoding="utf-8")
            self.assertEqual(mod.main([str(src), "-o", str(Path(t) / "out" / "r.md")]), 0)
            names = {p.name for p in (Path(t) / "out").iterdir()}
            for n in ("r.md", "r.summary.md", "r.jira.csv", "r.diagnostic.md", "r.questions.md"):
                self.assertIn(n, names)
            self.assertEqual(mod.main([str(src), "-o", str(src)]), 2)


# --- task 2: first screen ----------------------------------------------------------------------------------------------

class FirstScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _offline(AGENT / "docs" / "requirements.md")
        cls.md = report_md.render(cls.res)
        cls.head = cls.md.split("**Разделы:**", 1)[0]

    def test_parts_and_length(self):
        for part in ("**Итог:**", "Индекс готовности документа", "**Блокеры", "**Сначала спросить автора:**",
                     "**Что исправить в первую очередь:**", "Прогон: "):
            self.assertIn(part, self.head)
        self.assertLessEqual(len(self.head.split()), 300)

    def test_questions_are_capped_and_say_when_they_remove_a_blocker(self):
        block = self.head.split("**Сначала спросить автора:**", 1)[1].split("**Что исправить", 1)[0]
        items = re.findall(r"^\d+\. ", block, re.M)
        self.assertLessEqual(len(items), 5)
        self.assertIn("снимет блокер", block)

    def test_summary_side_file_repeats_the_block(self):
        s = report_md.summary_md(self.res, "r.md")
        self.assertIn("**Сначала спросить автора:**", s)
        self.assertIn("(r.md#", s)
        self.assertNotRegex(s, r"\]\(#")
        plain = report_md.summary_md(self.res)
        self.assertNotIn("](", plain)


    def test_answers_line_names_the_blockers_answers_leave_open(self):
        from tests.dialog_helpers import provider_from_text
        text = (AGENT / "reports" / "sample_answers.md").read_text(encoding="utf-8")
        res = pipeline.run(str(AGENT / "docs" / "requirements.md"), backend=None, answers=provider_from_text(text))
        head = report_md.render(res).split("**Разделы:**", 1)[0]
        line = next(x for x in head.splitlines() if "Ответы автора учтены" in x)
        left = [f.fid for f in res.findings if f.status == "open" and f.severity == "CRITICAL" and not f.closed_by_answer]
        self.assertTrue(left, "the sample leaves the section blocker open")
        self.assertEqual(res.verdict_after_answers, res.verdict)
        self.assertIn("не меняется", line)
        for fid in left:
            self.assertIn(fid, line)
        self.assertNotIn("модель не использовалась", line)


# --- task 3: length ----------------------------------------------------------------------------------------------------

class Length(unittest.TestCase):
    # The brief's target is 5 500 words; this bound pins what wave 5A reached (15 250 before it) so the report does
    # not grow back. Problems and recommendations of findings are never shortened to meet it.
    OFFLINE_MAX = 8000

    def test_offline_sample_bound(self):
        res = _offline(AGENT / "docs" / "requirements.md")
        md = report_md.render(res)
        self.assertLessEqual(len(md.split()), self.OFFLINE_MAX)
        det = report_md.render_details(res, main_text=md)
        for f in res.findings:  # nothing a reader needs is cut: every problem and recommendation is in the details
            if f.status == "open" and not f.advisory:
                self.assertIn(f'<a id="{f.fid.lower()}"></a>', md, f.fid)
                card = det.split(f'<a id="x-{f.fid.lower()}"></a>', 1)[1].split("<a id=", 1)[0]
                self.assertTrue(f.recommendation[:40] in det or "как в [D-" in card, f.fid)

    def test_card_prints_each_quote_line_once(self):
        res = _offline(AGENT / "docs" / "requirements.md")
        md = report_md.render(res)
        for f in res.findings:
            if f.status != "open" or f.advisory:
                continue
            card = md.split(f'<a id="{f.fid.lower()}"></a>', 1)[1].split("<a id=", 1)[0]
            places = re.findall(r"строк[аи] [\d–, ]+(?:, \[REQ-\d+\]\(#req-\d+\))?: «[^»]*»", card)
            self.assertEqual(len(places), len(set(places)), f.fid)


# --- task 8: Jira import file ------------------------------------------------------------------------------------------

class JiraCsv(unittest.TestCase):
    def test_parses_back_with_csv(self):
        res = _offline(AGENT / "docs" / "requirements.md")
        text = exports.jira_csv(res)
        self.assertTrue(text.startswith("﻿"))
        rows = list(csv.reader(io.StringIO(text[1:], newline="")))
        self.assertEqual(tuple(rows[0]), exports.JIRA_COLUMNS)
        counted = [f for f in res.findings if f.status == "open" and not f.advisory]
        self.assertEqual(len(rows) - 1, len(counted))
        for f, row in zip(counted, rows[1:]):
            summary, desc, prio, labels, kind = row
            self.assertEqual(summary, f"[{f.severity}] {' '.join(f.title.split())}".lstrip("'") if not
                             summary.startswith("'") else summary)
            self.assertEqual(prio, {"CRITICAL": "Highest", "MAJOR": "High", "MINOR": "Low"}[f.severity])
            self.assertEqual(labels.split(), ["revizor", f.criterion, f.fid])
            self.assertEqual(kind, "Bug")
            self.assertIn(f.fid, desc)
            if f.quotes:
                self.assertIn("строк", desc)

    def test_formula_like_text_is_neutralised(self):
        res = _offline(AGENT / "docs" / "requirements.md")
        f = next(f for f in res.findings if f.status == "open" and not f.advisory)
        f.title = "=HYPERLINK(1)"
        rows = list(csv.reader(io.StringIO(exports.jira_csv(res)[1:], newline="")))
        self.assertTrue(any(r[0].startswith("[") for r in rows[1:]))
        self.assertFalse(any(r[0].startswith("=") for r in rows[1:]))


# --- task 7: HTML next to the report -----------------------------------------------------------------------------------

class HtmlPage(unittest.TestCase):
    def test_page_with_verdict_class_and_never_failing(self):
        res = _offline(AGENT / "docs" / "requirements.md")
        md = report_md.render(res)
        files = dict((p.name, t) for p, t in exports.extra_files(res, Path("/x/r.md"), md))
        self.assertEqual(list(files)[-1], "r.html")
        self.assertIn('<blockquote class="verdict verdict-blocked">', files["r.html"])
        broken = copy.deepcopy(res)
        broken.findings = None  # every builder fails: still no exception, only warnings
        out = exports.extra_files(broken, Path("/x/r.md"), md)
        self.assertTrue(any("не записан" in w for w in broken.run.warnings))
        self.assertTrue(all(isinstance(t, str) for _p, t in out))


# --- task 9: no unsourced manual-review estimate ---------------------------------------------------------------------

class NoManualEstimate(unittest.TestCase):
    def test_null_in_json_and_absent_in_text(self):
        res = _offline(AGENT / "docs" / "requirements.md")
        self.assertIsNone(json.loads(report_json.render(res))["run"]["manual_minutes_estimate"])
        self.assertNotIn("вручную", report_md.render(res).split('<a id="app">', 1)[0])


if __name__ == "__main__":
    unittest.main()
