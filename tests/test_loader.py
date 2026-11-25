"""Loader: every bad file becomes a Diagnostic, good files of every format load."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from revizor.loader import load
from revizor.model import DIAGNOSTIC_CODES, Diagnostic, SourceDoc

ROOT = Path(__file__).resolve().parent.parent
BROKEN = ROOT / "examples" / "broken"
FIX = ROOT / "tests" / "fixtures"
REQ_MD = ROOT / "docs" / "requirements.md"


class PluralTests(unittest.TestCase):
    def test_too_short_plural(self):
        from revizor.loader import _diag, _plural

        words = ("непустая строка", "непустые строки", "непустых строк")
        for n, want in ((1, "непустая строка"), (2, "непустые строки"), (4, "непустые строки"), (5, "непустых строк"),
                        (11, "непустых строк"), (12, "непустых строк"), (14, "непустых строк"), (21, "непустая строка"),
                        (22, "непустые строки"), (0, "непустых строк"), (111, "непустых строк")):
            self.assertEqual(_plural(n, *words), want, n)
        self.assertIn("2 непустые строки", _diag("TOO_SHORT", "x.md", chars=12, lines=2).what_happened)
        self.assertIn("1 знак текста и 1 непустая строка", _diag("TOO_SHORT", "x.md", chars=1, lines=1).what_happened)
        self.assertIn("0 знаков текста и 0 непустых строк", _diag("TOO_SHORT", "x.md", chars=0, lines=0).what_happened)


class BrokenExamples(unittest.TestCase):
    EXPECTED = {
        "empty.md": "EMPTY_FILE",
        "spaces.txt": "ONLY_WHITESPACE",
        "scan_no_text.pdf": "PDF_NO_TEXT",
        "broken.pdf": "PDF_BROKEN",
        "fake.pdf": "WRONG_TYPE",
        "broken.docx": "DOCX_BROKEN",
        "tiny.md": "TOO_SHORT",
    }

    def test_every_broken_example_has_its_code(self):
        self.assertEqual(sorted(os.listdir(BROKEN)), sorted(self.EXPECTED))
        for name, code in self.EXPECTED.items():
            with self.subTest(name=name):
                d = load(BROKEN / name)
                self.assertIsInstance(d, Diagnostic)
                self.assertEqual(d.code, code)
                self.assertIn(d.code, DIAGNOSTIC_CODES)
                self.assertTrue(d.title and d.what_happened and d.what_to_do)
                self.assertEqual(d.name, name)

    def test_fake_pdf_names_real_type(self):
        d = load(BROKEN / "fake.pdf")
        self.assertIn("PNG", d.detected_type)

    def test_extra_fixtures(self):
        cases = {
            "encrypted.pdf": "PDF_ENCRYPTED",
            "no_pages.pdf": "PDF_NO_PAGES",
            "old_format.doc": "DOC_OLD_FORMAT",
            "binary.txt": "BINARY",
            "no_document.docx": "DOCX_BROKEN",
            "image.png.txt": "WRONG_TYPE",
            "empty_text.html": "TOO_SHORT",
        }
        for name, code in cases.items():
            with self.subTest(name=name):
                d = load(FIX / name)
                self.assertIsInstance(d, Diagnostic)
                self.assertEqual(d.code, code)

    def test_missing_path_directory_and_none(self):
        self.assertEqual(load(ROOT / "no-such-file.md").code, "NOT_FOUND")
        self.assertEqual(load(ROOT / "examples").code, "IS_DIRECTORY")
        self.assertIsInstance(load(None), Diagnostic)
        self.assertIsInstance(load(""), Diagnostic)

    def test_too_large(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "big.md"
            p.write_text(("Клиент получает уведомление о платеже. " * 30 + "\n") * 500, encoding="utf-8")
            self.assertEqual(load(p).code, "TOO_LARGE")

    def test_never_raises_on_junk(self):
        junk = [b"\x00" * 10, b"%PDF-1.7\n%%EOF", b"PK\x03\x04garbage", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
                bytes(range(256)), b"<html><body>" + b"x" * 5]
        with tempfile.TemporaryDirectory() as tmp:
            for k, data in enumerate(junk):
                for ext in (".md", ".pdf", ".docx", ".txt", ".html", ".bin"):
                    p = Path(tmp) / f"j{k}{ext}"
                    p.write_bytes(data)
                    with self.subTest(file=p.name):
                        self.assertIsInstance(load(p), Diagnostic)


class GoodFiles(unittest.TestCase):
    def test_markdown(self):
        d = load(REQ_MD)
        self.assertIsInstance(d, SourceDoc)
        self.assertEqual(d.fmt, "md")
        self.assertTrue(d.blocks)
        self.assertEqual(len(d.lines), len(REQ_MD.read_text(encoding="utf-8").split("\n")))

    def test_cp1251(self):
        d = load(FIX / "cp1251.txt")
        self.assertIsInstance(d, SourceDoc)
        self.assertIn("push-уведомление", d.text)
        self.assertTrue(any("1251" in w for w in d.warnings))

    def test_utf16(self):
        d = load(FIX / "utf16.txt")
        self.assertIsInstance(d, SourceDoc)
        self.assertIn("истории операций", d.text)

    def test_docx(self):
        d = load(FIX / "sample.docx")
        self.assertIsInstance(d, SourceDoc)
        self.assertEqual(d.fmt, "docx")
        heads = [b.text for b in d.blocks if b.kind == "heading"]
        self.assertIn("Спецификация: выписка по счёту", heads)
        self.assertIn("Требования", heads)
        ids = [b.doc_id for b in d.blocks if b.doc_id]
        self.assertIn("ВП-1", ids)
        rows = [b for b in d.blocks if b.kind == "table_row"]
        self.assertEqual(rows[0].header, ["Поле", "Тип", "Описание"])
        self.assertIn("Сумма в тиынах", rows[-1].text)

    def test_html(self):
        d = load(FIX / "sample.html")
        self.assertIsInstance(d, SourceDoc)
        self.assertEqual(d.fmt, "html")
        self.assertNotIn("этот текст не должен", d.text)  # scripts are dropped
        kinds = {b.kind for b in d.blocks}
        self.assertTrue({"heading", "list_item", "table_row", "code"} <= kinds)
        self.assertIn("КБ-1", [b.doc_id for b in d.blocks])
        self.assertTrue(any(b.raw.lstrip().startswith("<!--") for b in d.blocks))  # comments are kept

    def test_pdf(self):
        d = load(ROOT / "examples" / "requirements.pdf")
        self.assertIsInstance(d, SourceDoc)
        self.assertEqual(d.fmt, "pdf")
        self.assertGreaterEqual(d.pages or 0, 2)
        self.assertEqual(len(d.line_pages), len(d.lines))
        self.assertTrue(any(b.page and b.page > 1 for b in d.blocks))
        self.assertIn("Ф-1", [b.doc_id for b in d.blocks])
        # layout extraction keeps the column gaps: tables come back as rows with headers
        rows = [b for b in d.blocks if b.kind == "table_row"]
        self.assertGreaterEqual(len(rows), 20)
        self.assertTrue(any(b.header and b.header[0] == "Parameter" for b in rows))
        # headings are recognised by font size, h3/h4 included, even without a blank line after them
        heads = {b.text: b.level for b in d.blocks if b.kind == "heading"}
        self.assertIn("Статусы", heads)
        self.assertIn("Блок согласующих", heads)
        self.assertTrue(any(b.kind == "code" for b in d.blocks))

    def test_plain_text(self):
        d = load(FIX / "plain_spec.txt")
        self.assertIsInstance(d, SourceDoc)
        self.assertEqual(d.fmt, "txt")

    def test_invisible_characters_kept_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "inv.md"
            body = "# ТЗ\n\n" + "\n".join(f"- Ф-{k}. Клиент видит баланс карты на главном экране приложения." for k in range(1, 6))
            p.write_text(body.replace("баланс", "ба​ланс", 1), encoding="utf-8")
            d = load(p)
            self.assertIn("​", d.text)
            self.assertTrue(any("невидим" in w.lower() for w in d.warnings))


if __name__ == "__main__":
    unittest.main()
