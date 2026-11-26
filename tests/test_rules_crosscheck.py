"""Rules that compare places of one document: value sets, examples against tables, numbers against rules."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from revizor import rules as R
from revizor.loader import load
from revizor.model import SourceDoc
from revizor.slicer import heuristic_slice

HEAD = "# Спецификация сервиса переводов\n\nВерсия 1.0 от 01.11.2026. Автор: отдел цифровых продуктов.\n\n"
IDS = ("set-members-differ", "example-field-mismatch", "parameter-two-values", "example-arithmetic",
       "example-out-of-range", "requirement-without-criterion")

API = (
    "## 3. Интерфейс\n\n### Query-параметры\n\n"
    "| Parameter | Format | Mandatory | Logic | Description |\n| :--- | :--- | :--- | :--- | :--- |\n"
    "| `amount` | integer | Да | 100–500 000 | Сумма |\n"
    "| `phone` | string | Да | | Телефон |\n"
    "| `mode` | string | Нет | FAST / SLOW | Режим |\n"
    "| `day` | string | Нет | YYYY-MM-DD | День |\n\n"
    "### Request body\n\n```json\n%s\n```\n\n"
    "### Ответ\n\n| Parameter | Format | Description |\n| :--- | :--- | :--- |\n"
    "| `result` | string | Исход |\n| `id` | integer | Номер |\n\n"
    "#### 200. Successful\n\n```json\n%s\n```\n"
)
GOOD_REQ = '{"amount": 5000, "phone": "7770000000", "mode": "FAST", "day": "2026-12-01"}'
GOOD_RESP = '{"result": "OK", "id": 17}'


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
    def fires(self, rule, body, head=HEAD, severity=None):
        found = [f for f in run(head + body) if f.rule_ids[0] == rule]
        self.assertTrue(found, f"{rule} не сработало на:\n{body}")
        f = found[0]
        self.assertEqual(f.source, f"rule:{rule}")
        self.assertEqual(f.stage, "rules")
        self.assertIn(f.severity, ("MINOR", "MAJOR"))
        if severity:
            self.assertEqual(f.severity, severity)
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


class SetMembers(Case):
    TABLE = "## 3. Статусы заявки\n\n| Статус | Описание |\n| :--- | :--- |\n| NEW | Создана |\n| PAID | Оплачена |\n| REJECTED | Отклонена |\n| CLOSED | Закрыта |\n\n"

    def test_fires_table_against_text(self):
        f = self.fires("set-members-differ", self.TABLE + "## 5. Интеграция\n\nСтатусы заявки: NEW, PAID, REJECTED, EXPIRED.\n", severity="MAJOR")
        self.assertIn("CLOSED", f.problem)
        self.assertIn("EXPIRED", f.problem)
        self.assertEqual(len(f.quotes), 2)

    def test_fires_two_lists(self):
        self.fires("set-members-differ",
                   "## 2. Роли\n\nРоли пользователей: ADMIN, MANAGER, CASHIER.\n\n## 6. Доступ\n\nДопустимые роли — ADMIN, MANAGER, AUDITOR.\n")

    def test_fires_subset_of_two_full_lists(self):
        self.fires("set-members-differ", self.TABLE + "## 5. Интеграция\n\nСтатусы заявки: NEW, PAID, REJECTED.\n")

    def test_fires_example_value_not_in_list(self):
        self.fires("set-members-differ", self.TABLE + '## 5. Пример\n\n```json\n{"id": 5, "status": "FROZEN"}\n```\n')

    def test_silent(self):
        self.silent("set-members-differ", self.TABLE + "## 5. Интеграция\n\nСтатусы заявки: NEW, PAID, REJECTED, CLOSED.\n")
        # only one shared member: different sets with a common word
        self.silent("set-members-differ", self.TABLE + "## 5. Интеграция\n\nСтатусы платежа: NEW, SENT, FAILED.\n")
        # a happy-path chain is not a list
        self.silent("set-members-differ", self.TABLE + "## 5. Жизнь заявки\n\nСтатус меняется так: NEW → PAID → CLOSED.\n")
        self.silent("set-members-differ", self.TABLE + '## 5. Пример\n\n```json\n{"id": 5, "status": "PAID"}\n```\n')
        # different entities, both named
        self.silent("set-members-differ", self.TABLE + "## 5. Платежи\n\nСтатусы платежа: NEW, PAID, DONE, FAILED.\n")


class ExampleFields(Case):
    def test_fires_extra_key(self):
        f = self.fires("example-field-mismatch", API % ('{"amount": 5000, "phone": "777", "extra": 1}', GOOD_RESP))
        self.assertIn("extra", f.problem)

    def test_fires_type(self):
        f = self.fires("example-field-mismatch", API % ('{"amount": "5000", "phone": "777"}', GOOD_RESP))
        self.assertIn("amount", f.problem)
        f = self.fires("example-field-mismatch", API % (GOOD_REQ, '{"result": "OK", "id": "17"}'))
        self.assertIn("id", f.problem)

    def test_fires_mandatory_not_carried(self):
        f = self.fires("example-field-mismatch", API % ('{"amount": 5000}', GOOD_RESP))
        self.assertIn("phone", f.problem)

    def test_silent_consistent(self):
        self.silent("example-field-mismatch", API % (GOOD_REQ, GOOD_RESP))

    def test_silent_optional_and_error_examples(self):
        # an optional field may be absent; an error body is not the success table
        self.silent("example-field-mismatch", API % ('{"amount": 5000, "phone": "777"}', GOOD_RESP))
        self.silent("example-field-mismatch", API % (GOOD_REQ, GOOD_RESP) + '\n#### 400. Unsuccessful\n\n```json\n{"message": "Ошибка", "details": []}\n```\n')

    def test_silent_path_parameter(self):
        body = ("## 3. Интерфейс\n\n`GET /cards/{cardId}/info`\n\n### Параметры запроса\n\n"
                "| Parameter | Format | Mandatory | Description |\n| :--- | :--- | :--- | :--- |\n"
                "| `cardId` | string | Да | Карта |\n| `month` | string | Да | Месяц |\n\n"
                "### Request body\n\n```json\n{\"month\": \"2026-12\"}\n```\n")
        self.silent("example-field-mismatch", body)

    def test_silent_without_json_or_tables(self):
        self.silent("example-field-mismatch", "## 1. Описание\n\nСервис принимает переводы по номеру телефона.\n")


class TwoValues(Case):
    def test_fires(self):
        f = self.fires("parameter-two-values",
                       "## 1. Требования\n\n- ФТ-1. Максимальное время ответа — не более 2 секунд.\n\n## 4. Интеграция\n\nМаксимальное время ответа — не более 5 секунд.\n",
                       severity="MAJOR")
        self.assertEqual(len(f.quotes), 2)
        self.fires("parameter-two-values",
                   "## 1. Требования\n\n- ФТ-1. Срок хранения журнала составляет 5 лет.\n- ФТ-2. Срок хранения журнала составляет 3 года.\n")

    def test_fires_table(self):
        self.fires("parameter-two-values",
                   "## 1. Параметры\n\n| Параметр | Значение |\n| :--- | :--- |\n| Лимит перевода за сутки | 500 000 ₸ |\n\n"
                   "## 2. Требования\n\n- ФТ-1. Лимит перевода за сутки — 300 000 ₸.\n")

    def test_silent(self):
        self.silent("parameter-two-values", "## 1. Требования\n\n- ФТ-1. Время ответа — не более 2 секунд.\n- ФТ-2. Время хранения — не более 5 секунд.\n")
        # the same value in different units
        self.silent("parameter-two-values", "## 1. Требования\n\n- ФТ-1. Срок хранения журнала — 24 часа.\n- ФТ-2. Срок хранения журнала — 1 сутки.\n")
        # a minimum and a maximum of one parameter are different statements
        self.silent("parameter-two-values", "## 1. Требования\n\n- ФТ-1. Сумма перевода — не менее 100 ₸.\n- ФТ-2. Сумма перевода — не более 500 000 ₸.\n")
        # conditional alternatives
        self.silent("parameter-two-values", "## 1. Требования\n\n- ФТ-1. Для физических лиц лимит перевода — 500 000 ₸.\n- ФТ-2. Для юридических лиц лимит перевода — 5 000 000 ₸.\n")
        self.silent("parameter-two-values", "## 1. Требования\n\n- ФТ-1. Срок хранения журнала — 5 лет.\n- ФТ-2. Срок хранения заявок — 3 года.\n")


class Arithmetic(Case):
    def test_fires_percent(self):
        f = self.fires("example-arithmetic", "## 1. Расчёт\n\n- ФТ-1. Комиссия: 5% от 20 000 ₸ = 1 050 ₸.\n", severity="MAJOR")
        self.assertIn("1 050", f.title)
        self.assertIn("1000", f.problem)

    def test_fires_sum_and_product(self):
        self.fires("example-arithmetic", "## 1. Расчёт\n\n- ФТ-1. Пример: 48 500 + 1 500 = 51 000 ₸.\n")
        self.fires("example-arithmetic", "## 1. Расчёт\n\n- ФТ-1. Пример: 20 000 × 5% = 1 100 ₸.\n")
        self.fires("example-arithmetic", "## 1. Расчёт\n\n- ФТ-1. Пример: 3 × 4 = 13.\n")

    def test_fires_total_row(self):
        f = self.fires("example-arithmetic",
                       "## 1. Итоги\n\n| Статья | Сумма |\n| :--- | :--- |\n| Комиссия | 100 ₸ |\n| Налог | 250 ₸ |\n| Итого | 400 ₸ |\n")
        self.assertIn("350", f.title)

    def test_silent_correct(self):
        self.silent("example-arithmetic", "## 1. Расчёт\n\n- ФТ-1. Комиссия: 5% от 20 000 ₸ = 1 000 ₸.\n- ФТ-2. 48 500 + 1 500 = 50 000 ₸.\n- ФТ-3. 20 000 × 5% = 1 000 ₸.\n")
        self.silent("example-arithmetic", "## 1. Итоги\n\n| Статья | Сумма |\n| :--- | :--- |\n| Комиссия | 100 ₸ |\n| Налог | 250 ₸ |\n| Итого | 350 ₸ |\n")

    def test_silent_rounding_and_approx(self):
        self.silent("example-arithmetic", "## 1. Расчёт\n\n- ФТ-1. Комиссия: 0,95% от 10 000 ₸ = 95 ₸.\n- ФТ-2. 100 / 3 = 33,33.\n- ФТ-3. 100 / 3 = 33.\n")
        self.silent("example-arithmetic", "## 1. Расчёт\n\n- ФТ-1. 5% от 20 050 ₸ ≈ 1 000 ₸.\n- ФТ-2. Округление: 5% от 20 050 ₸ = 1 000 ₸ после округления.\n")
        self.silent("example-arithmetic", "## 1. Даты\n\n- ФТ-1. Период 01.11.2026 - 30.11.2026 = 30 дней. Версия 1.2 + 1.3.\n")


class OutOfRange(Case):
    def test_fires_range(self):
        f = self.fires("example-out-of-range", API % ('{"amount": 50, "phone": "777"}', GOOD_RESP))
        self.assertIn("100", f.problem)

    def test_fires_enum_and_date(self):
        f = self.fires("example-out-of-range", API % ('{"amount": 5000, "phone": "777", "mode": "TURBO"}', GOOD_RESP))
        self.assertIn("TURBO", f.problem)
        f = self.fires("example-out-of-range", API % ('{"amount": 5000, "phone": "777", "day": "01.12.2026"}', GOOD_RESP))
        self.assertIn("YYYY-MM-DD", f.problem)

    def test_silent(self):
        self.silent("example-out-of-range", API % (GOOD_REQ, GOOD_RESP))
        self.silent("example-out-of-range", API % ('{"amount": 100, "phone": "777", "mode": "SLOW"}', GOOD_RESP))
        self.silent("example-out-of-range", API % ('{"amount": 500000, "phone": "777"}', GOOD_RESP))


class CounterExample(Case):
    def test_two_values_example(self):
        f = self.fires("parameter-two-values",
                       "## 1. Требования\n\n- ФТ-1. Максимальное время ответа — не более 2 секунд.\n\n## 4. Интеграция\n\nМаксимальное время ответа — не более 6 секунд.\n")
        self.assertIn("Пример:", f.problem)
        self.assertIn("4 секунд", f.problem)

    def test_arithmetic_recount(self):
        f = self.fires("example-arithmetic", "## 1. Расчёт\n\n- ФТ-1. Комиссия: 1,5% от 12 000 ₸ = 150 ₸.\n")
        self.assertIn("Пересчёт: 1,5% от 12 000 ₸ = 180, в примере 150", f.problem)


class Echo(Case):
    def test_echo_leaves_trace(self):
        found = run(HEAD + "## 1. Требования\n\n- ФТ-1. Сервис всегда доступен клиентам.\n")
        self.assertFalse([f for f in found if f.advisory and "всегда" in f.title.lower()])
        keepers = [f for f in found if any(r.get("by") == "code" for r in f.absorbed)]
        self.assertTrue(keepers)
        rec = keepers[0].absorbed[0]
        self.assertEqual(set(rec), {"title", "problem", "recommendation", "severity", "criterion", "quotes", "source", "by"})
        self.assertTrue(rec["quotes"] and "line" in rec["quotes"][0])


class Traceability(Case):
    REQ = "## 1. Требования\n\n" + "".join(f"- ФТ-{k}. Система выполняет действие {k} по заявке клиента.\n" for k in range(1, 6))

    def test_fires(self):
        body = self.REQ + "\n## 2. Критерии приёмки\n\n| № | Условие | Результат |\n| :--- | :--- | :--- |\n| КП-1 | Действие по ФТ-1 | Выполнено |\n| КП-2 | Действие по ФТ-2 | Выполнено |\n| КП-3 | Действие по ФТ-3 | Выполнено |\n"
        f = self.fires("requirement-without-criterion", body)
        self.assertIn("ФТ-4", f.title)
        self.assertIn("ФТ-5", f.title)

    def test_silent_all_covered_or_unlinked(self):
        rows = "".join(f"| КП-{k} | Действие по ФТ-{k} | Выполнено |\n" for k in range(1, 6))
        self.silent("requirement-without-criterion", self.REQ + "\n## 2. Критерии приёмки\n\n| № | Условие | Результат |\n| :--- | :--- | :--- |\n" + rows)
        # criteria that do not name requirement numbers: nothing to compare
        self.silent("requirement-without-criterion", self.REQ + "\n## 2. Критерии приёмки\n\n| № | Условие | Результат |\n| :--- | :--- | :--- |\n| КП-1 | Клиент подаёт заявку | Заявка создана |\n")
        self.silent("requirement-without-criterion", self.REQ)

    def test_range_reference(self):
        body = self.REQ + "\n## 2. Критерии приёмки\n\n| № | Условие | Результат |\n| :--- | :--- | :--- |\n| КП-1 | Действия по ФТ-1 – ФТ-5 | Выполнено |\n| КП-2 | Повтор по ФТ-1 | Выполнено |\n| КП-3 | Повтор по ФТ-2 | Выполнено |\n"
        self.silent("requirement-without-criterion", body)


CLEAN = (
    "## 1. Статусы заявки\n\n| Статус | Описание |\n| :--- | :--- |\n| NEW | Создана |\n| PAID | Оплачена |\n| CLOSED | Закрыта |\n\n"
    "## 2. Требования\n\n"
    "- ФТ-1. Время ответа — не более 2 секунд для 95% запросов.\n"
    "- ФТ-2. Комиссия: 5% от 20 000 ₸ = 1 000 ₸. Статусы заявки: NEW, PAID, CLOSED.\n"
    "- ФТ-3. Срок хранения журнала — 5 лет.\n\n"
    + API % (GOOD_REQ, GOOD_RESP)
    + "\n## 6. Критерии приёмки\n\n| № | Условие | Результат |\n| :--- | :--- | :--- |\n"
      "| КП-1 | Проверка ФТ-1 | Выполнено |\n| КП-2 | Проверка ФТ-2 | Выполнено |\n| КП-3 | Проверка ФТ-3 | Выполнено |\n"
)


class CleanDocument(Case):
    def test_all_silent(self):
        found = [f for f in run(HEAD + CLEAN) if f.rule_ids[0] in IDS]
        self.assertFalse(found, [(f.rule_ids, [q.text for q in f.quotes]) for f in found])


if __name__ == "__main__":
    unittest.main()
