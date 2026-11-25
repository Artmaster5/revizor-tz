"""Тесты калькулятора окупаемости tools/roi.py: арифметика, порог, чтение отчёта, плохой ввод, форматы вывода."""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "tools" / "roi.py"
_spec = importlib.util.spec_from_file_location("revizor_roi", _PATH)
roi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(roi)


def run(argv):
    """Запуск main: (код возврата, stdout, stderr). Ошибки argparse дают SystemExit — ловим как код."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = roi.main(argv, out=out.write)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def params(**kw):
    base = {"run_cost_usd": 1.0, "run_seconds": None, "usd_kzt": 500.0, "docs_per_month": 10,
            "pages_per_doc": 30.0, "reviewers": 3, "pace": 15.0, "prep_hours": 1.0,
            "hourly_rate_kzt": 3000.0, "report_reading_minutes": 20.0}
    base.update(kw)
    return {k: {"value": v, "origin": "тест"} for k, v in base.items()}


class ArithmeticTest(unittest.TestCase):
    def test_manual_review_cost(self):
        r = roi.compute(params())
        # 30 / 15 + 1 = 3 ч на проверяющего; 3 проверяющих = 9 человеко-часов; 9 × 3000 = 27 000 ₸
        self.assertAlmostEqual(r["hours_per_reviewer"], 3.0)
        self.assertAlmostEqual(r["manual_person_hours"], 9.0)
        self.assertAlmostEqual(r["manual_kzt"], 27000.0)
        self.assertAlmostEqual(r["manual_elapsed_minutes"], 120.0)

    def test_run_cost_and_analyst_minutes(self):
        r = roi.compute(params())
        # $1 × 500 = 500 ₸; 500 / 3000 × 60 = 10 минут аналитика; чтение 20 мин = 1000 ₸
        self.assertAlmostEqual(r["run_kzt"], 500.0)
        self.assertAlmostEqual(r["run_in_analyst_minutes"], 10.0)
        self.assertAlmostEqual(r["reading_kzt"], 1000.0)
        self.assertAlmostEqual(r["agent_kzt_per_doc"], 1500.0)

    def test_break_even(self):
        r = roi.compute(params())
        # порог = 10 + 20 = 30 мин; ручная проверка 540 человеко-минут → 30 / 540
        self.assertAlmostEqual(r["break_even_minutes"], 30.0)
        self.assertAlmostEqual(r["break_even_share"], 30 / 540)
        self.assertTrue(r["pays_back_possible"])

    def test_break_even_is_where_saving_is_zero(self):
        r = roi.compute(params())
        at = roi.compute(params(saved_share=r["break_even_share"]))
        self.assertAlmostEqual(at["scenarios"][0]["net_kzt_per_doc"], 0.0, places=6)

    def test_no_payback_when_review_is_cheap(self):
        r = roi.compute(params(pages_per_doc=1.0, prep_hours=0.0, reviewers=1))
        self.assertGreater(r["break_even_share"], 1)
        self.assertFalse(r["pays_back_possible"])

    def test_scenarios_month_year(self):
        r = roi.compute(params())
        self.assertFalse(r["scenarios_are_facts"])
        self.assertEqual([s["share"] for s in r["scenarios"]], [0.10, 0.25, 0.50])
        s = r["scenarios"][1]  # 25 %
        self.assertAlmostEqual(s["net_kzt_per_doc"], 0.25 * 27000 - 1500)
        self.assertAlmostEqual(s["net_kzt_month"], (0.25 * 27000 - 1500) * 10)
        self.assertAlmostEqual(s["net_kzt_year"], (0.25 * 27000 - 1500) * 120)
        self.assertAlmostEqual(r["month"]["manual_kzt"], 270000.0)
        self.assertAlmostEqual(r["year"]["agent_kzt"], 1500.0 * 120)

    def test_given_share_replaces_scenarios(self):
        r = roi.compute(params(saved_share=0.4))
        self.assertTrue(r["scenarios_are_facts"])
        self.assertEqual(len(r["scenarios"]), 1)

    def test_free_run(self):
        r = roi.compute(params(run_cost_usd=0.0))
        self.assertEqual(r["run_kzt"], 0)
        self.assertAlmostEqual(r["break_even_minutes"], 20.0)

    def test_default_rate_matches_source(self):
        self.assertEqual(roi.HOURLY_RATE, round(340278 / 164))
        self.assertEqual(roi.USD_KZT, 458.28)


class ReportTest(unittest.TestCase):
    def _report(self, run):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = Path(d.name) / "r.json"
        path.write_text(json.dumps({"run": run, "summary": {}}, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def test_reads_cost_and_duration(self):
        path = self._report({"cost_usd": 0.5, "duration_s": 300, "doc_name": "spec.md", "model": "m", "doc_words": 900})
        code, out, err = run(["--report", path, "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["params"]["run_cost_usd"]["value"], 0.5)
        self.assertIn("замер", data["params"]["run_cost_usd"]["origin"])
        self.assertEqual(data["params"]["run_seconds"]["value"], 300)
        self.assertEqual(data["report"]["doc"], "spec.md")
        self.assertAlmostEqual(data["result"]["run_kzt"], 0.5 * 458.28)

    def test_offline_report_is_free(self):
        path = self._report({"cost_usd": 0.0, "duration_s": 0.1, "doc_name": "spec.md"})
        code, out, _ = run(["--report", path])
        self.assertEqual(code, 0)
        self.assertIn("прогон без модели бесплатен", out)

    def test_report_without_cost_field(self):
        path = self._report({"doc_name": "spec.md"})
        code, out, _ = run(["--report", path])
        self.assertEqual(code, 0)
        self.assertIn("прогон без модели бесплатен", out)

    def test_flag_overrides_report(self):
        path = self._report({"cost_usd": 0.5, "duration_s": 300})
        code, out, _ = run(["--report", path, "--run-cost-usd", "2", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["params"]["run_cost_usd"]["value"], 2.0)

    def test_missing_and_broken_report(self):
        code, _, err = run(["--report", "/nonexistent/report.json"])
        self.assertEqual(code, 2)
        self.assertIn("не найден", err)
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        bad = Path(d.name) / "bad.json"
        bad.write_text("{не json", encoding="utf-8")
        code, _, err = run(["--report", str(bad)])
        self.assertEqual(code, 2)
        no_run = Path(d.name) / "no_run.json"
        no_run.write_text("{}", encoding="utf-8")
        code, _, err = run(["--report", str(no_run)])
        self.assertEqual(code, 2)
        self.assertIn("run", err)


class BadInputTest(unittest.TestCase):
    def test_zero_and_negative(self):
        for argv in (["--pace", "0"], ["--reviewers", "-1"], ["--pages-per-doc", "0"], ["--usd-kzt", "0"],
                     ["--hourly-rate-kzt", "-5"], ["--docs-per-month", "0"], ["--prep-hours", "-1"],
                     ["--report-reading-minutes", "-1"], ["--run-seconds", "-1"], ["--saved-share", "0"],
                     ["--saved-share", "1.5"]):
            code, out, err = run(["--run-cost-usd", "1", *argv])
            self.assertEqual(code, 2, argv)
            self.assertIn("Ошибка", err)
            self.assertEqual(out, "")
        code, _, _ = run(["--run-cost-usd", "-1"])
        self.assertEqual(code, 2)

    def test_not_a_number(self):
        for value in ("abc", "inf", "nan"):
            code, _, err = run(["--run-cost-usd", "1", "--pace", value])
            self.assertEqual(code, 2, value)
            self.assertIn("число", err)

    def test_no_cost_source(self):
        code, _, err = run([])
        self.assertEqual(code, 2)
        self.assertIn("--report", err)

    def test_comma_decimal_accepted(self):
        code, out, _ = run(["--run-cost-usd", "0,19", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["params"]["run_cost_usd"]["value"], 0.19)


class OutputShapeTest(unittest.TestCase):
    ARGV = ["--run-cost-usd", "0.19", "--run-seconds", "710"]

    def test_text(self):
        code, out, _ = run(self.ARGV)
        self.assertEqual(code, 0)
        for part in ("1. Ручная проверка", "2. Прогон агента", "3. Точка окупаемости", "4. Месяц и год",
                     "Что здесь что", "работы аналитика", "Сценарии, не факты", "по умолчанию:", "hh.kz",
                     "NASA-STD-8739.9", "25.11.2026", "Налоги и накладные расходы не включены", "1–3 страницы"):
            self.assertIn(part, out)

    def test_markdown(self):
        code, out, _ = run(self.ARGV + ["--markdown"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("### Расчёт окупаемости"))
        self.assertIn("| Параметр | Значение | Откуда |", out)
        self.assertIn("#### 3. Точка окупаемости", out)
        table = [ln for ln in out.splitlines() if ln.startswith("| ")]
        self.assertTrue(all(ln.count("|") == 4 for ln in table), table)

    def test_json(self):
        code, out, _ = run(self.ARGV + ["--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(set(data), {"params", "report", "result", "notes"})
        self.assertIsNone(data["report"])
        self.assertIn("break_even_minutes", data["result"])
        self.assertEqual(data["notes"]["unknown_until_pilot"], "saved_share")
        self.assertFalse(data["notes"]["scenarios_are_facts"])
        for p in data["params"].values():
            self.assertIn("origin", p)

    def test_no_payback_message(self):
        code, out, _ = run(["--run-cost-usd", "5", "--pages-per-doc", "1", "--reviewers", "1"])
        self.assertEqual(code, 0)
        self.assertIn("не окупается", out)

    def test_markdown_and_json_exclusive(self):
        code, _, _ = run(self.ARGV + ["--markdown", "--json"])
        self.assertEqual(code, 2)


class NumberFormatTest(unittest.TestCase):
    def test_russian_format(self):
        self.assertEqual(roi.num(1234567.891, 2), "1 234 567,89")
        self.assertEqual(roi.num(-1500), "−1 500")
        self.assertEqual(roi.minutes(90), "1 ч 30 мин")
        self.assertEqual(roi.pct(0.25), "25 %")


if __name__ == "__main__":
    unittest.main()
