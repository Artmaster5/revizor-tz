import json
import tempfile
import unittest
from pathlib import Path

from revizor import cli, compare


def finding(fid, rule, quote, title="Дефект", sev="MAJOR", crit="clarity", status="open"):
    return {"fid": fid, "severity": sev, "title": title, "criterion": crit, "rule_ids": [rule], "status": status,
            "quotes": [{"text": quote, "line_start": 1, "line_end": 1}]}


def report(findings, index, blockers, doc="tz.md"):
    return {"format": "revizor-report/1", "findings": findings, "verdict": "BLOCKED",
            "run": {"doc_name": doc, "started_at": "2026-11-27T10:00:00"},
            "summary": {"readiness": index}, "readiness": {"dev": {"blockers": blockers}}}


OLD = report([finding("D-001", "vague-time", "Сервис отвечает быстро", "Нет числа", crit="measurability"),
              finding("D-002", "get-mutates", "Метод GET сохраняет порог", "GET меняет данные", "CRITICAL", "correctness"),
              finding("D-003", "tbd", "Ждём ответа банка", "Заглушка", "MINOR", "completeness")], 40, ["D-002"])
NEW = report([finding("D-005", "tbd", "Ждём ответа банка", "Заглушка", "MINOR", "completeness"),   # осталась, другой номер
              finding("D-006", "no-retry", "Повтор без числа попыток", "Нет попыток", "MINOR", "reliability")],  # новая
             55, [], "tz_v2.md")


class CompareTest(unittest.TestCase):
    def test_closed_remaining_new(self):
        c = compare.compare(OLD, NEW)
        self.assertEqual([x["fid"] for x in c["closed"]], ["D-001", "D-002"])
        self.assertEqual([x["fid"] for x in c["remaining"]], ["D-005"])
        self.assertEqual(c["remaining"][0]["old_fid"], "D-003")
        self.assertEqual([x["fid"] for x in c["new_items"]], ["D-006"])
        self.assertEqual(compare.summary_line(c), "Закрыто 2 · Осталось 1 · Новых 1")
        self.assertEqual((c["old"]["index"], c["new"]["index"], c["old"]["blockers"], c["new"]["blockers"]), (40, 55, 1, 0))
        self.assertIn("Метод GET сохраняет порог", c["closed"][1]["quote"])

    def test_line_shift_does_not_matter(self):
        shifted = json.loads(json.dumps(OLD))
        for f in shifted["findings"]:
            f["quotes"][0]["line_start"] = 300
        c = compare.compare(OLD, shifted)
        self.assertEqual((len(c["closed"]), len(c["remaining"]), len(c["new_items"])), (0, 3, 0))

    def test_changed_quote_wording_still_matches(self):
        v2 = report([finding("D-009", "vague-time", "Сервис отвечает быстро.", "Нет числа", crit="measurability")], 50, [])
        c = compare.compare(report(OLD["findings"][:1], 40, []), v2)
        self.assertEqual(len(c["remaining"]), 1)

    def test_advisory_and_dropped_ignored(self):
        n = json.loads(json.dumps(NEW))
        n["findings"].append(finding("D-007", "x", "Что-то", status="dropped"))
        self.assertEqual(len(compare.compare(OLD, n)["new_items"]), 1)

    def test_renderers(self):
        c = compare.compare(OLD, NEW)
        md, html = compare.to_markdown(c), compare.to_html(c)
        self.assertIn("**Закрыто 2 · Осталось 1 · Новых 1**", md)
        self.assertIn("Индекс готовности: 40 → 55", md)
        self.assertIn("Блокеры: 1 → 0", md)
        self.assertIn("Закрыто 2", html)
        self.assertNotIn("http", html)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "a.json").write_text(json.dumps(OLD), encoding="utf-8")
            (d / "b.json").write_text(json.dumps(NEW), encoding="utf-8")
            lines = []
            rc = cli.main(["--compare", str(d / "a.json"), str(d / "b.json"), "-o", str(d / "out.md")], env={}, out=lines.append)
            self.assertEqual(rc, 0)
            self.assertTrue((d / "out.compare.md").exists() and (d / "out.compare.html").exists())
            self.assertIn("Закрыто 2 · Осталось 1 · Новых 1", lines[0])
            rc = cli.main(["--compare", str(d / "a.json"), str(d / "нет.json")], env={}, out=lines.append)
            self.assertEqual(rc, 1)
            (d / "bad.json").write_text("{}", encoding="utf-8")
            self.assertEqual(cli.main(["--compare", str(d / "a.json"), str(d / "bad.json")], env={}, out=lines.append), 1)


if __name__ == "__main__":
    unittest.main()
