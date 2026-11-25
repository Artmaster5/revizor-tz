"""Block structure: sections, line numbers, IDs, tables, code, lists — Markdown and plain text."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from revizor.loader import load
from revizor.structure import blocks_for_prompt, detect_doc_id, numbered_text, parse_blocks

ROOT = Path(__file__).resolve().parent.parent
REQ_MD = ROOT / "docs" / "requirements.md"
FIX = ROOT / "tests" / "fixtures"


class DetectDocId(unittest.TestCase):
    def test_positive(self):
        cases = {
            "**Ф-1.** Клиент задаёт порог": "Ф-1",
            "Ф-1. Клиент": "Ф-1",
            "FR-12 The system": "FR-12",
            "НФТ 3. Время ответа": "НФТ 3",
            "REQ.07 Something": "REQ.07",
            "1.2.3 Клиент получает": "1.2.3",
            "**ВП-10:** выписка": "ВП-10",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(detect_doc_id(text), want)

    def test_negative(self):
        for text in ["HTTP 200 возвращается", "Клиент получает push", "ISO 8601 формат", "RFC 9110 говорит",
                     "200. Ответ в случае успеха", "2026 год", "Версия 1.2 от 14.10.2026"]:
            with self.subTest(text=text):
                self.assertEqual(detect_doc_id(text), "")


class OrganisersDocument(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load(REQ_MD)
        cls.blocks = cls.doc.blocks
        cls.lines = REQ_MD.read_text(encoding="utf-8").split("\n")

    def by_id(self, doc_id):
        return next(b for b in self.blocks if b.doc_id == doc_id)

    def test_functional_ids_and_lines(self):
        ids = [b.doc_id for b in self.blocks if b.doc_id]
        self.assertEqual(ids, ["Ф-1", "Ф-2", "Ф-3", "Ф-4"])
        for k, did in enumerate(ids):
            b = self.by_id(did)
            self.assertEqual(b.kind, "list_item")
            self.assertIn(did, self.lines[b.line_start - 1])
            self.assertTrue(b.section.endswith("Требования к изменениям › Функциональные требования"))
            self.assertNotIn("**", b.text)

    def test_line_numbers_match_source(self):
        for b in self.blocks:
            with self.subTest(bid=b.bid):
                self.assertEqual(b.raw, "\n".join(self.lines[b.line_start - 1:b.line_end]))

    def test_headings_and_sections(self):
        heads = [b for b in self.blocks if b.kind == "heading"]
        self.assertEqual(heads[0].text, "Уведомление о расходах по карте")
        self.assertEqual(heads[0].level, 1)
        h = next(b for b in heads if b.text.startswith("403."))
        self.assertIn("Часть B. REST API › Статусы › 403.", h.section)

    def test_table_rows_keep_headers(self):
        q = [b for b in self.blocks if b.kind == "table_row" and b.cells and b.cells[0] == "cardId"][0]
        self.assertEqual(q.header[:4], ["Parameter", "Format", "Size", "Mandatory"])
        self.assertEqual(q.cells[0], "cardId")
        self.assertTrue(q.text.startswith("Parameter: cardId | Format: string"))
        approvers = [b for b in self.blocks if b.kind == "table_row" and b.section.endswith("Блок согласующих")]
        self.assertTrue(approvers and all(b.is_empty for b in approvers))

    def test_fenced_json_is_code(self):
        codes = [b for b in self.blocks if b.kind == "code"]
        texts = [b.text for b in codes]
        self.assertIn('{"result":"ERROR","message":"Проверьте сумму"}', texts)
        for b in codes:
            self.assertNotIn("```", b.text)

    def test_numbered_steps_are_list_items(self):
        steps = [b for b in self.blocks if b.section.endswith("Логика работы метода") and b.kind != "heading"]
        self.assertEqual(len(steps), 5)
        self.assertTrue(all(b.kind == "list_item" for b in steps))
        self.assertEqual(steps[0].text, "Проверить пользовательскую сессию.")

    def test_prompt_helpers(self):
        nt = numbered_text(self.doc).split("\n")
        self.assertEqual(len(nt), len(self.doc.lines))
        self.assertEqual(nt[0], "   1| " + self.doc.lines[0])
        objs = [json.loads(x) for x in blocks_for_prompt(self.doc).split("\n")]
        self.assertEqual(len(objs), len(self.blocks))
        self.assertEqual(set(objs[0]), {"id", "kind", "section", "text"})
        self.assertTrue(any(o["kind"] == "code" for o in objs))
        self.assertTrue(any(o["kind"] == "table_row" and o["text"] == "" for o in objs))  # empty rows included


class MarkdownShapes(unittest.TestCase):
    def test_setext_comment_and_continuation(self):
        text = ("Заголовок\n=========\n\n<!-- скрыто -->\n\n- Ф-1. Первая строка\n  продолжение пункта\n"
                "- Ф-2. Второй пункт\n\nТекст абзаца\nв две строки.\n")
        bl = parse_blocks(text, "md")
        self.assertEqual(bl[0].kind, "heading")
        self.assertEqual(bl[0].text, "Заголовок")
        self.assertEqual(bl[1].kind, "paragraph")
        self.assertEqual(bl[1].text, "скрыто")
        self.assertEqual(bl[2].text, "Ф-1. Первая строка продолжение пункта")
        self.assertEqual((bl[2].line_start, bl[2].line_end), (6, 7))
        self.assertEqual(bl[4].text, "Текст абзаца в две строки.")


class PlainText(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load(FIX / "plain_spec.txt")
        cls.blocks = cls.doc.blocks

    def test_numbered_headings(self):
        heads = [(b.level, b.text) for b in self.blocks if b.kind == "heading"]
        self.assertIn((1, "1. ТРЕБОВАНИЯ К НАПОМИНАНИЯМ"), heads)
        self.assertIn((1, "3. СОГЛАСОВАНИЕ"), heads)

    def test_wrapped_numbered_item_is_one_paragraph(self):
        b = next(b for b in self.blocks if b.doc_id == "1.1")
        self.assertEqual(b.kind, "paragraph")
        self.assertEqual((b.line_start, b.line_end), (9, 10))
        self.assertIn("до даты платежа", b.text)

    def test_space_drawn_table(self):
        rows = [b for b in self.blocks if b.kind == "table_row" and b.section.startswith("2.")]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].header, ["N", "Канал", "Условие"])
        self.assertEqual(rows[1].cells, ["2", "SMS", "push недоступен"])
        sign = [b for b in self.blocks if b.kind == "table_row" and b.section.startswith("3.")]
        self.assertEqual(len(sign), 2)
        self.assertEqual(sign[0].cells, ["Управление розничных продуктов", "", ""])

    def test_heading_hints_from_pdf_fonts(self):
        text = "Обзор\nСервис показывает расходы.\nСтатусы\nКод 200 означает успех.\n"
        bl = parse_blocks(text, "pdf", [1, 1, 1, 1], headings={1: 2, 3: 3})
        self.assertEqual([(b.kind, b.level) for b in bl if b.kind == "heading"], [("heading", 2), ("heading", 3)])
        self.assertEqual(bl[-1].section, "Обзор › Статусы")
        plain = parse_blocks(text, "pdf", [1, 1, 1, 1])  # without hints the call behaves as before
        self.assertNotIn("heading", [b.kind for b in plain])

    def test_plain_json_is_code(self):
        text = ("Ответ сервиса:\n{\n  \"status\": \"error\",\n  \"code\": 404\n}\n"
                "200. Ответ возвращается при успехе.\n")
        bl = parse_blocks(text, "txt")
        self.assertEqual([b.kind for b in bl], ["paragraph", "code", "paragraph"])
        self.assertEqual((bl[1].line_start, bl[1].line_end), (2, 5))

    def test_space_table_across_page_break(self):
        lines = ["Проверка              Есть        Проверено",
                 "Требование описано    Да / Нет    ☐",
                 "Есть тесты            Да / Нет    ☐",
                 "", "",
                 "  Есть схема отката       Да / Нет      ☐",
                 "  Есть модель данных      Да / Нет      ☐",
                 "", "Конец."]
        pages = [1, 1, 1, 1, 2, 2, 2, 2, 2]
        bl = parse_blocks("\n".join(lines) + "\n", "pdf", pages)
        rows = [b for b in bl if b.kind == "table_row"]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[2].cells, ["Есть схема отката", "Да / Нет", "☐"])
        self.assertEqual(rows[2].page, 2)
        self.assertEqual(rows[2].header, ["Проверка", "Есть", "Проверено"])

    def test_header_only_table_is_one_empty_row(self):
        bl = parse_blocks("Согласование\n\nПодразделение    ФИО    Подпись    Дата\n\n\nКонец документа.\n", "txt")
        rows = [b for b in bl if b.kind == "table_row"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].header, ["Подразделение", "ФИО", "Подпись", "Дата"])
        self.assertTrue(rows[0].is_empty)

    def test_numbered_sequence_is_not_heading(self):
        bl = parse_blocks("Шаги:\n\n1. Открыть экран\n2. Ввести сумму\n3. Подтвердить\n", "txt")
        self.assertEqual([b.kind for b in bl], ["paragraph", "list_item", "list_item", "list_item"])


if __name__ == "__main__":
    unittest.main()
