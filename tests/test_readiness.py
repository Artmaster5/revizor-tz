"""Brief F: two kinds of readiness, the ceiling for blockers, the standard line behind each finding, the run passport."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from revizor import clarify, consistency, gate, pipeline, readiness, report_html, report_json, report_md, standard, webui
from revizor.model import (REQ_CRITERIA, SET_CRITERIA, Finding, Question, Quote, Requirement, Result, RunInfo)
from revizor.rules import RULES

DOC_COVERED = """# Уведомления

## Требования

- **Ф-1.** Сервис отправляет push клиенту в течение 5 секунд после покупки.
- **Ф-2.** Клиент задаёт порог в тенге от 1 000 до 1 000 000 включительно.
- **Ф-3.** Клиент выключает уведомления в настройках приложения.

## Критерии приёмки

- **КП-1.** Покупка достигла порога (Ф-1, Ф-2): клиент получает один push.
- **КП-2.** Клиент выключил уведомления (Ф-3): push не приходит.
"""

DOC_PARTIAL = """# Уведомления

## Требования

- **Ф-1.** Сервис отправляет push клиенту в течение 5 секунд после покупки.
- **Ф-2.** Клиент задаёт порог в тенге от 1 000 до 1 000 000 включительно.
- **Ф-3.** Клиент выключает уведомления в настройках приложения.
- **Ф-4.** Клиент видит историю уведомлений за 30 дней.

## Критерии приёмки

- **КП-1.** Покупка достигла порога (Ф-1): клиент получает один push.
- **КП-2.** Порог ниже минимума (Ф-2): сохранить нельзя.
- **КП-3.** Клиент выключил уведомления (Ф-3): push не приходит.
"""

DOC_NO_CRITERIA = """# Уведомления

## Требования

