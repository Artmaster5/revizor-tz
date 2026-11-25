"""One defect = one place AND one topic. A shared quote alone never merges two findings (wave 5, merge fix).

Offline, invented texts only."""
from __future__ import annotations

import unittest

from revizor import merge
from revizor.model import Finding, Quote

RULE = "Возврат уменьшает итог месяца исходной операции, отмена не учитывается"
LIMIT = "Клиент задаёт лимит от 100 до 5 000 единиц включительно"


def q(text: str = RULE, line: int = 10, rid: str = "REQ-001") -> Quote:
    return Quote(text=text, line_start=line, line_end=line, rid=rid)


def f(title: str, problem: str = "", *, sev: str = "MAJOR", crit: str = "completeness", scope: str = "requirement",
      quotes=None, gaps=None, fid: str = "", source: str = "model", rules=None, reqs=None) -> Finding:
    return Finding(scope=scope, criterion=crit, severity=sev, title=title, problem=problem, recommendation="",
                   quotes=quotes if quotes is not None else [q()], req_ids=reqs if reqs is not None else ["REQ-001"],
                   source=source, rule_ids=rules or [], gap_ids=gaps or [], fid=fid)


class DedupeByTopic(unittest.TestCase):
    def test_same_quote_different_topics_stay_apart(self):
        a = f("Не описан возврат за прошлый месяц")
        b = f("Не определено, как учитываются операции в другой валюте")
        audit: list[dict] = []
        kept = merge.dedupe_model([a, b], audit)
        self.assertEqual(len(kept), 2)
        self.assertEqual(audit, [])

    def test_same_quote_same_topic_merge(self):
        a = f("Метод сохранения лимита описан как GET")
        b = f("Сохранение лимита описано методом GET, который не должен менять данные")
        kept = merge.dedupe_model([a, b], [])
        self.assertEqual(len(kept), 1)

    def test_stock_openers_do_not_make_a_topic(self):
        a = f("Не описано поведение при выключенном переключателе")
        b = f("Не описано поведение, если карта заблокирована")
        self.assertFalse(merge.words_alike(a, b))
        self.assertEqual(len(merge.dedupe_model([a, b], [])), 2)

    def test_codes_count_as_topic_words(self):
        a = f("Для чужого объекта в заголовке 403, в тексте 404")
        b = f("Заголовок 403 не совпадает с ответом 404")
        self.assertTrue(merge.words_alike(a, b))

    def test_non_atomic_note_is_not_swallowed_by_an_undefined_term(self):
        a = f("«Итог месяца» не определён", crit="unambiguity")
        b = f("Три правила в одном пункте", crit="singularity")  # same family «clear», same quote
        self.assertEqual(len(merge.dedupe_model([a, b], [])), 2)

    def test_problem_text_can_carry_the_topic(self):
        a = f("Единицы суммы разные", "Сумма операции хранится в копейках, лимит задан в рублях, правила пересчёта нет.")
        b = f("Нет правила пересчёта", "Лимит задан в рублях, сумма операции хранится в копейках; правила пересчёта нет.")
        self.assertEqual(len(merge.dedupe_model([a, b], [])), 1)


class ChecklistFindings(unittest.TestCase):
    def test_different_checklist_items_never_merge(self):
        a = f("Не задан шаг значения лимита", gaps=["VL1"], scope="set", crit="set_completeness", quotes=[q(LIMIT)])
        b = f("Не задан шаг для нескольких карт лимита", gaps=["VL6"], scope="set", crit="set_completeness",
              quotes=[q(LIMIT)])
        self.assertFalse(merge.same_topic(a, b))  # the words are close, the checklist items differ
        self.assertEqual(len(merge.dedupe_model([a, b], [])), 2)

    def test_same_checklist_item_at_one_place_merges(self):
        a = f("Не задан шаг значения лимита", gaps=["VL1"], scope="set", crit="set_completeness", quotes=[q(LIMIT)])
        b = f("Кратность суммы не указана", gaps=["VL1"], scope="set", crit="set_completeness", quotes=[q(LIMIT)])
        kept = merge.dedupe_model([a, b], [])
        self.assertEqual(len(kept), 1)

    def test_checklist_finding_is_not_lost_in_a_lens_finding_about_something_else(self):
        lens = f("Термин «активный клиент» не определён", quotes=[q(LIMIT)])
        gap = f("Не задан шаг значения лимита", gaps=["VL1"], scope="set", crit="set_completeness", quotes=[q(LIMIT)])
        kept = merge.dedupe_model([lens, gap], [])
        self.assertEqual(len(kept), 2)
        self.assertEqual(lens.gap_ids, [])

    def test_checklist_finding_joins_a_lens_finding_about_the_same_thing(self):
        lens = f("Шаг значения лимита не задан", quotes=[q(LIMIT)])
        gap = f("Не задан шаг значения лимита", gaps=["VL1"], scope="set", crit="set_completeness", quotes=[q(LIMIT)])
        kept = merge.dedupe_model([lens, gap], [])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].gap_ids, ["VL1"])  # the completeness map follows the survivor


