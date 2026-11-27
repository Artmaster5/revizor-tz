"""Report layout 2: a short main report and the details file (``*.details.md``). Print-only: no network, no model."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

from revizor import consistency, exports, pipeline, report_json, report_md
from revizor.llm import FakeBackend
from revizor.model import Question
from revizor.textfmt import anchor

from tests.test_cli import _Base as _CliBase
from tests.test_pipeline import make_script

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT / "tools"))
import rerender  # noqa: E402

_A = re.compile(r'<a id="([^"]+)"></a>')
_L = re.compile(r"\]\(([^)\s#]*)#([^)\s]+)\)")
SERVICE = ("Сюда же", "(цитата выше)", "объединение с правило", "Источник: модель", "источник: модель")


def _saved(name: str):
    """A committed JSON result re-rendered the way ``tools/rerender.py`` does it (the file is only read)."""
    return rerender.prepare(report_json.load_result(AGENT / "reports" / name))


class _Texts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = {}
        for label, res in (("offline", pipeline.run(str(AGENT / "docs" / "requirements.md"), backend=None)),
                           ("model", _saved("report.json")), ("answered", _saved("report_answered.json"))):
            main = report_md.render(res, "r.md")
            cls.cases[label] = (res, main, report_md.render_details(res, "r.md", main))


class MainReport(_Texts):
    def test_every_counted_finding_has_exactly_one_anchor_in_main(self):
        for label, (res, main, _d) in self.cases.items():
            ids = _A.findall(main)
            for f in consistency.counted(res.findings):
                self.assertEqual(ids.count(anchor(f.fid)), 1, (label, f.fid))

    def test_no_service_phrases_in_main(self):
        for label, (_r, main, _d) in self.cases.items():
            for phrase in SERVICE:
                self.assertNotIn(phrase, main, (label, phrase))
            self.assertNotIn("Уровень: ", main, label)

    def test_critical_and_major_link_to_their_details(self):
        for label, (res, main, det) in self.cases.items():
            for f in consistency.counted(res.findings):
                link = f"(r.details.md#x-{anchor(f.fid)})"
                if f.severity in ("CRITICAL", "MAJOR"):
                    self.assertIn("[подробно]" + link, main, (label, f.fid))
                    self.assertIn(f'<a id="x-{anchor(f.fid)}"></a>', det, (label, f.fid))

    def test_in_file_links_resolve(self):
        for label, (_r, main, _d) in self.cases.items():
            own = set(_A.findall(main))
            bad = [a for file, a in _L.findall(main) if not file and a not in own]
            self.assertEqual(bad, [], label)
            self.assertEqual(consistency.check_consistency(self.cases[label][0], main), [], label)

    def test_layout_row_in_the_passport(self):
        res, main, _d = self.cases["model"]
        self.assertIn("| Вёрстка отчёта | 2: короткий отчёт и файл подробностей |", main)
        again = report_md.render(res, "r.md", rerendered=True)
        self.assertIn("файл подробностей; перерисован из сохранённого результата, модель не вызывалась |", again)

    def test_pointer_to_the_details_file_after_the_sections_line(self):
        _r, main, _d = self.cases["offline"]
        head = main.split('<a id="s1"></a>', 1)[0]
        after = head.split("**Разделы:**", 1)[1]
        self.assertIn("— в файле `r.details.md`.", after)


class DetailsFile(_Texts):
    def test_anchors_are_prefixed_and_links_resolve(self):
        for label, (_r, main, det) in self.cases.items():
            own, other = set(_A.findall(det)), set(_A.findall(main))
            self.assertTrue(own and all(a.startswith("x-") for a in own), label)
            for file, a in _L.findall(det):
                if not file:
                    self.assertIn(a, own, (label, a))
                else:
                    self.assertEqual(file, "r.md", label)
                    self.assertIn(a, other, (label, a))
            for file, a in _L.findall(main):
                if file:
                    self.assertEqual(file, "r.details.md", label)
                    self.assertIn(a, own, (label, a))

    def test_card_wording(self):
        for label, (_r, _m, det) in self.cases.items():
            for phrase in ("Сюда же", "(цитата выше)", "объединение с правило"):
                self.assertNotIn(phrase, det, (label, phrase))
        det = self.cases["model"][2]
        # the stored report is re-rendered: its merged cards give the list with title, problem and place (wave 9)
        self.assertRegex(det, r"Объединено с похожими замечаниями \(\d+\):\n  - \*\*")
        self.assertIn("объединено с правилом `flow-happy-path` — MAJOR (уровень задан автоматическим правилом)", det)
        for part in ("## А. Разбор по каждому требованию", "## Б. Замечания к набору требований", "## В. Карта полноты",
                     "## Г. Вопросы автору полностью"):
            self.assertIn(part, det)

    def test_level_trail_variants(self):
        f = consistency.counted(self.cases["model"][0].findings)[0]
        f.level_trail = [{"by": "model", "level": "MINOR"},
                         {"by": "merge", "level": "MAJOR", "with": "D-012", "why": "уровень взят по старшему"},
                         {"by": "merge", "level": "CRITICAL", "with": "правило a, b"},
                         {"by": "merge", "level": "MAJOR", "with": "Какое-то замечание"},
                         {"by": "skeptic", "level": "MINOR", "why": "мелочь."}]
        self.assertEqual(report_md.level_trail_text(f),
                         "модель — MINOR → объединено с замечанием D-012 — MAJOR (уровень взят по старшему) → "
                         "объединено с правилами `a`, `b` — CRITICAL → объединено с замечанием «Какое-то замечание» — "
                         "MAJOR → перепроверка — MINOR (мелочь)")

    def test_not_written_for_an_unreadable_document(self):
        res = pipeline.run(str(AGENT / "examples" / "broken" / "empty.md"), backend=None)
        self.assertIsNotNone(res.diagnostic)
        self.assertIsNone(report_md.render_details(res))
        names = [p.name for p, _t in exports.extra_files(res, Path("/nowhere/e.md"))]
        self.assertNotIn("e.details.md", names)


class HtmlPage(_Texts):
    def test_one_page_without_links_to_md(self):
        for label, (res, main, det) in self.cases.items():
            page = exports.html_page(res, main, det, "r.md")
            self.assertIsNotNone(page)
            self.assertEqual(re.findall(r'href="[^"#]*\.md', page), [], label)
            ids = set(re.findall(r'id="([^"]+)"', page))
            self.assertEqual({h for h in re.findall(r'href="#([^"]+)"', page)} - ids, set(), label)
            self.assertIn("Подробности проверки", page)


class AnsweredReport(_Texts):
    def test_head_says_what_the_answers_close(self):
        res, main, _d = self.cases["answered"]
        head = main.split('<a id="s1"></a>', 1)[0]
        self.assertIn("· ответы автора закрывают 19 замечаний, из них блокеров 6; после внесения ответов в текст останется 60, блокеров 1", head)
        self.assertIn("Индекс считается по тексту документа; пока ответы не внесены в текст, он прежний.", head)
        self.assertRegex(head, r"блокеров 7 · существенных 20 · мелких 52")  # the numbers consistency reads stay

    def test_dialog_lines_and_question_table(self):
        _r, main, det = self.cases["answered"]
        s7 = main.split('<a id="s7"></a>', 1)[1].split('<a id="s8"></a>', 1)[0]
        self.assertIn("### Диалог с автором", s7)
        self.assertIn("→ «Раздел заполняет QA-лид команды вместе с автором документа", s7)
        self.assertIn("→ замечания остаются", s7)
        self.assertIn("→ закрывает [D-076](#d-076)", s7)
        self.assertIn("| № | Вопрос | Кому | Приоритет | К замечаниям |", s7)
        self.assertIn('| <a id="v-01"></a>В-01 |', s7)
        self.assertNotIn("Варианты ответа:", s7)
        self.assertIn("Варианты ответа:", det)


class QuestionGain(_Texts):
    def test_one_and_two_questions_per_blocker(self):
        res = self.cases["model"][0]
        by = {q.qid: q for q in res.questions}
        self.assertEqual(report_md._q_gain(by["В-01"], res), " — снимет 2 блокера")
        self.assertEqual(report_md._q_gain(by["В-03"], res), " — снимет блокер [D-076](#d-076)")
        extra = Question(qid="В-99", text="?", why="", addressee="analyst", priority="blocker",
                         finding_ids=["D-076"])
        res.questions.append(extra)
        try:
            self.assertEqual(report_md._q_gain(by["В-03"], res), " — один из 2 вопросов к блокеру [D-076](#d-076)")
        finally:
            res.questions.remove(extra)

    def test_an_answered_question_does_not_count(self):
        res = _saved("report.json")
        by = {q.qid: q for q in res.questions}
        extra = Question(qid="В-99", text="?", why="", addressee="analyst", priority="blocker",
                         finding_ids=["D-076"])
        res.questions.append(extra)
        self.assertEqual(report_md._q_gain(by["В-03"], res), " — один из 2 вопросов к блокеру [D-076](#d-076)")
        extra.answer = "ответ"
        self.assertEqual(report_md._q_gain(by["В-03"], res), " — снимет блокер [D-076](#d-076)")


class FileList(_CliBase):
    def written(self) -> list[str]:
        line = next(x for x in self.lines if x.startswith("Записано: "))
        return [Path(p).name for p in line[len("Записано: "):].split(", ")]

    def test_model_run_writes_12_files(self):
        out = self.dir / "m.md"
        self.assertEqual(self.main("-i", self.doc(), "-o", str(out), backend=FakeBackend(make_script(), concurrency=1)), 0)
        names = self.written()
        self.assertEqual(len(names), 12, names)
        self.assertIn("m.details.md", names)
        self.assertIn("m.letter.md", names)
        self.assertEqual(names[-1], "m.html")
        md = out.read_text(encoding="utf-8")
        self.assertIn("(m.details.md#x-", md)
        self.assertTrue(all((self.dir / n).exists() for n in names))

    def test_offline_run_writes_11_files(self):
        self.assertEqual(self.main("--provider", "offline", "-i", self.doc(), "-o", str(self.dir / "o.md")), 0)
        names = self.written()
        self.assertEqual(len(names), 11, names)
        self.assertIn("o.details.md", names)
        self.assertIn("o.letter.md", names)

    def test_unreadable_document_writes_3_files(self):
        bad = str(AGENT / "examples" / "broken" / "empty.md")
        self.assertEqual(self.main("--provider", "offline", "-i", bad, "-o", str(self.dir / "e.md")), 0)
        names = self.written()
        self.assertEqual(sorted(names), ["e.html", "e.json", "e.md"], names)


if __name__ == "__main__":
    unittest.main()
