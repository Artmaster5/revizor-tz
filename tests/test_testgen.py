"""Acceptance-test drafts: boundary generator, blocking by open questions, the `tests` stage, section 8, tests.md."""
from __future__ import annotations

import unittest

from revizor import report_md, sidefiles, testgen
from revizor.llm import FakeBackend
from revizor.model import Finding, Question, Quote, Requirement

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
        self.assertIn("допустимо только", outside.then)  # inclusiveness is stated: the expected result is derivable
        self.assertTrue(inside.then)
        self.assertTrue(inside.explicit_limit and outside.explicit_limit)
        self.assertIn("шаг значения не задан: взяты соседние целые", inside.when)
        self.assertEqual(inside.values, ["1 000", "1 000 000"])
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
        step = "Отправить запрос с заполненным полем и получить ответ"
        return [testgen.TestDraft(tid="", title="t2", kind="positive", req_ids=["REQ-002"], when=step, then="ok"),
                testgen.TestDraft(tid="", title="t1", kind="positive", req_ids=["REQ-001"], when=step + " снова", then="ok")]

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


class ReadinessTests(unittest.TestCase):
    """Boundary drafts from an explicit range, disputes, duplicates and template steps (invented texts)."""

    def setUp(self):
        self.req = R("REQ-001", "Размер партии — целое число от 10 до 500 штук включительно.", "Ф-1")
        self.other_q = Question(qid="В-01", text="Какой формат ошибки?", why="", addressee="analyst", req_ids=["REQ-001"])
        self.other_f = Finding(scope="requirement", criterion="completeness", severity="MAJOR", title="Не описан формат ошибки",
                               problem="Формат сообщения не задан", recommendation="", req_ids=["REQ-001"], fid="D-001",
                               question_id="В-01", quotes=[Quote(text="Размер партии", line_start=1, line_end=1)])

    def run_final(self, req, findings=(), questions=(), extra=()):
        audit = []
        out = testgen.finalize(testgen.boundary_drafts([req]) + list(extra), [req], list(findings), list(questions), audit)
        return out, audit

    def test_inclusive_range_is_ready_whatever_other_questions_exist(self):
        out, _ = self.run_final(self.req, [self.other_f], [self.other_q])
        self.assertEqual(len(out), 2)
        self.assertTrue(all(t.then and not t.blocked_by for t in out))
        self.assertIn("10, 500", out[0].when)
        self.assertIn("9, 501", out[1].when)

    def test_exclusive_range_takes_inner_and_border_values(self):
        req = R("REQ-001", "Температура от 10 до 20 градусов не включая границы. Нужны целые значения.")
        req.quote = "Размер от 10 до 20 штук, не включая границы; значения целые."
        out, _ = self.run_final(req)
        self.assertIn("11, 19", out[0].when)
        self.assertIn("10, 20", out[1].when)
        self.assertTrue(all(t.then and not t.blocked_by for t in out))

    def test_range_disputed_by_a_finding_waits_for_that_question_only(self):
        disputed = Finding(scope="requirement", criterion="correctness", severity="MINOR", title="Граница 500 противоречит другому разделу",
                           problem="Верхняя граница 500 не согласована", recommendation="", req_ids=["REQ-001"], fid="D-002",
                           question_id="В-02", quotes=[Quote(text="500 штук", line_start=1, line_end=1)])
        q2 = Question(qid="В-02", text="Верхняя граница 500 или 400?", why="", addressee="analyst", req_ids=["REQ-001"])
        out, _ = self.run_final(self.req, [self.other_f, disputed], [self.other_q, q2])
        self.assertTrue(all(t.blocked_by == ["В-02"] for t in out))

    def test_disputed_range_without_a_question_goes_to_the_list(self):
        disputed = Finding(scope="requirement", criterion="correctness", severity="MINOR", title="Граница 500 спорна", problem="",
                           recommendation="", req_ids=["REQ-001"], fid="D-002")
        out, audit = self.run_final(self.req, [disputed])
        self.assertEqual(out, [])
        self.assertEqual(len(audit[0]["items"]), 2)

    def test_unstated_inclusiveness_is_not_promoted(self):
        req = R("REQ-001", "Размер партии от 10 до 500 штук.")
        out, audit = self.run_final(req, [self.other_f], [self.other_q])
        self.assertTrue(all(not t.explicit_limit for t in out))

    def test_template_step_is_not_a_draft(self):
        t = testgen.TestDraft(tid="", title="Ф-5: что-то", kind="positive", req_ids=["REQ-001"], origin="rule",
                              when="Выполнить действие, описанное в требовании, в том месте, где найдено замечание.", then="")
        out, audit = self.run_final(self.req, extra=[t])
        self.assertFalse(any(x.title == "Ф-5: что-то" for x in out))
        self.assertTrue(any(i["what"] == "Ф-5: что-то" for i in audit[0]["items"]))

    def test_duplicates_collapse(self):
        mk = lambda title: testgen.TestDraft(tid="", title=title, kind="negative", req_ids=["REQ-001"], origin="model",
                                            when="Отправить партию из 0 штук", then="Сервис отклоняет партию с ошибкой")
        out, _ = self.run_final(self.req, extra=[mk("Пустая партия"), mk("Партия без штук")])
        self.assertEqual(sum(1 for x in out if x.kind == "negative"), 1)

    def test_header_counts_ready_and_waiting(self):
        from revizor.model import Result, RunInfo
        out, audit = self.run_final(self.req)
        out[0].blocked_by = ["В-07"]
        res = Result(run=RunInfo(doc_name="d.md"), requirements=[self.req], tests=out, audit=audit)
        text = sidefiles.tests_md(res)
        self.assertIn("Готовы к прогону: 1. Ждут ответа автора: 1 (В-07 — 1)", text)


