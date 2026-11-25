"""--baseline OLD.json: fixed / still open / new findings; broken or foreign files only warn."""
from __future__ import annotations

import json
import unittest

from revizor import baseline, report_json, report_md

from tests.dialog_helpers import Base
from tests import test_pipeline as tp

OLD = tp.DOC
NEW = tp.DOC.replace("быстро после покупки", "не позднее 60 секунд после покупки") + \
    "\n- **Ф-4.** Сервис хранит историю по возможности долго.\n"


class BaselineTests(Base):
    def old_json(self) -> str:
        res = self.run_offline(path=self.doc(OLD, "old.md"))
        self.assertTrue(res.findings)
        p = self.dir / "old.json"
        p.write_text(report_json.render(res), encoding="utf-8")
        return str(p)

    def test_fixed_still_open_and_new(self):
        old = self.old_json()
        res = self.run_offline(path=self.doc(NEW, "new.md"), baseline_path=old)
        b = baseline.entry(res.audit)
        self.assertIsNotNone(b)
        self.assertGreaterEqual(len(b["fixed"]), 1)                     # «быстро» was rewritten
        self.assertTrue(any("быстро" in x["quote"].lower() or "быстро" in x["title"].lower() for x in b["fixed"]))
        self.assertGreaterEqual(len(b["new"]), 1)                       # «по возможности»
        self.assertTrue(all(x["new_fid"].startswith("D-") for x in b["still_open"]))
        self.assertEqual(len(b["fixed"]) + len(b["still_open"]), b["old_findings"])
        md = report_md.render(res)
        self.assertIn("**С прошлой проверки**", md)
        self.assertRegex(md, r"исправлено \d+, осталось \d+, новых \d+")
        data = json.loads(report_json.render(res))
        self.assertEqual(data["summary"]["baseline"]["new"], len(b["new"]))

    def test_same_document_has_nothing_fixed_or_new(self):
        old = self.old_json()
        res = self.run_offline(path=self.doc(OLD, "again.md"), baseline_path=old)
        b = baseline.entry(res.audit)
        self.assertEqual((len(b["fixed"]), len(b["new"])), (0, 0))

    def test_broken_foreign_and_missing_files_only_warn(self):
        bad = self.dir / "bad.json"
        for name, content in (("broken", "{not json"), ("foreign", json.dumps({"hello": [1, 2]})),
                              ("list", "[1,2,3]"), ("nofindings", json.dumps({"format": "revizor-report/1"}))):
            with self.subTest(name):
                bad.write_text(content, encoding="utf-8")
                res = self.run_offline(path=self.doc(OLD, "d.md"), baseline_path=str(bad))
                self.assertIsNone(baseline.entry(res.audit))
                self.assertTrue(any("сравнение не выполнено" in w for w in res.run.warnings))
                self.assertTrue(res.findings)                           # the run itself went on
        res = self.run_offline(path=self.doc(OLD, "d.md"), baseline_path=str(self.dir / "нет.json"))
        self.assertTrue(any("сравнение не выполнено" in w for w in res.run.warnings))

    def test_cli_flag(self):
        old = self.old_json()
        out = self.dir / "r.md"
        self.assertEqual(self.cli("--provider", "offline", "-i", self.doc(NEW, "new.md"), "-o", str(out), "--baseline", old), 0)
        self.assertIn("С прошлой проверки", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
