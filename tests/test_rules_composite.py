"""Composite rules (defects made of several parts): each on a tiny firing text and at least two silent ones."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from revizor import rules as R
from revizor.loader import load
from revizor.model import SourceDoc
from revizor.slicer import heuristic_slice

HEAD = "# Спецификация сервиса\n\nВерсия 1.0 от 01.11.2026. Автор: отдел цифровых продуктов.\n\n## 1. Требования\n\n"
IDS = ("refund-checklist", "error-body-shapes", "error-code-unlinked", "api-access-codes", "api-ownership",
       "list-no-order-limit", "notification-checklist", "enum-no-values", "param-table-no-required")
API_HEAD = ("# API сервиса карт\n\nВерсия 1.0 от 01.11.2026.\n\n## 1. Общее\n\n"
            "Базовый адрес `/api/v1`. Методы GET и POST, коды ответов 200 и 400.\n\n")


def run(body: str, head: str = HEAD):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "doc.md"
        p.write_text(head + body + "\n", encoding="utf-8")
        doc = load(p)
    assert isinstance(doc, SourceDoc), getattr(doc, "code", doc)
    reqs, skipped = heuristic_slice(doc)
    found = R.run_rules(doc, reqs, skipped)
    assert not R.last_errors, R.last_errors
    return found


class Case(unittest.TestCase):
    def fires(self, rule, body, head=HEAD):
        found = [f for f in run(body, head) if f.rule_ids[0] == rule]
        self.assertTrue(found, f"{rule} не сработало на:\n{body}")
        self.assertEqual(len(found), 1, "одна находка на документ")
        f = found[0]
        self.assertEqual(f.source, f"rule:{rule}")
        self.assertEqual(f.stage, "rules")
        self.assertIn(f.severity, ("MINOR", "MAJOR"))
        self.assertTrue(f.quotes)
        self.assertTrue(f.question and f.question.text.endswith("?"))
        return f

    def silent(self, rule, body, head=HEAD):
        hits = [f for f in run(body, head) if f.rule_ids[0] == rule]
        self.assertFalse(hits, f"{rule} сработало зря на:\n{body}\n{[q.text for f in hits for q in f.quotes]}")


class Registry(unittest.TestCase):
    def test_registered(self):
        for rid in IDS:
            self.assertIn(rid, R.RULES)
            self.assertEqual(R.RULES[rid].scope, "set")
            self.assertFalse(R.RULES[rid].advisory)
            self.assertNotEqual(R.RULES[rid].severity, "CRITICAL")

    def test_group_known(self):
        from revizor.clarify import RULE_GROUP
        for rid in IDS:
            self.assertIn(rid, RULE_GROUP)


class Refund(Case):
    def test_fires(self):
        f = self.fires("refund-checklist", "- ТР-1. Кэшбэк не начисляется за покупки, по которым оформлен возврат покупки.\n")
        self.assertIn("частичный возврат", f.problem)

    def test_silent_when_covered(self):
        body = ("- ТР-1. Кэшбэк за покупку уменьшается при возврате покупки.\n"
                "- ТР-2. Частичный возврат уменьшает кэшбэк пропорционально возвращённой части.\n"
                "- ТР-3. Возврат в другом месяце, чем покупка, уменьшает кэшбэк текущего месяца; закрытый период не пересчитывается.\n"
                "- ТР-4. Каждый возврат связан с исходной покупкой по её идентификатору.\n"
                "- ТР-5. Отмена авторизации снимает блокировку и не считается возвратом.\n"
                "- ТР-6. Кэшбэк за месяц не может стать отрицательным.\n")
        self.silent("refund-checklist", body)

    def test_fires_bare_refund_word(self):
        f = self.fires("refund-checklist", "- ТР-1. Возврат уменьшает расходы месяца исходной покупки.\n")
        self.assertNotIn("связь возврата", f.problem)
        self.assertNotIn("другом периоде", f.problem)

    def test_silent_look_alike(self):
        self.silent("refund-checklist", "- ТР-1. Клиент видит лимит перевода в разделе «Лимиты».\n- ТР-2. Лимит обновляется раз в сутки.\n")
        self.silent("refund-checklist", "- ТР-1. Возврат товара оформляется в отделении магазина по чеку.\n")


class ErrorBody(Case):
    def test_fires(self):
        body = ("### 400\n\n```json\n{\"code\": \"BAD\", \"message\": \"Неверный запрос\"}\n```\n\n"
                "### 404\n\n```json\n{\"error\": \"NOT_FOUND\", \"description\": \"Нет объекта\"}\n```\n")
        self.fires("error-body-shapes", body, API_HEAD)

    def test_silent_same_shape(self):
        body = ("### 400\n\n```json\n{\"code\": \"BAD\", \"message\": \"Неверный запрос\"}\n```\n\n"
                "### 404\n\n```json\n{\"code\": \"NOT_FOUND\", \"message\": \"Нет объекта\"}\n```\n")
        self.silent("error-body-shapes", body, API_HEAD)

    def test_silent_extra_field(self):
        body = ("### 400\n\n```json\n{\"code\": \"BAD\", \"message\": \"Неверный запрос\"}\n```\n\n"
                "### 422\n\n```json\n{\"code\": \"BAD\", \"message\": \"Ошибка\", \"details\": [\"amount\"]}\n```\n")
        self.silent("error-body-shapes", body, API_HEAD)


class ErrorCodes(Case):
    TABLE = ("| Method | Path | Errors |\n| :--- | :--- | :--- |\n| GET | `/cards` | 400, 401 |\n| POST | `/cards` | 400, 409 |\n\n"
             "| HTTP | Code | When |\n| :--- | :--- | :--- |\n")

    def test_fires_code_without_use(self):
        body = self.TABLE + "| 400 | `BAD` | Неверный параметр |\n| 401 | `NO_AUTH` | Нет токена |\n| 409 | `CONFLICT` | Дубль |\n| 429 | `LIMIT` | Много запросов |\n"
        f = self.fires("error-code-unlinked", body, API_HEAD)
        self.assertIn("429", f.problem)

    def test_fires_empty_condition(self):
        body = self.TABLE + "| 400 | `BAD` | Неверный параметр |\n| 401 | `NO_AUTH` | TBD |\n| 409 | `CONFLICT` | Дубль |\n"
        self.fires("error-code-unlinked", body, API_HEAD)

    def test_silent_consistent(self):
        body = self.TABLE + "| 400 | `BAD` | Неверный параметр |\n| 401 | `NO_AUTH` | Нет токена |\n| 409 | `CONFLICT` | Дубль |\n"
        self.silent("error-code-unlinked", body, API_HEAD)

    def test_silent_no_endpoint_table(self):
        self.silent("error-code-unlinked", "| HTTP | Code | When |\n| :--- | :--- | :--- |\n| 400 | `BAD` | Неверный параметр |\n", API_HEAD)


class AccessCodes(Case):
    BODY = "Каждый запрос содержит заголовок Authorization: Bearer <token>.\n\n### GET /api/v1/cards/{cardId}\n\nВозвращает карту. Ответ 200.\n"

    def test_fires(self):
        f = self.fires("api-access-codes", self.BODY, API_HEAD)
        self.assertIn("401", f.problem)
        self.assertIn("404", f.problem)

    def test_silent_when_declared(self):
        self.silent("api-access-codes", self.BODY + "\nОшибки: 401 — нет токена; 403 — нет прав; 404 — карта не найдена.\n", API_HEAD)

    def test_silent_no_auth_no_path_param(self):
        self.silent("api-access-codes", "### GET /api/v1/rates\n\nВозвращает курсы. Ответ 200.\n", API_HEAD)


class Ownership(Case):
    BODY = "Каждый запрос содержит заголовок Authorization: Bearer <token>.\n\n### GET /api/v1/cards/{cardId}\n\nВозвращает карту. Ответ 200. Ошибки: 401, 403, 404.\n"

    def test_fires(self):
        self.fires("api-ownership", self.BODY, API_HEAD)

    def test_silent_with_ownership_words(self):
        self.silent("api-ownership", self.BODY + "\nСервис проверяет, что карта принадлежит клиенту из токена; для чужой карты возвращается 404.\n", API_HEAD)

    def test_silent_no_identifier(self):
        self.silent("api-ownership", "Каждый запрос содержит заголовок Authorization: Bearer <token>.\n\n### GET /api/v1/rates\n\nВозвращает курсы.\n", API_HEAD)


class ListRule(Case):
    def test_fires(self):
        self.fires("list-no-order-limit", "### GET /api/v1/transactions\n\nВозвращает список операций клиента.\n", API_HEAD)

    def test_silent_with_order_or_size(self):
        self.silent("list-no-order-limit", "### GET /api/v1/transactions\n\nВозвращает список операций, новые первыми; параметр pageSize от 1 до 100.\n", API_HEAD)
        self.silent("list-no-order-limit", "### GET /api/v1/transactions\n\nВозвращает список операций клиента.\n\nВсе списки сортируются от новых к старым и возвращаются страницами по 50 записей.\n", API_HEAD)

    def test_silent_single_object(self):
        self.silent("list-no-order-limit", "### GET /api/v1/transactions/{id}\n\nВозвращает список полей одной операции.\n", API_HEAD)


class Notifications(Case):
    def test_fires(self):
        body = ("- ТР-1. При блокировке карты клиент получает push.\n- ТР-2. При смене лимита клиент получает SMS.\n"
                "- ТР-3. При входе с нового устройства клиент получает e-mail.\n")
        f = self.fires("notification-checklist", body)
        self.assertIn("язык", f.problem)

    def test_silent_when_described(self):
        body = ("- ТР-1. При блокировке карты клиент получает push.\n- ТР-2. При смене лимита клиент получает SMS.\n"
                "- ТР-3. Текст уведомления берётся из шаблона; язык — по языку приложения клиента.\n"
                "- ТР-4. Если уведомление не доставлено, система повторяет отправку один раз через 5 минут и не отправляет дубли.\n"
                "- ТР-5. Уведомления не отправляются ночью; клиент отключает их в настройках.\n")
        self.silent("notification-checklist", body)

    def test_silent_two_mentions(self):
        self.silent("notification-checklist", "- ТР-1. При блокировке карты клиент получает push.\n- ТР-2. При смене лимита клиент получает SMS.\n")

    def test_silent_single_mention(self):
        self.silent("notification-checklist", "- ТР-1. При блокировке карты клиент получает push.\n- ТР-2. Сервис уведомлений входит в состав системы.\n")


class Enum(Case):
    def test_fires(self):
        self.fires("enum-no-values", "- ТР-1. Поле «тип операции» принимает одно из допустимых значений.\n")

    def test_silent_with_values(self):
        self.silent("enum-no-values", "- ТР-1. Поле «тип операции» принимает одно из допустимых значений: `PAY`, `REFUND`, `HOLD`.\n")
        self.silent("enum-no-values", "- ТР-1. Поле «тип» принимает одно из допустимых значений (см. справочник типов в разделе 4).\n")

    def test_silent_list_follows(self):
        self.silent("enum-no-values", "- ТР-1. Поле «тип операции» принимает одно из допустимых значений:\n  - оплата\n  - возврат\n")


class ParamTable(Case):
    def test_fires(self):
        body = ("Запрос:\n\n| Параметр | Тип | Описание |\n| :--- | :--- | :--- |\n| `cardId` | string | Идентификатор карты |\n"
                "| `amount` | integer | Сумма в тиынах |\n")
        self.fires("param-table-no-required", body, API_HEAD)

    def test_silent_with_required_column(self):
        body = ("Запрос:\n\n| Параметр | Тип | Обязательный | Описание |\n| :--- | :--- | :--- | :--- |\n| `cardId` | string | да | Идентификатор карты |\n"
                "| `amount` | integer | нет | Сумма в тиынах |\n")
        self.silent("param-table-no-required", body, API_HEAD)

    def test_silent_response_table(self):
        body = ("Ответ:\n\n| Параметр | Тип | Описание |\n| :--- | :--- | :--- |\n| `cardId` | string | Идентификатор карты |\n"
                "| `amount` | integer | Сумма в тиынах |\n")
        self.silent("param-table-no-required", body, API_HEAD)


if __name__ == "__main__":
    unittest.main()
