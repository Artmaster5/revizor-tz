"""Requirement slicing: heuristic on real documents, and assembling the model's answer safely."""
from __future__ import annotations

import unittest
from pathlib import Path

from revizor.loader import load
from revizor.model import REQ_KINDS, SKIP_CATEGORIES
from revizor.slicer import assemble, fill_missing, heuristic_slice

ROOT = Path(__file__).resolve().parent.parent
REQ_MD = ROOT / "docs" / "requirements.md"
FIX = ROOT / "tests" / "fixtures"


class HeuristicOnOrganisersDoc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load(REQ_MD)
        cls.reqs, cls.skipped = heuristic_slice(cls.doc)

    def test_every_block_classified_once(self):
        used = [b for r in self.reqs for b in r.block_ids] + [b for s in self.skipped for b in s.block_ids]
        self.assertEqual(sorted(used), sorted(b.bid for b in self.doc.blocks))

    def test_numbering_and_order(self):
        self.assertEqual([r.rid for r in self.reqs], [f"REQ-{k:03d}" for k in range(1, len(self.reqs) + 1)])
        starts = [r.line_start for r in self.reqs]
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(all(r.kind in REQ_KINDS for r in self.reqs))
        self.assertTrue(all(s.category in SKIP_CATEGORIES for s in self.skipped))
        self.assertTrue(all(r.origin == "heuristic" for r in self.reqs))

    def test_functional_requirements(self):
        f = [r for r in self.reqs if r.doc_id.startswith("Ф-")]
        self.assertEqual([r.doc_id for r in f], ["Ф-1", "Ф-2", "Ф-3", "Ф-4"])
        self.assertTrue(all(r.kind == "functional" for r in f))
        self.assertIn("Держатель активной карты", f[0].quote)

    def test_meta_template_placeholder_skipped(self):
        cats = {}
        for s in self.skipped:
            cats.setdefault(s.category, []).append(s)
        self.assertTrue(any("Общие сведения" in s.section for s in cats["meta"]))
        self.assertTrue(any("Ревью требований" in s.section for s in cats["meta"]))
        self.assertTrue(any("Блок согласующих" in s.section for s in self.skipped))
        self.assertTrue(any(s.text == "заполняются тестировщиками" for s in cats["placeholder"]))
        self.assertFalse(any("Ревью требований" in r.section for r in self.reqs))

    def test_code_attaches_to_its_requirement(self):
        code_ids = {b.bid for b in self.doc.blocks if b.kind == "code"}
        self.assertFalse(any(set(s.block_ids) & code_ids for s in self.skipped))
        # the two JSON bodies under «200. Successful — Created» and the «или» between them are one requirement
        r = next(r for r in self.reqs if "Проверьте сумму" in r.quote)
        kinds = [next(b.kind for b in self.doc.blocks if b.bid == x) for x in r.block_ids]
        self.assertEqual(kinds, ["code", "paragraph", "code"])
        self.assertIn("или", r.quote)

    def test_heading_is_skipped(self):
        heads = {b.bid for b in self.doc.blocks if b.kind == "heading"}
        self.assertFalse(any(set(r.block_ids) & heads for r in self.reqs))


class HeuristicOnPlainText(unittest.TestCase):
    def test_plain_spec(self):
        doc = load(FIX / "plain_spec.txt")
        reqs, skipped = heuristic_slice(doc)
        ids = [r.doc_id for r in reqs]
        self.assertEqual(ids[:3], ["1.1", "1.2", "1.3"])
        self.assertTrue(any("Версия: 1.0" in s.text for s in skipped))  # document details are not requirements
        self.assertTrue(any(s.text.startswith("на сервис напоминаний") for s in skipped))  # title page
        self.assertFalse(any("Управление розничных продуктов" in r.quote for r in reqs))  # approvers list


class Assemble(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load(REQ_MD)
        cls.ids = {b.doc_id: b.bid for b in cls.doc.blocks if b.doc_id}

    def test_model_answer_is_sanitised(self):
        f1, f2, f3 = self.ids["Ф-1"], self.ids["Ф-2"], self.ids["Ф-3"]
        reqs, skipped, missing = assemble(
            self.doc,
            [{"block_ids": [f2], "doc_id": "", "kind": "functional", "summary": "Месяц по дате покупки"},
             {"block_ids": [f1, "B999"], "doc_id": "", "kind": "functional", "summary": "Порог"},
             {"block_ids": [f1], "doc_id": "X", "kind": "functional", "summary": "повтор"},  # already used
             {"block_ids": ["B001", f3], "kind": "nonsense-kind", "summary": "не подряд"}],
            [{"block_ids": ["B004"], "category": "meta", "reason": "заголовок"}],
        )
        self.assertEqual([r.doc_id for r in reqs], ["", "Ф-1", "Ф-2", "Ф-3"])  # sorted by position, split runs
        self.assertEqual([r.rid for r in reqs], ["REQ-001", "REQ-002", "REQ-003", "REQ-004"])
        self.assertEqual(reqs[1].summary, "Порог")
        self.assertTrue(all(r.kind in REQ_KINDS for r in reqs))
        self.assertEqual(skipped[0].block_ids, ["B004"])
        self.assertNotIn("B999", [b for r in reqs for b in r.block_ids])
        self.assertNotIn(f1, missing)
        self.assertIn("B002", missing)
        self.assertNotIn("**", reqs[1].quote)

    def test_fill_missing(self):
        reqs, skipped, missing = assemble(self.doc, [{"block_ids": [self.ids["Ф-1"]], "kind": "functional"}], [])
        reqs2, skipped2 = fill_missing(self.doc, reqs, skipped, missing)
        used = [b for r in reqs2 for b in r.block_ids] + [b for s in skipped2 for b in s.block_ids]
        self.assertEqual(sorted(used), sorted(b.bid for b in self.doc.blocks))
        self.assertEqual([r.rid for r in reqs2], [f"REQ-{k:03d}" for k in range(1, len(reqs2) + 1)])
        self.assertEqual(next(r for r in reqs2 if r.doc_id == "Ф-1").origin, "model")

    def test_garbage_input(self):
        reqs, skipped, missing = assemble(self.doc, [None, {"block_ids": "B002"}, {"x": 1}, "junk"], None)
        self.assertEqual([r.block_ids for r in reqs], [["B002"]])
        self.assertEqual(len(missing), len(self.doc.blocks) - 1)


if __name__ == "__main__":
    unittest.main()
