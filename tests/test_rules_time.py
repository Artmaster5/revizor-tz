"""Rules on time and periods: each on a tiny positive and negative example, then on a fully specified text."""
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
TIME_IDS = ("time-zone-missing", "period-boundary", "days-without-kind", "month-end-day", "period-undefined", "relative-period")


def run(body: str, head: str = HEAD):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "doc.md"
        p.write_text(head + body + "\n" + FILLER, encoding="utf-8")
        doc = load(p)
    assert isinstance(doc, SourceDoc), getattr(doc, "code", doc)
    reqs, skipped = heuristic_slice(doc)
    found = R.run_rules(doc, reqs, skipped)
    assert not R.last_errors, R.last_errors
    return found


class Case(unittest.TestCase):
    def fires(self, rule, body):
        found = [f for f in run(body) if f.rule_ids[0] == rule]
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
        return f

    def silent(self, rule, body):
        hits = [f for f in run(body) if f.rule_ids[0] == rule]
        self.assertFalse(hits, f"{rule} сработало зря на:\n{body}\n{[q.text for f in hits for q in f.quotes]}")


class Registry(unittest.TestCase):
    def test_registered(self):
        for rid in TIME_IDS:
            self.assertIn(rid, R.RULES)
            self.assertEqual(R.RULES[rid].scope, "set")
            self.assertFalse(R.RULES[rid].advisory)
            self.assertNotEqual(R.RULES[rid].severity, "CRITICAL")


class TimeZone(Case):
    def test_fires(self):
        f = self.fires("time-zone-missing", "- ТР-1. Счётчик операций обнуляется в 00:00.\n- ТР-2. Отчёт формируется до 23:59.\n")
        self.assertEqual(f.severity, "MINOR")
        self.assertEqual(len(f.quotes), 2)
        self.fires("time-zone-missing", "- ТР-1. Кэшбэк начисляется на следующий день после покупки.\n")
        self.fires("time-zone-missing", "- TR-1. The report is built at 23:00 every day.\n")

    def test_silent(self):
        self.silent("time-zone-missing", "- ТР-1. Все даты и время в документе — по времени Астаны (UTC+5).\n- ТР-2. Счётчик обнуляется в 00:00.\n")
        self.silent("time-zone-missing", "- ТР-1. Счётчик обнуляется в 00:00 по UTC.\n")
        self.silent("time-zone-missing", "- ТР-1. Счётчик обнуляется в 00:00 по местному времени клиента.\n")
        self.silent("time-zone-missing", "- ТР-1. Месяц определяется по дате покупки в Алматы.\n- ТР-2. Счётчик обнуляется в 00:00.\n")
        self.silent("time-zone-missing", "- ТР-1. Лимит обновляется в 00:00 по Алматы уақытымен.\n")
        self.silent("time-zone-missing", "- ТР-1. Время события в ответе передаётся в формате 2026-12-01T04:00:00Z, счётчик обнуляется в 00:00.\n")

    def test_nothing_without_times(self):
        self.silent("time-zone-missing", "- ТР-1. Клиент видит остаток по карте.\n")


class PeriodBoundary(Case):
    def test_fires(self):
        self.fires("period-boundary", "- ТР-1. Возврат возможен в течение 30 дней с даты покупки.\n")
        self.fires("period-boundary", "- ТР-1. Подать заявку можно с 1 по 15 число месяца.\n")
        self.fires("period-boundary", "- ТР-1. Заявки принимаются до 5 числа.\n")
        self.fires("period-boundary", "- TR-1. A refund is possible within 14 days after purchase.\n")

    def test_silent(self):
        self.silent("period-boundary", "- ТР-1. Возврат возможен в течение 30 дней с даты покупки включительно.\n")
        self.silent("period-boundary", "- ТР-1. Подать заявку можно с 1 по 15 число месяца включительно.\n")
        self.silent("period-boundary", "- ТР-1. Заявки принимаются до 5 числа, не включая 5-е число.\n")
        self.silent("period-boundary", "- ТР-1. Срок отсчитывается со следующего дня после покупки.\n- ТР-2. Возврат возможен в течение 30 дней с даты покупки.\n")

    def test_not_a_deadline(self):
        self.silent("period-boundary", "- ТР-1. Сообщение приходит в течение 24 часов.\n")
        self.silent("period-boundary", "- ТР-1. Согласовать текст оферты до 01.12.2026.\n")


class DaysWithoutKind(Case):
    def test_fires(self):
        self.fires("days-without-kind", "- ТР-1. Заявка на возврат рассматривается в течение 5 дней.\n")
        self.fires("days-without-kind", "- TR-1. A refund request is reviewed within 5 days.\n")

    def test_silent(self):
        self.silent("days-without-kind", "- ТР-1. Заявка на возврат рассматривается в течение 5 рабочих дней.\n")
        self.silent("days-without-kind", "- ТР-1. Все сроки в документе указаны в календарных днях.\n- ТР-2. Заявка на возврат рассматривается в течение 5 дней.\n")
        self.silent("days-without-kind", "- ТР-1. Документы клиента хранятся 90 дней и удаляются.\n")
        self.silent("days-without-kind", "- ТР-1. Одноразовый код действует 3 дня.\n")
        self.silent("days-without-kind", "- ТР-1. Возврат возможен в течение 30 календарных дней.\n- ТР-2. Заявка на возврат рассматривается в течение 30 дней.\n")
        self.silent("days-without-kind", "- ТР-1. Отмена возможна не позднее 23:00 дня, предшествующего дате.\n")
        self.silent("days-without-kind", "- ТР-1. Клиент получает push за 7 дней до целевой даты.\n")


