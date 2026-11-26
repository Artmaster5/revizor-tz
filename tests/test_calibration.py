"""Severity calibration (closed list of blocker reasons) and the lower-effort retry of a cut-off answer.

Offline: FakeBackend only, no network, no key."""
from __future__ import annotations

import json
import re
import tempfile
import unittest

from revizor import report_json, report_md
from revizor.llm import FakeBackend, LLMBadOutput, Usage
from revizor.llm.schema import validate
from revizor.gate import apply_gate
from revizor.merge import RULE_BLOCKER, rule_blocker
from revizor.model import BLOCKER_REASONS, BLOCKER_RU, Finding, Quote
from revizor.prompts import (REVIEW_LENSES, REVIEW_SCHEMA, SET_SCHEMA, SET_STAGES, GATE_HEAD, GATE_SCHEMA,
                             SYSTEM_RULES, review_user, set_user)

from tests import test_pipeline as tp


# --- prompts and schemas ---------------------------------------------------------------------------------------


class ScaleInPrompts(unittest.TestCase):
    def test_system_rules_carry_the_closed_list(self):
        for code in BLOCKER_REASONS:
            self.assertIn(code, SYSTEM_RULES)
        self.assertIn("Всё остальное — не CRITICAL", SYSTEM_RULES)
        self.assertIn("если сомневаешься между двумя уровнями, бери нижний", SYSTEM_RULES)
        self.assertIn("два грамотных разработчика", SYSTEM_RULES)
        self.assertIn("бизнес-цели", SYSTEM_RULES)
        self.assertIn("Поле blocker заполняй только для CRITICAL", SYSTEM_RULES)

    def test_shapes_ask_for_blocker(self):
        batch = [{"rid": "REQ-001", "text": "т"}]
        for lens in list(REVIEW_LENSES) + [None]:  # None = the combined pass of --depth fast
            u = review_user(batch, [], lens)
            self.assertIn('"blocker":"contradiction|main_flow|protocol|loss|missing_part|none', u)
            self.assertIn("пяти оснований", u)
        for st in SET_STAGES:
            u = set_user(st, [{"rid": "REQ-001"}], [])
            self.assertIn('"blocker":"contradiction|main_flow|protocol|loss|missing_part|none', u)

    def test_gate_prompt_names_every_answer_word(self):
        # a contract between the prompt (tuned by hand) and the schema, independent of the wording
        props = GATE_SCHEMA["properties"]["decisions"]["items"]["properties"]
        for field in ("decision", "why", "blocker", "severity"):
            for word in props[field]["enum"]:
                if word:
                    self.assertIn(word, GATE_HEAD, (field, word))
        self.assertIn('"blocker"', GATE_HEAD)

    def test_schemas_have_blocker_with_default(self):
        rev = REVIEW_SCHEMA["properties"]["reviews"]["items"]["properties"]["findings"]["items"]["properties"]
        st = SET_SCHEMA["properties"]["findings"]["items"]["properties"]
        sk = GATE_SCHEMA["properties"]["decisions"]["items"]["properties"]
        for props in (rev, st):
            self.assertEqual(props["blocker"]["default"], "none")
            self.assertEqual(set(props["blocker"]["enum"]), set(BLOCKER_REASONS) | {"none"})
        self.assertIn("", sk["blocker"]["enum"])
        data, errors = validate({"findings": [{"title": "t", "quotes": ["q"], "blocker": "мне так кажется"}]},
                                SET_SCHEMA)
        self.assertEqual(errors, [])
        self.assertEqual(data["findings"][0]["blocker"], "none")


# --- skeptic decisions ------------------------------------------------------------------------------------------


def _f(fid, sev, *, source="model", rules=(), reason="", crit="completeness"):
    return Finding(scope="requirement", criterion=crit, severity=sev, title="t " + fid, problem="p",
                   recommendation="r", quotes=[Quote(text="x " + fid, line_start=int(fid[-1]), line_end=int(fid[-1]))],
                   source=source, rule_ids=list(rules), fid=fid, blocker_reason=reason)


