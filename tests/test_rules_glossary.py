"""Rules on terms: each on a tiny positive and negative example, then on a fully specified clean text."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from revizor import rules as R
from revizor.loader import load
from revizor.model import SourceDoc
from revizor.slicer import heuristic_slice

HEAD = "# Спецификация сервиса заявок\n\nВерсия 1.0 от 01.11.2026. Автор: отдел цифровых продуктов.\n\n## 1. Требования\n\n"
IDS = ("abbreviation-undefined", "term-defined-twice", "condition-undefined")
CLEAN = (
    "## 1. Термины\n\n| Термин | Определение |\n| :--- | :--- |\n"
    "| КДН | Коэффициент долговой нагрузки: платежи за месяц, делённые на доход. |\n"
    "| Стабильный доход | Доход, который поступал на счёт каждый из последних 6 месяцев. |\n\n"
    "## 2. Требования\n\n"
    "- ФТ-1. Клиент со стабильным доходом подаёт заявку через приложение.\n"
    "- ФТ-2. Система считает КДН при каждой заявке и сохраняет КДН в журнале.\n"
    "- ФТ-3. Результат отправляется по SMS и в push; SMS содержит номер заявки.\n"
)


def run(text: str):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "doc.md"
        p.write_text(text, encoding="utf-8")
        doc = load(p)
    assert isinstance(doc, SourceDoc), getattr(doc, "code", doc)
    reqs, skipped = heuristic_slice(doc)
    found = R.run_rules(doc, reqs, skipped)
    assert not R.last_errors, R.last_errors
    return found


class Case(unittest.TestCase):
    def fires(self, rule, body, head=HEAD):
        found = [f for f in run(head + body) if f.rule_ids[0] == rule]
        self.assertTrue(found, f"{rule} не сработало на:\n{body}")
        self.assertEqual(len(found), 1, "одна находка на документ")
        f = found[0]
        self.assertEqual(f.source, f"rule:{rule}")
        self.assertEqual(f.stage, "rules")
        self.assertIn(f.severity, ("MINOR", "MAJOR"))
        self.assertTrue(f.quotes)
        self.assertLessEqual(len(f.quotes), 5)
        self.assertTrue(f.question and f.question.text.endswith("?"))
        for q in f.quotes:
            self.assertGreaterEqual(q.line_start, 1)
            self.assertTrue(q.text.strip())
        return f

    def silent(self, rule, body, head=HEAD):
        hits = [f for f in run(head + body) if f.rule_ids[0] == rule]
        self.assertFalse(hits, f"{rule} сработало зря на:\n{body}\n{[q.text for f in hits for q in f.quotes]}")


class Registry(unittest.TestCase):
    def test_registered(self):
        for rid in IDS:
            self.assertIn(rid, R.RULES)
            self.assertEqual(R.RULES[rid].scope, "set")
            self.assertFalse(R.RULES[rid].advisory)
            self.assertNotEqual(R.RULES[rid].severity, "CRITICAL")


class Abbreviation(Case):
    def test_fires(self):
        f = self.fires("abbreviation-undefined", "- ФТ-1. Система считает КДН по заявке.\n- ФТ-2. КДН сохраняется в журнале.\n")
        self.assertEqual(f.severity, "MINOR")
        self.assertIn("КДН", f.title)

    def test_fires_latin_and_lists_all(self):
        f = self.fires("abbreviation-undefined", "- ФТ-1. Система передаёт RRN и КДН.\n- ФТ-2. RRN и КДН хранятся 5 лет.\n")
        self.assertIn("RRN", f.title)
        self.assertIn("КДН", f.title)

    def test_silent_when_expanded(self):
        for body in (
            "- ФТ-1. Система считает КДН (коэффициент долговой нагрузки).\n- ФТ-2. КДН сохраняется в журнале.\n",
            "- ФТ-1. Коэффициент долговой нагрузки (КДН) считается по заявке.\n- ФТ-2. КДН сохраняется.\n",
            "- ФТ-1. КДН — коэффициент долговой нагрузки клиента.\n- ФТ-2. КДН сохраняется в журнале.\n",
            "- ФТ-1. Система считает КДН.\n- ФТ-2. КДН сохраняется.\n\n## 2. Термины\n\n| Термин | Определение |\n| :--- | :--- |\n| КДН | Долговая нагрузка клиента |\n",
        ):
            self.silent("abbreviation-undefined", body)

    def test_silent_known_and_single(self):
        self.silent("abbreviation-undefined", "- ФТ-1. Сервис отправляет SMS и push по API в формате JSON.\n- ФТ-2. SMS содержит ИИН и PIN. API возвращает JSON.\n")
        self.silent("abbreviation-undefined", "- ФТ-1. Система считает КДН по заявке.\n")

    def test_silent_values_and_ids(self):
        # a status value in quotes or a table cell is not an abbreviation; neither is an identifier prefix
        self.silent("abbreviation-undefined", "- ФТ-1. Статус «ОТКАЗ» ставится при просрочке.\n- ФТ-2. Решение «ОТКАЗ» отправляется клиенту.\n")
        self.silent("abbreviation-undefined", "- ФТ-1. Заявка получает статус ACTIVE.\n- ФТ-2. Статус ACTIVE сохраняется.\n")
        self.silent("abbreviation-undefined", "- ЦН-1. Клиент входит.\n- ЦН-2. Клиент выходит. См. ЦН-1 и ЦН-2.\n")


class TermTwice(Case):
    def test_fires_numbers(self):
        f = self.fires("term-defined-twice",
                       "- Активный клиент — это клиент, у которого была операция за последние 30 дней.\n"
                       "- ФТ-1. Активный клиент — это клиент, у которого была операция за последние 90 дней.\n")
        self.assertEqual(len(f.quotes), 2)

    def test_fires_glossary_table_and_prose(self):
        self.fires("term-defined-twice",
                   "| Термин | Определение |\n| :--- | :--- |\n| Лимит перевода | Максимальная сумма одного перевода, 500 000 тенге. |\n\n"
                   "- ФТ-1. Под лимитом перевода понимается максимальная сумма за сутки по всем переводам, 300 000 тенге.\n")

    def test_silent(self):
        self.silent("term-defined-twice", "- Активный клиент — это клиент, у которого была операция за последние 30 дней.\n- ФТ-1. Заявку подаёт клиент.\n")
        # the same definition repeated is not a conflict
        self.silent("term-defined-twice",
                    "- Активный клиент — это клиент, у которого была операция за последние 30 дней.\n"
                    "- ФТ-1. Активный клиент — это клиент, у которого была операция за последние 30 дней.\n")
        self.silent("term-defined-twice", "- Заявка — это запрос клиента на кредит.\n- Платёж — это списание суммы со счёта клиента.\n")


class ConditionUndefined(Case):
    def test_fires(self):
        f = self.fires("condition-undefined", "- ФТ-1. Заявку подаёт клиент, если у него стабильный доход.\n")
        self.assertIn("стабильный доход", f.title)
        self.fires("condition-undefined", "- ФТ-1. Держатель активной карты задаёт порог расходов.\n")
        self.fires("condition-undefined", "- ФТ-1. При подозрительной операции система блокирует перевод.\n")
        self.fires("condition-undefined", "- ФТ-1. При крупной сумме перевода требуется подтверждение.\n")

    def test_fires_english(self):
        self.fires("condition-undefined", "- FR-1. A transfer is blocked if the customer has a suspicious transaction.\n")

    def test_silent_when_defined(self):
        self.silent("condition-undefined",
                    "- ФТ-1. Заявку подаёт клиент, если у него стабильный доход.\n- ФТ-2. Доход считается стабильным, если он поступал каждый из последних 6 месяцев.\n")
        self.silent("condition-undefined",
                    "- ФТ-1. Держатель активной карты задаёт порог.\n\n| Термин | Определение |\n| :--- | :--- |\n| Активная карта | Карта с операцией за 90 дней |\n")
        self.silent("condition-undefined", "- ФТ-1. Активная карта — карта, по которой была операция за 90 дней.\n- ФТ-2. Держатель активной карты задаёт порог.\n")

    def test_silent_without_condition(self):
        # an adjective that is not used as a condition, or names everybody
        self.silent("condition-undefined", "- ФТ-1. Лимит для новых и действующих клиентов — 500 000 тенге.\n")
        self.silent("condition-undefined", "- ФТ-1. Как клиент, я хочу видеть крупные цифры остатка на экране.\n")
        self.silent("condition-undefined", "- ФТ-1. Клиент видит остаток по карте.\n")


class CleanDocument(Case):
    def test_all_silent(self):
        found = [f for f in run(HEAD + CLEAN) if f.rule_ids[0] in IDS]
        self.assertFalse(found, [(f.rule_ids, [q.text for q in f.quotes]) for f in found])


if __name__ == "__main__":
    unittest.main()
