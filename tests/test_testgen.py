"""Acceptance-test drafts: boundary generator, blocking by open questions, the `tests` stage, section 8, tests.md."""
from __future__ import annotations

import unittest

from revizor import report_md, sidefiles, testgen
from revizor.llm import FakeBackend
from revizor.model import Finding, Question, Requirement

from tests.dialog_helpers import Base, REQ_DOC, quiet_script


def R(rid, text, doc_id="", kind="functional"):
    return Requirement(rid=rid, doc_id=doc_id, section="", kind=kind, quote=text, summary=text[:80], block_ids=[],
                       line_start=1, line_end=1)


class LimitsTests(unittest.TestCase):
    def test_range_gives_four_values(self):
        ds = testgen.boundary_drafts([R("REQ-001", "Клиент задаёт порог от 1 000 до 1 000 000 включительно.", "Ф-1")])
        self.assertEqual(len(ds), 2)
        inside, outside = ds
        self.assertIn("1 000, 1 000 000", inside.when)
        self.assertIn("999, 1 000 001", outside.when)
        self.assertEqual(outside.then, "")            # the document is silent about what happens outside
        self.assertTrue(inside.then)
        self.assertEqual(inside.kind, "boundary")
        self.assertEqual(outside.blocked_by, [])

    def test_max_min_and_strict_limits(self):
        cases = {
            "Пароль не более 64 символов.": ("64", "65"),
            "Пароль не менее 8 символов.": ("8", "7"),
            "Допускается до 3 попыток.": ("3", "4"),
            "Сумма более 100 тенге.": ("101", "100"),
            "Значение ≤ 5 раз.": ("5", "6"),
        }
        for text, (inside, outside) in cases.items():
            with self.subTest(text):
                a, b = testgen.boundary_drafts([R("REQ-001", text)])
                self.assertIn(inside, a.when)
                self.assertIn(outside, b.when)

    def test_silence_without_limits_and_for_noise(self):
        for text in ("Сервис должен отправлять push клиенту после покупки.",
                     "Версия 1.2.3 от 12.05.2026, см. Ф-12 и REQ-77, код `limit 500`, ответ 200 OK.",
                     "Документ подготовлен в 2026 году, раздел 3."):
            with self.subTest(text):
                self.assertEqual(testgen.boundary_drafts([R("REQ-001", text)]), [])

    def test_rejection_text_in_requirement_fills_then(self):
        ds = testgen.boundary_drafts([R("REQ-001", "Порог не более 500. Большее значение отклоняется с ошибкой.")])
        self.assertIn("отклоня", ds[1].then)


class FinalizeTests(unittest.TestCase):
    def setUp(self):
        self.reqs = [R("REQ-001", "A"), R("REQ-002", "B")]
        f = Finding(scope="requirement", criterion="completeness", severity="MAJOR", title="Нет ошибки", problem="",
                    recommendation="", req_ids=["REQ-002"], fid="D-001", question_id="В-01")
        self.f = f
        self.q = Question(qid="В-01", text="?", why="", addressee="analyst")

    def drafts(self):
        return [testgen.TestDraft(tid="", title="t2", kind="positive", req_ids=["REQ-002"], then="ok"),
                testgen.TestDraft(tid="", title="t1", kind="positive", req_ids=["REQ-001"], then="ok")]

    def test_ids_are_deterministic_and_follow_document_order(self):
        a = testgen.finalize(self.drafts(), self.reqs, [self.f], [self.q])
        b = testgen.finalize(list(reversed(self.drafts())), self.reqs, [self.f], [self.q])
        self.assertEqual([(t.tid, t.title) for t in a], [("Т-01", "t1"), ("Т-02", "t2")])
        self.assertEqual([(t.tid, t.title) for t in b], [("Т-01", "t1"), ("Т-02", "t2")])

    def test_blocked_by_open_major_until_answered(self):
        a = testgen.finalize(self.drafts(), self.reqs, [self.f], [self.q])
        self.assertEqual(a[1].blocked_by, ["В-01"])
        self.assertEqual(a[0].blocked_by, [])
        self.q.answer = "вариант а"
        b = testgen.finalize(self.drafts(), self.reqs, [self.f], [self.q])
        self.assertEqual(b[1].blocked_by, [])

    def test_minor_does_not_block(self):
        self.f.severity = "MINOR"
        self.assertEqual(testgen.finalize(self.drafts(), self.reqs, [self.f], [self.q])[1].blocked_by, [])

    def test_merge_drops_duplicates_of_deterministic_drafts(self):
        det = testgen.boundary_drafts([R("REQ-001", "Порог от 10 до 20.")])
        dup = testgen.TestDraft(tid="", title="Границы 10 и 20", kind="boundary", req_ids=["REQ-001"],
                                when="значения 10, 20", then="x", origin="model")
        other = testgen.TestDraft(tid="", title="Пустое значение", kind="negative", req_ids=["REQ-001"], origin="model")
        merged = testgen.merge(det, [dup, other])
        self.assertEqual(len(merged), len(det) + 1)

    def test_parse_model_drops_unknown_ids(self):
        audit = []
        out = testgen.parse_model({"tests": [
            {"title": "ok", "req_ids": ["REQ-001", "REQ-404"], "then": "x", "depends_on": "В-01", "finding_ids": ["D-001", "D-9"]},
            {"title": "orphan", "req_ids": ["REQ-404"]}, {"title": "", "req_ids": ["REQ-001"]}, "junk"],
        }, self.reqs, [self.f], [self.q], audit)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].req_ids, ["REQ-001"])
        self.assertEqual(out[0].blocked_by, ["В-01"])
        self.assertEqual(out[0].finding_ids, ["D-001"])