class SkepticBlockers(unittest.TestCase):
    def test_keep_critical_without_reason_lowers_to_major(self):
        f = _f("D-001", "CRITICAL", reason="main_flow")
        audit: list[dict] = []
        out = apply_gate([f], [{"fid": "D-001", "decision": "keep", "severity": "CRITICAL", "blocker": "none"}],
                            injection=False, audit=audit)
        self.assertEqual(f.severity, "MAJOR")
        self.assertEqual(f.blocker_reason, "")
        self.assertEqual(out.critical_demoted, 1)
        self.assertIn("не названо основание блокера", f.status_reason)
        self.assertTrue(any(a["what"] == "critical_without_reason" and a["stage"] == "skeptic" for a in audit))

    def test_keep_critical_with_reason_stays(self):
        f = _f("D-001", "CRITICAL", reason="main_flow")
        out = apply_gate([f], [{"fid": "D-001", "decision": "keep", "blocker": "contradiction"}],
                            injection=False, audit=[])
        self.assertEqual((f.severity, f.blocker_reason), ("CRITICAL", "contradiction"))
        self.assertEqual(out.critical_demoted, 0)

    def test_over_detail_is_lowered_to_minor_not_rejected(self):
        f = _f("D-001", "CRITICAL", reason="main_flow")
        out = apply_gate([f], [{"fid": "D-001", "decision": "keep", "severity": "MINOR", "blocker": "none"}],
                            injection=False, audit=[])
        self.assertEqual((f.status, f.severity, f.blocker_reason), ("open", "MINOR", ""))
        self.assertEqual(out.rejected, 0)
        self.assertEqual(out.critical_demoted, 0)

    def test_raise_to_critical_needs_reason(self):
        a, b = _f("D-001", "MAJOR"), _f("D-002", "MAJOR")
        why = "Суть та же: " + a.title  # a higher level must be argued with this very finding (see test_merge_topic)
        apply_gate([a, b], [{"fid": "D-001", "decision": "keep", "severity": "CRITICAL", "blocker": "loss",
                                "reason": why},
                               {"fid": "D-002", "decision": "keep", "severity": "CRITICAL", "blocker": "",
                                "reason": why}],
                      injection=False, audit=[])
        self.assertEqual((a.severity, a.blocker_reason), ("CRITICAL", "loss"))
        self.assertEqual((b.severity, b.blocker_reason), ("MAJOR", ""))

    def test_rule_critical_keeps_its_reason_and_protections_hold(self):
        r = _f("D-001", "CRITICAL", source="rule:get-mutation", rules=["get-mutation"], reason="protocol",
               crit="set_feasibility")
        apply_gate([r], [{"fid": "D-001", "decision": "keep"}], injection=False, audit=[])
        self.assertEqual((r.severity, r.blocker_reason), ("CRITICAL", "protocol"))
        audit: list[dict] = []
        out = apply_gate([r], [{"fid": "D-001", "decision": "reject", "reason": "x"}], injection=False, audit=audit)
        self.assertEqual((r.status, r.severity), ("open", "CRITICAL"))
        self.assertEqual(out.protected, 1)
        # under a suspected injection a rule finding can be neither merged nor lowered
        m = _f("D-002", "MAJOR")
        out = apply_gate([r, m], [{"fid": "D-001", "decision": "merge", "merge_into": "D-002", "severity": "MINOR"}],
                            injection=True, audit=[])
        self.assertEqual((r.status, r.severity, r.blocker_reason), ("open", "CRITICAL", "protocol"))
        self.assertEqual(out.protected, 2)
        self.assertEqual(out.critical_demoted, 0)

    def test_rule_reason_map(self):
        for rid, code in RULE_BLOCKER.items():
            self.assertIn(code, BLOCKER_REASONS)
            self.assertEqual(rule_blocker(_f("D-001", "CRITICAL", source="rule:" + rid, rules=[rid])), code)
        self.assertEqual(rule_blocker(_f("D-001", "CRITICAL", rules=["new-rule"], crit="set_consistency")),
                         "contradiction")


