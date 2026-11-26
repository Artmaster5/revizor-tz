"""Rules on quantities and thresholds: each on a tiny positive and negative example, then on a fully specified text."""
from __future__ import annotations

import unittest

from revizor import rules as R
from tests.test_rules_time import Case, run

VALUE_IDS = ("rounding-missing", "value-without-unit", "step-missing", "threshold-conflict", "limit-no-behaviour")


class Registry(unittest.TestCase):
    def test_registered(self):
        for rid in VALUE_IDS:
            self.assertIn(rid, R.RULES)
            self.assertEqual(R.RULES[rid].scope, "set")
            self.assertFalse(R.RULES[rid].advisory)
            self.assertNotEqual(R.RULES[rid].severity, "CRITICAL")
        self.assertEqual(R.RULES["threshold-conflict"].severity, "MAJOR")
        self.assertEqual(R.RULES["rounding-missing"].severity, "MAJOR")


class Rounding(Case):
    def test_fires(self):
        f = self.fires("rounding-missing", "- ТР-1. Комиссия за перевод — 1,5 % от суммы перевода.\n")
        self.assertEqual(f.severity, "MAJOR")
        self.fires("rounding-missing", "- ТР-1. Остаток конвертируется по курсу банка в валюту счёта.\n")
        self.fires("rounding-missing", "- TR-1. Cashback is 2 % of the purchase amount.\n")

    def test_silent(self):
        self.silent("rounding-missing", "- ТР-1. Комиссия — 1,5 % от суммы перевода.\n- ТР-2. Суммы округляются до тиына по математическим правилам.\n")
        self.silent("rounding-missing", "- ТР-1. Комиссия — 1,5 % от суммы; результат хранится в тиынах.\n")
        self.silent("rounding-missing", "- TR-1. Cashback is 2 % of the purchase amount, rounded down to the nearest tiyn.\n")

    def test_not_money(self):
        self.silent("rounding-missing", "- ТР-1. Доступность сервиса — не ниже 99,9 % за календарный месяц.\n")
        self.silent("rounding-missing", "- ТР-1. Число обращений в контакт-центр снижается на 20 %.\n")
        self.silent("rounding-missing", "- TR-1. The data is available within 60 seconds for 99 % of purchases.\n")


class Unit(Case):
    def test_fires(self):
        self.fires("value-without-unit", "- ТР-1. Комиссия за перевод — 150.\n")
        self.fires("value-without-unit", "- ТР-1. Сумма перевода — от 100 до 500000.\n")
        self.fires("value-without-unit", "- ТР-1. Одноразовый код действует 180.\n")
        self.fires("value-without-unit", "- ТР-1. Размер файла не более 5.\n")

    def test_silent(self):
        self.silent("value-without-unit", "- ТР-1. Комиссия за перевод — 150 ₸.\n")
        self.silent("value-without-unit", "- ТР-1. Сумма перевода — от 100 до 500 000 тенге включительно.\n")
        self.silent("value-without-unit", "- ТР-1. Все суммы в документе указаны в тенге.\n- ТР-2. Комиссия за перевод — 150.\n")
        self.silent("value-without-unit", "- ТР-1. Одноразовый код действует 180 секунд.\n")
        self.silent("value-without-unit", "- ТР-1. Размер файла не более 5 МБ.\n")
        self.silent("value-without-unit", "- ТР-1. Комиссия за перевод — 0,95 % от суммы.\n")

    def test_counts_are_not_amounts(self):
        self.silent("value-without-unit", "- ТР-1. Лимит попыток ввода — 5.\n")
        self.silent("value-without-unit", "- ТР-1. Комиссия за уровень 1.\n")


class Step(Case):
    def test_fires(self):
        self.fires("step-missing", "- ТР-1. Клиент задаёт порог уведомления от 1 000 до 50 000 ₸.\n")
        self.fires("step-missing", "- TR-1. The administrator sets the daily limit from 100 to 5000.\n")

    def test_silent(self):
        self.silent("step-missing", "- ТР-1. Клиент задаёт порог уведомления — целое число тенге от 1 000 до 50 000.\n")
        self.silent("step-missing", "- ТР-1. Клиент задаёт порог уведомления от 1 000 до 50 000 ₸ с шагом 1 000 ₸.\n")
        self.silent("step-missing", "- ТР-1. Клиент задаёт порог уведомления от 1 000 до 50 000 ₸, кратно 1 000.\n")
        self.silent("step-missing", "- ТР-1. Клиент задаёт порог уведомления от 1 000 до 50 000 ₸.\n- ТР-2. Значение порога — целое.\n")

    def test_not_set_by_user(self):
        self.silent("step-missing", "- ТР-1. Сумма перевода — от 100 до 500 000 ₸.\n")
        self.silent("step-missing", "- ТР-1. Клиент задаёт срок кредита от 6 до 60 месяцев, целым числом месяцев.\n")


