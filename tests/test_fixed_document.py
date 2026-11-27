"""Исправленная редакция документа (tools/fixed_document.py): только дословно-уникальные правки, остальное без изменений."""
import io
import sys
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from xml.dom import minidom

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import fixed_document as fd  # noqa: E402

DOC = ("# Заголовок\n\nСрок выдачи — 14 дней.\n\nОтвет сервиса быстро.\n\nПовтор.\n\nПовтор.\n\n"
       "Хвост документа без правок.\n")


def row(before, after, fid="D-001", rid="REQ-001"):
    return NS(rid=rid, before=before, after=after, fids=[fid])


class FixedDocument(unittest.TestCase):
    def setUp(self):
        rows = [row("Срок выдачи — 14 дней.", "Срок выдачи — 14 дней, продление один раз.", "D-001"),
                row("Ответ сервиса быстро.", "Сервис отвечает не позднее чем через 2 секунды.", "D-002"),
                row("Повтор.", "Повтор исправлен.", "D-003"),
                row("Этого текста в документе нет.", "Что-то.", "D-004")]
        self.segs, self.placed, self.skipped = fd.apply_rewrites(DOC, rows)
        self.head = fd.header("r.json", "d.md", self.placed, self.skipped)

    def test_only_unique_verbatim_replaced(self):
        body = "".join(t for t, _c in self.segs)
        self.assertEqual([p.fids[0] for p in self.placed], ["D-001", "D-002"])
        self.assertEqual([k.fids[0] for k in self.skipped], ["D-003", "D-004"])
        self.assertIn("продление один раз", body)
        self.assertIn("через 2 секунды", body)
        self.assertEqual(body.count("Повтор."), 2)
        self.assertNotIn("Повтор исправлен", body)

    def test_rest_byte_identical(self):
        unchanged = "".join(t for t, c in self.segs if not c)
        expect = DOC.replace("Срок выдачи — 14 дней.", "").replace("Ответ сервиса быстро.", "")
        self.assertEqual(unchanged, expect)

    def test_header_counts_and_lines(self):
        self.assertIn("Заменено мест: 2. Не вставлено: 2.", self.head)
        self.assertIn("строка 3 (D-001)", self.head)
        self.assertIn("строка 5 (D-002)", self.head)
        self.assertEqual(self.head.count("не вставлено:"), 2)
        md = fd.to_markdown(self.head, self.segs)
        self.assertTrue(md.endswith("Хвост документа без правок.\n"))

    def test_docx_is_valid_zip_with_xml(self):
        data = fd.to_docx_bytes(self.head, self.segs)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIsNone(z.testzip())
            self.assertIn("word/document.xml", z.namelist())
            xml = z.read("word/document.xml").decode("utf-8")
            minidom.parseString(z.read("word/document.xml"))
            minidom.parseString(z.read("[Content_Types].xml"))
        self.assertIn("w:highlight", xml)
        self.assertIn("продление один раз", xml)

    def test_overlap_not_forced(self):
        rows = [row("Срок выдачи — 14 дней.", "А", "D-1"), row("выдачи — 14 дней. ", "Б", "D-2")]
        _s, placed, skipped = fd.apply_rewrites("Срок выдачи — 14 дней. Конец", rows)
        self.assertEqual(len(placed), 1)
        self.assertEqual(len(skipped), 1)


if __name__ == "__main__":
    unittest.main()
