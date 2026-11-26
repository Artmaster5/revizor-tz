"""Wave 9 (brief A): nothing the agent found is hidden from the reader.

The full record of every merged-away finding (``Finding.absorbed``), one topic or no merge on every path, the level of a
merge, the sub-items on the card, «Как найдено», the judge input and the program score, and the part of the details file
for what was taken out of the list. No model, no network."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from revizor import consistency, exports, gate, merge, report_html, report_json, report_md
from revizor.model import DroppedQuote, Finding, Question, Quote, Requirement, Result, RunInfo
from revizor.merge import absorb

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "eval") not in sys.path:
    sys.path.insert(0, str(ROOT / "eval"))

import judge_input  # noqa: E402
import score  # noqa: E402


def q(text: str, line: int, rid: str = "REQ-001", end: int | None = None) -> Quote:
    return Quote(text=text, line_start=line, line_end=end or line, rid=rid)


def f(fid: str, title: str, *, sev: str = "MAJOR", crit: str = "completeness", quotes=None, problem: str = "",
      rec: str = "", reqs=("REQ-001",), source: str = "model", rules=(), stage: str = "review", qid: str = "",
      scope: str = "requirement") -> Finding:
    return Finding(scope=scope, criterion=crit, severity=sev, title=title, problem=problem or title,
                   recommendation=rec, quotes=list(quotes) if quotes is not None else [q("Сервис отвечает", 5)],
                   req_ids=list(reqs), source=source, rule_ids=list(rules), stage=stage, fid=fid, question_id=qid)


CODE200 = "Ошибка валидации возвращается с кодом 200 и описанием в теле ответа."


# --- 1. the full record, on every path ---------------------------------------------------------------------------


class Record(unittest.TestCase):
    def test_record_has_every_field(self):
        a = f("D-001", "Код 200 не гарантирует успех", sev="MAJOR")
        b = f("D-002", "Не задан срок ответа", sev="MINOR", crit="unambiguity", problem="Срок не назван.",
              rec="Назовите срок.", quotes=[q("отвечает быстро", 7), q("не позднее", 9)])
        absorb(a, b, by="recheck")
        (rec,) = a.absorbed
        self.assertEqual(rec["title"], "Не задан срок ответа")
        self.assertEqual(rec["problem"], "Срок не назван.")
        self.assertEqual(rec["recommendation"], "Назовите срок.")
        self.assertEqual((rec["severity"], rec["criterion"], rec["source"], rec["by"]),
                         ("MINOR", "unambiguity", "model", "recheck"))
        self.assertEqual(rec["quotes"], [{"line": 7, "text": "отвечает быстро"}, {"line": 9, "text": "не позднее"}])
        self.assertEqual(a.also, ["Не задан срок ответа"])  # the old field stays

    def test_nested_merges_are_flattened(self):
        a, b, c = f("D-001", "Первое"), f("D-002", "Второе"), f("D-003", "Третье")
        absorb(b, c, by="dedup")
        absorb(a, b, by="recheck")
        self.assertEqual([(x["title"], x["by"]) for x in a.absorbed], [("Второе", "recheck"), ("Третье", "dedup")])
        absorb(a, b, by="recheck")  # twice: no duplicates
        self.assertEqual(len(a.absorbed), 2)

    def test_recheck_path(self):
        a = f("D-001", "Не задан шаг значения лимита", sev="MINOR")
        b = f("D-002", "Шаг значения лимита не задан", sev="MAJOR")
        gate.apply_gate([a, b], [{"fid": "D-002", "decision": "same", "same_as": "D-001"}], injection=False, audit=[])
        self.assertEqual([(x["title"], x["by"]) for x in b.absorbed], [("Не задан шаг значения лимита", "recheck")])

    def test_dedup_path(self):
        a = f("D-001", "Не задан шаг значения лимита", sev="MAJOR")
        b = f("D-002", "Шаг значения лимита не задан", sev="MINOR")
        gate.apply_dedup([a, b], [{"keep": "D-001", "same": ["D-002"], "why": "то же"}], injection=False, audit=[])
        self.assertEqual([(x["title"], x["by"]) for x in a.absorbed], [("Шаг значения лимита не задан", "dedup")])

    def test_code_path_duplicates_by_code(self):
        a = f("D-001", "Не описан сбой отправки push", crit="verifiability", qid="В-01",
              quotes=[q("Сервис передаёт push на отправку в течение минуты", 46)],
              problem="Что происходит при сбое отправки push, не сказано.")
        b = f("D-002", "Сбой передачи push на отправку не описан", sev="MINOR", qid="В-01",
              quotes=[q("передаёт push на отправку", 46)],
              problem="Нет поведения при сбое передачи push на отправку и повторных попыток.")
        self.assertEqual(merge.merge_duplicates([a, b], [], [], []), 1)
        self.assertEqual([(x["title"], x["by"], x["severity"]) for x in a.absorbed],
                         [("Сбой передачи push на отправку не описан", "code", "MINOR")])

    def test_code_path_before_numbering_and_rule_folding(self):
        a = f("", "Не задан шаг значения лимита", sev="MAJOR", quotes=[q("лимит от 100 до 5 000", 3)])
        b = f("", "Шаг значения лимита не задан", sev="MINOR", quotes=[q("лимит от 100 до 5 000", 3)])
        (kept,) = merge.dedupe_model([a, b], [])
        self.assertEqual([x["by"] for x in kept.absorbed], ["code"])
        rule = f("", "Диапазон без шага", sev="MINOR", source="rule:step-missing", rules=["step-missing"],
                 quotes=[q("лимит от 100 до 5 000", 3)], stage="rules", problem="Для диапазона не задан шаг значения лимита.")
        out = merge.fold_rules([rule], [kept], [])
        self.assertEqual(len(out), 1)
        self.assertEqual({x["source"] for x in kept.absorbed}, {"model", "rule:step-missing"})
        self.assertTrue(all(x["by"] == "code" for x in kept.absorbed))

    def test_folded_formal_remarks_keep_every_original_wording(self):
        a = f("D-001", "В пункте несколько утверждений (первый)", sev="MINOR", crit="singularity",
              quotes=[q("Первое и второе.", 3, "REQ-001")], problem="Два правила в одном пункте A.")
        b = f("D-002", "В пункте несколько утверждений (второй)", sev="MINOR", crit="singularity",
              quotes=[q("Третье и четвёртое.", 9, "REQ-002")], reqs=("REQ-002",), problem="Два правила в пункте B.")
        head = merge.fold_formal([a, b], [], [], [])
        self.assertIs(head, a)
        self.assertEqual([x["problem"] for x in a.absorbed], ["Два правила в одном пункте A.", "Два правила в пункте B."])
        self.assertTrue(all(x["by"] == "code" for x in a.absorbed))
        self.assertEqual(report_md.absorbed_lines(a), [])  # the card lists the places of a folded remark instead


# --- 2. one topic or no merge, on the paths of the program -------------------------------------------------------


class OneTopic(unittest.TestCase):
    def test_validation_code_and_error_in_body_are_two_findings(self):
        # one line, one requirement, one question, one criterion family: only the place says «same»
        quote = q(CODE200, 12)
        a = f("D-001", "Код 200 не гарантирует успех: в теле может быть ERROR", sev="MAJOR", crit="consistency",
              quotes=[quote], qid="В-03",
              problem="Клиент проверяет только HTTP-код и не увидит ошибку, записанную в теле ответа.")
        b = f("D-002", "Ошибка валидации возвращается под кодом 200", sev="MAJOR", crit="consistency", quotes=[quote],
              qid="В-03", problem="Неверный ввод должен получать код 4xx, а не 200: иначе его нельзя отличить от успеха.")
        fs = [a, b]
        self.assertEqual(merge.merge_duplicates(fs, [], [], []), 0)
        self.assertEqual([x.status for x in fs], ["open", "open"])
        self.assertEqual((a.absorbed, b.absorbed), ([], []))

    def test_statuses_and_sms_are_two_findings(self):
        a = f("D-001", "Не описан сбой отправки SMS", sev="MAJOR", crit="completeness", qid="В-07",
              quotes=[q("Сервис отправляет SMS и меняет статус заявки", 20)],
              problem="Что делать, если оператор связи не принял SMS, не сказано.")
        b = f("D-002", "Не определены допустимые статусы заявки", sev="MAJOR", crit="completeness", qid="В-07",
              quotes=[q("меняет статус заявки", 20)],
              problem="Перечень статусов и переходы между ними не заданы, тест не может проверить результат.")
        fs = [a, b]
        self.assertEqual(merge.merge_duplicates(fs, [], [], []), 0)
        self.assertEqual([x.status for x in fs], ["open", "open"])

    def test_a_real_repeat_still_merges(self):
        a = f("D-001", "Не описан сбой отправки push", crit="verifiability", qid="В-01", sev="MAJOR",
              quotes=[q("Сервис передаёт push на отправку", 46)],
              problem="Что происходит при сбое отправки push, не сказано.")
        b = f("D-002", "Сбой передачи push на отправку не описан", crit="completeness", qid="В-01", sev="MINOR",
              quotes=[q("передаёт push на отправку", 46)],
              problem="Нет поведения при сбое передачи push на отправку и повторных попыток.")
        fs = [a, b]
        self.assertEqual(merge.merge_duplicates(fs, [], [], []), 1)
        self.assertEqual((a.status, b.status), ("open", "merged"))

    def test_the_guard_is_one_function_for_the_re_check_and_the_code(self):
        a = f("D-001", "Не задан шаг значения лимита")
        b = f("D-002", "Часовой пояс сброса не указан")
        for guard in ("topic", "place_or_topic", "none"):
            self.assertEqual(gate.merge_allowed(a, b, guard), merge.guard_allows(a, b, guard))
        self.assertEqual(merge.CODE_GUARD, gate.MERGE_GUARD)  # the program obeys the same choice as the re-check

    def test_checklist_items_decide_on_the_code_path_too(self):
        a = f("D-001", "Не описан отказ сервиса push", qid="В-01", quotes=[q("Сервис передаёт push", 4)])
        b = f("D-002", "Не описан отказ сервиса push", qid="В-01", quotes=[q("Сервис передаёт push", 4)])
        a.gap_ids, b.gap_ids = ["G-01"], ["G-02"]
        self.assertEqual(merge.merge_duplicates([a, b], [], [], []), 0)


# --- 3. levels ---------------------------------------------------------------------------------------------------


class Levels(unittest.TestCase):
    def test_merged_finding_is_not_lowered_first_and_the_survivor_takes_the_higher_level(self):
        a = f("D-001", "Не задан шаг значения лимита", sev="CRITICAL")
        a.blocker_reason = "main_flow"
        b = f("D-002", "Шаг значения лимита не задан", sev="MAJOR")
        # the re-check lowers D-001 in the first pass and merges D-002 into it in the second
        dec = [{"fid": "D-001", "decision": "keep", "severity": "MINOR", "reason": "Шаг значения лимита мелочь."},
               {"fid": "D-002", "decision": "same", "same_as": "D-001"}]
        gate.apply_gate([a, b], dec, injection=False, audit=[])
        self.assertEqual((a.status, b.status), ("merged", "open"))
        self.assertEqual(a.absorbed, [])
        self.assertEqual(b.absorbed[0]["severity"], "CRITICAL")  # its level before the step lowered it
        self.assertEqual((b.severity, b.blocker_reason), ("CRITICAL", "main_flow"))  # the higher of the two
        self.assertEqual([e["level"] for e in b.level_trail], ["MAJOR", "CRITICAL"])
        self.assertEqual(b.level_trail[-1]["by"], "merge")

    def test_higher_level_of_a_lowered_merged_finding_reaches_the_survivor(self):
        a = f("D-001", "Не задан шаг значения лимита", sev="MAJOR")
        b = f("D-002", "Шаг значения лимита не задан", sev="CRITICAL")
        b.blocker_reason = "main_flow"
        dec = [{"fid": "D-001", "decision": "keep", "severity": "MAJOR", "reason": ""},
               {"fid": "D-002", "decision": "same", "same_as": "D-001"}]
        gate.apply_gate([a, b], dec, injection=False, audit=[])
        self.assertEqual((a.status, b.status, b.severity), ("merged", "open", "CRITICAL"))
        self.assertEqual(b.blocker_reason, "main_flow")

    def test_survivor_level_is_the_higher_one_and_the_trail_agrees(self):
        low = f("D-001", "Диапазон без шага", sev="MINOR", quotes=[q("лимит от 100", 3)], reqs=("REQ-001",))
        high = f("", "Не задан шаг значения лимита", sev="MAJOR", source="rule:step-missing", rules=["step-missing"],
                 quotes=[q("лимит от 100", 3)], stage="rules")
        absorb(low, high, all_places=True, by="code")
        self.assertEqual(low.severity, "MAJOR")
        self.assertEqual(low.level_trail[-1]["level"], "MAJOR")
        self.assertEqual(low.level_trail[0]["level"], "MINOR")
        self.assertEqual(low.absorbed[0]["severity"], "MAJOR")

    def test_code_merge_keeps_the_higher_level(self):
        a = f("D-001", "Не описан сбой отправки push", sev="MINOR", crit="verifiability", qid="В-01",
              quotes=[q("Сервис передаёт push на отправку", 46)], problem="Что при сбое отправки push, не сказано.")
        b = f("D-002", "Сбой передачи push на отправку не описан", sev="MAJOR", qid="В-01",
              quotes=[q("передаёт push на отправку", 46)],
              problem="Нет поведения при сбое передачи push на отправку и повторных попыток.")
        merge.merge_duplicates([a, b], [], [], [])
        self.assertEqual((a.status, b.status, b.severity), ("merged", "open", "MAJOR"))
        self.assertEqual(a.absorbed, [])
        self.assertEqual(b.absorbed[0]["severity"], "MINOR")


# --- 4. what the report shows -------------------------------------------------------------------------------------


def make_result(findings: list[Finding], rejected=None, dropped=None) -> Result:
    reqs = [Requirement(rid="REQ-001", doc_id="Ф-1", section="Раздел", kind="functional",
                        quote="Сервис отвечает быстро.", summary="", block_ids=["b1"], line_start=5, line_end=5)]
    run = RunInfo(doc_name="doc.md", doc_title="Тест", mode="offline", provider="offline")
    return Result(run=run, requirements=reqs, findings=findings, rejected=rejected or [], dropped=dropped or [])


LONG_PROBLEM = ("Клиент проверяет только HTTP-код и не увидит ошибку, записанную в теле ответа, поэтому интеграция "
                "сочтёт запрос успешным и продолжит работу с неверными данными, а оператор узнает о сбое только "
                "из жалоб клиентов, причём спустя несколько дней после того, как данные уже попали в отчётность. "
                "Последнее слово.")


def merged_pair() -> list[Finding]:
    a = f("D-001", "Код 200 не гарантирует успех", sev="MAJOR", crit="consistency", quotes=[q(CODE200, 12)])
    b = f("D-002", "Ошибка валидации возвращается под кодом 200", sev="MINOR", crit="consistency",
          quotes=[q("Неверный ввод получает код 200", 14)], problem=LONG_PROBLEM)
    absorb(a, b, by="recheck")
    return [a]


class Card(unittest.TestCase):
    def test_card_lists_every_merged_finding_with_problem_and_place(self):
        (a,) = merged_pair()
        text = "\n".join(report_md.absorbed_lines(a))
        self.assertIn("Объединено с похожими замечаниями (1):", text)
        self.assertIn("Ошибка валидации возвращается под кодом 200", text)
        self.assertIn("строка 14: «Неверный ввод получает код 200»", text)
        self.assertIn("Клиент проверяет только HTTP-код", text)
        self.assertNotIn("Последнее слово", text)  # the problem is cut at about 240 characters
        self.assertNotIn("absorbed", text)
        body = text.split("**Ошибка валидации возвращается под кодом 200**.", 1)[1].split("строка 14", 1)[0]
        self.assertLessEqual(len(body), 250)
        self.assertTrue(body.strip().endswith("…"))

    def test_cut_is_at_a_word_boundary(self):
        cut = report_md._trim_words("слово " * 100, 240)
        self.assertLessEqual(len(cut), 241)
        self.assertTrue(cut.endswith("слово…"))

    def test_details_and_main_report_show_it_and_html_too(self):
        res = make_result(merged_pair())
        main = report_md.render(res, "r.md")
        details = report_md.render_details(res, "r.md", main)
        for text in (main, details):
            self.assertIn("Объединено с похожими замечаниями", text)
            self.assertIn("Ошибка валидации возвращается под кодом 200", text)
        self.assertIn("строка 14: «Неверный ввод получает код 200»", details)
        self.assertIn("Клиент проверяет только HTTP-код", details)
        page = report_html.render_html(exports.one_page_md(main, details, "r.md"), "t")
        self.assertIn("Объединено с похожими замечаниями", page)
        self.assertIn("Как найдено", page)

    def test_old_report_without_absorbed_still_renders(self):
        res = make_result(merged_pair())
        data = json.loads(report_json.render(res))
        for x in data["findings"]:
            x.pop("absorbed", None)
        back = report_json.from_data(data)
        self.assertEqual(back.findings[0].absorbed, [])
        main = report_md.render(back, "r.md")
        self.assertIn("Объединено с похожими замечаниями: «Ошибка валидации возвращается под кодом 200»", main)
        self.assertIsNotNone(report_md.render_details(back, "r.md", main))

    def test_old_report_gets_the_full_record_from_its_merged_cards(self):
        a = f("D-001", "Код 200 не гарантирует успех", crit="consistency", quotes=[q(CODE200, 12)])
        g = f("D-002", "Ошибка валидации возвращается под кодом 200", sev="MINOR", crit="consistency",
              quotes=[q("Неверный ввод получает код 200", 14)], problem="Нужен код 4xx.")
        g.status, g.merged_into, g.status_reason = "merged", "D-001", "повтор: то же, что D-001"
        res = make_result([a], rejected=[g])
        consistency.settle(res)
        (rec,) = a.absorbed
        self.assertEqual((rec["title"], rec["problem"], rec["by"]), (g.title, "Нужен код 4xx.", "dedup"))
        self.assertEqual(rec["quotes"][0]["line"], 14)

    def test_json_carries_absorbed_and_reads_back(self):
        res = make_result(merged_pair())
        data = json.loads(report_json.render(res))
        self.assertEqual(data["findings"][0]["absorbed"][0]["title"], "Ошибка валидации возвращается под кодом 200")
        back = report_json.from_data(data)
        self.assertEqual(back.findings[0].absorbed, res.findings[0].absorbed)

    def test_jira_export_lists_the_merged_findings(self):
        csv_text = exports.jira_csv(make_result(merged_pair()))
        self.assertIn("Объединено с похожими замечаниями:", csv_text)
        self.assertIn("Ошибка валидации возвращается под кодом 200", csv_text)
        self.assertIn("Как найдено:", csv_text)
        self.assertNotIn("`", csv_text.split("Как найдено:", 1)[1])

    def test_more_than_five_are_listed_by_title(self):
        a = f("D-001", "Главное замечание", sev="MAJOR")
        for k in range(7):
            absorb(a, f(f"D-1{k}", f"Повтор номер {k}", sev="MINOR", problem=f"Суть {k}."), by="code")
        lines = report_md.absorbed_lines(a)
        self.assertEqual(len(lines), 1 + 5 + 1)
        self.assertIn("И ещё 2", lines[-1])
        self.assertIn("Повтор номер 6", lines[-1])

    def test_no_internal_words_in_the_text(self):
        main = report_md.render(make_result(merged_pair()), "r.md")
        for word in ("absorbed", "дедупликац", "recheck"):
            self.assertNotIn(word, main)


class HowFound(unittest.TestCase):
    def test_rule_without_model(self):
        r = f("D-001", "Размытое слово", source="rule:vague-word", rules=["vague-word"], stage="rules", crit="unambiguity")
        text = report_md.how_found(r)
        self.assertTrue(text.startswith("правило без модели: «"))
        self.assertIn("vague-word", text)

    def test_model_review_names_the_criterion(self):
        text = report_md.how_found(f("D-001", "Х", crit="unambiguity", stage="review_clarity"))
        self.assertEqual(text, "модель, проверка требования по критерию «однозначность»")

    def test_model_set_stage(self):
        text = report_md.how_found(f("D-001", "Х", crit="set_consistency", stage="set_consistency", scope="set"))
        self.assertEqual(text, "модель, сверка разделов между собой")

    def test_both(self):
        text = report_md.how_found(f("D-001", "Х", source="both", rules=["vague-word"], crit="unambiguity",
                                     stage="review_clarity"))
        self.assertIn("правило: «", text)
        self.assertIn("и модель: проверка требования по критерию «однозначность»", text)

    def test_checklist_question(self):
        g = f("D-001", "Х", stage="set_gaps", crit="set_completeness", scope="set")
        g.gap_ids = ["G-04"]
        self.assertEqual(report_md.how_found(g), "модель, контрольный вопрос G-04")

    def test_every_card_form_has_the_line(self):
        crit = merged_pair()[0]
        crit.severity, crit.blocker_reason = "CRITICAL", "contradiction"
        minor = f("D-005", "Мелочь", sev="MINOR", quotes=[q("Сервис отвечает", 5)])
        major = f("D-006", "Существенное", sev="MAJOR", scope="set", crit="set_completeness", stage="set_completeness",
                  reqs=())
        res = make_result([crit, minor, major])
        main = report_md.render(res, "r.md")
        details = report_md.render_details(res, "r.md", main)
        for fid in ("D-001", "D-005", "D-006"):
            block = details.split(f'<a id="x-{fid.lower()}"></a>', 1)
            self.assertEqual(len(block), 2, fid)
            self.assertIn("Как найдено:", block[1].split("<a id=", 1)[0], fid)
        self.assertIn("Как найдено:", main.split('<a id="d-001"></a>', 1)[1].split("<a id=", 1)[0])
        self.assertIn("Как найдено: модель, проверка полноты набора требований",
                      main.split('<a id="d-006"></a>', 1)[1].split("<a id=", 1)[0])


# --- 7. what was taken out of the list ----------------------------------------------------------------------------


class Withdrawn(unittest.TestCase):
    def test_details_list_what_the_re_check_and_the_quote_check_took_out(self):
        gone = f("D-009", "Слабое замечание к набору", sev="MINOR", problem="Проверяющий ошибся.",
                 quotes=[q("Клиент может выключить уведомления", 30)])
        gone.status, gone.status_reason = "rejected", "Цитата этого не говорит."
        dq = DroppedQuote(stage="review_clarity", title="Выдуманная цитата", quote="Такого в документе нет",
                          reason="цитата не найдена в документе", problem="Описание выдуманного дефекта.")
        res = make_result(merged_pair(), rejected=[gone], dropped=[dq])
        main = report_md.render(res, "r.md")
        details = report_md.render_details(res, "r.md", main)
        self.assertIn("## Д. Что было снято и почему", details)
        self.assertIn("Слабое замечание к набору", details)
        self.assertIn("Цитата этого не говорит.", details)
        self.assertIn("Выдуманная цитата", details)
        self.assertIn("Описание выдуманного дефекта.", details)
        self.assertIn("Такого в документе нет", details)
        self.assertIn("(#x-part-d)", details)

    def test_nothing_taken_out_no_heading(self):
        res = make_result(merged_pair())
        details = report_md.render_details(res, "r.md", report_md.render(res, "r.md"))
        self.assertNotIn("Что было снято", details)

    def test_a_finding_without_a_verified_quote_is_never_open(self):
        from types import SimpleNamespace
        from revizor.merge import QuoteStats, model_finding
        qi = SimpleNamespace(find=lambda text, prefer_rid="": None)
        raw = {"criterion": "unambiguity", "severity": "MAJOR", "title": "Х", "problem": "Суть дефекта.",
               "quote": "Такой фразы нет в документе совсем"}
        found, dq = model_finding(raw, scope="requirement", stage="review_clarity", qi=qi, stats=QuoteStats(),
                                  known_rids=set(), rid="")
        self.assertIsNone(found)
        self.assertEqual(dq.problem, "Суть дефекта.")


# --- 6. judge input and program score ---------------------------------------------------------------------------


DOC = ("# Док\n"
       "Ошибка суммы возвращается с кодом 200 вместо кода ошибки.\n"
       "Срок хранения данных об операциях в документе не указан.\n")
KEY = {"document": "doc.md", "defects": [
    {"id": "T-01", "severity": "MAJOR", "criterion": "completeness", "title": "Срок хранения не указан",
     "anchors": ["Срок хранения данных об операциях в документе не указан"],
     "keywords": [["хранен"], ["не указан"]], "why": "-"}], "good": []}


def stored(findings):
    return {"run": {"doc_name": "doc.md", "mode": "offline"}, "requirements": [], "findings": findings}


def raw_finding(fid, title, problem, text, line, absorbed=None):
    return {"fid": fid, "scope": "requirement", "criterion": "completeness", "severity": "MAJOR", "title": title,
            "problem": problem, "recommendation": "", "status": "open", "advisory": False,
            "quotes": [{"text": text, "line_start": line, "line_end": line}], "req_ids": [],
            "absorbed": absorbed or []}


class JudgeAndScore(unittest.TestCase):
    def merged_stored(self):
        sub = {"title": "Срок хранения не указан", "problem": "Сколько хранить данные об операциях, не сказано.",
               "quotes": [{"line": 3, "text": "Срок хранения данных об операциях в документе не указан"}]}
        return stored([raw_finding("D-001", "Код 200 не гарантирует успех", "Клиент видит только код.",
                                   "Ошибка суммы возвращается с кодом 200 вместо кода ошибки.", 2, [sub])])

    def test_judge_input_passes_absorbed(self):
        out = judge_input.slim(self.merged_stored())
        (item,) = out["findings"]
        self.assertEqual(item["absorbed"][0]["title"], "Срок хранения не указан")
        self.assertEqual(item["absorbed"][0]["problem"], "Сколько хранить данные об операциях, не сказано.")
        self.assertEqual(item["absorbed"][0]["quotes"][0]["line"], 3)

    def test_judge_input_old_report_has_an_empty_list(self):
        old = stored([raw_finding("D-001", "Х", "У", "Ошибка суммы", 2)])
        for x in old["findings"]:
            x.pop("absorbed")
        self.assertEqual(judge_input.slim(old)["findings"][0]["absorbed"], [])

    def test_brief_describes_the_field(self):
        brief = (ROOT / "eval" / "judge" / "BRIEF.md").read_text(encoding="utf-8")
        self.assertIn("`absorbed`", brief)

    def test_score_counts_the_sub_items_by_default_and_not_with_the_option(self):
        res = self.merged_stored()
        with_abs = score.score_result(res, KEY, DOC)
        without = score.score_result(res, KEY, DOC, use_absorbed=False)
        self.assertEqual(with_abs["defects"][0]["status"], "strict")
        self.assertEqual(without["defects"][0]["status"], "missed")
        self.assertEqual(with_abs["defects"][0]["strict_fids"], ["D-001"])
        self.assertTrue(with_abs["options"]["absorbed"])
        self.assertFalse(without["options"]["absorbed"])

    def test_score_command_line_option(self):
        import contextlib
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "doc.md").write_text(DOC, encoding="utf-8")
            (d / "doc.key.json").write_text(json.dumps(KEY, ensure_ascii=False), encoding="utf-8")
            (d / "r.json").write_text(json.dumps(self.merged_stored(), ensure_ascii=False), encoding="utf-8")
            outs = []
            for extra in ([], ["--no-absorbed"]):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    score.main([str(d / "r.json"), "--key", str(d / "doc.key.json"), "--json"] + extra)
                outs.append(json.loads(buf.getvalue()))
            self.assertEqual([o["defects"][0]["status"] for o in outs], ["strict", "missed"])


if __name__ == "__main__":
    unittest.main()
