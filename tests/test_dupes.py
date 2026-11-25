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
        self.assertIn("повторных попыток", fs[0].problem)  # the longer problem text survives
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
        self.assertEqual(merge.merge_duplicates(fs, [], [], []), 0)

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
