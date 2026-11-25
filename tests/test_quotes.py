"""Quote verification (spec §4): the report may only show text that is really in the document."""
from __future__ import annotations

import unittest
from pathlib import Path

from revizor.loader import load
from revizor.quotes import QuoteIndex, normalize
from revizor.slicer import heuristic_slice

ROOT = Path(__file__).resolve().parent.parent
REQ_MD = ROOT / "docs" / "requirements.md"


class Normalize(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(normalize("**Ф-1.**  Ёлка — «тест»\n`code`"), 'ф-1. елка - "тест" code')
        self.assertEqual(normalize("| a | b |"), "a b")
        self.assertEqual(normalize("Сумма 100 000"), "сумма 100 000")


class FindQuotes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load(REQ_MD)
        cls.reqs, _ = heuristic_slice(cls.doc)
        cls.qi = QuoteIndex(cls.doc, cls.reqs)

    def test_exact_inside_requirement(self):
        q = self.qi.find("задаёт целый порог от 1 000 до 1 000 000 ₸ включительно")
        self.assertIsNotNone(q)
        self.assertTrue(q.exact)
        self.assertEqual((q.line_start, q.line_end), (37, 37))
        self.assertEqual(q.text, "задаёт целый порог от 1 000 до 1 000 000 ₸ включительно")
        req = next(r for r in self.reqs if r.doc_id == "Ф-1")
        self.assertEqual(q.rid, req.rid)
        self.assertIn("Функциональные требования", q.section)

    def test_markup_and_typography_ignored(self):
        q = self.qi.find('Ф-1. Держатель активной карты KZT задает целый порог')
        self.assertIsNotNone(q)
        self.assertTrue(q.text.startswith("**Ф-1.** Держатель"))  # original text, with its markup
        q2 = self.qi.find("Итог 50 000 ₸ и один push")
        self.assertEqual(q2.line_start, 63)

    def test_multiline(self):
        q = self.qi.find("По умолчанию уведомление выключено. Ф-2. Месяц определяется по дате совершения покупки")
        self.assertIsNotNone(q)
        self.assertEqual((q.line_start, q.line_end), (37, 38))

    def test_too_short_rejected_unless_whole_block(self):
        self.assertIsNone(self.qi.find("push"))
        self.assertIsNone(self.qi.find("порог"))
        q = self.qi.find("404")  # a whole paragraph block
        self.assertIsNotNone(q)
        self.assertEqual(q.line_start, 190)
        self.assertIsNotNone(self.qi.find("GET"))  # a whole table cell

    def test_changed_number_rejected(self):
        self.assertIsNone(self.qi.find("задаёт целый порог от 1 000 до 2 000 000 ₸ включительно"))
        self.assertIsNone(self.qi.find("сумма от 100 до 1 000 000 ₸ включительно"))
        # a retyped quote whose numbers all occur in the block is restored to the real text, never kept as typed
        q = self.qi.find("порог от 1 000 до 1 000 ₸ включительно")
        if q is not None:
            self.assertFalse(q.exact)
            self.assertIn("1 000 000 ₸", q.text)
        # a number must not match inside a longer one: no exact hit, only a restored (fuzzy) one with the real number
        q = self.qi.find("до 1 000 ₸ включительно. По умолчанию")
        self.assertTrue(q is None or (not q.exact and "1 000 000 ₸" in q.text))

    def test_fuzzy_replaced_by_source_text(self):
        q = self.qi.find("Сервис передает push на отправку в течение одной минуты после получения события")
        self.assertIsNotNone(q)
        self.assertFalse(q.exact)
        self.assertEqual(q.line_start, 46)
        self.assertIn("в течение минуты после получения события", q.text)
        self.assertNotIn("одной", q.text)

    def test_invented_text_rejected(self):
        self.assertIsNone(self.qi.find("Система блокирует карту после трёх неверных вводов ПИН-кода"))

    def test_ellipsis(self):
        q = self.qi.find("Хранить идентификатор карты … порог в тенге")
        self.assertIsNotNone(q)
        self.assertEqual(q.line_start, 49)
        self.assertTrue(q.text.startswith("Хранить идентификатор карты"))
        self.assertTrue(q.text.endswith("порог в тенге"))
        self.assertIsNone(self.qi.find("Хранить идентификатор карты ... Карта не найдена"))  # too far apart

    def test_prefer_rid(self):
        r = next(r for r in self.reqs if r.doc_id == "Ф-3")
        q = self.qi.find("клиент получает push", prefer_rid=r.rid)
        self.assertIsNotNone(q)
        self.assertEqual(q.rid, r.rid)

    def test_from_block(self):
        b = next(b for b in self.doc.blocks if b.doc_id == "Ф-2")
        whole = self.qi.from_block(b)
        self.assertEqual(whole.line_start, 38)
        self.assertIn("Месяц определяется", whole.text)
        start = b.text.index("Возврат")
        part = self.qi.from_block(b, start, start + len("Возврат уменьшает расходы"))
        self.assertEqual(part.text, "Возврат уменьшает расходы")
        code = next(b for b in self.doc.blocks if b.kind == "code" and "Проверьте сумму" in b.text)
        cq = self.qi.from_block(code)
        self.assertNotIn("```", cq.text)
        self.assertEqual(cq.line_start, 171)

    def test_pdf_quote_has_page(self):
        pdf = load(ROOT / "examples" / "requirements.pdf")
        qi = QuoteIndex(pdf)
        q = qi.find("Для чужой карты возвращается 404")
        self.assertIsNotNone(q)
        self.assertGreaterEqual(q.page, 2)


if __name__ == "__main__":
    unittest.main()