# --- through the pipeline --------------------------------------------------------------------------------------


def _review_critical(stage, user):
    ans = tp.review_answer("review_clarity" if stage == "review" else stage, user)
    for r in ans["reviews"]:
        for f in r["findings"]:
            if f["title"].startswith("Не задан срок"):
                f["severity"] = "CRITICAL"  # no blocker given
    return ans


def _set_critical(stage):
    ans = tp.set_answer(stage)
    if stage == "set_completeness":
        ans["findings"][0]["severity"] = "CRITICAL"
        ans["findings"][0]["blocker"] = "main_flow"
    return ans


def _skeptic_echo(user):
    out = []
    for ln in user.split("ЗАМЕЧАНИЯ:\n", 1)[1].splitlines():
        m = re.match(r"(D-\d{3}) \|", ln)
        if m:
            b = "main_flow" if "основание: main_flow" in ln else "none"
            out.append({"fid": m.group(1), "decision": "keep", "blocker": b, "reason": "проверено"})
    return {"decisions": out}


class PipelineBlockers(tp._Base):
    def script(self):
        inner = tp.make_script(skeptic=_skeptic_echo)

        def s(stage, system, user):
            if stage.startswith("review"):
                return _review_critical(stage, user)
            if stage.startswith("set_"):
                return _set_critical(stage)
            return inner(stage, system, user)
        return s

    def test_critical_without_reason_demoted_with_reason_kept_and_reported(self):
        res, b = self.run_fake(self.script())
        fast = [f for f in res.findings if "быстро" in " ".join(q.text for q in f.quotes)][0]
        self.assertEqual(fast.severity, "MAJOR")
        crit = [f for f in res.findings if f.severity == "CRITICAL"]
        self.assertEqual([f.title for f in crit], ["Нет сценария отказа сервиса push"])
        self.assertEqual(crit[0].blocker_reason, "main_flow")
        self.assertEqual(res.run.critical_demoted, 1)
        self.assertTrue(any(a.get("what") == "critical_without_reason" for a in res.audit))
        self.assertIn("основание: main_flow", next(u for st, _s, u in b.log if st == "skeptic"))
        md = report_md.render(res)
        self.assertIn("- Почему блокер: " + BLOCKER_RU["main_flow"], md)
        self.assertEqual(md.count("Почему блокер:"), 1)
        self.assertIn("понижено блокеров без основания: 1", md)
        data = json.loads(report_json.render(res))
        by_title = {f["title"]: f for f in data["findings"]}
        self.assertEqual(by_title["Нет сценария отказа сервиса push"]["blocker_reason"], "main_flow")
        self.assertEqual(by_title[fast.title]["blocker_reason"], "")
        self.assertEqual(data["run"]["critical_demoted"], 1)
        self.assertEqual(data["summary"]["by_severity"]["CRITICAL"], 1)
        self.assertIn("блокеров 1 · ", md)

    def test_fast_depth_uses_the_same_scale(self):
        res, b = self.run_fake(self.script(), depth="fast")
        review_users = [u for st, _s, u in b.log if st == "review"]
        self.assertTrue(review_users)
        self.assertIn('"blocker"', review_users[0])
        self.assertEqual(res.run.critical_demoted, 1)
        self.assertTrue(all(f.blocker_reason in BLOCKER_REASONS for f in res.findings if f.severity == "CRITICAL"))

    def test_every_open_critical_has_a_reason_rules_included(self):
        res, _ = self.run_fake(text=tp.DOC + tp.INJECTION)
        inj = [f for f in res.findings if "prompt-injection" in f.rule_ids]
        self.assertEqual((inj[0].severity, inj[0].blocker_reason), ("CRITICAL", "loss"))
        for f in res.findings:
            if f.severity == "CRITICAL":
                self.assertIn(f.blocker_reason, BLOCKER_REASONS, f.title)
        self.assertIn("Почему блокер: " + BLOCKER_RU["loss"], report_md.render(res))


# --- cut-off answers: lower effort, not a bigger bill --------------------------------------------------------------


