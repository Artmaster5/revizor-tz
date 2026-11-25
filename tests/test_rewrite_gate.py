"""The gate for proposed rewrites (revizor/rewrite_gate.py): one test group per condition, invented texts only."""
import unittest

from revizor import rewrite_gate as g, sidefiles
from revizor.model import Finding, Question, Quote, Requirement, Result, RunInfo

LOAN = ("Срок выдачи книги — 14 дней. Продлить выдачу можно один раз на 7 дней. "
        "За просрочку читатель платит 50 ₸ в сутки. Абонемент с кодом ABON_VIP продлению не подлежит.")
OTHER = "Администратор получает письмо на адрес из поля notify_email, когда в каталоге появляется новая книга."


def req(rid, text, start):
    return Requirement(rid=rid, doc_id="", section="", kind="functional", quote=text, summary="", block_ids=[],
                       line_start=start, line_end=start)


def fin(fid, rid, start, wording, *, title="Неясно", problem="Требование неоднозначно", qtext=None, qid="",
        req_ids=None):
    q = Quote(text=qtext or "Срок выдачи книги", line_start=start, line_end=start, rid=rid)
    return Finding(scope="requirement", criterion="unambiguity", severity="MAJOR", title=title, problem=problem,
                   recommendation="Уточнить", quotes=[q], req_ids=req_ids or [rid], fid=fid, question_id=qid,
                   fixed_wording=wording)


def result(reqs, findings, questions=()):
    return Result(run=RunInfo(doc_name="d.md"), requirements=list(reqs), findings=list(findings),
                  questions=list(questions))


class Base(unittest.TestCase):
    def setUp(self):
        self.r1, self.r2 = req("REQ-001", LOAN, 1), req("REQ-002", OTHER, 2)

    def decide(self, wording, **kw):
        f = fin("D-001", "REQ-001", 1, wording, **kw)
        rows, held = g.plan(result([self.r1, self.r2], [f]))
        return rows, held


GOOD = ("Срок выдачи книги — 14 дней. Продлить выдачу можно один раз на 7 дней. "
        "За просрочку читатель платит 50 ₸ в сутки, начиная со дня, следующего за последним днём срока. "
        "Абонемент с кодом ABON_VIP продлению не подлежит.")


class RightTarget(Base):
    def test_same_requirement_passes(self):
        rows, held = self.decide(GOOD)
        self.assertEqual((len(rows), len(held)), (1, 0))

    def test_rewrite_of_another_requirement_is_withheld(self):
        rows, held = self.decide(OTHER)
        self.assertEqual(len(rows), 0)
        self.assertIn("другое требование", " ".join(held[0].reasons))

    def test_first_quote_in_another_requirement_names_that_requirement(self):
        f = fin("D-001", "REQ-002", 2, GOOD, req_ids=["REQ-001", "REQ-002"])
        rows, held = g.plan(result([self.r1, self.r2], [f]))
        self.assertEqual(held[0].rid, "REQ-002")  # the first quote decides, not the first id

    def test_threshold_edges(self):
        t = "Читатель получает книгу на абонементе библиотеки после предъявления билета регистратору"
        keep_half = "Читатель получает книгу абонемента, а регистратор проверяет иное"
        self.assertGreaterEqual(g._overlap(g.stems(t), g.stems(keep_half)), g.MIN_OVERLAP)
        self.assertLess(g._overlap(g.stems(t), g.stems("Регистратор печатает отчёт")), g.MIN_OVERLAP)


class NothingLost(Base):
    def test_lost_number_with_unit(self):
        rows, held = self.decide(GOOD.replace("14 дней", "срок по правилам библиотеки"))
        self.assertEqual(len(rows), 0)
        self.assertIn("14", " ".join(held[0].reasons))

    def test_lost_identifier(self):
        rows, held = self.decide(GOOD.replace("ABON_VIP", "особого вида"))
        self.assertEqual(len(rows), 0)
        self.assertIn("abon_vip", " ".join(held[0].reasons))

    def test_wrong_unit_counts_as_lost(self):
        rows, held = self.decide(GOOD.replace("14 дней", "14 часов"))
        self.assertEqual(len(rows), 0)

    def test_value_named_by_the_finding_may_change(self):
        rows, _ = self.decide(GOOD.replace("50 ₸", "30 ₸"), title="Размер штрафа 50 ₸ не согласован",
                              problem="Штраф 50 ₸ в сутки противоречит тарифу")
        self.assertEqual(len(rows), 0)  # 30 is invented
        rows, _ = self.decide(GOOD.replace("50 ₸ в сутки", "⟦уточнить: размер штрафа — В-03⟧ в сутки"),
                              title="Размер штрафа 50 ₸ не согласован")
        self.assertEqual(len(rows), 1)

    def test_whole_requirement_quote_exempts_nothing(self):
        rows, _ = self.decide(GOOD.replace("14 дней", "срок по правилам"), qtext=LOAN)
        self.assertEqual(len(rows), 0)

    def test_leading_numbering_may_go(self):
        r = req("REQ-001", "2.3. " + LOAN, 1)
        f = fin("D-001", "REQ-001", 1, GOOD)
        rows, _ = g.plan(result([r], [f]))
        self.assertEqual(len(rows), 1)


