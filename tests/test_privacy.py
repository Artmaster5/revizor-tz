"""Masking of card numbers and ИИН/БИН before the text goes anywhere."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from revizor.loader import load
from revizor.privacy import iin_check_ok, luhn_ok, mask_sensitive

VALID_CARD = "4400 4301 2345 6789"  # passes the Luhn check
INVALID_CARD = "4400 4301 2345 6788"
VALID_IIN = "900105300128"  # born 05.01.1990, correct check digit
BAD_IIN = "900105300127"


class Checksums(unittest.TestCase):
    def test_luhn(self):
        self.assertTrue(luhn_ok(VALID_CARD.replace(" ", "")))
        self.assertFalse(luhn_ok(INVALID_CARD.replace(" ", "")))

    def test_iin(self):
        self.assertTrue(iin_check_ok(VALID_IIN))
        self.assertFalse(iin_check_ok(BAD_IIN))


class Mask(unittest.TestCase):
    def test_valid_card_masked_same_length(self):
        text = f"Тестовая карта {VALID_CARD} для оплаты."
        out, notes = mask_sensitive(text)
        self.assertEqual(len(out), len(text))
        self.assertNotIn(VALID_CARD, out)
        self.assertIn("4400 43", out)  # first six digits kept
        self.assertIn("6789", out)  # last four kept
        self.assertEqual(len(notes), 1)
        self.assertRegex(notes[0], r"^строка 1: номер карты \(16 цифр\)")

    def test_invalid_card_untouched(self):
        text = f"Номер договора {INVALID_CARD}."
        self.assertEqual(mask_sensitive(text), (text, []))

    def test_money_and_plain_numbers_untouched(self):
        for text in ["Порог от 1 000 до 1 000 000 ₸ включительно.", "Сумма 1000000 тиын.", "Телефон 701 123 45 67",
                     "Дата 2026-11-12, код 0000000000000000", "Идентификатор 123456789012"]:
            with self.subTest(text=text):
                self.assertEqual(mask_sensitive(text), (text, []))

    def test_iin(self):
        out, notes = mask_sensitive(f"ИИН клиента {VALID_IIN}.")
        self.assertNotIn(VALID_IIN, out)
        self.assertTrue(out.startswith("ИИН клиента 90"))
        self.assertIn("28.", out)
        self.assertEqual(len(out), len(f"ИИН клиента {VALID_IIN}."))
        self.assertIn("ИИН", notes[0])
        self.assertEqual(mask_sensitive(f"ИИН {BAD_IIN}"), (f"ИИН {BAD_IIN}", []))
        self.assertEqual(mask_sensitive("ИИН 000000000000")[1], [])  # obvious test value

    def test_line_numbers_in_notes(self):
        _, notes = mask_sensitive(f"первая\nвторая\nкарта {VALID_CARD}\n")
        self.assertTrue(notes[0].startswith("строка 3:"))


class LoaderMasks(unittest.TestCase):
    def test_masked_before_blocks_including_code(self):
        body = ("# ТЗ на платёж\n\nВерсия 1.0 от 01.11.2026.\n\n"
                "- Ф-1. Клиент платит картой и видит чек в истории операций приложения.\n"
                "- Ф-2. Платёж проходит проверку лимитов карты до списания средств.\n"
                "- Ф-3. Пример запроса к сервису платежей приведён ниже.\n\n"
                "```json\n{\"pan\": \"4400430123456789\", \"iin\": \"" + VALID_IIN + "\"}\n```\n")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "pay.md"
            p.write_text(body, encoding="utf-8")
            doc = load(p)
        self.assertNotIn("4400430123456789", doc.text)
        self.assertNotIn(VALID_IIN, doc.text)
        code = next(b for b in doc.blocks if b.kind == "code")
        self.assertNotIn("4400430123456789", code.text)
        self.assertEqual(len(doc.masked), 2)
        self.assertTrue(any("Замаскировано 2 значени" in w for w in doc.warnings))


if __name__ == "__main__":
    unittest.main()


class WellKnownTestCards(unittest.TestCase):
    def test_visa_and_mastercard_test_numbers_are_masked_in_every_notation(self):
        for card in ("4111 1111 1111 1111", "4111-1111-1111-1111", "4111111111111111",
                     "5555 5555 5555 4444", "5555-5555-5555-4444", "5555555555554444"):
            with self.subTest(card=card):
                out, notes = mask_sensitive(f"Тестовая карта {card}, срок 12/30.")
                self.assertEqual(len(out), len(f"Тестовая карта {card}, срок 12/30."))
                self.assertEqual(len(notes), 1)
                self.assertNotIn(card, out)
                self.assertEqual(out.count("*"), 6)
                self.assertTrue(out.startswith("Тестовая карта ") and out.endswith(", срок 12/30."))

    def test_same_length_mask_keeps_head_and_tail(self):
        out, _ = mask_sensitive("4111111111111111")
        self.assertEqual(out, "411111******1111")

    def test_non_luhn_number_stays(self):
        for t in ("4111 1111 1111 1112", "5555555555554443", "Сумма 1234567890123456 ₸"):
            with self.subTest(t=t):
                self.assertEqual(mask_sensitive(t), (t, []))

    def test_all_equal_digits_stay(self):
        self.assertEqual(mask_sensitive("0000000000000000")[1], [])


class EmailAndPhones(unittest.TestCase):
    def test_email_local_part_is_masked_same_length(self):
        t = "Писать на ivan.petrov@bank.kz или support+tz@mail.example.com."
        out, notes = mask_sensitive(t)
        self.assertEqual(len(out), len(t))
        self.assertEqual(out, "Писать на i**********@bank.kz или s*********@mail.example.com.")
        self.assertEqual(len(notes), 2)

    def test_phones_in_unmistakable_formats(self):
        for ph in ("+7 701 123 45 67", "+7(701)123-45-67", "+77011234567", "8 (701) 123-45-67", "8 701 123 45 67",
                   "+44 20 7946 0958", "+380501234567"):
            with self.subTest(ph=ph):
                t = f"Тел. {ph}, доб. 5."
                out, notes = mask_sensitive(t)
                self.assertEqual(len(out), len(t))
                self.assertEqual(len(notes), 1)
                self.assertIn("номер телефона", notes[0])
                self.assertNotIn(ph, out)

    def test_amounts_ids_dates_and_codes_stay(self):
        for t in ("Сумма 8 000 000 000 ₸ и +1 000 000 000 ₸.", "Идентификатор операции 87011234567 в журнале.",
                  "Дата 2026-11-25, версия 1.2.3, код ERR_70112345.", "Лимит 100 000 тенге, ставка 12,5 %.",
                  "Заявка 8701123 от 25.11.2026.", "Порог 5 000 000 и 1 000 000", "Пишите на @username или a@b"):
            with self.subTest(t=t):
                self.assertEqual(mask_sensitive(t), (t, []))

    def test_card_is_not_taken_for_a_phone(self):
        out, notes = mask_sensitive("Карта 4111 1111 1111 1111.")
        self.assertEqual(len(notes), 1)
        self.assertIn("номер карты", notes[0])
