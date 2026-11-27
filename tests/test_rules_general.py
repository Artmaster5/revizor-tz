"""General rules: each on a tiny positive and two negative examples (a correct wording and a look-alike)."""
from __future__ import annotations

import unittest

from revizor import rules as R
from tests.test_rules_time import Case

GENERAL_IDS = (
    "retry-unbounded", "timeout-no-outcome", "percent-base-missing", "once-without-period", "retention-missing",
    "masking-missing", "req-id-clash", "req-id-gap", "priority-mixed", "external-no-answer", "setting-no-audit",
    "measure-period-missing", "language-missing", "story-no-purpose", "story-no-criteria", "calc-rounding-missing",
)


class Registry(unittest.TestCase):
    def test_registered(self):
        for rid in GENERAL_IDS:
            self.assertIn(rid, R.RULES)
            self.assertEqual(R.RULES[rid].scope, "set")
            self.assertFalse(R.RULES[rid].advisory)
            self.assertNotEqual(R.RULES[rid].severity, "CRITICAL")

    def test_order(self):
        ids = list(R.RULES)
        advisory = [i for i in ids if R.RULES[i].advisory]
        self.assertGreater(ids.index(advisory[0]), ids.index("calc-rounding-missing"))


class Retry(Case):
    def test_fires(self):
        self.fires("retry-unbounded", "- ТР-1. Если шлюз не принял платёж, система повторяет отправку.\n")
        self.fires("retry-unbounded", "- TR-1. If the gateway fails, the service retries the request up to 3 times.\n")

    def test_silent(self):
        self.silent("retry-unbounded", "- ТР-1. Если шлюз не принял платёж, система повторяет отправку до 3 раз с интервалом 5 минут.\n")
        self.silent("retry-unbounded", "- ТР-1. Повторный запрос с тем же ключом идемпотентности возвращает первый ответ.\n")
        self.silent("retry-unbounded", "- ТР-1. Клиент может повторить попытку ввода кода.\n")


class Timeout(Case):
    def test_fires(self):
        self.fires("timeout-no-outcome", "- ТР-1. Тайм-аут ответа процессинга — 30 секунд.\n")

    def test_silent(self):
        self.silent("timeout-no-outcome", "- ТР-1. Тайм-аут ответа процессинга — 30 секунд; по его истечении операция отклоняется.\n")
        self.silent("timeout-no-outcome", "- ТР-1. Клиент вводит код в течение 5 минут.\n")


class Percent(Case):
    def test_fires(self):
        self.fires("percent-base-missing", "- ТР-1. Комиссия за перевод — 1,5 %.\n")

    def test_silent(self):
        self.silent("percent-base-missing", "- ТР-1. Комиссия за перевод — 1,5 % от суммы перевода.\n")
        self.silent("percent-base-missing", "- ТР-1. Доступность сервиса — не ниже 99,9 %.\n")
        self.silent("percent-base-missing", "- ТР-1. Скидка — 5 % от стоимости покупки.\n")


class Once(Case):
    def test_fires(self):
        self.fires("once-without-period", "- ТР-1. Бонус начисляется не чаще одного раза.\n")
        self.fires("once-without-period", "- TR-1. The promo code can be used only once.\n")

    def test_silent(self):
        self.silent("once-without-period", "- ТР-1. Бонус начисляется не чаще одного раза в календарный месяц.\n")
        self.silent("once-without-period", "- ТР-1. Бонус начисляется однократно на клиента за всё время.\n")
        self.silent("once-without-period", "- ТР-1. Одноразовый код действует 5 минут.\n")


class Retention(Case):
    def test_fires(self):
        self.fires("retention-missing", "- ТР-1. Система хранит сканы документов клиента.\n")

    def test_silent(self):
        self.silent("retention-missing", "- ТР-1. Система хранит сканы документов клиента 5 лет после закрытия договора.\n")
        self.silent("retention-missing", "- ТР-1. Система не хранит сканы документов клиента.\n")
        self.silent("retention-missing", "- ТР-1. Система хранит настройки интерфейса.\n")


class Masking(Case):
    def test_fires(self):
        self.fires("masking-missing", "- ТР-1. Экран показывает номер карты клиента.\n")
        f = self.fires("masking-missing", "- ТР-1. Система пишет в журнал номер карты и ИИН клиента: логируется каждая операция.\n")
        self.assertEqual(f.severity, "MAJOR")

    def test_silent(self):
        self.silent("masking-missing", "- ТР-1. Экран показывает номер карты клиента в маскированном виде: последние 4 цифры.\n")
        self.silent("masking-missing", "- ТР-1. Экран показывает баланс карты клиента.\n")


class Numbering(Case):
    def test_clash_fires(self):
        f = self.fires("req-id-clash", "- ТР-1. Клиент входит по PIN-коду.\n- ТР-2. Клиент видит баланс.\n- ТР-2. Клиент видит историю платежей.\n")
        self.assertEqual(f.severity, "MAJOR")

    def test_clash_silent(self):
        self.silent("req-id-clash", "- ТР-1. Клиент входит по PIN-коду.\n- ТР-2. Клиент видит баланс.\n- ТР-3. Клиент видит историю платежей.\n")
        self.silent("req-id-clash", "- ТР-1. Клиент входит по PIN-коду.\n- ТР-2. Клиент видит баланс.\n- ТР-2. Клиент видит баланс.\n")

    def test_gap_fires(self):
        body = "".join(f"- ФТ-{n}. Клиент выполняет действие номер {n} в приложении.\n" for n in (1, 2, 3, 4, 6, 7, 8))
        self.fires("req-id-gap", body)

    def test_gap_silent(self):
        body = "".join(f"- ФТ-{n}. Клиент выполняет действие номер {n} в приложении.\n" for n in range(1, 9))
        self.silent("req-id-gap", body)
        body = "".join(f"- ФТ-{n}. Клиент выполняет действие номер {n} в приложении.\n" for n in (1, 3, 5))
        self.silent("req-id-gap", body)  # a short series is not judged