class StageTests(Base):
    def test_offline_gives_useful_drafts_for_the_requirements_doc(self):
        res = self.run_offline()
        self.assertGreaterEqual(len(res.tests), 10)
        ready = [t for t in res.tests if t.then and not t.blocked_by]
        blocked = [t for t in res.tests if t.blocked_by]
        self.assertGreaterEqual(len(ready), 5)
        self.assertGreaterEqual(len(blocked), 3)
        self.assertEqual([t.tid for t in res.tests], [f"Т-{k:02d}" for k in range(1, len(res.tests) + 1)])
        qids = {q.qid for q in res.questions}
        self.assertTrue(all(b in qids for t in blocked for b in t.blocked_by))
        again = self.run_offline()
        self.assertEqual([(t.tid, t.title) for t in again.tests], [(t.tid, t.title) for t in res.tests])

    def test_report_section_and_side_file(self):
        res = self.run_offline()
        md = report_md.render(res)
        self.assertIn("## 8. Черновики приёмочных тестов", md)
        self.assertIn("Можно написать уже сейчас:", md)
        self.assertIn("заблокирован: ждёт ответа", md)
        self.assertIn("Тестов можно написать уже сейчас", md)
        tm = sidefiles.tests_md(res)
        self.assertIn("**Дано:**", tm)
        self.assertIn("**Тогда:** ожидаемый результат в документе не описан", tm)
        self.assertIn("Нельзя дописать, пока не получен ответ на В-", tm)
        out = self.dir / "r.md"
        self.assertEqual(self.cli("--provider", "offline", "-i", REQ_DOC, "-o", str(out)), 0)
        self.assertTrue((self.dir / "r.tests.md").exists())

    def test_model_stage_merges_and_respects_depth(self):
        def tests_answer(user):
            return {"tests": [{"title": "Пустой порог", "kind": "negative", "req_ids": ["REQ-005", "REQ-999"],
                               "given": "g", "when": "w", "then": "", "depends_on": "В-04"}]}
        res, b = self.run_fake(quiet_script(tests=tests_answer))
        self.assertIn("tests", [s for s, _, _ in b.log])
        self.assertTrue(any(t.origin == "model" and t.title == "Пустой порог" for t in res.tests))
        self.assertTrue(any(t.origin == "rule" for t in res.tests))
        fast, b2 = self.run_fake(quiet_script(tests=tests_answer), depth="fast")
        self.assertNotIn("tests", [s for s, _, _ in b2.log])
        self.assertTrue(fast.tests)           # the deterministic part is still there

    def test_failing_model_stage_keeps_rule_drafts_and_warns(self):
        res, _ = self.run_fake(quiet_script(tests=RuntimeError("boom")))
        self.assertTrue(res.tests)
        self.assertTrue(any("Черновики тестов" in w for w in res.run.warnings))

    def test_answered_question_unblocks_drafts(self):
        base = self.run_offline()
        blocked = next(t for t in base.tests if t.blocked_by)
        qid = blocked.blocked_by[0]
        from revizor import answers as ans_mod
        res = self.run_offline(answers=lambda qs: ans_mod.from_raw({qid: "а"}, qs))
        same = next(t for t in res.tests if t.title == blocked.title)
        self.assertNotIn(qid, same.blocked_by)


if __name__ == "__main__":
    unittest.main()
