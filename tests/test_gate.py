"""Wave 7: the new re-check (stage 7) — the policy of gate.py, the order and the chunks of the calls, the dedup call,
the estimate and a full offline run. FakeBackend only: no network, no key, no paid call.

The tests never depend on the wording of the prompts (the lead tunes them): they read only the «fid | …» lines and the
answer shapes."""
from __future__ import annotations

import json
import re
import unittest
from unittest import mock

from revizor import consistency, estimate, exports, gate, loader, merge, pipeline, prompts, report_json, report_md, sidefiles
from revizor.llm import FakeBackend
from revizor.model import Finding, Quote
from revizor.quotes import QuoteIndex

from tests import test_pipeline as tp


_PINNED: list = []


def setUpModule() -> None:
    _PINNED.extend(tp.pin_policy())


def tearDownModule() -> None:
    tp.unpin_policy(_PINNED)

DOC_TEXT = """# Лимиты

- **Ф-1.** Клиент задаёт лимит от 100 до 5 000 единиц включительно.
- **Ф-2.** Лимит сбрасывается в первый день месяца по времени Алматы.
"""


def q(text: str = "Клиент задаёт лимит", line: int = 3, rid: str = "REQ-001") -> Quote:
    return Quote(text=text, line_start=line, line_end=line, rid=rid)


def f(fid: str, title: str, *, sev: str = "MINOR", source: str = "model", rules=(), quotes=None, problem: str = "",
      reqs=None, crit: str = "completeness") -> Finding:
    return Finding(scope="requirement", criterion=crit, severity=sev, title=title, problem=problem or title,
                   recommendation="", quotes=list(quotes) if quotes is not None else [q()],
                   req_ids=list(reqs) if reqs is not None else ["REQ-001"], source=source, rule_ids=list(rules),
                   fid=fid)


def drop(fid: str, why: str, **kw) -> dict:
    return {"fid": fid, "decision": "drop", "why": why, "reason": kw.pop("reason", "причина"), **kw}


