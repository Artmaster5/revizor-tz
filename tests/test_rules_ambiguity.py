"""Rules on words with two readings and on one rule stated twice: each on a tiny positive and negative example."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from revizor import rules as R
from revizor.loader import load
from revizor.model import SourceDoc
from revizor.slicer import heuristic_slice

HEAD = "# Спецификация сервиса платежей\n\nВерсия 1.0 от 01.11.2026. Автор: отдел цифровых продуктов.\n\n## 1. Требования\n\n"
FILLER = ("- ТР-91. Клиент видит историю платежей за последние 12 месяцев в разделе «История».\n"
          "- ТР-92. Квитанция по платежу сохраняется в формате PDF из карточки операции.\n")
AMB_IDS = ("period-event", "city-or-zone", "revert-no-list", "moment-vs-posted", "restated-rule", "amount-currency",
           "balance-hold", "operation-date", "fee-inclusion", "actor-names")


def run(body: str, head: str = HEAD, filler: str = FILLER):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "doc.md"
        p.write_text(head + body + "\n" + filler, encoding="utf-8")
        doc = load(p)
    assert isinstance(doc, SourceDoc), getattr(doc, "code", doc)
    reqs, skipped = heuristic_slice(doc)
    found = R.run_rules(doc, reqs, skipped)
    assert not R.last_errors, R.last_errors
    return found


class Case(unittest.TestCase):
    def fires(self, rule, body, head: str = HEAD):
        found = [f for f in run(body, head) if f.rule_ids[0] == rule]
        self.assertTrue(found, f"{rule} не сработало на:\n{body}")
        f = found[0]
        self.assertEqual(f.source, f"rule:{rule}")
        self.assertEqual(f.stage, "rules")
        self.assertIn(f.severity, ("MINOR", "MAJOR"))
        self.assertTrue(f.quotes)
        self.assertLessEqual(len(f.quotes), 5)
        self.assertTrue(f.question and f.question.text.endswith("?"))
        for q in f.quotes:
            self.assertGreaterEqual(q.line_start, 1)
        return f

    def silent(self, rule, body, head: str = HEAD):
        hits = [f for f in run(body, head) if f.rule_ids[0] == rule]
        self.assertFalse(hits, f"{rule} сработало зря на:\n{body}\n{[q.text for f in hits for q in f.quotes]}")


class Registry(unittest.TestCase):
    def test_registered(self):
        for rid in AMB_IDS:
            self.assertIn(rid, R.RULES)
            self.assertEqual(R.RULES[rid].scope, "set")
            self.assertFalse(R.RULES[rid].advisory)
            self.assertNotEqual(R.RULES[rid].severity, "CRITICAL")


EVENTS = "- ТР-1. Покупка сначала проходит карточную авторизацию, затем банк проводит операцию: проведение занимает до трёх дней.\n"


class PeriodEvent(Case):
    def test_fires(self):
        f = self.fires("period-event", EVENTS + "- ТР-2. Лимит покупок — не более 300 000 ₸ в календарный месяц.\n")
        self.assertEqual(f.severity, "MINOR")
        self.assertEqual(len(f.quotes), 1)
        self.fires("period-event", EVENTS + "- ТР-2. Клиент может сделать не более 10 переводов за сутки.\n")

    def test_silent_when_event_is_named(self):
        self.silent("period-event", EVENTS + "- ТР-2. Лимит покупок — не более 300 000 ₸ в календарный месяц.\n"
                                             "- ТР-3. Операция относится к месяцу по дате проведения.\n")
        self.silent("period-event", EVENTS + "- ТР-2. Месяц определяется по дате совершения покупки.\n- ТР-3. Лимит — не более 300 000 ₸ в месяц.\n")
        self.silent("period-event", EVENTS + "- ТР-2. Лимит покупок по дате проведения — не более 300 000 ₸ в календарный месяц.\n")
        self.silent("period-event", "- TR-1. A purchase is authorised first and posted later.\n- TR-2. The month of a purchase is determined by the transaction posting date.\n"
                                    "- TR-3. Cashback is capped at 30 000 KZT per month.\n")

    def test_silent_look_alikes(self):
        # one dated event only: nothing to choose from
        self.silent("period-event", "- ТР-1. Лимит покупок — не более 300 000 ₸ в календарный месяц.\n")
        # the login «авторизация» is not a card authorisation; «курс покупки» is not a purchase
        self.silent("period-event", "- ТР-1. Для входа нужна авторизация клиента по коду.\n- ТР-2. Курс покупки валюты проводится казначейством.\n"
                                    "- ТР-3. Клиент может обменять не более 10 000 USD в сутки.\n")
        # a monthly payment is not a counted limit
        self.silent("period-event", EVENTS + "- ТР-2. Ежемесячный платёж по кредиту списывается 5 числа.\n")


class CityZone(Case):
    def test_fires(self):
        self.fires("city-or-zone", "- ТР-1. Месяц определяется по дате совершения покупки в Алматы.\n")
        self.fires("city-or-zone", "- ТР-1. Сутки заканчиваются в 00:00 в Астане, тогда обнуляется лимит.\n")

    def test_silent(self):
        self.silent("city-or-zone", "- ТР-1. Месяц определяется по дате совершения покупки по времени Астаны.\n")
        self.silent("city-or-zone", "- ТР-1. Месяц определяется по дате совершения покупки в Алматы.\n- ТР-2. Все даты и время — в UTC+5.\n")
        self.silent("city-or-zone", "- ТР-1. Отделение банка находится в Алматы, режим работы с 9:00 до 18:00.\n")
        self.silent("city-or-zone", "- ТР-1. Месяц определяется по дате совершения покупки.\n")


class RevertNoList(Case):
    def test_fires(self):
        self.fires("revert-no-list", "- ТР-1. При отклонении заявки система возвращает объект в прежнее состояние.\n")
        self.fires("revert-no-list", "- ТР-1. При ошибке банка операция откатывается.\n")
        self.fires("revert-no-list", "- TR-1. On failure the system rolls back the changes.\n")

    def test_silent(self):
        self.silent("revert-no-list", "- ТР-1. При отклонении заявки возвращается прежнее состояние: статус заявки, сумма на счёте и лимит.\n")
        self.silent("revert-no-list", "- ТР-1. При отклонении заявки возвращается прежнее состояние, а именно статус и сумма.\n")
        self.silent("revert-no-list", "- ТР-1. При ошибке клиент возвращается на предыдущий экран.\n")
        self.silent("revert-no-list", "- ТР-1. Клиент отменяет заявку, и она получает статус «Отменена».\n")


class MomentVsPosted(Case):
    def test_fires(self):
        f = self.fires("moment-vs-posted", "- ТР-1. Клиент узнаёт о достижении лимита расходов в момент покупки.\n"
                                           "- ТР-2. В расходы входят проведённые покупки.\n")
        self.assertEqual(len(f.quotes), 2)

    def test_silent(self):
        self.silent("moment-vs-posted", "- ТР-1. Клиент узнаёт о достижении лимита расходов в момент покупки.\n- ТР-2. Покупки обрабатываются в порядке поступления.\n")
        self.silent("moment-vs-posted", "- ТР-1. В расходы входят проведённые покупки.\n")
        self.silent("moment-vs-posted", "- ТР-1. Клиент узнаёт о достижении лимита расходов в момент покупки.\n- ТР-2. В расходы входят проведённые покупки.\n"
                                        "- ТР-3. Проведённая покупка считается в расходах в момент проведения, окончательная сумма расчёта берётся из проводки.\n")
        self.silent("moment-vs-posted", "- ТР-1. Заявка создаётся в момент подачи.\n- ТР-2. Рассматриваются только обработанные заявки.\n")


class RestatedRule(Case):
    BODY_A = "## 2. Лимиты\n\n- ТР-10. Клиент может изъять с депозита не более двух раз в календарный месяц, отклонённые изъятия в счётчик не входят.\n\n"
    BODY_B = "## 3. Ограничения\n\n- ТР-20. Клиент может изъять с депозита не более трёх раз в календарный месяц, отклонённые изъятия в счётчик не входят.\n\n"

    def test_fires_with_different_numbers(self):
        f = self.fires("restated-rule", self.BODY_A + self.BODY_B)
        self.assertEqual(f.severity, "MAJOR")
        self.assertEqual(len(f.quotes), 2)

    def test_fires_same_numbers_other_words(self):
        b = ("## 3. Ограничения\n\n- ТР-20. Изъять с депозита клиент может не более двух раз в календарный месяц, при этом отклонённые изъятия не входят в счётчик.\n\n")
        f = self.fires("restated-rule", self.BODY_A + b)
        self.assertEqual(f.severity, "MINOR")

    def test_silent(self):
        # an exact copy belongs to the rule «duplicate»
        self.silent("restated-rule", self.BODY_A + self.BODY_A.replace("ТР-10", "ТР-20").replace("2. Лимиты", "3. Ограничения"))
        # different rules with the same vocabulary but another subject
        self.silent("restated-rule", self.BODY_A + "## 3. Ограничения\n\n- ТР-20. Клиент может открыть карту в приложении не более пяти раз в год, закрытые карты в счётчик не входят.\n\n")
        # same section
        self.silent("restated-rule", self.BODY_A.rstrip() + "\n- ТР-11. Клиент может изъять с депозита не более трёх раз в календарный месяц, отклонённые изъятия в счётчик не входят.\n\n")


class AmountCurrency(Case):
    def test_fires(self):
        self.fires("amount-currency", "- ТР-1. Покупка возможна в тенге и в долларах США, конвертация идёт по курсу банка.\n"
                                      "- ТР-2. Кэшбэк начисляется от суммы операции.\n")

    def test_silent(self):
        self.silent("amount-currency", "- ТР-1. Покупка возможна в тенге и в долларах США, конвертация идёт по курсу банка.\n"
                                       "- ТР-2. Кэшбэк начисляется от суммы операции в тенге.\n")
        self.silent("amount-currency", "- ТР-1. Покупка возможна в тенге и в долларах США.\n- ТР-2. Сумма операции — сумма в валюте счёта после конвертации.\n"
                                       "- ТР-3. Кэшбэк начисляется от суммы операции.\n")
        self.silent("amount-currency", "- ТР-1. Кэшбэк начисляется от суммы операции.\n")  # one currency only


class BalanceHold(Case):
    def test_fires(self):
        self.fires("balance-hold", "- ТР-1. При авторизации сумма покупки блокируется на счёте (холд) до проведения.\n"
                                   "- ТР-2. Покупка отклоняется, если остаток на счёте меньше суммы.\n")

    def test_silent(self):
        self.silent("balance-hold", "- ТР-1. При авторизации сумма покупки блокируется на счёте (холд) до проведения.\n"
                                    "- ТР-2. Покупка отклоняется, если доступный остаток на счёте меньше суммы.\n")
        self.silent("balance-hold", "- ТР-1. При авторизации сумма покупки блокируется на счёте (холд) до проведения.\n"
                                    "- ТР-2. Доступный остаток — остаток на счёте за вычетом заблокированных сумм.\n- ТР-3. Остаток на счёте показывается на главном экране.\n")
        self.silent("balance-hold", "- ТР-1. Остаток на счёте показывается на главном экране.\n")  # no holds in the document


class OperationDate(Case):
    DOC = "- ТР-1. Покупка сначала проходит карточную авторизацию, затем банк проводит операцию.\n"

    def test_fires(self):
        self.fires("operation-date", self.DOC + "- ТР-2. В выписке показывается дата операции.\n")

    def test_silent(self):
        self.silent("operation-date", self.DOC + "- ТР-2. В выписке показывается дата проведения операции.\n")
        self.silent("operation-date", self.DOC + "- ТР-2. Дата операции — дата авторизации.\n- ТР-3. В выписке показывается дата операции.\n")
        self.silent("operation-date", "- ТР-1. В выписке показывается дата операции.\n")


class FeeInclusion(Case):
    def test_fires(self):
        self.fires("fee-inclusion", "- ТР-1. Комиссия за перевод — 150 ₸.\n- ТР-2. Максимальная сумма перевода — 500 000 ₸.\n")

    def test_silent(self):
        self.silent("fee-inclusion", "- ТР-1. Комиссия за перевод — 150 ₸.\n- ТР-2. Максимальная сумма перевода без комиссии — 500 000 ₸.\n")
        self.silent("fee-inclusion", "- ТР-1. Комиссия за перевод — 150 ₸.\n- ТР-2. Лимит проверяется по сумме перевода без учёта комиссии.\n"
                                     "- ТР-3. Максимальная сумма перевода — 500 000 ₸.\n")
        self.silent("fee-inclusion", "- ТР-1. Максимальная сумма перевода — 500 000 ₸.\n")  # no fee in the document


class ActorNames(Case):
    TEXT = ("- ТР-1. Клиент задаёт порог в настройках.\n- ТР-2. Клиент видит сумму за месяц.\n- ТР-3. Клиент нажимает «Сохранить».\n"
            "- ТР-4. Пользователь получает push о достижении порога.\n- ТР-5. Пользователь может выключить уведомление.\n- ТР-6. Пользователь видит историю.\n")

    def test_fires(self):
        f = self.fires("actor-names", self.TEXT, head=HEAD)
        self.assertIn("клиент", f.title)

    def test_silent(self):
        self.silent("actor-names", "- ТР-0. Клиент (далее — пользователь) — физическое лицо с картой банка.\n" + self.TEXT)
        self.silent("actor-names", self.TEXT.replace("Пользователь", "Клиент"))
        self.silent("actor-names", "## Роли\n\n- Клиент — владелец карты.\n- Пользователь — любой вошедший в приложение.\n\n## 2. Требования\n\n" + self.TEXT)


class Quiet(Case):
    """A text where everything is specified: none of the rules of this file may speak."""

    SPEC = (
        "- ТР-1. Все даты и время в документе — по времени Астаны (UTC+5). Операция относится к месяцу и суткам по дате проведения.\n"
        "- ТР-2. Покупка сначала проходит карточную авторизацию, затем банк проводит операцию. Дата операции — дата проведения.\n"
        "- ТР-3. Лимит покупок — не более 300 000 ₸ в календарный месяц; расходы считаются по проведённым покупкам, о достижении лимита клиент узнаёт после проведения.\n"
        "- ТР-4. Доступный остаток — остаток на счёте за вычетом заблокированных сумм (холдов); для покупки берётся доступный остаток.\n"
        "- ТР-5. Комиссия за перевод — 150 ₸. Максимальная сумма перевода — 500 000 ₸ без учёта комиссии.\n"
        "- ТР-6. Сумма операции — сумма в тенге по курсу банка; покупки в долларах конвертируются.\n"
        "- ТР-7. При отклонении заявки возвращаются: статус заявки, сумма на счёте и лимит; отправленные уведомления не возвращаются.\n"
        "- ТР-8. Клиент (далее — пользователь) видит остаток и историю операций.\n"
    )

    def test_silent(self):
        found = run(self.SPEC)
        hits = [(f.rule_ids[0], [q.text for q in f.quotes]) for f in found if f.rule_ids[0] in AMB_IDS]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
