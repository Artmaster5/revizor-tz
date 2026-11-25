"""Deterministic merge of near-duplicate findings (merge.merge_duplicates) and its place in the pipeline."""
from __future__ import annotations

import unittest

from revizor import merge
from revizor.model import Finding, FixAction, Question, Quote


def q(text: str, a: int, b: int | None = None, rid: str = "REQ-010") -> Quote:
    return Quote(text=text, line_start=a, line_end=b or a, rid=rid)


def f(fid, crit, sev, title, quote, *, rids=("REQ-010",), qid="", source="model", rule_ids=(), scope="requirement",
      problem="", stage="review") -> Finding:
    return Finding(scope=scope, criterion=crit, severity=sev, title=title, problem=problem or title,
                   recommendation="", quotes=[quote], req_ids=list(rids), source=source, rule_ids=list(rule_ids),
                   stage=stage, fid=fid, question_id=qid)


PUSH = "Сервис передаёт push на отправку в течение минуты после получения события."


def push_pair() -> list[Finding]:
    a = f("D-001", "verifiability", "MAJOR", "Не описан сбой отправки push", q(PUSH, 46), qid="В-01",
          problem="Что происходит при сбое отправки push, не сказано.")
    b = f("D-002", "completeness", "MINOR", "Сбой передачи push на отправку не описан", q("передаёт push на отправку", 46),
          qid="В-01", problem="Нет поведения при сбое передачи push на отправку и повторных попыток, сервис не знает, что делать.")
    return [a, b]


class MergeDuplicates(unittest.TestCase):
    def test_push_failure_pair_merges(self):
        fs = push_pair()
        qs = [Question(qid="В-01", text="?", why="", addressee="analyst", finding_ids=["D-001", "D-002"])]
        audit: list[dict] = []
        n = merge.merge_duplicates(fs, qs, [], audit)
        self.assertEqual(n, 1)
        self.assertEqual(fs[0].status, "open")
        self.assertEqual(fs[1].status, "merged")
        self.assertEqual(fs[1].merged_into, "D-001")
        self.assertEqual(fs[0].severity, "MAJOR")
        # title, problem and level come from one finding (the survivor); the absorbed one leaves a visible trace
        self.assertEqual(fs[0].problem, "Что происходит при сбое отправки push, не сказано.")
        self.assertEqual(fs[0].also, ["Сбой передачи push на отправку не описан"])
        self.assertEqual(len(fs[0].quotes), 2)
        self.assertEqual(qs[0].finding_ids, ["D-001"])
        self.assertTrue(any(a.get("what") == "duplicate_merged_by_code" for a in audit))

    def test_different_criteria_and_quotes_do_not_merge(self):
        a = f("D-001", "verifiability", "MAJOR", "Срок без условий измерения", q(PUSH, 46), qid="В-01")
        b = f("D-002", "completeness", "MAJOR", "Не описан порог для карты", q("Клиент задаёт порог в тенге", 39), qid="В-01")
        fs = [a, b]
        self.assertEqual(merge.merge_duplicates(fs, [], [], []), 0)
        self.assertEqual([x.status for x in fs], ["open", "open"])

    def test_no_shared_requirement_no_merge(self):
        fs = push_pair()
        fs[1].req_ids = ["REQ-011"]
        fs[1].quotes = [q("передаёт push на отправку", 80, rid="REQ-011")]  # far away: not the «two lines apart» case
        self.assertEqual(merge.merge_duplicates(fs, [], [], []), 0)

    def test_same_words_two_lines_apart_merge_without_shared_requirement(self):
        stub = f("D-001", "set_completeness", "CRITICAL", "Раздел «Тестирование требований» не заполнен, вместо "
                 "содержания заглушка", Quote("заполняются тестировщиками", 57, 57, section="A › Тестирование"),
                 rids=(), scope="set", source="rule:empty-section", rule_ids=("empty-section",), stage="rules")
        head = f("D-002", "set_completeness", "MAJOR", "Раздел тестирования не заполнен, приёмка только по примерам",
                 Quote("Требования к тестированию", 54, 54, section="A"), rids=(), scope="set", stage="set_completeness")
        fs = [stub, head]
        self.assertEqual(merge.merge_duplicates(fs, [], [], []), 1)
        self.assertEqual((stub.status, head.status, head.merged_into), ("open", "merged", "D-001"))

    def test_neighbour_rows_about_different_parameters_stay_two(self):
        a = f("D-001", "set_consistency", "MAJOR", "Параметр «amount» описан с разными типами: integer и string",
              Quote("amount | integer", 143, 143, section="API"), rids=(), scope="set", source="rule:param-type-mismatch")
        b = f("D-002", "set_consistency", "MAJOR", "Параметр «enabled» описан с разными типами: integer и string",
              Quote("enabled | integer", 144, 144, section="API"), rids=(), scope="set", source="rule:param-type-mismatch")
        self.assertEqual(merge.merge_duplicates([a, b], [], [], []), 0)

    def test_two_missing_branches_next_to_each_other_stay_two(self):
        a = f("D-001", "completeness", "MAJOR", "Не описано, что происходит при возврате после отправленного push",
              q("Возврат уменьшает расходы месяца", 38, rid="REQ-006"), rids=("REQ-006",))
        b = f("D-002", "completeness", "MINOR", "Не описана ветка «накопленная сумма не ниже нового порога»",
              q("При изменении порога накопленная сумма сохраняется", 40, rid="REQ-008"), rids=("REQ-008",))
        self.assertEqual(merge.merge_duplicates([a, b], [], [], []), 0)

    def test_model_remark_inside_a_rule_finding_merges(self):
        rule = f("D-001", "set_completeness", "MINOR", "Таблица согласующих не заполнена",
                 Quote("Подразделение | ФИО | Подпись", 89, 89, section="Блок согласующих"), rids=(), scope="set",
                 source="rule:empty-table", rule_ids=("empty-table",), stage="rules")
        rule.quotes.append(Quote("Блок согласующих", 87, 87, section="Блок согласующих"))
        model = f("D-002", "completeness", "MINOR", "Согласующие в таблице не указаны",
                  Quote("Подразделение | ФИО | Подпись", 89, 89, section="Блок согласующих"), rids=(), scope="set",
                  stage="set_completeness")
        model.quotes[0] = Quote("Подразделение | ФИО | Подпись", 89, 89, section="Другой раздел")
        self.assertEqual(merge.merge_duplicates([rule, model], [], [], []), 1)
        self.assertEqual(model.merged_into, "D-001")