class RuleFolding(unittest.TestCase):
    def test_rule_joins_the_model_finding_on_its_exact_line(self):
        model = f("Срок «быстро» нельзя измерить", crit="verifiability", quotes=[q("Ответ приходит быстро", 20)])
        rule = f("Неизмеримое слово", crit="verifiability", quotes=[q("быстро", 20)], source="rule:vague-word",
                 rules=["vague-word"])
        out = merge.fold_rules([rule], [model], [])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].source, "both")


class SkepticGuards(unittest.TestCase):
    def test_merge_of_unrelated_findings_is_refused(self):
        a = f("Нет версии и даты документа", fid="D-001", quotes=[q("Версия:", 3, "")], reqs=[])
        b = f("Блок согласующих пуст", fid="D-002", quotes=[q("| Подразделение | ФИО | Подпись |", 90, "")], reqs=[])
        audit: list[dict] = []
        out = merge.apply_skeptic([a, b], [{"fid": "D-002", "decision": "merge", "merge_into": "D-001",
                                            "reason": "тоже про оформление"}], injection=False, audit=audit)
        self.assertEqual((b.status, b.severity), ("open", "MAJOR"))
        self.assertEqual(out.merged, 0)
        self.assertTrue(any(x["what"] == "skeptic_merge_refused" for x in audit))

    def test_merge_at_one_place_is_accepted(self):
        a = f("Для чужого объекта указан код 403", fid="D-001")
        b = f("Расхождение заголовка и текста ответа", fid="D-002")
        out = merge.apply_skeptic([a, b], [{"fid": "D-002", "decision": "merge", "merge_into": "D-001"}],
                                  injection=False, audit=[])
        self.assertEqual((b.status, b.merged_into, out.merged), ("merged", "D-001", 1))

    def test_merge_by_words_across_places_is_accepted(self):
        a = f("Адрес боевого контура не задан", fid="D-001", quotes=[q("Prod: —", 40, "REQ-004")], reqs=["REQ-004"])
        b = f("Не задан адрес боевого контура, стоит прочерк", fid="D-002", quotes=[q("URL", 70, "REQ-009")],
              reqs=["REQ-009"])
        merge.apply_skeptic([a, b], [{"fid": "D-002", "decision": "merge", "merge_into": "D-001"}],
                            injection=False, audit=[])
        self.assertEqual(b.status, "merged")

    def test_raise_argued_with_another_defect_is_refused(self):
        a = f("Не описана ветка, когда лимит уже превышен", fid="D-001")
        audit: list[dict] = []
        merge.apply_skeptic([a], [{"fid": "D-001", "decision": "keep", "severity": "CRITICAL", "blocker": "protocol",
                                   "reason": "Новое замечание, которого не было в списке: метод сохранения описан "
                                             "как GET, хотя он меняет настройку."}], injection=False, audit=audit)
        self.assertEqual((a.severity, a.blocker_reason), ("MAJOR", ""))
        self.assertTrue(any(x["what"] == "skeptic_raise_refused" for x in audit))
        b = f("Идентификатор передаётся в адресе запроса и попадает в журналы", fid="D-002")
        merge.apply_skeptic([b], [{"fid": "D-002", "decision": "keep", "severity": "CRITICAL", "blocker": "protocol",
                                   "reason": "Метод сохранения настроек описан как GET, изменение данных таким "
                                             "методом нарушает правила протокола."}], injection=False, audit=[])
        self.assertEqual(b.severity, "MAJOR")

    def test_raise_argued_with_the_finding_itself_is_accepted(self):
        a = f("Непонятно, по какой дате определяется месяц операции", fid="D-001")
        merge.apply_skeptic([a], [{"fid": "D-001", "decision": "keep", "severity": "CRITICAL",
                                   "blocker": "contradiction",
                                   "reason": "Одно правило относит операцию к месяцу по дате совершения, пример — "
                                             "к следующему месяцу; от выбора меняется сумма."}],
                            injection=False, audit=[])
        self.assertEqual((a.severity, a.blocker_reason), ("CRITICAL", "contradiction"))

    def test_lowering_needs_no_fit(self):
        a = f("Не описан редкий сбой", fid="D-001")
        merge.apply_skeptic([a], [{"fid": "D-001", "decision": "keep", "severity": "MINOR", "reason": ""}],
                            injection=False, audit=[])
        self.assertEqual(a.severity, "MINOR")


if __name__ == "__main__":
    unittest.main()