def trunc(limit: int) -> LLMBadOutput:
    e = LLMBadOutput("Ответ модели обрезан по лимиту токенов", kind="truncated")
    e.limit = limit
    e.usage = Usage(output_tokens=limit)
    return e


class Rec(FakeBackend):
    """Records (max_tokens, effort) of every raw call; stage «x» runs at ``effort``."""

    def __init__(self, items, effort="high", **kw):
        super().__init__({"x": items}, **kw)
        self.effort = "medium"
        self.effort_locked = False
        self.stage_effort = {"x": effort}
        self.sent: list[tuple[int, str | None]] = []

    def _raw(self, system, user, max_tokens, *, effort=None):
        self.sent.append((max_tokens, effort))
        return super()._raw(system, user, max_tokens, effort=effort)


class TruncationRetry(unittest.TestCase):
    def test_retry_once_with_lower_effort_same_limit(self):
        b = Rec([trunc(64000), {"a": 1}], effort="high")
        data, usage = b.complete_json("s", "u", max_tokens=64000, stage="x")
        self.assertEqual(data, {"a": 1})
        self.assertEqual(b.sent, [(64000, "high"), (64000, "medium")])
        self.assertGreaterEqual(usage.output_tokens, 64000)  # the cut-off attempt is still counted
        note = next(n for n in b.notes if "обрезан" in n)
        self.assertIn("лимит 64000, усилие high", note)
        self.assertIn("лимитом 64000 и усилием medium", note)

    def test_effort_steps(self):
        for start, lower in (("max", "high"), ("xhigh", "high"), ("medium", "low")):
            b = Rec([trunc(64000), {"a": 1}], effort=start)
            b.complete_json("s", "u", max_tokens=64000, stage="x")
            self.assertEqual([e for _n, e in b.sent], [start, lower], start)

    def test_lower_effort_retry_raises_a_small_limit_to_the_ceiling(self):
        b = Rec([trunc(32000), {"a": 1}], effort="high")
        b.complete_json("s", "u", max_tokens=16000, stage="x")
        self.assertEqual(b.sent, [(16000, "high"), (64000, "medium")])

    def test_low_effort_keeps_the_doubled_limit(self):
        b = Rec([trunc(32000), {"a": 1}], effort="low")
        b.complete_json("s", "u", max_tokens=32000, stage="x")
        self.assertEqual(b.sent, [(32000, "low"), (64000, "low")])

    def test_low_effort_at_the_ceiling_raises_after_one_call(self):
        b = Rec([trunc(64000)], effort="low")
        with self.assertRaises(LLMBadOutput):
            b.complete_json("s", "u", max_tokens=64000, stage="x")
        self.assertEqual(len(b.sent), 1)

    def test_no_effort_backend_doubles(self):
        b = FakeBackend({"x": [trunc(16000), {"a": 1}]})
        b.complete_json("s", "u", max_tokens=16000, stage="x")
        self.assertEqual(b.calls, 2)
        self.assertTrue(any("лимитом 32000 вместо 16000" in n for n in b.notes))

    def test_explicit_limit_disables_the_retry(self):
        b = Rec([trunc(64000), {"a": 1}], effort="high")
        b.max_tokens_override = 64000
        with self.assertRaises(LLMBadOutput):
            b.complete_json("s", "u", max_tokens=64000, stage="x")
        self.assertEqual(len(b.sent), 1)

    def test_retry_answer_cached_under_the_requested_effort(self):
        with tempfile.TemporaryDirectory() as d:
            b1 = Rec([trunc(64000), {"a": 1}], effort="high", cache_dir=d)
            b1.complete_json("s", "u", max_tokens=64000, stage="x")
            self.assertEqual(len(b1.sent), 2)
            b2 = Rec([{"a": 2}], effort="high", cache_dir=d)  # the next identical run
            data, usage = b2.complete_json("s", "u", max_tokens=64000, stage="x")
            self.assertEqual(data, {"a": 1})
            self.assertTrue(usage.from_cache)
            self.assertEqual(b2.sent, [])


if __name__ == "__main__":
    unittest.main()