class StageTests(Base):
    def test_offline_gives_useful_drafts_for_the_requirements_doc(self):
        res = self.run_offline()
        self.assertGreaterEqual(len(res.tests), 10)
        ready = [t for t in res.tests if t.then and not t.blocked_by]
        self.assertGreaterEqual(len(ready), 10)
        self.assertTrue(all(t.when and not testgen.is_template(t) for t in res.tests))  # no template steps are printed
        unver = [x for a in res.audit if a.get("type") == "test_unverifiable" for x in a["items"]]
        self.assertGreaterEqual(len(unver), 3)  # what the document cannot answer is listed, not faked
        self.assertEqual([t.tid for t in res.tests], [f"Т-{k:02d}" for k in range(1, len(res.tests) + 1)])
        qids = {q.qid for q in res.questions}
        self.assertTrue(all(b in qids for x in unver for b in x["blocked_by"]))
        again = self.run_offline()
        self.assertEqual([(t.tid, t.title) for t in again.tests], [(t.tid, t.title) for t in res.tests])

    def test_report_section_and_side_file(self):
        def tests_answer(user):
            return {"tests": [{"title": "Пустой порог", "kind": "negative", "req_ids": ["REQ-005"],
                               "given": "Карта активна", "when": "Отправить запрос сохранения с пустым порогом",
                               "then": "Сервис возвращает ошибку проверки", "depends_on": "В-04"}]}
        res, _ = self.run_fake(quiet_script(tests=tests_answer))
        md = report_md.render(res)
        self.assertIn("## 8. Черновики приёмочных тестов", md)
        tm = sidefiles.tests_md(res)
        self.assertIn("заблокирован: ждёт ответа", tm)
        self.assertIn("**Дано:**", tm)
        self.assertIn("Готовы к прогону:", tm)
        self.assertIn("Что нельзя проверить, пока нет ответа", tm)
        self.assertIn("Нельзя дописать, пока не получен ответ на В-", tm)
        out = self.dir / "r.md"
        self.assertEqual(self.cli("--provider", "offline", "-i", REQ_DOC, "-o", str(out)), 0)
        self.assertTrue((self.dir / "r.tests.md").exists())

    def test_model_stage_merges_and_respects_depth(self):
        def tests_answer(user):
            return {"tests": [{"title": "Пустой порог", "kind": "negative", "req_ids": ["REQ-005", "REQ-999"],
                               "given": "g", "when": "Отправить пустой порог в запросе", "then": "Сервис возвращает ошибку",
                               "depends_on": "В-04"}]}
        res, b = self.run_fake(quiet_script(tests=tests_answer))
        self.assertIn("tests", [s for s, _, _ in b.log])
        self.assertTrue(any(t.origin == "model" and t.title == "Пустой порог" for t in res.tests))
        self.assertTrue(any(t.origin == "rule" for t in res.tests))
        fast, b2 = self.run_fake(quiet_script(tests=tests_answer), depth="fast")
        self.assertNotIn("tests", [s for s, _, _ in b2.log])
        self.assertTrue(fast.tests)           # the deterministic part is still there

    def test_model_draft_without_result_goes_to_the_list(self):
        def tests_answer(user):
            return {"tests": [{"title": "Пустой порог", "kind": "negative", "req_ids": ["REQ-005"], "given": "g",
                               "when": "w", "then": "", "depends_on": "В-04"}]}
        res, _ = self.run_fake(quiet_script(tests=tests_answer))
        self.assertFalse(any(t.title == "Пустой порог" for t in res.tests))

    def test_failing_model_stage_keeps_rule_drafts_and_warns(self):
        res, _ = self.run_fake(quiet_script(tests=RuntimeError("boom")))
        self.assertTrue(res.tests)
        self.assertTrue(any("Черновики тестов" in w for w in res.run.warnings))

    def test_answered_question_unblocks_drafts(self):
        req = R("REQ-001", "Когда клиент вводит неверный код, сервис показывает сообщение об ошибке и просит повторить ввод.")
        d = testgen.TestDraft(tid="", title="Неверный код", kind="negative", req_ids=["REQ-001"], origin="model",
                              when="Ввести неверный код подтверждения", then="Показано сообщение об ошибке")
        f = Finding(scope="requirement", criterion="completeness", severity="MAJOR", title="x", problem="", recommendation="",
                    req_ids=["REQ-001"], fid="D-001", question_id="В-01")
        q = Question(qid="В-01", text="?", why="", addressee="analyst", req_ids=["REQ-001"])
        self.assertEqual(testgen.finalize([d], [req], [f], [q])[0].blocked_by, ["В-01"])
        q.answer = "а"
        self.assertEqual(testgen.finalize([testgen.TestDraft(**{**d.to_dict()})], [req], [f], [q])[0].blocked_by, [])


if __name__ == "__main__":
    unittest.main()