class FoldFormal(unittest.TestCase):
    def test_minor_singularity_remarks_fold_into_one_set_finding(self):
        fs = [f(f"D-00{k}", "singularity", "MINOR", t, q(f"пункт {k}", 10 * k, rid=f"REQ-00{k}"), rids=(f"REQ-00{k}",),
                qid=("В-0" + str(k)) if k == 2 else "")
              for k, t in ((1, "Два правила в одном пункте"), (2, "Четыре правила в одном пункте"),
                           (3, "В одной ячейке два проверяемых результата"))]
        other = f("D-009", "completeness", "MINOR", "Нет ветки ошибки", q("пункт 1", 10, rid="REQ-001"),
                  rids=("REQ-001",))
        qs = [Question(qid="В-02", text="?", why="", addressee="analyst", finding_ids=["D-002"])]
        audit: list[dict] = []
        head = merge.fold_formal(fs + [other], qs, [], audit)
        self.assertIs(head, fs[0])
        self.assertEqual((head.scope, head.stage, head.severity), ("set", merge.FOLD_STAGE, "MINOR"))
        self.assertEqual(head.title, "В 3 пунктах записано по нескольку утверждений")
        self.assertEqual([x.line_start for x in head.quotes], [10, 20, 30])  # every place, no cap
        self.assertEqual(head.req_ids, ["REQ-001", "REQ-002", "REQ-003"])
        self.assertEqual(head.question_id, "В-02")  # the first one with a question
        self.assertEqual([x.status for x in fs[1:]], ["merged", "merged"])
        self.assertEqual(other.status, "open")  # a remark about content is never folded
        self.assertEqual(qs[0].finding_ids, ["D-001"])
        self.assertTrue(any(a.get("what") == "formal_folded" for a in audit))

    def test_single_or_major_remarks_are_not_folded(self):
        one = [f("D-001", "singularity", "MINOR", "Два правила в одном пункте", q("x", 10))]
        self.assertIsNone(merge.fold_formal(one, [], [], []))
        two = one + [f("D-002", "singularity", "MAJOR", "Три правила в одном пункте", q("y", 20, rid="REQ-011"),
                       rids=("REQ-011",))]
        self.assertIsNone(merge.fold_formal(two, [], [], []))

    def test_rule_finding_survives_and_marks_both(self):
        a = f("D-001", "verifiability", "MAJOR", "Не описан сбой отправки push", q(PUSH, 46), qid="В-01")
        r = f("D-002", "verifiability", "MAJOR", "Сбой отправки push не описан", q(PUSH, 46), qid="В-01",
              source="rule:no-error-path", rule_ids=["no-error-path"], stage="rules")
        fs = [a, r]
        self.assertEqual(merge.merge_duplicates(fs, [], [], []), 1)
        survivors = [x for x in fs if x.status == "open"]
        self.assertEqual(len(survivors), 1)
        self.assertEqual(survivors[0].source, "both")
        self.assertEqual(survivors[0].rule_ids, ["no-error-path"])

    def test_survivor_is_the_higher_severity_and_set_level_wins_ties(self):
        a = f("D-001", "set_consistency", "CRITICAL", "Сбой push: противоречие разделов", q(PUSH, 46), qid="В-01",
              scope="set", stage="set_consistency")
        b = f("D-002", "consistency", "MINOR", "Сбой push: противоречие разделов", q(PUSH, 46), qid="В-01")
        fs = [b, a]
        merge.merge_duplicates(fs, [], [], [])
        self.assertEqual((a.status, b.status), ("open", "merged"))
        self.assertEqual(a.severity, "CRITICAL")
        c = f("D-003", "consistency", "CRITICAL", "Сбой push: противоречие разделов", q(PUSH, 46), qid="В-01")
        d = f("D-004", "set_consistency", "CRITICAL", "Сбой push: противоречие разделов", q(PUSH, 46), qid="В-01",
              scope="set", stage="set_consistency")
        fs2 = [c, d]
        merge.merge_duplicates(fs2, [], [], [])
        self.assertEqual((c.status, d.status), ("merged", "open"))

    def test_advisory_and_closed_findings_are_left_alone(self):
        fs = push_pair()
        fs[1].advisory = True
        self.assertEqual(merge.merge_duplicates(fs, [], [], []), 0)

    def test_similar_titles_without_shared_question(self):
        a = f("D-001", "verifiability", "MAJOR", "Не описан сбой отправки push клиенту", q(PUSH, 46))
        b = f("D-002", "verifiability", "MAJOR", "Не описан сбой отправки push клиенту при ошибке", q(PUSH, 46))
        fs = [a, b]
        self.assertEqual(merge.merge_duplicates(fs, [], [], []), 1)

    def test_fix_first_and_questions_are_remapped(self):
        fs = push_pair()
        qs = [Question(qid="В-01", text="?", why="", addressee="analyst", finding_ids=["D-002", "D-001"])]
        ff = [FixAction(action="x", finding_ids=["D-002"])]
        merge.merge_duplicates(fs, qs, ff, [])
        self.assertEqual(qs[0].finding_ids, ["D-001"])
        self.assertEqual(ff[0].finding_ids, ["D-001"])

    def test_deterministic_regardless_of_input_order(self):
        outs = []
        for order in ([0, 1], [1, 0]):
            base = push_pair()
            fs = [base[i] for i in order]
            merge.merge_duplicates(fs, [], [], [])
            outs.append(sorted((x.fid, x.status, x.merged_into, x.severity, len(x.quotes)) for x in base))
        self.assertEqual(outs[0], outs[1])


if __name__ == "__main__":
    unittest.main()
