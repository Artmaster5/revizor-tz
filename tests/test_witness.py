"""«Пример-свидетель»: the program keeps a case only with both quotes found in the document word for word."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from revizor import loader, report_json, report_md, witness
from revizor.llm import FakeBackend
from revizor.model import Finding, Quote
from revizor.quotes import QuoteIndex

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "requirements.md"
REPORT = ROOT / "reports" / "report.json"

A = "Месяц определяется по дате совершения покупки в Алматы"
B = "Провести 1 февраля в 00:03 по Алматы"
GOOD = {"fid": "D-022", "case": "Покупка 31 января в 23:58, проведена 1 февраля в 00:03.",
        "a_quote": A, "a_outcome": "покупка январская", "b_quote": B, "b_outcome": "растёт февральский счётчик"}


def finding(fid="D-022", criterion="set_consistency", severity="CRITICAL", blocker="contradiction", status="open"):
    return Finding(scope="set", criterion=criterion, severity=severity, title="Месяц покупки", problem="Разный месяц.",
                   recommendation="", quotes=[Quote(text=A, line_start=1, line_end=1)], fid=fid, status=status,
                   blocker_reason=blocker if severity == "CRITICAL" else "")


class WitnessCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = loader.load(str(DOC))
        cls.qi = QuoteIndex(cls.doc)

    def test_quotes_exist_in_the_sample(self):
        self.assertIsNotNone(self.qi.find(A))
        self.assertIsNotNone(self.qi.find(B))

    def test_targets_pick_open_contradictions_most_severe_first(self):
        fs = [finding("D-003", severity="MINOR"), finding("D-001", criterion="unambiguity", severity="MAJOR", blocker=""),
              finding("D-022"), finding("D-030", status="rejected"),
              finding("D-040", criterion="completeness", blocker="contradiction")]
        self.assertEqual([f.fid for f in witness.targets(fs)], ["D-022", "D-040", "D-003"])

    def test_good_case_is_kept_with_document_text_and_lines(self):
        kept, dropped = witness.check({"witnesses": [GOOD]}, [finding()], self.qi, model="m")
        self.assertEqual(dropped, [])
        w = kept["D-022"]
        self.assertIn(A, w["a"]["text"])
        self.assertGreater(w["b"]["line"], 0)
        self.assertEqual(w["by"], "m")

    def test_bad_cases_are_dropped_with_reason(self):
        bad = [dict(GOOD, fid="D-999"),
               dict(GOOD, a_quote="Месяц определяется по дате проведения покупки"),
               dict(GOOD, b_quote=A),
               dict(GOOD, b_outcome="Покупка январская!"),
               dict(GOOD, case="коротко")]
        for w in bad:
            kept, dropped = witness.check({"witnesses": [w]}, [finding()], self.qi)
            self.assertEqual(kept, {}, w)
            self.assertEqual(len(dropped), 1, w)
        kept, dropped = witness.check({"witnesses": [GOOD, GOOD]}, [finding()], self.qi)
        self.assertEqual((len(kept), dropped[0]["reason"]), (1, "второй пример к тому же замечанию"))

    def test_card_line(self):
        f = finding()
        kept, _ = witness.check({"witnesses": [GOOD]}, [f], self.qi)
        self.assertEqual(witness.apply([f], kept), 1)
        line = witness.witness_text(f)
        self.assertTrue(line.startswith("Пример-свидетель: Покупка 31 января в 23:58"))
        self.assertIn("  - По строке 38 («Месяц определяется по дате совершения покупки в Алматы»): покупка январская.", line)
        self.assertIn("нашла в документе дословно", line)
        self.assertEqual(witness.witness_text(finding()), "")


class WitnessReportTests(unittest.TestCase):
    def test_saved_report_gets_witness_without_changing_numbers(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import witness as tool  # tools/witness.py

        res = report_json.load_result(REPORT)
        for f in res.findings:  # start from the report without witnesses
            f.witness = None
        res.audit = [a for a in res.audit if not (isinstance(a, dict) and a.get("what") == "witness")]
        before = report_md.render(res, "report.md")
        doc = loader.load(str(DOC))
        target = next(f for f in witness.targets(res.findings) if f.fid == "D-022")
        kept, _ = witness.check({"witnesses": [GOOD]}, [target], QuoteIndex(doc))
        witness.apply(res.findings, kept)
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "report.json"
            paths = tool.write(res, src)
            md = (Path(d) / "report.md").read_text(encoding="utf-8")
            self.assertFalse(any(p.name.endswith(".questions.md") for p in paths))
            again = report_json.load_result(src)
        self.assertIn("Пример-свидетель: Покупка 31 января", md)
        self.assertIn("| Примеры-свидетели | 1 из 12 противоречий; обе цитаты", md)
        self.assertNotIn("модель не вызывалась", md)
        self.assertEqual(next(f for f in again.findings if f.fid == "D-022").witness["case"], GOOD["case"])
        # only the witness lines are new: every line of the old report is still there, in order
        old_lines = [x for x in before.splitlines()]
        extra = set(witness.witness_lines(next(f for f in res.findings if f.fid == "D-022")))
        new_lines = [x for x in md.splitlines() if "Примеры-свидетели" not in x
                     and x not in extra and x[2:] not in extra]
        self.assertEqual(old_lines, new_lines)

    def test_backend_schema_round_trip(self):
        b = FakeBackend({witness.STAGE: {"witnesses": [GOOD]}})
        data, _ = b.complete_json("s", witness.user_prompt([finding()]), schema=witness.SCHEMA, stage=witness.STAGE)
        self.assertEqual(data["witnesses"][0]["fid"], "D-022")
        self.assertIn("D-022 | CRITICAL | Месяц покупки", b.log[0][2])


if __name__ == "__main__":
    unittest.main()