class Threshold(Case):
    def test_fires(self):
        f = self.fires("threshold-conflict",
                       "- ТР-1. Переводы на сумму более 1 000 ₸ требуют подтверждения кодом.\n"
                       "- ТР-2. Переводы на сумму от 1 000 ₸ проходят дополнительную проверку.\n")
        self.assertEqual(f.severity, "MAJOR")
        self.assertEqual(len(f.quotes), 2)
        self.assertEqual(f.criterion, "set_consistency")
        self.assertIn("Пример: при значении ровно 1 000", f.problem.replace("\xa0", " "))
        self.assertIn("не действует", f.problem)
        self.fires("threshold-conflict",
                   "- ТР-1. Лимит перевода — до 5 000 ₸.\n- ТР-2. Перевод не более 5 000 ₸ включительно проходит без подтверждения.\n")
        self.fires("threshold-conflict",
                   "- ТР-1. Комиссия не взимается с переводов менее 2 000 ₸.\n- ТР-2. Переводы не более 2 000 ₸ включительно не облагаются комиссией.\n")

    def test_silent(self):
        # a clean partition: «до N» and «от N» (or «более N» and «не более N») describe the two sides of one boundary
        self.silent("threshold-conflict",
                    "- ТР-1. Переводы до 1 000 ₸ проходят без кода.\n- ТР-2. Переводы от 1 000 ₸ требуют подтверждения кодом.\n")
        self.silent("threshold-conflict",
                    "- ТР-1. Переводы на сумму не более 1 000 ₸ проходят без кода.\n- ТР-2. Переводы на сумму более 1 000 ₸ требуют подтверждения кодом.\n")

    def test_unrelated_numbers(self):
        self.silent("threshold-conflict",
                    "- ТР-1. Переводы на сумму более 1 000 ₸ требуют кода.\n- ТР-2. Срок кредита — от 1 000 дней.\n")
        self.silent("threshold-conflict",
                    "- ТР-1. Пароль не короче 8 знаков.\n- ТР-2. Код подтверждения — от 8 цифр.\n")

    def test_range_in_one_sentence(self):
        self.silent("threshold-conflict", "- ТР-1. Сумма перевода — от 100 до 500 000 ₸ включительно.\n")
        self.silent("threshold-conflict", "- ТР-1. Возраст — от 21 года до 63 лет.\n- ТР-2. Время обработки до 21:00.\n")


class LimitNoBehaviour(Case):
    def test_fires(self):
        self.fires("limit-no-behaviour", "- ТР-1. Код подтверждения можно ввести не более 3 попыток.\n")
        self.fires("limit-no-behaviour", "- ТР-1. Клиент может отправить не более 5 запросов в минуту.\n")

    def test_silent(self):
        self.silent("limit-no-behaviour", "- ТР-1. Код можно ввести не более 3 попыток; после третьей неверной попытки вход блокируется на 15 минут.\n")
        self.silent("limit-no-behaviour", "- ТР-1. Код можно ввести не более 3 попыток.\n- ТР-2. При превышении числа попыток вход блокируется.\n")
        self.silent("limit-no-behaviour",
                    "- ТР-1. Клиент может отправить не более 5 запросов в минуту.\n- ТР-2. Шестой запрос отклоняется с кодом 429.\n")
        self.silent("limit-no-behaviour", "- ТР-1. Описание — не более 500 знаков.\n")


class Quiet(Case):
    SPEC = (
        "- ТР-1. Все суммы в документе указаны в тенге. Суммы округляются до тиына по математическим правилам.\n"
        "- ТР-2. Комиссия за перевод — 0,95 % от суммы перевода.\n"
        "- ТР-3. Клиент задаёт порог уведомления — целое число тенге от 1 000 до 50 000 включительно.\n"
        "- ТР-4. Переводы до 1 000 ₸ проходят без кода, переводы от 1 000 ₸ требуют подтверждения кодом.\n"
        "- ТР-5. Код подтверждения действует 180 секунд; допускается не более 3 попыток ввода, после третьей неверной попытки вход блокируется на 15 минут.\n"
        "- ТР-6. Размер файла — не более 5 МБ.\n"
    )

    def test_silent(self):
        found = run(self.SPEC)
        hits = [(f.rule_ids[0], [q.text for q in f.quotes]) for f in found if f.rule_ids[0] in VALUE_IDS]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