class NoShrinking(Base):
    def test_four_rules_into_one_is_withheld(self):
        rows, held = self.decide("Срок выдачи книги — 14 дней, читатель платит 50 ₸ в сутки, абонемент ABON_VIP, 7 дней.")
        self.assertEqual(len(rows), 0)
        self.assertIn("правил", " ".join(held[0].reasons))

    def test_one_sentence_less_is_allowed_when_not_short(self):
        two = "Читатель возвращает книгу в срок. Срок выдачи книги — 14 дней."
        r = req("REQ-001", two, 1)
        f = fin("D-001", "REQ-001", 1, "Читатель возвращает книгу в срок, срок выдачи книги — 14 дней.")
        self.assertEqual(len(g.plan(result([r], [f]))[0]), 1)

    def test_single_sentence_requirement_is_not_checked_for_length(self):
        r = req("REQ-001", "Срок выдачи книги — 14 дней.", 1)
        f = fin("D-001", "REQ-001", 1, "Срок выдачи книги — 14 дней.")
        self.assertEqual(len(g.plan(result([r], [f]))[0]), 1)


class NoInvention(Base):
    def test_new_number_is_withheld_with_a_gap_marker(self):
        rows, held = self.decide(GOOD.replace("в сутки,", "в сутки, но не более 500 ₸,"))
        self.assertEqual(len(rows), 0)
        self.assertIn("500", " ".join(held[0].reasons))
        self.assertIn("⟦уточнить", " ".join(held[0].gaps))

    def test_new_field_name_is_withheld(self):
        rows, held = self.decide(GOOD + " Поле penalty_total хранит итог.")
        self.assertEqual(len(rows), 0)

    def test_value_from_the_authors_answer_is_allowed(self):
        f = fin("D-001", "REQ-001", 1, GOOD.replace("в сутки,", "в сутки, но не более 500 ₸,"), qid="В-01")
        q = Question(qid="В-01", text="Предел штрафа?", why="", addressee="analyst", answer="Не более 500 ₸")
        rows, _ = g.plan(result([self.r1, self.r2], [f], [q]))
        self.assertEqual(len(rows), 1)

    def test_gap_marker_is_not_an_invention(self):
        rows, _ = self.decide(GOOD.replace("в сутки,", "в сутки, но не более ⟦уточнить: предел штрафа — В-01⟧,"))
        self.assertEqual(len(rows), 1)


class OnePerRequirement(Base):
    def test_two_rewrites_of_the_same_sentence_print_none(self):
        a = fin("D-001", "REQ-001", 1, GOOD)
        b = fin("D-002", "REQ-001", 1, GOOD.replace("начиная со дня, следующего", "начиная с дня после"))
        rows, held = g.plan(result([self.r1, self.r2], [a, b]))
        self.assertEqual(rows, [])
        self.assertEqual(held[0].fids, ["D-001", "D-002"])

    def test_rewrites_of_different_sentences_are_composed(self):
        a = fin("D-001", "REQ-001", 1, LOAN.replace("Срок выдачи книги — 14 дней.",
                                                    "Срок выдачи книги — 14 календарных дней со дня выдачи."))
        b = fin("D-002", "REQ-001", 1, LOAN.replace("Продлить выдачу можно один раз на 7 дней.",
                                                    "Читатель может продлить выдачу один раз на 7 дней."))
        rows, held = g.plan(result([self.r1, self.r2], [a, b]))
        self.assertEqual(held, [])
        self.assertTrue(rows[0].composed)
        self.assertIn("календарных дней", rows[0].after)
        self.assertIn("Читатель может продлить", rows[0].after)
        self.assertIn("ABON_VIP", rows[0].after)

    def test_same_text_for_two_requirements_stays_with_the_better_fit(self):
        a = fin("D-001", "REQ-001", 1, GOOD)
        b = fin("D-002", "REQ-002", 2, GOOD)
        rows, held = g.plan(result([self.r1, self.r2], [a, b]))
        self.assertEqual([r.rid for r in rows], ["REQ-001"])
        self.assertEqual(held[0].rid, "REQ-002")


class SideFile(Base):
    def test_apply_clears_wording_records_reason_and_fixed_md_counts(self):
        good = fin("D-001", "REQ-001", 1, GOOD)
        bad = fin("D-002", "REQ-002", 2, "Администратор получает письмо.", qid="В-01", req_ids=["REQ-002"])
        q = Question(qid="В-01", text="Какое письмо?", why="", addressee="analyst")
        res = result([self.r1, self.r2], [good, bad], [q])
        self.assertEqual(g.apply(res), 1)
        self.assertEqual(bad.fixed_wording, "")
        self.assertEqual(good.fixed_wording, GOOD)
        self.assertTrue(any(a.get("what") == "rewrite_withheld" for a in res.audit))
        text = sidefiles.fixed_md(res)
        self.assertIn("предложено формулировок 2, напечатано 1, не напечатано 1", text)
        self.assertIn("notify", text)  # the original stays in the row
        self.assertIn("В-01", text)
        self.assertEqual(g.apply(res), 0)  # idempotent
        self.assertIn("не напечатано 1", sidefiles.fixed_md(res))


if __name__ == "__main__":
    unittest.main()
