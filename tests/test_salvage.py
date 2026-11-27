"""Rescue of a broken model answer item by item (``salvage_items``) after both attempts broke the JSON."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from revizor.llm import FakeBackend, LLMBadOutput
from revizor.llm.schema import salvage_items
from revizor.prompts import GAPS_SCHEMA, GATE_SCHEMA, SLICE_SCHEMA

# The second decision copies a quote from the document with its JSON inside, quotes not escaped.
BROKEN = ('{"decisions":['
          '{"fid":"D-001","decision":"keep","severity":"MAJOR","reason":"ok"},'
          '{"fid":"D-002","decision":"drop","reason":"в документе {"enabled": true} есть"},'
          '{"fid":"D-003","decision":"same","same_as":"D-001","reason":"повтор"}]}')


class SalvageItemsTests(unittest.TestCase):
    def test_keeps_whole_items_and_counts_broken(self):
        got = salvage_items(BROKEN, GATE_SCHEMA)
        self.assertIsNotNone(got)
        data, broken = got
        self.assertEqual([d["fid"] for d in data["decisions"]], ["D-001", "D-003"])
        self.assertEqual(broken, 1)

    def test_nested_objects_are_not_taken_for_items(self):
        text = ('{"items":[{"id":"ST1","status":"gap","findings":[{"title":"нет","quotes":["а"]}]},'
                '{"id":"ST2","status":"covered","note":"цитата "x" без экранирования"},'
                '{"id":"ST3","status":"na"}]}')
        data, broken = salvage_items(text, GAPS_SCHEMA)
        self.assertEqual([i["id"] for i in data["items"]], ["ST1", "ST3"])
        self.assertEqual(data["items"][0]["findings"][0]["title"], "нет")
        self.assertEqual(broken, 1)

    def test_stops_at_the_next_top_level_field(self):
        text = ('{"requirements":[{"block_ids":["B1"],"title":"раз"},{"block_ids":["B2"],"title":"о"ой"}],'
                '"skipped":[{"block_ids":["B9"],"why":"оглавление"}]}')
        data, broken = salvage_items(text, SLICE_SCHEMA)
        self.assertEqual([r["block_ids"] for r in data["requirements"]], [["B1"]])
        self.assertEqual(broken, 1)

    def test_nothing_to_rescue(self):
        self.assertIsNone(salvage_items("совсем не JSON", GATE_SCHEMA))
        self.assertIsNone(salvage_items('{"decisions":[{"fid":"D-1","reason":"о"й"}]}', GATE_SCHEMA))
        self.assertIsNone(salvage_items(BROKEN, {"type": "object", "properties": {}}))


class BackendSalvageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_both_broken_gives_whole_items_with_note_and_no_cache(self):
        b = FakeBackend({"skeptic": [BROKEN, BROKEN]}, cache_dir=self.tmp.name)
        data, _ = b.complete_json("s", "u", schema=GATE_SCHEMA, stage="skeptic")
        self.assertEqual([d["fid"] for d in data["decisions"]], ["D-001", "D-003"])
        self.assertEqual(data["decisions"][0]["why"], "")  # defaults filled by the schema
        self.assertEqual(len(b.log), 2)  # one repair, as before
        self.assertTrue(any("испорченных пропущено: 1" in n and "«skeptic»" in n for n in b.notes), b.notes)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])  # a partial answer is not cached

    def test_good_repair_is_still_preferred(self):
        good = '{"decisions":[{"fid":"D-001","decision":"keep"},{"fid":"D-002","decision":"drop"}]}'
        b = FakeBackend({"skeptic": [BROKEN, good]})
        data, _ = b.complete_json("s", "u", schema=GATE_SCHEMA, stage="skeptic")
        self.assertEqual([d["fid"] for d in data["decisions"]], ["D-001", "D-002"])
        self.assertFalse(any("испорчен" in n for n in b.notes))

    def test_garbage_twice_still_fails(self):
        b = FakeBackend({"skeptic": ["мусор", "снова мусор"]})
        with self.assertRaises(LLMBadOutput):
            b.complete_json("s", "u", schema=GATE_SCHEMA, stage="skeptic")


if __name__ == "__main__":
    unittest.main()