class _Doc(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        p = Path(self.tmp.name) / "doc.md"
        p.write_text(DOC_TEXT, encoding="utf-8")
        self.qi = QuoteIndex(loader.load(str(p)), [])

    def tearDown(self) -> None:
        self.tmp.cleanup()


class ShippedPolicy(_Doc):
    """The policy as shipped. Measured on stored runs: «stated», «misread» and «formal» removed real defects more often
    than false ones, so they never remove a finding; the other soft reasons move only a MINOR finding to observations."""

    def setUp(self) -> None:
        super().setUp()
        self._ps = [mock.patch.dict(gate.EFFECT, tp.SHIPPED_EFFECT),
                    mock.patch.object(gate, "SOFT_ON_HIGHER", tp.SHIPPED_ON_HIGHER)]
        for x in self._ps:
            x.start()

    def tearDown(self) -> None:
        for x in self._ps:
            x.stop()
        super().tearDown()

    def test_the_table(self):
        self.assertEqual(tp.SHIPPED_EFFECT, {"stated": "keep", "misread": "keep", "formal": "keep", "scope": "advisory",
                                             "design": "advisory", "clear": "advisory", "guess": "advisory"})
        self.assertEqual(tp.SHIPPED_ON_HIGHER, "keep")

    def test_nothing_is_rejected_whatever_the_model_says(self):
        for why, kw in (("stated", {"answer": "от 100 до 5 000 единиц включительно", "answer_line": 3}),
                        ("misread", {}), ("formal", {})):
            for sev in ("MINOR", "MAJOR"):
                a = f("D-001", "Не задана граница лимита", sev=sev)
                audit: list[dict] = []
                out = gate.apply_gate([a], [drop("D-001", why, severity=sev, **kw)], qi=self.qi, injection=False,
                                      audit=audit)
                self.assertEqual((a.status, a.advisory, a.severity, out.rejected, out.advisory, out.kept),
                                 ("open", False, sev, 0, 0, 1), (why, sev))
                self.assertEqual(next(x for x in audit if x["what"] == "gate_drop_refused")["cause"], "policy_keep")

    def test_old_reject_word_keeps_the_finding(self):
        a = f("D-001", "Лимит до 50 000", sev="MAJOR")
        gate.apply_gate([a], [{"fid": "D-001", "decision": "reject", "reason": "В тексте 5 000."}], qi=self.qi,
                        injection=False, audit=[])
        self.assertEqual((a.status, a.advisory), ("open", False))

    def test_minor_nitpick_becomes_an_observation(self):
        for why in ("scope", "design", "clear", "guess"):
            a = f("D-002", "Мелочь", crit="unambiguity")
            out = gate.apply_gate([a], [drop("D-002", why, reason="Это решает команда.")], qi=self.qi,
                                  injection=False, audit=[])
            self.assertEqual((a.status, a.advisory, out.advisory), ("open", True, 1), why)
            self.assertTrue(a.advisory_reason.endswith("Это решает команда."))

    def test_a_reason_that_does_not_fit_the_criterion_is_refused(self):
        for why, crit in (("clear", "completeness"), ("clear", "consistency"), ("design", "necessity"),
                          ("scope", "set_feasibility")):
            a = f("D-002", "Не описан случай", crit=crit)
            audit: list[dict] = []
            out = gate.apply_gate([a], [drop("D-002", why)], qi=self.qi, injection=False, audit=audit)
            self.assertEqual((a.status, a.advisory, out.advisory, out.refused), ("open", False, 0, 1), (why, crit))
            self.assertEqual(next(x for x in audit if x["what"] == "gate_drop_refused")["cause"], "reason_does_not_fit")
        a = f("D-002", "Слово «быстро» без числа", crit="unambiguity")
        gate.apply_gate([a], [drop("D-002", "clear")], qi=self.qi, injection=False, audit=[])
        self.assertTrue(a.advisory)

    def test_major_is_neither_moved_nor_lowered(self):
        a = f("D-001", "Нет ветки отказа", sev="MAJOR")
        audit: list[dict] = []
        out = gate.apply_gate([a], [drop("D-001", "design", severity="MAJOR")], qi=self.qi, injection=False,
                              audit=audit)
        self.assertEqual((a.status, a.advisory, a.severity, out.refused), ("open", False, "MAJOR", 1))
        self.assertEqual(next(x for x in audit if x["what"] == "gate_drop_refused")["cause"], "level")


# --- apply_gate ---------------------------------------------------------------------------------------------------


class Drop(_Doc):
    def test_stated_with_a_verified_answer_is_rejected_with_the_quote(self):
        a = f("D-001", "Не задана граница лимита", sev="MAJOR")
        audit: list[dict] = []
        out = gate.apply_gate([a], [drop("D-001", "stated", answer="от 100 до 5 000 единиц включительно",
                                         answer_line=3, reason="Границы заданы.")],
                              qi=self.qi, injection=False, audit=audit)
        self.assertEqual((a.status, out.rejected, out.kept), ("rejected", 1, 0))
        self.assertIn("Границы заданы.", a.status_reason)
        self.assertIn("строка 3", a.status_reason)
        self.assertIn("от 100 до 5 000 единиц включительно", a.status_reason)
        self.assertTrue(any(x["what"] == "skeptic_rejected" and x["why"] == "stated" for x in audit))

    def test_stated_without_a_verified_answer_is_refused(self):
        for answer, qi in (("", self.qi), ("лимит задаётся в настройках профиля клиента", self.qi),
                           ("от 100 до 5 000 единиц включительно", None)):
            a = f("D-001", "Не задана граница лимита", sev="MAJOR")
            audit: list[dict] = []
            out = gate.apply_gate([a], [drop("D-001", "stated", answer=answer)], qi=qi, injection=False, audit=audit)
            self.assertEqual((a.status, a.advisory, out.refused, out.kept), ("open", False, 1, 1), answer)
            ref = [x for x in audit if x["what"] == "gate_drop_refused"]
            self.assertEqual((ref[0]["why"], ref[0]["cause"]), ("stated", "answer_not_verified"))

    def test_stated_needs_no_quote_when_switched_off(self):
        a = f("D-001", "Не задана граница лимита")
        with mock.patch.object(gate, "STATED_NEEDS_QUOTE", False):
            gate.apply_gate([a], [drop("D-001", "stated")], qi=None, injection=False, audit=[])
        self.assertEqual(a.status, "rejected")

    def test_misread_is_rejected(self):
        a = f("D-001", "Лимит до 50 000", sev="CRITICAL")
        out = gate.apply_gate([a], [drop("D-001", "misread", reason="В тексте 5 000.")], injection=False, audit=[])
        self.assertEqual((a.status, a.status_reason, out.rejected), ("rejected", "В тексте 5 000.", 1))

    def test_each_soft_reason_on_minor_makes_an_observation(self):
        for why, label in (("scope", "вне предмета документа"), ("design", "деталь проектирования"),
                           ("clear", "формулировка ясна из текста"), ("guess", "предположение без опоры на текст"),
                           ("formal", "оформление")):
            a = f("D-002", "Придирка", crit="unambiguity")
            audit: list[dict] = []
            out = gate.apply_gate([a], [drop("D-002", why, reason="Это решает команда.")], injection=False,
                                  audit=audit)
            self.assertEqual((a.status, a.advisory, out.advisory, out.kept), ("open", True, 1, 0), why)
            self.assertEqual(a.advisory_reason, f"{label} — Это решает команда.")
            self.assertEqual(a.fid, "Н-01")
            entry = next(x for x in audit if x["what"] == "gate_advisory")
            self.assertEqual((entry["fid"], entry["observation"], entry["why"]), ("D-002", "Н-01", why))

    def test_soft_reason_lowered_to_minor_by_the_gate_itself_is_an_observation(self):
        a = f("D-001", "Придирка", sev="MAJOR")
        gate.apply_gate([a], [drop("D-001", "design", severity="MINOR")], injection=False, audit=[])
        self.assertEqual((a.advisory, a.severity), (True, "MINOR"))
        self.assertEqual(a.level_trail[-1]["level"], "MINOR")

    def test_soft_reason_on_major_is_kept_as_minor(self):
        a = f("D-001", "Придирка", sev="MAJOR")
        audit: list[dict] = []
        out = gate.apply_gate([a], [drop("D-001", "design", severity="MAJOR")], injection=False, audit=audit)
        self.assertEqual((a.status, a.advisory, a.severity, out.refused), ("open", False, "MINOR", 1))
        self.assertEqual(next(x for x in audit if x["what"] == "gate_drop_refused")["cause"], "level_lowered")

    def test_soft_reason_on_critical_with_keep_policy_keeps_the_gate_level(self):
        a = f("D-001", "Придирка", sev="CRITICAL")
        a.blocker_reason = "main_flow"
        with mock.patch.object(gate, "SOFT_ON_HIGHER", "keep"):
            gate.apply_gate([a], [drop("D-001", "scope", severity="MAJOR")], injection=False, audit=[])
        self.assertEqual((a.status, a.advisory, a.severity, a.blocker_reason), ("open", False, "MAJOR", ""))

    def test_soft_reason_on_major_when_not_only_minor(self):
        a = f("D-001", "Придирка", sev="MAJOR")
        with mock.patch.object(gate, "SOFT_ONLY_MINOR", False):
            gate.apply_gate([a], [drop("D-001", "guess")], injection=False, audit=[])
        self.assertTrue(a.advisory)

    def test_clear_with_differ_filled_is_refused(self):
        a = f("D-001", "Неясное слово", crit="unambiguity")
        audit: list[dict] = []
        gate.apply_gate([a], [drop("D-001", "clear", differ="Разработчик и тестировщик посчитают по-разному.")],
                        injection=False, audit=audit)
        self.assertFalse(a.advisory)
        self.assertEqual(next(x for x in audit if x["what"] == "gate_drop_refused")["cause"], "differ_filled")
        b = f("D-002", "Неясное слово")  # another soft reason with differ filled is not refused
        gate.apply_gate([b], [drop("D-002", "design", differ="что-то")], injection=False, audit=[])
        self.assertTrue(b.advisory)

    def test_drop_without_a_known_reason_is_refused(self):
        for why in ("", "мне не нравится"):
            a = f("D-001", "Замечание")
            audit: list[dict] = []
            gate.apply_gate([a], [drop("D-001", why)], injection=False, audit=audit)
            self.assertEqual((a.status, a.advisory), ("open", False))
            self.assertEqual(next(x for x in audit if x["what"] == "gate_drop_refused")["cause"], "no_reason")

    def test_effect_keep_is_refused(self):
        a = f("D-001", "Замечание")
        with mock.patch.dict(gate.EFFECT, {"formal": "keep"}):
            gate.apply_gate([a], [drop("D-001", "formal")], injection=False, audit=[])
        self.assertEqual((a.status, a.advisory), ("open", False))

    def test_rule_finding_is_never_dropped(self):
        for why in ("misread", "design", "stated"):
            r = f("D-001", "Неизмеримое слово", source="rule:vague-word", rules=["vague-word"])
            audit: list[dict] = []
            out = gate.apply_gate([r], [drop("D-001", why, answer="от 100 до 5 000 единиц включительно")],
                                  qi=self.qi, injection=False, audit=audit)
            self.assertEqual((r.status, r.advisory, out.protected, out.kept), ("open", False, 1, 1), why)
            self.assertTrue(any(x["what"] == "rule_finding_protected" for x in audit))

    def test_rule_finding_level_is_protected_under_injection(self):
        r = f("D-001", "Неизмеримое слово", sev="MAJOR", source="rule:vague-word", rules=["vague-word"])
        out = gate.apply_gate([r], [{"fid": "D-001", "decision": "keep", "severity": "MINOR"}], injection=True,
                              audit=[])
        self.assertEqual((r.severity, out.protected), ("MAJOR", 1))
        r2 = f("D-001", "Неизмеримое слово", sev="MAJOR", source="rule:vague-word", rules=["vague-word"])
        gate.apply_gate([r2], [{"fid": "D-001", "decision": "keep", "severity": "MINOR"}], injection=False, audit=[])
        self.assertEqual(r2.severity, "MINOR")

    def test_observations_continue_the_numbering_in_document_order(self):
        obs = f("Н-01", "Наблюдение правила")
        obs.advisory = True
        a = f("D-001", "Вторая придирка", quotes=[q(line=9)])
        b = f("D-002", "Первая придирка", quotes=[q(line=4)])
        c = f("D-003", "Настоящий дефект", sev="MAJOR")
        gate.apply_gate([obs, a, b, c], [drop("D-001", "formal"), drop("D-002", "design")], injection=False, audit=[])
        self.assertEqual((obs.fid, b.fid, a.fid, c.fid), ("Н-01", "Н-02", "Н-03", "D-003"))

    def test_absent_findings_stay_unchanged(self):
        a = f("D-001", "Замечание", sev="MAJOR")
        out = gate.apply_gate([a], [{"fid": "D-777", "decision": "drop", "why": "misread"}], injection=False, audit=[])
        self.assertEqual((a.status, a.severity, out.kept), ("open", "MAJOR", 1))


class Keep(unittest.TestCase):
    def test_keep_uses_the_level_logic(self):
        a = f("D-001", "Замечание", sev="CRITICAL")
        a.blocker_reason = "main_flow"
        out = gate.apply_gate([a], [{"fid": "D-001", "decision": "keep", "severity": "CRITICAL", "blocker": "none"}],
                              injection=False, audit=[])
        self.assertEqual((a.severity, out.critical_demoted), ("MAJOR", 1))

    def test_unknown_decision_word_is_keep(self):
        a = f("D-001", "Замечание")
        out = gate.apply_gate([a], [{"fid": "D-001", "decision": "maybe"}], injection=False, audit=[])
        self.assertEqual((a.status, out.kept), ("open", 1))


class Same(unittest.TestCase):
    def test_same_topic_merges_into_the_more_severe(self):
        a = f("D-001", "Не задан шаг значения лимита", sev="MINOR")
        b = f("D-002", "Шаг значения лимита не задан", sev="MAJOR", quotes=[q("до 5 000 единиц", 3)])
        audit: list[dict] = []
        out = gate.apply_gate([a, b], [{"fid": "D-002", "decision": "same", "same_as": "D-001"}], injection=False,
                              audit=audit)
        self.assertEqual((a.status, a.merged_into, b.status, out.merged), ("merged", "D-002", "open", 1))
        self.assertTrue(any(x["what"] == "skeptic_merge_swapped" for x in audit))
        self.assertIn(a.title, b.also)

    def test_different_topic_is_refused_and_both_stay(self):
        a = f("D-001", "Не задан шаг значения лимита")
        b = f("D-002", "Часовой пояс сброса не указан")
        audit: list[dict] = []
        out = gate.apply_gate([a, b], [{"fid": "D-002", "decision": "same", "same_as": "D-001"}], injection=False,
                              audit=audit)
        self.assertEqual((a.status, b.status, out.merged, out.kept), ("open", "open", 0, 2))
        ref = next(x for x in audit if x["what"] == "gate_merge_refused")
        self.assertEqual((ref["fid"], ref["into"], ref["cause"]), ("D-002", "D-001", "guard_topic"))

    def test_guard_none_merges_anything(self):
        a = f("D-001", "Не задан шаг значения лимита")
        b = f("D-002", "Часовой пояс сброса не указан", quotes=[q("по времени Алматы", 4, "REQ-002")], reqs=["REQ-002"])
        with mock.patch.object(gate, "MERGE_GUARD", "none"):
            gate.apply_gate([a, b], [{"fid": "D-002", "decision": "same", "same_as": "D-001"}], injection=False,
                            audit=[])
        self.assertEqual(b.status, "merged")

    def test_old_vocabulary(self):
        a = f("D-001", "Не задан шаг значения лимита")
        b = f("D-002", "Шаг значения лимита не задан")
        c = f("D-003", "Выдумка")
        out = gate.apply_gate([a, b, c], [{"fid": "D-002", "decision": "merge", "merge_into": "D-001"},
                                          {"fid": "D-003", "decision": "reject", "reason": "нет такого"}],
                              injection=False, audit=[])
        self.assertEqual((b.status, b.merged_into, c.status, out.merged, out.rejected), ("merged", "D-001", "rejected", 1, 1))
        self.assertEqual(gate.decision_of({"decision": "reject"}), ("drop", "misread", ""))
        self.assertEqual(gate.decision_of({"decision": "merge", "merge_into": "D-001"}), ("same", "", "D-001"))

    def test_chained_merges_follow_the_survivor(self):
        a = f("D-001", "Не задан шаг значения лимита")
        b = f("D-002", "Шаг значения лимита не задан")
        c = f("D-003", "Не указан шаг значения лимита")
        out = gate.apply_gate([a, b, c], [{"fid": "D-001", "decision": "same", "same_as": "D-002"},
                                          {"fid": "D-002", "decision": "same", "same_as": "D-003"}],
                              injection=False, audit=[])
        self.assertEqual((a.merged_into, b.merged_into, c.status, out.merged), ("D-002", "D-003", "open", 2))

    def test_merge_into_an_observation_is_refused(self):
        a = f("D-001", "Не задан шаг значения лимита")
        b = f("D-002", "Шаг значения лимита не задан")
        audit: list[dict] = []
        gate.apply_gate([a, b], [drop("D-001", "design"), {"fid": "D-002", "decision": "same", "same_as": "D-001"}],
                        injection=False, audit=audit)
        self.assertEqual((a.advisory, b.status, b.advisory), (True, "open", False))
        self.assertEqual(next(x for x in audit if x["what"] == "gate_merge_refused")["cause"], "target_observation")

    def test_rule_finding_is_not_merged_away_under_injection(self):
        r = f("D-001", "Не задан шаг значения лимита", source="rule:x", rules=["x"])
        m = f("D-002", "Шаг значения лимита не задан", sev="MAJOR")
        out = gate.apply_gate([r, m], [{"fid": "D-001", "decision": "same", "same_as": "D-002"}], injection=True,
                              audit=[])
        self.assertEqual((r.status, out.protected), ("open", 1))
        r2 = f("D-001", "Не задан шаг значения лимита", source="rule:x", rules=["x"])
        m2 = f("D-002", "Шаг значения лимита не задан", sev="MAJOR")
        gate.apply_gate([r2, m2], [{"fid": "D-002", "decision": "same", "same_as": "D-001"}], injection=True, audit=[])
        self.assertEqual((r2.status, m2.status, m2.merged_into), ("open", "merged", "D-001"))  # the rule survives


# --- apply_dedup --------------------------------------------------------------------------------------------------


class Dedup(unittest.TestCase):
    def test_group_merges_into_the_more_severe(self):
        a = f("D-001", "Не задан шаг значения лимита")
        b = f("D-002", "Шаг значения лимита не задан", sev="MAJOR")
        c = f("D-003", "Не указан шаг значения лимита")
        audit: list[dict] = []
        n = gate.apply_dedup([a, b, c], [{"keep": "D-001", "same": ["D-002", "D-003"], "why": "шаг лимита"}],
                             injection=False, audit=audit)
        self.assertEqual(n, 2)
        self.assertEqual((a.status, a.merged_into, b.status, c.merged_into), ("merged", "D-002", "open", "D-002"))
        self.assertEqual(c.status_reason, "повтор: шаг лимита")
        self.assertEqual(sum(1 for x in audit if x["what"] == "dedup_merged"), 2)

    def test_unknown_closed_self_and_observations_are_ignored(self):
        a = f("D-001", "Не задан шаг значения лимита")
        b = f("D-002", "Шаг значения лимита не задан")
        b.status = "rejected"
        o = f("Н-01", "Шаг значения лимита не задан")
        o.advisory = True
        n = gate.apply_dedup([a, b, o], [{"keep": "D-001", "same": ["D-001", "D-002", "Н-01", "D-999"]},
                                         {"keep": "D-999", "same": ["D-001"]}, "мусор"], injection=False, audit=[])
        self.assertEqual((n, a.status, b.status, o.status), (0, "open", "rejected", "open"))

    def test_guard_refuses_different_topics(self):
        a = f("D-001", "Не задан шаг значения лимита")
        b = f("D-002", "Часовой пояс сброса не указан")
        audit: list[dict] = []
        n = gate.apply_dedup([a, b], [{"keep": "D-001", "same": ["D-002"]}], injection=False, audit=audit)
        self.assertEqual((n, b.status), (0, "open"))
        self.assertTrue(any(x["what"] == "dedup_merge_refused" for x in audit))
        with mock.patch.object(gate, "DEDUP_GUARD", "none"):
            n = gate.apply_dedup([a, b], [{"keep": "D-001", "same": ["D-002"]}], injection=False, audit=[])
        self.assertEqual((n, b.status), (1, "merged"))

    def test_rule_findings_under_injection(self):
        r = f("D-001", "Не задан шаг значения лимита", source="rule:x", rules=["x"], sev="MINOR")
        m = f("D-002", "Шаг значения лимита не задан", sev="MAJOR")
        n = gate.apply_dedup([r, m], [{"keep": "D-002", "same": ["D-001"]}], injection=True, audit=[])
        self.assertEqual((n, r.status, m.status, m.merged_into), (1, "open", "merged", "D-001"))
        r1 = f("D-001", "Не задан шаг значения лимита", source="rule:x", rules=["x"])
        r2 = f("D-002", "Шаг значения лимита не задан", source="rule:y", rules=["y"])
        n = gate.apply_dedup([r1, r2], [{"keep": "D-001", "same": ["D-002"]}], injection=True, audit=[])
        self.assertEqual((n, r2.status), (0, "open"))

    def test_chained_groups(self):
        a = f("D-001", "Не задан шаг значения лимита")
        b = f("D-002", "Шаг значения лимита не задан")
        c = f("D-003", "Не указан шаг значения лимита")
        n = gate.apply_dedup([a, b, c], [{"keep": "D-001", "same": ["D-002"]}, {"keep": "D-002", "same": ["D-003"]}],
                             injection=False, audit=[])
        self.assertEqual((n, c.merged_into), (2, "D-001"))


# --- order, chunks, dedup call, estimate ------------------------------------------------------------------------


def many(n: int) -> list[Finding]:
    out = []
    for i in range(n):
        line = (i * 7) % n + 1  # ids and positions disagree
        out.append(f(f"D-{i + 1:03d}", f"Замечание {i}", quotes=[q(f"текст {i}", line)]))
    return out


class Order(unittest.TestCase):
    def test_chunks_follow_the_position(self):
        fs = many(30)
        fs[0].status = "rejected"
        fs[1].advisory = True
        chunks = pipeline.gate_chunks(fs)
        self.assertEqual([len(c) for c in chunks], [12, 12, 4])
        flat = [x for c in chunks for x in c]
        self.assertEqual([x.quotes[0].line_start for x in flat], sorted(x.quotes[0].line_start for x in flat))
        self.assertNotIn(fs[0], flat)
        self.assertNotIn(fs[1], flat)
        self.assertEqual([len(c) for c in pipeline.gate_chunks(fs, economy=True)], [28])

    def test_ties_by_id_and_findings_without_quotes_last(self):
        a, b, c = f("D-002", "x", quotes=[q(line=5)]), f("D-001", "y", quotes=[q(line=5)]), f("D-003", "z", quotes=[])
        self.assertEqual([x.fid for x in pipeline.gate_order([c, a, b])], ["D-001", "D-002", "D-003"])

    def test_dedup_line_format(self):
        a = f("D-001", "Заголовок", sev="MAJOR", quotes=[Quote(text="t", line_start=4, line_end=6)])
        self.assertEqual(prompts.dedup_line(a), "D-001 | стр. 4–6 | MAJOR | completeness | Заголовок | Заголовок")


def _gate_lines(user: str) -> list[str]:
    return [ln for ln in user.split("ЗАМЕЧАНИЯ:\n", 1)[1].splitlines() if re.match(r"D-\d{3} \|", ln)]


class Calls(tp._Base):
    def test_dedup_called_once_in_full_mode(self):
        res, b = self.run_fake()
        st = [s for s, _, _ in b.log]
        self.assertEqual(st.count("dedup"), 1)
        self.assertLess(st.index("skeptic"), st.index("dedup"))
        self.assertLess(st.index("dedup"), st.index("questions"))
        user = next(u for s, _, u in b.log if s == "dedup")
        self.assertTrue(all(re.match(r"D-\d{3} \| стр\. ", ln) for ln in user.split("ЗАМЕЧАНИЯ:\n", 1)[1].splitlines()))

    def test_no_dedup_in_economy_and_fast_modes(self):
        b = FakeBackend(tp.make_script(), concurrency=1)
        b.economy = True
        _res, _ = self.run_fake(backend=b)
        st = [s for s, _, _ in b.log]
        self.assertIn("skeptic", st)
        self.assertNotIn("dedup", st)
        _res, b2 = self.run_fake(depth="fast")
        self.assertFalse({"skeptic", "dedup"} & {s for s, _, _ in b2.log})

    def test_chunks_of_twelve_in_document_order(self):
        seen: list[list[str]] = []

        def sk(user):
            seen.append(_gate_lines(user))
            return tp.skeptic_answer(user, reject_titles=())
        fs = many(30)
        gate_obj = pipeline.Gate(FakeBackend(tp.make_script(skeptic=sk), concurrency=1), "s", None, [])
        info = pipeline.RunInfo()
        pipeline._skeptic(gate_obj, fs, info, "", [], [], lambda _s: None)
        self.assertEqual([len(x) for x in seen], [12, 12, 6])
        lines = [int(re.search(r"строка (\d+)", ln).group(1)) for ch in seen for ln in ch]
        self.assertEqual(lines, sorted(lines))
        self.assertEqual(info.skeptic_kept, 30)

    def test_failed_chunk_degrades(self):
        from revizor.llm import LLMBadOutput
        state = {"n": 0}

        def sk(user):
            state["n"] += 1
            if state["n"] == 1:
                raise LLMBadOutput("сломано")
            return {"decisions": [{"fid": ln.split(" |")[0], "decision": "drop", "why": "misread"}
                                  for ln in _gate_lines(user)]}
        fs = many(20)
        unchecked: list[str] = []
        gate_obj = pipeline.Gate(FakeBackend(tp.make_script(skeptic=sk), concurrency=1, max_attempts=1), "s", None, [])
        info = pipeline.RunInfo()
        pipeline._skeptic(gate_obj, fs, info, "", [], unchecked, lambda _s: None)
        self.assertEqual(sum(1 for x in fs if x.status == "open"), 12)  # the failed chunk's findings stay
        self.assertEqual((info.skeptic_rejected, info.skeptic_kept), (8, 12))
        self.assertTrue(any("Перепроверка не выполнена" in u for u in unchecked))


class Estimate(tp._Base):
    def test_calls_of_the_new_stage(self):
        path = self.doc()
        full = estimate.build(path, depth="full")
        eco = estimate.build(path, depth="full", economy=True)
        fast = estimate.build(path, depth="fast")
        n = lambda e, st: sum(1 for c in e.calls if c.stage == st)
        total = n(full, "skeptic")
        self.assertGreaterEqual(total, 1)
        self.assertEqual(n(full, "dedup"), 1)
        self.assertEqual(n(eco, "dedup"), 0)
        self.assertLessEqual(n(eco, "skeptic"), total)
        self.assertEqual((n(fast, "skeptic"), n(fast, "dedup")), (0, 0))
        text = "\n".join(estimate.report(full, provider="anthropic", model="claude-haiku-5-5", depth="full", env={}))
        self.assertIn("+ повторы 1", text)
        self.assertIn("dedup: 1 ×", text)

    def test_chunk_count_follows_the_constant(self):
        path = self.doc()
        with mock.patch.object(estimate, "GATE_CHUNK", 1):
            e = estimate.build(path, depth="full")
        base = estimate.build(path, depth="full")
        self.assertGreater(sum(1 for c in e.calls if c.stage == "skeptic"),
                           sum(1 for c in base.calls if c.stage == "skeptic"))

    def test_dedup_effort_follows_the_skeptic(self):
        locked, over = estimate.stage_efforts({"ANTHROPIC_EFFORT_STAGES": "skeptic=high"})
        self.assertEqual(estimate.effort_of("dedup", locked, over), "high")
        locked, over = estimate.stage_efforts({"ANTHROPIC_EFFORT_STAGES": "skeptic=high,dedup=low"})
        self.assertEqual(estimate.effort_of("dedup", locked, over), "low")
        self.assertEqual(estimate.effort_of("dedup", "", {}), "medium")


# --- a full offline run: one observation, one reject, one merge --------------------------------------------------


def _set_answer(stage: str) -> dict:
    if stage == "set_standards":
        return {"findings": [{"criterion": "set_feasibility", "severity": "MINOR",
                              "title": "Не описан отказ сервиса push",
                              "problem": "Что делать, если сервис push не ответил.",
                              "quotes": ["Сервис должен отправлять push клиенту"], "req_ids": ["REQ-001"],
                              "recommendation": "Опишите отказ."}]}
    return tp.set_answer(stage)


def _gate_answer(user: str) -> dict:
    lines = _gate_lines(user)
    fid_of = {}
    for ln in lines:
        fid_of[ln.split(" | ")[4]] = ln.split(" |")[0]
    out = []
    for ln in lines:
        fid, title = ln.split(" |")[0], ln.split(" | ")[4]
        if title == "Не сказано, что с тиынами":
            out.append({"fid": fid, "decision": "drop", "why": "design", "severity": "MINOR", "differ": "",
                        "reason": "Формат суммы решает команда при проектировании."})
        elif title == "Слабое замечание к набору":
            out.append({"fid": fid, "decision": "drop", "why": "misread", "reason": "Цитата этого не говорит."})
        elif title == "Не описан отказ сервиса push":
            out.append({"fid": fid, "decision": "same", "same_as": fid_of.get("Нет сценария отказа сервиса push", ""),
                        "reason": "Тот же отказ сервиса push."})
        else:
            out.append({"fid": fid, "decision": "keep", "reason": "дефект есть"})
    return {"decisions": out}


class FullRun(tp._Base):
    def script(self):
        inner = tp.make_script(skeptic=_gate_answer)

        def s(stage, system, user):
            if stage.startswith("set_"):
                return _set_answer(stage)
            return inner(stage, system, user)
        return s

    def test_observation_reject_and_merge_with_a_consistent_report(self):
        res, b = self.run_fake(self.script())
        self.assertEqual((res.run.skeptic_advisory, res.run.skeptic_rejected, res.run.skeptic_merged), (1, 1, 1))
        obs = [x for x in res.findings if x.advisory and x.advisory_reason]
        self.assertEqual([x.title for x in obs], ["Не сказано, что с тиынами"])
        o = obs[0]
        self.assertTrue(o.fid.startswith("Н-"))
        self.assertEqual(o.advisory_reason, "деталь проектирования — Формат суммы решает команда при проектировании.")
        former = next(a for a in res.audit if a.get("what") == "gate_advisory")
        self.assertEqual(former["observation"], o.fid)
        self.assertTrue(former["fid"].startswith("D-"))
        merged = [x for x in res.rejected if x.status == "merged"]
        self.assertEqual([x.title for x in merged], ["Не описан отказ сервиса push"])
        survivor = next(x for x in res.findings if x.fid == merged[0].merged_into)
        self.assertEqual(survivor.title, "Нет сценария отказа сервиса push")
        self.assertEqual([x.title for x in res.rejected if x.status == "rejected"], ["Слабое замечание к набору"])

        # the observation is nowhere a defect is counted or linked
        self.assertFalse(any(o.fid in q.finding_ids or former["fid"] in q.finding_ids for q in res.questions))
        self.assertFalse(any(o.fid in a.finding_ids for a in res.fix_first))
        self.assertFalse(any(o.fid in t.finding_ids for t in res.tests))
        self.assertEqual(report_json.summary(res)["findings"], len(consistency.counted(res.findings)))
        self.assertNotIn(o.title, exports.jira_csv(res) or "")
        self.assertNotIn(o.title, sidefiles.findings_csv(res) or "")

        md = report_md.render(res)
        block = md.split('<a id="s4-obs"></a>', 1)[1].split("\n### ", 2)[1]
        self.assertIn(f"**{o.fid}**", block)
        self.assertIn("  - Почему не в основном списке: деталь проектирования — Формат суммы решает команда", block)
        self.assertIn("в наблюдения 1, отклонено 1, объединено 1", md)
        self.assertEqual(consistency.check_consistency(res, md), [])
        self.assertFalse(any("противоречить" in w for w in res.run.warnings))
        # every in-report link points at an anchor that exists
        anchors = set(re.findall(r'<a id="([^"]+)"></a>', md))
        for target in re.findall(r"\]\(#([^)]+)\)", md):
            self.assertIn(target, anchors)
        # and the JSON report reads back with the new field
        back = report_json.from_data(json.loads(report_json.render(res)))
        self.assertEqual(next(x for x in back.findings if x.fid == o.fid).advisory_reason, o.advisory_reason)

    def test_console_line_keeps_its_prefix(self):
        lines: list[str] = []
        self.run_fake(self.script(), progress=lines.append)
        line = next(x for x in lines if x.startswith("[7/9] Перепроверяю замечания…"))
        self.assertRegex(line, r"оставлено \d+, в наблюдения 1, отклонено 1, объединено 1")

    def test_old_report_json_without_the_field_reads(self):
        res, _ = self.run_fake(self.script())
        data = json.loads(report_json.render(res))
        for x in data["findings"]:
            x.pop("advisory_reason", None)
        data["run"].pop("skeptic_advisory", None)
        back = report_json.from_data(data)
        self.assertTrue(all(x.advisory_reason == "" for x in back.findings))
        self.assertEqual(back.run.skeptic_advisory, 0)


class StrictDedupSwitch(unittest.TestCase):
    def test_off_by_default_and_stricter_when_on(self):
        self.assertFalse(merge.DEDUPE_STRICT)
        a = f("D-001", "Не задан шаг значения лимита", quotes=[q("Клиент задаёт лимит от 100", 3)])
        b = f("D-002", "Шаг лимита: любое целое или кратное 1 000", quotes=[q("Клиент задаёт лимит от 100", 3)],
              problem="Не задан шаг значения лимита")
        loose = merge.model_twins(a, b)
        with mock.patch.object(merge, "DEDUPE_STRICT", True):
            strict = merge.model_twins(a, b)
        self.assertTrue(loose)
        self.assertFalse(strict)


if __name__ == "__main__":
    unittest.main()
