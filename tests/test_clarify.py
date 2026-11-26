"""Clarification, engine side: assumptions, fingerprints, checking a fix, the letter to the author."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from revizor import answers as ans_mod, clarify, report_json, report_md, rules, sidefiles
from revizor.llm import FakeBackend
from revizor.loader import load
from revizor.model import Finding, Question, Quote, Requirement, Result, RunInfo

from tests import test_pipeline as tp
from tests.dialog_helpers import AGENT, Base, REQ_DOC, provider_from_text, quiet_script
from tests.test_answers import fill

DEMO_ANSWERS = AGENT / "docs" / "demo" / "answers.md"


def question(qid="В-01", text="Какой часовой пояс?", quote="Срок оплаты — до 18:00", **kw):
    return Question(qid=qid, text=text, why="", addressee=kw.pop("addressee", "analyst"),
                    quote=Quote(text=quote, line_start=3, line_end=3) if quote is not None else None, **kw)


class AssumptionTests(Base):
    def test_every_rule_default_names_a_real_rule(self):
        self.assertTrue(set(clarify.RULE_ASSUMPTIONS) <= set(rules.RULES))
        for text in clarify.RULE_ASSUMPTIONS.values():
            self.assertTrue(text.strip())

    def test_every_question_of_an_offline_run_has_an_assumption(self):
        res = self.run_offline()
        self.assertTrue(res.questions)
        for q in res.questions:
            self.assertTrue(q.assumption, q.qid)
        real = [q for q in res.questions if clarify.has_assumption(q)]
        self.assertTrue(real)
        self.assertTrue(all(q.assumption.endswith(".") for q in real))

    def test_template_question_takes_the_rule_default(self):
        f = Finding(scope="set", criterion="set_completeness", severity="MINOR", title="t", problem="p",
                    recommendation="r", fid="D-001", rule_ids=["time-zone-missing"], source="rule:time-zone-missing")
        q = question(finding_ids=["D-001"])
        clarify.annotate([q], [f], by_model=False)
        self.assertIn("Алматы", q.assumption)
        self.assertEqual(clarify.assumption_line(q),
                         "Если автор не ответит, считаем так: время и границы суток считаем по времени Алматы, UTC+5.")

    def test_model_question_without_a_safe_assumption_says_work_cannot_start(self):
        f = Finding(scope="set", criterion="set_completeness", severity="MINOR", title="t", problem="p",
                    recommendation="r", fid="D-001", rule_ids=["time-zone-missing"])
        q = question(finding_ids=["D-001"], assumption="")
        clarify.annotate([q], [f], by_model=True)  # the model said «no safe assumption»: the rule default is not used
        self.assertEqual(q.assumption, clarify.NO_ASSUMPTION)
        self.assertIn("начинать нельзя", clarify.assumption_line(q))
        self.assertFalse(clarify.has_assumption(q))

    def test_model_assumption_is_kept(self):
        q = question(assumption="Считаем дату покупки по времени Алматы, UTC+5.")
        clarify.annotate([q], [], by_model=True)
        self.assertEqual(q.assumption, "Считаем дату покупки по времени Алматы, UTC+5.")

    def test_model_stage_prompt_and_schema_carry_the_field(self):
        from revizor import prompts

        user = prompts.questions_user(["D-001 | MAJOR | x"], "")
        self.assertIn("assumption", user)
        props = prompts.QUESTIONS_SCHEMA["properties"]["questions"]["items"]["properties"]
        self.assertIn("assumption", props)

    def test_model_run_reads_the_assumption(self):
        qs = {"questions": [{"text": "Как округлять?", "why": "w", "addressee": "business", "options": ["а", "б"],
                             "assumption": "Округляем до сотых.", "priority": "important", "finding_ids": []},
                            {"text": "Какой лимит?", "why": "w", "addressee": "business", "options": [],
                             "assumption": "", "priority": "important", "finding_ids": []}],
              "fix_first": [], "verdict_reason": "", "strengths": []}
        res, _b = self.run_fake(quiet_script(questions=qs))
        by_text = {q.text: q for q in res.questions}
        self.assertEqual(by_text["Как округлять?"].assumption, "Округляем до сотых.")
        self.assertEqual(by_text["Какой лимит?"].assumption, clarify.NO_ASSUMPTION)

    def test_report_json_questionnaire_and_card_show_it(self):
        res = self.run_offline()
        data = json.loads(report_json.render(res))
        self.assertTrue(all("assumption" in q and "fingerprint" in q and "letter_group" in q for q in data["questions"]))
        md = report_md.render(res, "r.md")
        self.assertIn("Если автор не ответит, считаем так", md)
        qmd = sidefiles.questions_md(res, input_path="i", output_path="o", questions_path="q")
        self.assertIn("Если автор не ответит", qmd)

    def test_unanswered_assumptions_listed_in_a_repeat_check(self):
        base = self.run_offline()
        text = sidefiles.questions_md(base, input_path="i", output_path="o", questions_path="q")
        first = base.questions[0].qid
        res = self.run_offline(answers=provider_from_text(fill(text, {first: "а"})))
        md = report_md.render(res, "r.md")
        det = report_md.render_details(res, "r.md", md) or ""
        self.assertIn("Принято без ответа автора", md + det)
        listed = report_md.assumptions_in_force(res)
        self.assertTrue(any("→" in x for x in listed))
        self.assertFalse(any(first + ")" in x for x in listed if "→" in x and x.startswith(f"- [{first}")))
        # a first run (no answers) has no such heading
        self.assertNotIn("Принято без ответа автора", report_md.render(base, "r.md"))


class GroupTests(unittest.TestCase):
    def f(self, **kw):
        base = dict(scope="set", criterion="completeness", severity="MINOR", title="t", problem="p", recommendation="r",
                    fid="D-001")
        base.update(kw)
        return Finding(**base)

    def test_group_names_are_the_four_addressees(self):
        self.assertEqual(clarify.LETTER_GROUPS, ("business", "architect", "security", "testing"))
        self.assertEqual(set(clarify.GROUP_RU), set(clarify.LETTER_GROUPS))
        for table in (clarify.RULE_GROUP, clarify.CRITERION_GROUP, clarify.ADDRESSEE_GROUP, clarify.STAGE_GROUP):
            self.assertTrue(set(table.values()) <= set(clarify.LETTER_GROUPS))

    def test_the_table_covers_every_rule_and_names_only_real_rules(self):
        main = {rid for rid, info in rules.RULES.items() if not info.advisory}
        self.assertEqual(main - set(clarify.RULE_GROUP), set(), "a rule without an addressee")
        self.assertEqual(set(clarify.RULE_GROUP) - set(rules.RULES), set())

    def test_every_criterion_has_an_addressee(self):
        from revizor.model import REQ_CRITERIA, SET_CRITERIA

        self.assertEqual((set(REQ_CRITERIA) | set(SET_CRITERIA)) - set(clarify.CRITERION_GROUP), set())

    def test_fixed_mapping(self):
        cases = [
            # the question's own addressee wins
            (question(addressee="security"), [], "security"),
            (question(addressee="qa"), [], "testing"),
            (question(addressee="architect"), [], "architect"),
            (question(addressee="business"), [], "business"),
            # «analyst» is the author himself: placed by rule, then criterion
            (question(finding_ids=["D-001"]), [self.f(rule_ids=["get-mutation"])], "architect"),
            (question(finding_ids=["D-001"]), [self.f(rule_ids=["sensitive-in-query"])], "security"),
            (question(finding_ids=["D-001"]), [self.f(rule_ids=["requirement-without-criterion"])], "testing"),
            (question(finding_ids=["D-001"]), [self.f(rule_ids=["time-zone-missing"])], "business"),
            (question(finding_ids=["D-001"]), [self.f(criterion="verifiability")], "testing"),
            (question(finding_ids=["D-001"]), [self.f(criterion="feasibility")], "architect"),
            (question(finding_ids=["D-001"]), [self.f(criterion="set_feasibility")], "architect"),
            (question(finding_ids=["D-001"]), [self.f(criterion="unambiguity")], "business"),
            # security words win over the criterion
            (question(text="Как хранится пароль клиента?", finding_ids=["D-001"]), [self.f(criterion="completeness")],
             "security"),
            # the stage decides when the criterion is unknown
            (question(finding_ids=["D-001"]), [self.f(criterion="?", stage="set_standards")], "architect"),
            # nothing known: the business customer
            (question(), [], "business"),
        ]
        for q, fs, want in cases:
            by_fid = {f.fid: f for f in fs}
            self.assertEqual(clarify.letter_group(q, by_fid), want, (q.text, q.addressee))


class FingerprintTests(Base):
    def test_fingerprint_ignores_case_spacing_and_number(self):
        a = question("В-01", "Какой  часовой пояс?")
        b = question("В-07", "какой часовой пояс")
        self.assertEqual(clarify.fingerprint(a), clarify.fingerprint(b))
        self.assertEqual(len(clarify.fingerprint(a)), 12)

    def test_fingerprint_changes_with_text_and_place(self):
        a = question()
        self.assertNotEqual(clarify.fingerprint(a), clarify.fingerprint(question(text="Другой вопрос?")))
        self.assertNotEqual(clarify.fingerprint(a), clarify.fingerprint(question(quote="Срок оплаты — до 20:00")))
        self.assertNotEqual(clarify.fingerprint(a), clarify.fingerprint(question(quote=None)))

    def test_questionnaire_carries_the_fingerprints(self):
        res = self.run_offline()
        text = sidefiles.questions_md(res, input_path="i", output_path="o", questions_path="q")
        for q in res.questions:
            self.assertIn(f"Отпечаток вопроса: {q.fingerprint}", text)
        self.assertIn("Отпечаток документа: " + res.run.doc_sha256[:12], text)

    def test_matching_fingerprints_apply(self):
        res = self.run_offline()
        text = sidefiles.questions_md(res, input_path="i", output_path="o", questions_path="q")
        p = ans_mod.parse(fill(text, {"В-01": "а"}), res.questions)
        self.assertEqual(set(p.answers), {"В-01"})
        self.assertEqual(p.stale, {})
        self.assertEqual(p.warnings, [])
        self.assertEqual(p.doc_fingerprint, res.run.doc_sha256[:12])

    def test_changed_question_is_not_applied_and_the_report_says_so(self):
        res = self.run_offline()
        text = sidefiles.questions_md(res, input_path="i", output_path="o", questions_path="q")
        old = res.questions[0].fingerprint
        wrong = fill(text, {"В-01": "а", "В-02": "а"}).replace(old, "0" * 12)
        res2 = self.run_offline(answers=provider_from_text(wrong))
        q1 = next(q for q in res2.questions if q.qid == "В-01")
        self.assertEqual(q1.answer, "")
        self.assertIn("не применён", q1.answer_effect)
        self.assertTrue(any("В-01 не применён" in w for w in res2.run.warnings))
        self.assertTrue(any(a.get("what") == "answer_stale" and a["qid"] == "В-01" for a in res2.audit))
        sm = ans_mod.summary(res2.audit)
        self.assertEqual(sm["stale"], ["В-01"])
        self.assertTrue(all(not f.closed_by_answer for f in res2.findings if f.question_id == "В-01"))

    def test_answers_without_fingerprints_keep_working_by_id(self):
        res = self.run_offline()
        old_style = "### В-01\nОтвет: а\n\n### В-02\nОтвет: б\n"
        p = ans_mod.parse(old_style, res.questions)
        self.assertEqual(set(p.answers), {"В-01", "В-02"})
        self.assertEqual(p.stale, {})

    def test_demo_answers_file_applies_exactly_as_before(self):
        text = DEMO_ANSWERS.read_text(encoding="utf-8")
        res = self.run_offline()
        p = ans_mod.parse(text, res.questions)
        self.assertEqual(p.stale, {})
        self.assertFalse(any("не применён" in w for w in p.warnings))
        self.assertEqual(p.doc_fingerprint, "")
        applied = self.run_offline(answers=provider_from_text(text))
        self.assertTrue(ans_mod.summary(applied.audit)["answered"] >= 1)

    def test_doc_and_answers_hashes_in_the_run_info(self):
        res = self.run_offline()
        self.assertEqual(res.run.answers_sha256, "")
        self.assertEqual(len(res.run.doc_sha256), 64)
        text = "### В-01\nОтвет: а\n"
        res2 = self.run_offline(answers=provider_from_text(text))
        self.assertEqual(res2.run.answers_sha256, clarify.sha256_text(text))
        self.assertEqual(res2.run.doc_sha256, res.run.doc_sha256)
        data = json.loads(report_json.render(res2))
        self.assertEqual(data["run"]["answers_sha256"], clarify.sha256_text(text))
        self.assertEqual(data["run"]["doc_sha256"], res.run.doc_sha256)

    def test_typed_answers_have_a_hash_too(self):
        res = self.run_offline()
        p = ans_mod.from_raw({"В-01": "а"}, res.questions)
        self.assertEqual(len(p.sha256), 64)

    def test_answer_for_another_version_of_the_document_is_flagged(self):
        res = self.run_offline()
        text = sidefiles.questions_md(res, input_path="i", output_path="o", questions_path="q").replace(
            "Отпечаток документа: " + res.run.doc_sha256[:12], "Отпечаток документа: " + "f" * 12)
        res2 = self.run_offline(answers=provider_from_text(fill(text, {"В-01": "а"})))
        self.assertTrue(any("другой версии документа" in w for w in res2.run.warnings))


class FixCheckTests(Base):
    DOC = ("# Требования\n\nВерсия 1.0 от 01.01.2026\n\n"
           "## Платежи\n\n"
           "- Ф-1. Система должна быстро обрабатывать платёж клиента.\n"
           "- Ф-2. Система обрабатывает платёж не более 3 секунд при нагрузке 100 запросов в секунду.\n")

    def fixture(self):
        path = self.doc(self.DOC)
        res = self.run_offline(path)
        doc = load(path)
        f = next(x for x in res.findings if "vague-word" in x.rule_ids and "быстро" in x.quotes[0].text)
        return res, doc, f

    def test_new_wording_without_the_problem_is_resolved(self):
        res, doc, f = self.fixture()
        f.fixed_wording = "Ф-1. Система должна обработать платёж клиента не более чем за 3 секунды при нагрузке 100 запросов в секунду."
        f.closed_by_answer = True
        reopened = clarify.fix_checks(res.findings, res.requirements, doc)
        self.assertEqual(f.fix_check["status"], "resolved")
        self.assertIn("правило больше не срабатывает на новой формулировке", f.fix_check["note"])
        self.assertEqual(reopened, [])
        self.assertTrue(f.closed_by_answer)

    def test_new_wording_that_still_trips_the_rule_stays_open(self):
        res, doc, f = self.fixture()
        f.fixed_wording = "Ф-1. Система должна быстро и надёжно обрабатывать платёж клиента."
        f.closed_by_answer = True
        reopened = clarify.fix_checks(res.findings, res.requirements, doc)
        self.assertEqual(f.fix_check["status"], "remains")
        self.assertTrue(f.fix_check["note"].startswith("на новой формулировке замечание остаётся"))
        self.assertEqual(reopened, [f.fid])
        self.assertFalse(f.closed_by_answer)
        self.assertIn("замечание остаётся", clarify.fix_check_text(f))

    def test_a_closed_finding_without_wording_is_unchecked_with_a_reason(self):
        res, doc, f = self.fixture()
        f.fixed_wording = ""
        f.closed_by_answer = True
        clarify.fix_checks(res.findings, res.requirements, doc)
        self.assertEqual(f.fix_check["status"], "unchecked")
        self.assertIn("готовой формулировки нет", f.fix_check["note"])
        self.assertTrue(f.closed_by_answer)

    def test_model_finding_is_unchecked_by_code(self):
        res, doc, f = self.fixture()
        g = Finding(scope="requirement", criterion="completeness", severity="MAJOR", title="t", problem="p",
                    recommendation="r", quotes=f.quotes, req_ids=f.req_ids, fid="D-900", source="model",
                    fixed_wording="Ф-1. Система обрабатывает платёж за 3 секунды.")
        clarify.fix_checks([g], res.requirements, doc)
        self.assertIsNone(g.fix_check)
        clarify.after_gate([g])
        self.assertEqual(g.fix_check["status"], "unchecked")
        self.assertIn("не правилом", g.fix_check["note"])

    def test_withheld_wording_cannot_be_called_a_fix(self):
        res, doc, f = self.fixture()
        f.fixed_wording = "Ф-1. Система обрабатывает платёж за 3 секунды."
        clarify.fix_checks(res.findings, res.requirements, doc)
        self.assertEqual(f.fix_check["status"], "resolved")
        f.fixed_wording = ""  # the gate of rewrites withheld it
        clarify.after_gate([f])
        self.assertEqual(f.fix_check["status"], "unchecked")
        self.assertIn("не прошла проверку смысла", f.fix_check["note"])

    def test_requirement_in_a_table_row_is_unchecked(self):
        text = ("# Требования\n\n## Лимиты\n\n| Код | Требование |\n| --- | --- |\n"
                "| Ф-1 | Система должна быстро обрабатывать платёж клиента. |\n"
                "| Ф-2 | Система хранит данные клиента пять лет. |\n")
        path = self.doc(text, "t.md")
        res = self.run_offline(path)
        f = next((x for x in res.findings if "vague-word" in x.rule_ids), None)
        if f is None:
            self.skipTest("the rule does not fire on the table fixture")
        f.fixed_wording = "Система обрабатывает платёж за 3 секунды."
        clarify.fix_checks(res.findings, res.requirements, load(path))
        self.assertEqual(f.fix_check["status"], "unchecked")

    def test_card_and_json_show_the_check(self):
        res, doc, f = self.fixture()
        f.fixed_wording = "Ф-1. Система обрабатывает платёж клиента за 3 секунды."
        clarify.fix_checks(res.findings, res.requirements, doc)
        md = report_md.render(res, "r.md")
        details = report_md.render_details(res, "r.md", md) or ""
        self.assertIn("Проверка исправления: снято", md + details)
        data = json.loads(report_json.render(res))
        got = [x for x in data["findings"] if x["fid"] == f.fid][0]
        self.assertEqual(got["fix_check"]["status"], "resolved")
        others = [x for x in data["findings"] if x["fid"] != f.fid and "fix_check" in x]
        self.assertTrue(all(x["fix_check"] for x in others))
        other = [x for x in data["findings"] if x["fid"] != f.fid and x.get("fix_check") is None]
        self.assertTrue(all("fix_check" not in x for x in other))

    def test_reopened_finding_is_taken_out_of_the_answers_summary(self):
        res, doc, f = self.fixture()
        q = Question(qid="В-01", text="t", why="", addressee="analyst", finding_ids=[f.fid], answer="а")
        audit = [{"type": "answers_summary", "closed": [f.fid, "D-777"], "closes": {f.fid: ["В-01"], "D-777": ["В-02"]}}]
        clarify.reopen_in_audit(audit, [q], [f.fid])
        self.assertEqual(audit[0]["closed"], ["D-777"])
        self.assertEqual(list(audit[0]["closes"]), ["D-777"])
        self.assertIn("замечание остаётся", q.answer_effect)

    def test_end_to_end_model_answer_with_a_bad_wording_keeps_the_finding_open(self):
        path = self.doc(self.DOC)
        base, _b0 = self.run_fake(quiet_script(), path)  # the model run numbers the requirements itself
        f0 = next(x for x in base.findings if "vague-word" in x.rule_ids and "быстро" in x.quotes[0].text)
        qid = f0.question_id
        self.assertTrue(qid)
        answer = {"answers": [{"qid": qid, "understood": "Автор уточнил.", "closes": [f0.fid],
                               "fixed": [{"rid": f0.req_ids[0], "wording": "Ф-1. Система должна быстро и надёжно обрабатывать платёж клиента."}]}]}
        script = quiet_script(answers=answer)
        text = "### " + qid + "\nОтвет: пусть будет быстро и надёжно\n"
        res, _b = self.run_fake(script, path, answers=provider_from_text(text))
        f = next((x for x in res.findings if x.fid == f0.fid), None)
        self.assertIsNotNone(f)
        self.assertEqual(f.fixed_wording.startswith("Ф-1"), True)
        self.assertFalse(f.closed_by_answer)
        self.assertEqual(f.fix_check["status"], "remains")


class LetterTests(Base):
    def test_letter_first_line_counts_blockers(self):
        res = self.run_offline()
        letter = clarify.letter_md(res, questions_name="r.questions.md")
        first = letter.split("\n")[0]
        blockers = sum(1 for q in res.questions if q.priority == "blocker")
        self.assertIn(f"Блокируют начало работы: {blockers} из {len(res.questions)}", first)

    def test_one_section_per_addressee_with_every_question_once(self):
        res = self.run_offline()
        letter = clarify.letter_md(res, questions_name="r.questions.md")
        heads = re.findall(r"^## (.+?) \((\d+)\)$", letter, re.M)
        self.assertTrue(heads)
        names = [h for h, _n in heads]
        order = [clarify.GROUP_RU[g] for g in clarify.LETTER_GROUPS if clarify.GROUP_RU[g] in names]
        self.assertEqual(names, order)
        self.assertEqual(sum(int(n) for _h, n in heads), len(res.questions))
        for q in res.questions:
            self.assertEqual(len(re.findall(rf"^### {q.qid}\b", letter, re.M)), 1, q.qid)
        for q in res.questions:
            self.assertIn(q.letter_group, clarify.LETTER_GROUPS)

    def test_each_question_has_place_reason_options_and_assumption(self):
        res = self.run_offline()
        letter = clarify.letter_md(res)
        self.assertIn("Место в документе", letter)
        self.assertIn("Почему спрашиваем", letter)
        self.assertIn("Варианты ответа", letter)
        self.assertIn("Если автор не ответит", letter)

    def test_answered_questions_are_not_asked_again(self):
        res = self.run_offline()
        res.questions[0].answer = "а"
        letter = clarify.letter_md(res)
        self.assertNotIn(f"### {res.questions[0].qid}", letter)
        for q in res.questions:
            q.answer = "а"
        self.assertIsNone(clarify.letter_md(res))

    def test_no_questions_no_letter(self):
        self.assertIsNone(clarify.letter_md(Result(run=RunInfo())))

    def test_letter_is_written_next_to_the_report_and_listed(self):
        out = self.dir / "r.md"
        code = self.cli("--input", REQ_DOC, "--output", str(out), "--provider", "offline")
        self.assertIn(code, (0, 3))
        letter = self.dir / "r.letter.md"
        self.assertTrue(letter.is_file())
        self.assertIn("Письмо автору", letter.read_text(encoding="utf-8"))
        self.assertIn("r.letter.md", self.text)
        from revizor.webui import file_kind

        self.assertEqual(file_kind("r.letter.md", "r")[0], "letter")


if __name__ == "__main__":
    unittest.main()