class MonthEndDay(Case):
    def test_fires(self):
        self.fires("month-end-day", "- ТР-1. Периодичность: ежемесячно в выбранный день месяца (от 1 до 31).\n")
        self.fires("month-end-day", "- ТР-1. Платёж списывается ежемесячно 31 числа.\n")

    def test_silent(self):
        self.silent("month-end-day", "- ТР-1. Платёж списывается ежемесячно в выбранный день месяца (от 1 до 28).\n")
        self.silent("month-end-day", "- ТР-1. Платёж списывается ежемесячно в выбранный день месяца (от 1 до 31).\n"
                                     "- ТР-2. Если в месяце нет выбранного числа, списание выполняется в последний день месяца.\n")
        self.silent("month-end-day", "- ТР-1. Платёж списывается ежемесячно 15 числа.\n")
        self.silent("month-end-day", "- ТР-1. Платёж списывается еженедельно в выбранный день недели.\n")


class PeriodUndefined(Case):
    def test_fires(self):
        f = self.fires("period-undefined", "- ТР-1. Количество изъятий — не более 2 в месяц.\n")
        self.assertIn("месяц", f.title)
        f = self.fires("period-undefined", "- ТР-1. Лимит переводов — не более 500 000 ₸ в сутки.\n")
        self.assertIn("сутки", f.title)

    def test_silent(self):
        self.silent("period-undefined", "- ТР-1. Количество изъятий — не более 2 в календарный месяц.\n")
        self.silent("period-undefined", "- ТР-1. Количество изъятий — не более 2 в месяц.\n- ТР-2. Месяц определяется по дате операции в Алматы.\n")
        self.silent("period-undefined", "- ТР-1. Лимит переводов — не более 500 000 ₸ в сутки; счётчик обнуляется в 00:00.\n")
        self.silent("period-undefined", "- ТР-1. Лимит переводов — не более 500 000 ₸ в сутки.\n- ТР-2. Календарный день — с 00:00 до 23:59.\n")

    def test_message_text_is_not_a_limit(self):
        self.silent("period-undefined", "- ТР-1. При превышении клиент видит «Суточный лимит исчерпан».\n")


class RelativePeriod(Case):
    def test_fires(self):
        self.fires("relative-period", "- ТР-1. Выписка строится за закрытый период.\n")
        self.fires("relative-period", "- ТР-1. Комиссия пересчитывается по итогам отчётного периода.\n")

    def test_silent(self):
        self.silent("relative-period", "- ТР-1. Выписка строится за закрытый период.\n- ТР-2. Закрытый период — месяц, по которому подписан акт сверки.\n")
        self.silent("relative-period", "- ТР-1. Выписка строится за отчётный период с 1 по 31 число месяца.\n")
        self.silent("relative-period", "- ТР-1. Клиент видит историю платежей.\n")


class Quiet(Case):
    """A text where everything is specified: none of the rules of this file may speak."""

    SPEC = (
        "- ТР-1. Все даты и время в документе — по времени Астаны (UTC+5). Календарный день — с 00:00 до 23:59 по этому времени.\n"
        "- ТР-2. Все сроки в днях — календарные, если не сказано иное. День события в срок не входит, отсчёт идёт со следующего дня.\n"
        "- ТР-3. Лимит переводов — не более 500 000 ₸ в календарные сутки; счётчик обнуляется в 00:00.\n"
        "- ТР-4. Не более 2 изъятий в календарный месяц; месяц определяется по дате операции.\n"
        "- ТР-5. Платёж списывается ежемесячно в выбранный день месяца (от 1 до 31); если в месяце нет такого числа, списание — в последний день месяца.\n"
        "- ТР-6. Возврат возможен в течение 30 календарных дней с даты покупки включительно.\n"
        "- ТР-7. Закрытый период — месяц, по которому подписан акт сверки.\n"
    )

    def test_silent(self):
        found = run(self.SPEC)
        hits = [(f.rule_ids[0], [q.text for q in f.quotes]) for f in found if f.rule_ids[0] in TIME_IDS]
        self.assertEqual(hits, [])


class Grouping(Case):
    def test_many_places_one_finding(self):
        body = "".join(f"- ТР-{k}. Счётчик {k} обнуляется в 0{k}:30.\n" for k in range(1, 9))
        f = self.fires("time-zone-missing", body)
        self.assertEqual(len(f.quotes), 5)
        self.assertIn("ещё 3", f.problem)


if __name__ == "__main__":
    unittest.main()
