"""Измерительный набор: сопоставление (score.py), проверка ключей (check_keys.py), прогон (run_all.py)."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
if str(EVAL) not in sys.path:
    sys.path.insert(0, str(EVAL))

import check_keys  # noqa: E402
import run_all  # noqa: E402
import score  # noqa: E402

DOC = (
    "# Тестовый документ\n"
    "Порог задаётся целым числом от 1 до 100 включительно, по умолчанию выключен.\n"
    "Срок хранения данных об операциях в документе не указан.\n"
    "Ошибка суммы возвращается с кодом 200 вместо кода ошибки.\n"
    "Месяц считается по дате совершения покупки в Алматы.\n"
)

KEY = {
    "document": "doc.md",
    "domain": "тест",
    "language": "ru",
    "defects": [
        {"id": "T-01", "severity": "MAJOR", "criterion": "completeness", "origin": "organisers",
         "title": "Срок хранения не указан",
         "anchors": ["Срок хранения данных об операциях в документе не указан"],
         "keywords": [["хранен"], ["не указан", "не определ"]], "why": "-"},
        {"id": "T-02", "severity": "CRITICAL", "criterion": "consistency", "origin": "own",
         "title": "Ошибка с кодом 200",
         "anchors": ["Ошибка суммы возвращается с кодом 200 вместо кода ошибки"],
         "keywords": [["200"], ["ошибк"]], "why": "-"},
        {"id": "T-03", "severity": "MINOR", "criterion": "unambiguity", "origin": "own",
         "title": "Часовой пояс",
         "anchors": ["Месяц считается по дате совершения покупки в Алматы"],
         "keywords": [["алматы", "месяц"], ["двусмыслен", "неоднознач"]], "why": "-"},
    ],
    "good": [{"anchor": "Порог задаётся целым числом от 1 до 100 включительно", "why": "точно"}],
}


def finding(fid, severity, title, text, line, problem="", recommendation="", **extra):
    f = {"fid": fid, "scope": "requirement", "criterion": "completeness", "severity": severity, "title": title,
         "problem": problem, "recommendation": recommendation, "status": "open", "advisory": False,
         "quotes": [{"text": text, "line_start": line, "line_end": line}], "req_ids": []}
    f.update(extra)
    return f


def result(findings):
    return {"run": {"provider": "offline", "model": "", "mode": "offline", "doc_name": "doc.md",
                    "cost_usd": 0.0, "duration_s": 1.5},
            "requirements": [{"rid": "REQ-001", "quote": "x"}], "findings": findings}


def rep_for(findings):
    return score.score_result(result(findings), KEY, DOC)


def status(rep, did):
    return next(r["status"] for r in rep["defects"] if r["id"] == did)


class Matcher(unittest.TestCase):
    def test_strict_hit(self):
        rep = rep_for([finding("D-001", "MAJOR", "Не указан срок хранения", "Срок хранения данных об операциях в документе не указан.", 3)])
        self.assertEqual(status(rep, "T-01"), "strict")
        self.assertEqual(rep["recall"]["overall"]["strict"], 1)
        self.assertEqual(rep["recall"]["by_origin"]["organisers"]["strict_share"], 1.0)
        self.assertEqual(rep["severity_agreement"]["same"], 1)
        self.assertEqual(rep["unmatched"], [])

    def test_loose_only_hit(self):
        rep = rep_for([finding("D-001", "MINOR", "Странная формулировка", "Месяц считается по дате совершения покупки в Алматы.", 5)])
        self.assertEqual(status(rep, "T-03"), "loose")
        self.assertEqual(rep["recall"]["overall"]["strict"], 0)
        self.assertEqual(rep["recall"]["overall"]["loose"], 1)

    def test_miss(self):
        rep = rep_for([])
        self.assertTrue(all(r["status"] == "missed" for r in rep["defects"]))
        self.assertEqual(rep["recall"]["overall"]["strict"], 0)

    def test_keywords_alone_do_not_count(self):
        rep = rep_for([finding("D-001", "MAJOR", "Не указан срок хранения", "Порог задаётся целым числом от 1 до 100 включительно", 2)])
        self.assertEqual(status(rep, "T-01"), "missed")

    def test_line_range_must_match(self):
        rep = rep_for([finding("D-001", "MAJOR", "Не указан срок хранения", "Срок хранения данных об операциях в документе не указан.", 5)])
        self.assertEqual(status(rep, "T-01"), "missed")

    def test_word_share_paraphrase(self):
        quote = "данных об операциях срок хранения в документе указан не"
        rep = rep_for([finding("D-001", "MAJOR", "Не указан срок хранения", quote, 3)])
        self.assertEqual(status(rep, "T-01"), "strict")

    def test_overbroad_quote_ignored(self):
        rep = rep_for([finding("D-001", "MAJOR", "Не указан срок хранения", DOC * 3, 1)])
        self.assertEqual(status(rep, "T-01"), "missed")

    def test_false_alarm_on_good_anchor(self):
        rep = rep_for([finding("D-001", "MAJOR", "Порог неясен", "Порог задаётся целым числом от 1 до 100 включительно", 2)])
        self.assertEqual([f["fid"] for f in rep["false_alarms"]], ["D-001"])
        self.assertEqual(rep["unmatched"], [])

    def test_minor_on_good_anchor_is_not_false_alarm(self):
        rep = rep_for([finding("D-001", "MINOR", "Порог неясен", "Порог задаётся целым числом от 1 до 100 включительно", 2)])
        self.assertEqual(rep["false_alarms"], [])
        self.assertEqual(len(rep["unmatched"]), 1)

    def test_confirming_finding_on_good_line_is_exempt(self):
        key = json.loads(json.dumps(KEY))
        key["defects"][0]["anchors"] = ["Порог задаётся целым числом от 1 до 100 включительно"]
        key["defects"][0]["keywords"] = [["порог"], ["шаг"]]
        res = result([finding("D-001", "MAJOR", "Не задан шаг порога", "Порог задаётся целым числом от 1 до 100 включительно", 2)])
        rep = score.score_result(res, key, DOC)
        self.assertEqual(rep["false_alarms"], [])
        self.assertEqual(rep["false_alarms_exempt"][0]["confirms"], ["T-01"])
        self.assertEqual(status(rep, "T-01"), "strict")

    def test_advisory_ignored(self):
        rep = rep_for([finding("D-001", "MAJOR", "Не указан срок хранения", "Срок хранения данных об операциях в документе не указан.", 3, advisory=True)])
        self.assertEqual(status(rep, "T-01"), "missed")
        self.assertEqual(rep["counts"]["advisory"], 1)
        self.assertEqual(rep["counts"]["findings_open"], 0)

    def test_rejected_ignored(self):
        rep = rep_for([finding("D-001", "MAJOR", "Не указан срок хранения", "Срок хранения данных об операциях в документе не указан.", 3, status="rejected")])
        self.assertEqual(status(rep, "T-01"), "missed")

    def test_severity_agreement_directions(self):
        rep = rep_for([finding("D-001", "CRITICAL", "Не указан срок хранения", "Срок хранения данных об операциях в документе не указан.", 3),
                       finding("D-002", "MAJOR", "Ошибка при коде 200", "Ошибка суммы возвращается с кодом 200 вместо кода ошибки.", 4)])
        self.assertEqual(rep["severity_agreement"], {"same": 0, "tool_higher": 1, "tool_lower": 1})

    def test_unmatched_listed_with_line(self):
        rep = rep_for([finding("D-009", "MAJOR", "Что-то постороннее", "Месяц считается по дате", 1)])
        self.assertEqual(rep["unmatched"], [{"fid": "D-009", "severity": "MAJOR", "title": "Что-то постороннее", "line": 1}])

    def test_files_round_trip_and_thresholds(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "doc.md").write_text(DOC, encoding="utf-8")
            (tmp / "doc.md.key.json").write_text(json.dumps(KEY, ensure_ascii=False), encoding="utf-8")
            (tmp / "r.json").write_text(json.dumps(result([])), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(score.main([str(tmp / "r.json"), "--key", str(tmp / "doc.md.key.json")]), 0)
            self.assertIn("Полнота", out.getvalue())
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(score.main([str(tmp / "r.json"), "--key", str(tmp / "doc.md.key.json"), "--json"]), 0)
            self.assertEqual(json.loads(out.getvalue())["recall"]["overall"]["total"], 3)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(score.main([str(tmp / "r.json"), "--key", str(tmp / "doc.md.key.json"), "--min-strict", "0.5"]), 2)
                self.assertEqual(score.main([str(tmp / "r.json"), "--key", str(tmp / "doc.md.key.json"), "--min-organisers", "0.1"]), 2)
                self.assertEqual(score.main([str(tmp / "r.json"), "--key", str(tmp / "doc.md.key.json"), "--min-strict", "50"]), 1)
            other = result([])
            other["run"]["doc_name"] = "another.md"
            (tmp / "o.json").write_text(json.dumps(other), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(score.main([str(tmp / "o.json"), "--key", str(tmp / "doc.md.key.json")]), 1)


class CheckKeys(unittest.TestCase):
    def test_all_five_keys_valid(self):
        keys = sorted((EVAL / "docs").glob("*.key.json"))
        self.assertEqual(len(keys), 5)
        for k in keys:
            self.assertEqual(check_keys.check_key(k), [], k.name)

    def test_organisers_key_shape(self):
        key = json.loads((EVAL / "docs" / "requirements.md.key.json").read_text(encoding="utf-8"))
        self.assertEqual(key["document"], "../../docs/requirements.md")
        self.assertEqual(len(key["defects"]), 52)
        self.assertEqual(sum(1 for d in key["defects"] if d["origin"] == "organisers"), 10)
        self.assertEqual([d["id"] for d in key["defects"]][:2], ["D-01", "D-02"])

    def test_detects_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "doc.md").write_text(DOC, encoding="utf-8")
            bad = json.loads(json.dumps(KEY))
            bad["defects"][0]["anchors"] = ["этого текста нет в документе вообще"]
            bad["defects"][1]["keywords"] = [["Ошибк"], ["(не закрыта"]]
            bad["defects"][1]["severity"] = "HUGE"
            bad["defects"][2]["id"] = "T-01"
            bad["defects"][2]["anchors"] = ["коротко"]
            (tmp / "doc.md.key.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            text = "\n".join(check_keys.check_key(tmp / "doc.md.key.json"))
            for needle in ("не найден в документе", "не в нижнем регистре", "не компилируется", "неизвестный severity", "повторяется", "длина якоря"):
                self.assertIn(needle, text)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(check_keys.main([str(tmp)]), 1)


class RunAll(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._saved = (run_all.RESULTS_DIR, run_all.RESULTS_MD)
        run_all.RESULTS_DIR = Path(self.tmp.name) / "results"
        run_all.RESULTS_MD = Path(self.tmp.name) / "RESULTS.md"
        self.addCleanup(lambda: setattr(run_all, "RESULTS_DIR", self._saved[0]))
        self.addCleanup(lambda: setattr(run_all, "RESULTS_MD", self._saved[1]))
        self.calls: list[tuple[list, dict]] = []

    def runner(self, cmd, env):
        self.calls.append((cmd, env))
        doc = Path(cmd[cmd.index("--input") + 1])
        out = Path(cmd[cmd.index("--output") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("# отчёт\n", encoding="utf-8")
        provider = cmd[cmd.index("--provider") + 1]
        out.with_suffix(".json").write_text(json.dumps({
            "run": {"provider": provider, "model": "stub-model", "doc_name": doc.name, "cost_usd": 0.5, "duration_s": 2.0},
            "requirements": [{"rid": "REQ-001"}], "findings": []}), encoding="utf-8")
        return 0

    def test_offline_run_all_five(self):
        buf = io.StringIO()
        rc = run_all.main([], runner=self.runner, out=buf)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.calls), 5)
        first_cmd, env = self.calls[0]
        self.assertEqual(env.get("REVIZOR_NO_DOTENV"), "1")
        self.assertIn("--provider", first_cmd)
        self.assertEqual(first_cmd[first_cmd.index("--provider") + 1], "offline")
        self.assertTrue(first_cmd[first_cmd.index("--output") + 1].endswith("requirements.offline.md"))
        self.assertIn("Запланированные команды", buf.getvalue())
        md = run_all.RESULTS_MD.read_text(encoding="utf-8")
        self.assertIn("## Провайдер `offline`, модель `stub-model`", md)
        self.assertIn("Слепые вместе (4)", md)
        self.assertIn("Честные оговорки", md)
        self.assertTrue((run_all.RESULTS_DIR / "requirements.offline.score.json").exists())

    def test_non_offline_refused_without_flag(self):
        buf = io.StringIO()
        rc = run_all.main(["--provider", "anthropic"], runner=self.runner, out=buf)
        self.assertEqual(rc, 2)
        self.assertEqual(self.calls, [])
        self.assertIn("main.py", buf.getvalue())
        self.assertIn("--i-accept-cost", buf.getvalue())
        self.assertFalse(run_all.RESULTS_MD.exists())

    def test_non_offline_with_flag_passes_model_and_depth(self):
        rc = run_all.main(["--provider", "anthropic", "--model", "m1", "--depth", "fast", "--only", "requirements", "--i-accept-cost"],
                          runner=self.runner, out=io.StringIO())
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.calls), 1)
        cmd, _env = self.calls[0]
        self.assertEqual(cmd[cmd.index("--model") + 1], "m1")
        self.assertEqual(cmd[cmd.index("--depth") + 1], "fast")
        self.assertEqual(cmd[cmd.index("--provider") + 1], "anthropic")

    def test_failed_run_reported(self):
        def bad(cmd, env):
            return 3
        buf = io.StringIO()
        rc = run_all.main(["--only", "requirements.md"], runner=bad, out=buf)
        self.assertEqual(rc, 1)
        self.assertIn("Прогон не удался", buf.getvalue())
        self.assertIn("прогон не удался: requirements.md", run_all.RESULTS_MD.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
