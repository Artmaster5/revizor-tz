"""«Чат по отчёту»: цитаты и номера замечаний в ответе проверяет программа."""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

from revizor import loader, report_json
from revizor.llm import FakeBackend
from revizor.quotes import QuoteIndex

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ask  # noqa: E402

DOC = ROOT / "docs" / "requirements.md"
REPORT = ROOT / "reports" / "report.json"
GOOD = "Месяц определяется по дате совершения покупки в Алматы"
FAKE = {"answer": "Месяц считают по дате покупки.", "findings": ["D-022", "D-9999"],
        "quotes": [{"text": GOOD}, {"text": "Этой фразы в документе нет и быть не может"}]}


class AskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = loader.load(str(DOC))
        cls.qi = QuoteIndex(cls.doc)
        cls.res = report_json.load_result(REPORT)

    def test_verify_keeps_real_and_drops_invented(self):
        v = ask.verify(FAKE, self.res, self.qi)
        self.assertEqual(len(v["quotes"]), 1)
        self.assertEqual(v["quotes"][0].line_start, 38)
        self.assertEqual(len(v["bad_quotes"]), 1)
        self.assertEqual([f.fid for f in v["findings"]], ["D-022"])
        self.assertEqual(v["bad_ids"], ["D-9999"])
        text = ask.render(v)
        self.assertIn("строка 38", text)
        self.assertIn("цитата не найдена в документе — убрана", text)
        self.assertNotIn("Этой фразы в документе нет и быть не может»: ", text)

    def test_user_prompt_has_report_and_question(self):
        p = ask.user_prompt(self.res, "Почему блокер?")
        self.assertIn("D-022", p)
        self.assertIn("Почему блокер?", p)

    def test_one_shot_output(self):
        backend = FakeBackend({ask.STAGE: FAKE})
        out = io.StringIO()
        argv = [str(REPORT), "--input", str(DOC), "Как считают месяц?"]
        with mock.patch.object(ask, "make_backend", return_value=backend), \
                mock.patch.object(ask, "build_env", return_value={}), contextlib.redirect_stdout(out):
            code = ask.main(argv)
        self.assertEqual(code, 0)
        s = out.getvalue()
        self.assertIn("Месяц считают по дате покупки.", s)
        self.assertIn("Цитаты (проверены программой)", s)
        self.assertIn("Токены:", s)

    def test_sha_mismatch_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            other = Path(d) / "other.md"
            other.write_text("# Другой документ\n\n" + "Система должна отправлять уведомление клиенту при каждом списании. " * 30, encoding="utf-8")
            err = io.StringIO()
            with mock.patch.object(ask, "make_backend", side_effect=AssertionError("модель не должна вызываться")), \
                    contextlib.redirect_stderr(err):
                code = ask.main([str(REPORT), "--input", str(other), "вопрос"])
        self.assertEqual(code, 1)
        self.assertIn("SHA-256", err.getvalue())


if __name__ == "__main__":
    unittest.main()