class Priority(Case):
    def test_fires(self):
        body = ("- ТР-1. Клиент входит по PIN-коду. Приоритет: высокий.\n- ТР-2. Клиент видит баланс. Приоритет: высокий.\n"
                "- ТР-3. Клиент видит историю платежей. Приоритет: средний.\n- ТР-4. Клиент меняет язык интерфейса.\n")
        self.fires("priority-mixed", body)

    def test_silent(self):
        # two unmarked requirements are always added by the test frame: a mark on only one of the rest is not a scheme
        self.silent("priority-mixed", "- ТР-1. Клиент входит по PIN-коду. Приоритет: высокий.\n- ТР-2. Клиент видит баланс.\n")
        self.silent("priority-mixed", "- ТР-1. Клиент входит по PIN-коду.\n- ТР-2. Клиент видит баланс.\n- ТР-3. Приоритет проверки — высокий.\n")


class External(Case):
    def test_fires(self):
        self.fires("external-no-answer", "- ТР-1. Система запрашивает кредитную историю клиента во внешней системе кредитного бюро.\n")

    def test_silent(self):
        self.silent("external-no-answer", "- ТР-1. Система запрашивает кредитную историю во внешней системе бюро. Если бюро недоступно, заявка передаётся на ручную проверку.\n")
        self.silent("external-no-answer", "- ТР-1. Система показывает клиенту кредитную историю.\n")


class Audit(Case):
    def test_fires(self):
        self.fires("setting-no-audit", "- ТР-1. Администратор может изменять лимиты переводов по клиентам.\n")

    def test_silent(self):
        self.silent("setting-no-audit", "- ТР-1. Администратор может изменять лимиты переводов.\n- ТР-2. Каждое изменение пишется в журнал аудита: кто и когда.\n")
        self.silent("setting-no-audit", "- ТР-1. Клиент может изменять свой лимит переводов.\n")


class Availability(Case):
    def test_fires(self):
        self.fires("measure-period-missing", "- ТР-1. Доступность сервиса — не менее 99,9 %.\n")

    def test_silent(self):
        self.silent("measure-period-missing", "- ТР-1. Доступность сервиса — не менее 99,9 % в календарный месяц.\n")
        self.silent("measure-period-missing", "- ТР-1. Вероятность ошибки перевода — не более 0,1 %.\n")


class Language(Case):
    BODY = ("- ТР-1. При ошибке клиент получает push «Перевод не выполнен: недостаточно средств».\n"
            "- ТР-2. После входа клиент получает SMS «Выполнен вход в приложение».\n")

    def test_fires(self):
        self.fires("language-missing", self.BODY)

    def test_silent(self):
        self.silent("language-missing", self.BODY + "- ТР-3. Тексты уведомлений даются на казахском и русском языках.\n")
        self.silent("language-missing", "- ТР-1. При ошибке клиент получает push «Перевод не выполнен: недостаточно средств».\n")


class Stories(Case):
    def test_purpose_fires(self):
        self.fires("story-no-purpose", "- Как кассир, я хочу видеть статус оплаты.\n")
        self.fires("story-no-purpose", "- As a cardholder, I want a monthly summary of my cashback.\n")

    def test_purpose_silent(self):
        self.silent("story-no-purpose", "- Как кассир, я хочу видеть статус оплаты, чтобы отдать товар без сомнений.\n")
        self.silent("story-no-purpose", "- As a cardholder, I want a monthly summary, so that I know how much I earned.\n")
        self.silent("story-no-purpose", "- Как правило, оплата проходит за секунды.\n")

    def _story(self, n, criteria=True):
        s = f"\n### US-{n}. История {n}\nКак кассир, я хочу действие {n}, чтобы результат {n}.\n"
        if criteria:
            s += "\nКритерии приёмки:\n- Дано: оплата выставлена. Когда кассир нажимает кнопку, Тогда статус меняется.\n"
        return s

    def test_criteria_fires(self):
        body = "".join(self._story(n) for n in (1, 2, 3)) + self._story(4, criteria=False) + self._story(5)
        f = self.fires("story-no-criteria", body)
        self.assertIn("действие 4", f.quotes[0].text)

    def test_criteria_silent(self):
        self.silent("story-no-criteria", "".join(self._story(n) for n in range(1, 6)))
        self.silent("story-no-criteria", "".join(self._story(n, criteria=False) for n in range(1, 6)))  # nobody has them: another question


class CalcRounding(Case):
    def test_fires(self):
        self.fires("calc-rounding-missing", "- ТР-1. Ежемесячный платёж по кредиту рассчитывается по формуле аннуитета; при досрочном погашении график пересчитывается.\n")

    def test_silent(self):
        self.silent("calc-rounding-missing", "- ТР-1. Ежемесячный платёж по кредиту рассчитывается по формуле аннуитета и округляется до тиына.\n")
        self.silent("calc-rounding-missing", "- ТР-1. Клиент видит график платежей по кредиту.\n")


if __name__ == "__main__":
    unittest.main()