- **Ф-1.** Сервис отправляет push клиенту в течение 5 секунд после покупки.
- **Ф-2.** Клиент задаёт порог в тенге от 1 000 до 1 000 000 включительно.
- **Ф-3.** Клиент выключает уведомления в настройках приложения.
"""


def run_doc(text: str) -> Result:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "doc.md"
        p.write_text(text, encoding="utf-8")
        return pipeline.run(str(p))


def finding(title, problem, reason, *, sev="CRITICAL", crit="set_completeness", rules=(), fid="D-001"):
    return Finding(scope="set", criterion=crit, severity=sev, title=title, problem=problem, recommendation="r",
                   quotes=[Quote(text="x", line_start=1, line_end=1)], fid=fid, blocker_reason=reason,
                   rule_ids=list(rules), source=("rule:" + rules[0]) if rules else "model")


class Readiness(unittest.TestCase):
    def test_all_covered(self):
        r = run_doc(DOC_COVERED)
        t = r.readiness["test"]
        self.assertEqual(t["coverage_pct"], 100)
        self.assertEqual(t["uncovered"], [])
        self.assertEqual(set(r.readiness), {"dev", "test"})
        self.assertEqual(set(r.readiness["dev"]), {"ok", "text", "blockers"})
        self.assertEqual(set(t), {"ok", "text", "coverage_pct", "uncovered"})

    def test_partial_lists_uncovered_and_asks(self):
        r = run_doc(DOC_PARTIAL)
        t = r.readiness["test"]
        self.assertEqual(t["coverage_pct"], 75)
        self.assertFalse(t["ok"])
        self.assertEqual(len(t["uncovered"]), 1)
        rid = t["uncovered"][0]
        self.assertEqual(next(x.doc_id for x in r.requirements if x.rid == rid).rstrip("."), "Ф-4")
        # the rule «requirement-without-criterion» already asked about it: no second question
        qs = [q for q in r.questions if rid in q.req_ids]
        self.assertEqual(len(qs), 1)
        self.assertFalse([q for q in r.questions if "Как проверим" in q.text])
        n = len(r.questions)
        consistency.settle(r)
        self.assertEqual(len(r.questions), n)

    def test_small_document_gets_a_question(self):
        r = run_doc(DOC_NO_CRITERIA + "\n## Критерии приёмки\n\n- **КП-1.** Покупка достигла порога (Ф-1, Ф-2): клиент получает один push.\n")
        t = r.readiness["test"]
        self.assertEqual(t["coverage_pct"], 67)
        qs = [q for q in r.questions if "Как проверим" in q.text]
        self.assertEqual(len(qs), 1)
        self.assertIn("Ф-3", qs[0].text)
        self.assertTrue(qs[0].qid.startswith("В-"))
        self.assertEqual(len({q.qid for q in r.questions}), len(r.questions))
        n = len(r.questions)
        consistency.settle(r)
        self.assertEqual(len(r.questions), n)  # idempotent

    def test_no_criteria_says_so(self):
        r = run_doc(DOC_NO_CRITERIA)
        t = r.readiness["test"]
        self.assertIsNone(t["coverage_pct"])
        self.assertFalse(t["ok"])
        self.assertIn("нет критериев приёмки", t["text"])
        self.assertFalse([q for q in r.questions if "Как проверим" in q.text])

    def test_existing_question_is_not_duplicated(self):
        r = run_doc(DOC_PARTIAL)
        rid = r.readiness["test"]["uncovered"][0]
        before = [q for q in r.questions if rid in q.req_ids]
        r.questions = [q for q in r.questions if rid not in q.req_ids]
        r.questions.append(Question(qid="В-90", text="Чем подтвердим, что история хранится 30 дней?", why="", addressee="qa",
                                    req_ids=[rid]))
        self.assertEqual(readiness.add_check_questions(r), 0)
        self.assertTrue(before)

    def test_dev_follows_blockers(self):
        r = run_doc(DOC_COVERED)
        self.assertEqual(r.readiness["dev"]["ok"], r.verdict != "BLOCKED")
        f = finding("Противоречие", "Два значения.", "contradiction", crit="set_consistency")
        r.findings.append(f)
        consistency.settle(r)
        self.assertFalse(r.readiness["dev"]["ok"])
        self.assertEqual(r.readiness["dev"]["blockers"], ["D-001"])
        self.assertIn("Нельзя отдавать в разработку", r.readiness["dev"]["text"])
        self.assertFalse(r.readiness["test"]["ok"])  # a blocker keeps testing closed too

    def test_report_head_and_json(self):
        r = run_doc(DOC_PARTIAL)
        md = report_md.render(r)
        head = md.split('<a id="s1"></a>', 1)[0]
        self.assertIn("Можно отдавать в разработку", head)
        self.assertIn("в тестирование", head)
        self.assertIn("75%", head)
        html = report_html.render(r) if hasattr(report_html, "render") else ""
        if html:
            self.assertIn("в тестирование", html)
        data = json.loads(report_json.render(r))
        self.assertEqual(data["readiness"]["test"]["coverage_pct"], 75)
        self.assertEqual(data["summary"]["verdict"], r.verdict)

    def test_old_json_without_new_fields_renders(self):
        r = run_doc(DOC_PARTIAL)
        data = json.loads(report_json.render(r))
        for k in ("readiness", "passport", "summary"):
            data.pop(k, None)
        for f in data["findings"]:
            f.pop("standard", None)
        data["run"].pop("answers_sha256", None)
        old = report_json.from_data(data)
        self.assertEqual(old.readiness, {})
        md = report_md.render(old)
        self.assertIn("в тестирование", md)
        self.assertTrue(report_json.render(old))
        consistency.settle(old)
        self.assertTrue(old.readiness)


def _many_doc(unnumbered: int, numbered_uncovered: int = 0) -> str:
    """Four numbered requirements covered by criteria, then ``unnumbered`` requirements without an id of their own."""
    rows = [f"- **Ф-{i}.** Сервис выполняет операцию номер {i} за 5 секунд после запроса клиента." for i in range(1, 5)]
    rows += [f"- Клиент видит в приложении раздел номер {i} с историей операций за 30 дней." for i in range(1, unnumbered + 1)]
    return ("# Сервис\n\n## Функциональные требования\n\n" + "\n".join(rows) +
            "\n\n## Критерии приёмки\n\n- **КП-1.** Запрос выполнен (Ф-1, Ф-2, Ф-3): клиент получает ответ.\n"
            "- **КП-2.** Запрос выполнен (Ф-4): ответ.\n")


def _group(r: Result) -> list[Question]:
    return [q for q in r.questions if readiness.is_group_question(q)]


class GroupedQuestion(unittest.TestCase):
    def test_one_question_for_many_requirements(self):
        r = run_doc(_many_doc(13))
        self.assertEqual(len(r.readiness["test"]["uncovered"]), 13)
        qs = _group(r)
        self.assertEqual(len(qs), 1)
        self.assertEqual(len([q for q in r.questions if "Как проверим" in q.text]), 1)
        q = qs[0]
        self.assertTrue(q.text.startswith("Для 13 требований не назван критерий приёмки. Как проверим каждое из них? "))
        self.assertEqual(q.text.count("строка "), 10)  # ten named, the rest in the tail
        self.assertIn("строка 9: «Клиент видит в приложении раздел номер 1", q.text)
        self.assertTrue(q.text.endswith(" и ещё 3 — полный список в подробностях."))
        self.assertNotIn("REQ-", q.text)
        self.assertEqual((q.priority, q.addressee, q.letter_group), ("nice", "qa", "testing"))
        self.assertEqual(len(q.req_ids), 13)
        self.assertTrue(q.assumption.startswith("Требования без критерия приёмки"))
        self.assertEqual(len(q.fingerprint), 12)
        self.assertTrue(q.why)

    def test_own_ids_are_used(self):
        rows = [Requirement(rid=f"REQ-{i:03d}", doc_id=f"Ф-{i}.", section="Требования", kind="functional", quote=f"Ф-{i}. Текст.",
                            summary="", block_ids=[f"b{i}"], line_start=i, line_end=i) for i in (7, 8, 9)]
        self.assertEqual([readiness.requirement_label(x) for x in rows], ["Ф-7", "Ф-8", "Ф-9"])
        self.assertEqual(readiness.group_text(["Ф-7", "Ф-8", "Ф-9"]),
                         "Для 3 требований не назван критерий приёмки. Как проверим каждое из них? Ф-7; Ф-8; Ф-9.")

    def test_line_label_and_plural(self):
        r = Requirement(rid="REQ-031", doc_id="", section="s", kind="functional",
                        quote="**Клиент** видит историю уведомлений за последние тридцать дней в приложении", summary="",
                        block_ids=["b"], line_start=14, line_end=14)
        label = readiness.requirement_label(r)
        self.assertEqual(label, "строка 14: «Клиент видит историю уведомлений за последние тридцать…»")
        self.assertNotIn("REQ", label)
        self.assertTrue(readiness.group_headline(1).endswith("Как проверим его?"))
        self.assertTrue(readiness.group_headline(21).startswith("Для 21 требования "))
        self.assertTrue(readiness.group_headline(12).startswith("Для 12 требований "))

    def test_tail_only_over_ten(self):
        labels = [f"Ф-{i}" for i in range(1, 11)]
        self.assertNotIn("и ещё", readiness.group_text(labels))
        self.assertIn("и ещё 1 — полный список в подробностях.", readiness.group_text(labels + ["Ф-11"]))

    def test_idempotent(self):
        r = run_doc(_many_doc(13))
        n = len(r.questions)
        first = _group(r)[0].text
        consistency.settle(r)
        consistency.settle(r)
        self.assertEqual(len(r.questions), n)
        self.assertEqual(_group(r)[0].text, first)
        self.assertEqual(readiness.add_check_questions(r), 0)

    def test_existing_criteria_question_blocks_the_group(self):
        r = run_doc(_many_doc(13))
        r.questions = [q for q in r.questions if not readiness.is_group_question(q)]
        r.questions.append(Question(qid="В-90", text="Чем подтвердим, что история хранится 30 дней?", why="", addressee="qa",
                                    req_ids=list(r.readiness["test"]["uncovered"])))
        self.assertEqual(readiness.add_check_questions(r), 0)
        self.assertFalse(_group(r))

    def test_group_covers_only_what_is_left(self):
        r = run_doc(_many_doc(13))
        r.questions = [q for q in r.questions if not readiness.is_group_question(q)]
        r.questions.append(Question(qid="В-90", text="Какой критерий приёмки у раздела 1?", why="", addressee="qa",
                                    req_ids=r.readiness["test"]["uncovered"][:3]))
        self.assertEqual(readiness.add_check_questions(r), 1)
        self.assertTrue(_group(r)[0].text.startswith("Для 10 требований "))

    def test_links_to_the_rule_finding(self):
        r = run_doc(_many_doc(13))
        q = _group(r)[0]
        rule = [f.fid for f in r.findings if "requirement-without-criterion" in f.rule_ids and f.status == "open"]
        self.assertEqual(q.finding_ids, rule)

    def test_shown_in_every_place(self):
        r = run_doc(_many_doc(13))
        q = _group(r)[0]
        main = report_md.render(r, "doc.md")
        self.assertIn("Как проверим каждое из них?", main)
        letter = clarify.letter_md(r)
        part = letter.split("## Тестированию")[1]
        self.assertIn(q.qid, part)
        self.assertIn("Для 13 требований не назван критерий приёмки", part)
        self.assertIn("Почему спрашиваем:", part)
        self.assertIn("Если автор не ответит, считаем так: требования без критерия", part)
        from revizor import sidefiles
        qmd = sidefiles.questions_md(r, input_path="d.md", output_path="r.md", questions_path="r.questions.md")
        self.assertIn(f"Отпечаток вопроса: {q.fingerprint}", qmd)
        data = json.loads(report_json.render(r))
        jq = [x for x in data["questions"] if x["qid"] == q.qid][0]
        self.assertEqual((jq["letter_group"], jq["fingerprint"]), ("testing", q.fingerprint))
        self.assertEqual(len(data["readiness"]["test"]["uncovered"]), 13)
        win = webui.details_from(r, offline=True)
        wq = [x for x in win["questions"] if x["id"] == q.qid][0]
        self.assertEqual(wq["letter_group"], "testing")
        self.assertTrue(wq["assumption"])
        self.assertEqual(len(wq["requirements"]), 13)
        self.assertNotIn("REQ-", " ".join(wq["requirements"]))

    def test_report_table_and_details(self):
        r = run_doc(_many_doc(13))
        q = _group(r)[0]
        det = report_md._questions_details(r)
        self.assertIn("Все требования без критерия приёмки (13):", det)
        self.assertEqual(det.count("- строка "), 13)
        table = report_md._questions(r, "doc.md")
        row = [x for x in table.splitlines() if x.startswith(f'| <a id="') and q.qid in x.split("|")[1]][0]
        self.assertIn("Как проверим каждое из них?", row)
        self.assertNotIn("строка 9", row)  # the list does not break off in the middle of a table cell

    def test_old_json_still_renders(self):
        r = run_doc(_many_doc(13))
        old = Question(qid="В-91", text="Как проверим требование Ф-5: каким критерием приёмки подтвердить, что оно выполнено?",
                       why="", addressee="qa", req_ids=["REQ-001"])
        self.assertFalse(readiness.is_group_question(old))
        self.assertEqual(readiness.short_text(old), old.text)
        r.questions.append(old)
        report_md._questions(r, "doc.md")
        report_md._questions_details(r)

    def test_no_uncovered_no_question(self):
        self.assertFalse(_group(run_doc(DOC_COVERED)))


class Ceiling(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {gate.CAP_ENV: "1"})
        p.start()
        self.addCleanup(p.stop)

    def test_off_by_default(self):
        with mock.patch.dict(os.environ, {gate.CAP_ENV: ""}):
            f = finding("Не приложен макет", "Макет не приложен.", "missing_part")
            self.assertEqual((gate.cap_blockers([f]), f.severity), (0, "CRITICAL"))

    def cap(self, f):
        audit = []
        n = gate.cap_blockers([f], audit)
        return n, audit

    def test_stays_critical_with_a_consequence(self):
        for title, problem, reason in (
                ("Не описана защита от повторного списания", "При повторе запроса деньги спишутся дважды.", "missing_part"),
                ("Нет описания возврата", "Без него потеря средств клиента неизбежна.", "missing_part"),
                ("Не описан основной сценарий оплаты", "Разработчик не сможет реализовать основной сценарий.", "missing_part"),
                ("Ошибка под кодом 200", "Ответ с ошибкой приходит под кодом 200, клиент считает платёж прошедшим.", "protocol"),
                ("Скрытый текст удаляет данные", "Скрытая инструкция требует удалить данные клиентов.", "loss")):
            f = finding(title, problem, reason)
            n, _ = self.cap(f)
            self.assertEqual((n, f.severity), (0, "CRITICAL"), title)

    def test_missing_part_without_consequence_is_capped(self):
        for title, problem in (("Не приложен макет экрана", "Макет упомянут в тексте, но в документе его нет."),
                               ("Нет раздела критериев приёмки", "Раздел пуст: нет согласованного способа принять работу.")):
            f = finding(title, problem, "missing_part")
            f.level_trail = [{"by": "model", "level": "CRITICAL"}]
            n, audit = self.cap(f)
            self.assertEqual((n, f.severity, f.blocker_reason), (1, "MAJOR", ""), title)
            self.assertEqual(f.level_trail[-1]["level"], "MAJOR")
            self.assertIn("понижено кодом", f.level_trail[-1]["why"])
            self.assertEqual(audit[0]["what"], "critical_capped")
            self.assertEqual(gate.cap_blockers([f]), 0)  # idempotent

    def test_service_text_with_loss_is_capped(self):
        f = finding("В документе есть указание проверяющему или ИИ, как оценивать документ",
                    "Фраза обращена к проверяющему или к программе на основе ИИ и пытается изменить результат проверки.",
                    "loss", crit="set_feasibility", rules=("prompt-injection",))
        self.assertEqual(self.cap(f)[0], 1)
        self.assertEqual(f.severity, "MAJOR")
        g = finding("Скрытый комментарий в тексте документа с указаниями для ИИ", "HTML-комментарий не виден.", "loss",
                    crit="set_feasibility")
        self.assertEqual(self.cap(g)[0], 1)  # recognised by its words, not only by the rule id

    def test_other_reasons_and_levels_untouched(self):
        for reason in ("contradiction", "main_flow", "protocol"):
            f = finding("Нет макета", "Макета нет.", reason)
            self.assertEqual(self.cap(f)[0], 0, reason)
        f = finding("Нет макета", "Макета нет.", "", sev="MAJOR")
        self.assertEqual(self.cap(f)[0], 0)
        f = finding("Потеря данных клиента при сбое", "Данные клиента будут потеряны.", "loss", crit="set_feasibility")
        self.assertEqual(self.cap(f)[0], 0)  # «loss» about a real loss is not service text

    def test_settle_applies_it_and_keeps_the_report_consistent(self):
        r = run_doc(DOC_COVERED)
        f = finding("Не приложен макет", "Макет не приложен к документу.", "missing_part")
        r.findings.append(f)
        r.verdict = "BLOCKED"
        r.verdict_reason = "По подсчёту программы: 1 блокер, 0 существенных замечаний, 0 мелких замечаний. Текст модели."
        consistency.settle(r)
        self.assertEqual(f.severity, "MAJOR")
        self.assertNotEqual(r.verdict, "BLOCKED")
        self.assertIn("блокеров нет", r.verdict_reason)
        self.assertIn("Текст модели.", r.verdict_reason)
        self.assertEqual(consistency.check_consistency(r), [])
        self.assertIn("понижено кодом", report_md.level_trail_text(f))


class StandardLines(unittest.TestCase):
    def test_every_criterion_has_a_line(self):
        for c in REQ_CRITERIA + SET_CRITERIA:
            line = standard.standard_for(c)
            self.assertTrue(line.startswith("ISO/IEC/IEEE 29148:2018, п. 5.2."), c)
            self.assertIn(" — ", line, c)
            self.assertEqual(line.count("п."), 1, c)

    def test_clauses_are_only_the_three_of_the_brief(self):
        for clause, _ in list(standard.CRITERION_STANDARD.values()) + list(standard.RULE_STANDARD.values()):
            self.assertIn(clause, ("5.2.5", "5.2.6", "5.2.7"))

    def test_no_other_norms(self):
        for _, what in list(standard.CRITERION_STANDARD.values()) + list(standard.RULE_STANDARD.values()):
            for bad in ("IEEE 830", "ГОСТ", "ст.", "закон", "RFC"):
                self.assertNotIn(bad, what)

    def test_every_rule_override_is_a_real_rule(self):
        for rid in standard.RULE_STANDARD:
            self.assertIn(rid, RULES)

    def test_wording_rule_points_at_language_clause(self):
        f = finding("Размытое слово", "быстро", "", sev="MAJOR", crit="unambiguity", rules=("vague-word",))
        self.assertIn("п. 5.2.7", standard.standard_of(f))
        f = finding("Двусмысленно", "x", "", sev="MAJOR", crit="unambiguity")
        self.assertIn("п. 5.2.5 — требование должно быть однозначным", standard.standard_of(f))

    def test_on_card_and_in_json(self):
        r = run_doc(DOC_PARTIAL + "\n- **Ф-5.** Сервис должен работать быстро и удобно.\n")
        self.assertTrue(r.findings)
        for f in r.findings:
            self.assertTrue(f.standard, f.fid)
        md = report_md.render(r)
        det = report_md.render_details(r) if hasattr(report_md, "render_details") else ""
        self.assertIn("Основание: ISO/IEC/IEEE 29148:2018", md + det)
        data = json.loads(report_json.render(r))
        self.assertTrue(all(f["standard"] for f in data["findings"]))


class Passport(unittest.TestCase):
    def test_hashes_and_version_in_report_and_json(self):
        r = run_doc(DOC_COVERED)
        self.assertEqual(len(r.run.doc_sha256), 64)
        r.run.answers_sha256 = "a" * 64
        md = report_md.render(r)
        self.assertIn(r.run.doc_sha256, md)
        self.assertIn("a" * 64, md)
        self.assertIn("Набор правил", md)
        p = json.loads(report_json.render(r))["passport"]
        self.assertEqual(p["doc_sha256"], r.run.doc_sha256)
        self.assertEqual(p["answers_sha256"], "a" * 64)
        self.assertEqual(p["ruleset_version"], r.run.rules_version)
        for k in ("model", "started_at", "cost_usd", "agent_version"):
            self.assertIn(k, p)

    def test_no_answers_row_without_a_file(self):
        r = run_doc(DOC_COVERED)
        self.assertNotIn("SHA-256 файла ответов", report_md.render(r))


if __name__ == "__main__":
    unittest.main()
