"""Per-stage effort, effort-aware cache key, per-stage usage, truncation retry (no network, no key)."""
from __future__ import annotations

import tempfile
import threading
import unittest

try:
    import anthropic
    import httpx2
except ImportError:  # pragma: no cover
    anthropic = None

from revizor.llm import FakeBackend, LLMBadOutput, Usage, make_backend, stage_usage_rows
from revizor.llm.base import STAGE_EFFORT, Backend, parse_stage_overrides
from revizor.llm.cache import DiskCache
from tests.test_llm_anthropic import KEY, Recorder, ok

NOWHERE = "/nonexistent-never-written"


def efforts(rec: Recorder) -> list:
    return [(r["body"].get("output_config") or {}).get("effort") for r in rec.requests]


@unittest.skipIf(anthropic is None, "anthropic SDK not installed")
class StageEffortTests(unittest.TestCase):
    def make(self, rec, env=None):
        e = {"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_MODEL": "claude-haiku-5-5"}
        e.update(env or {})
        b = make_backend("anthropic", e, cache_dir=NOWHERE, use_cache=False)
        b.cache = None
        b.client = anthropic.Anthropic(api_key=KEY, max_retries=0,
                                       http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(rec)))
        b.sleep = lambda _s: None
        return b

    def run_stages(self, b, rec, stages):
        for st in stages:
            b.complete_json("s", "u", stage=st)
        return efforts(rec)

    def test_map_applied_per_stage(self):
        rec = Recorder(ok())
        b = self.make(rec)
        stages = ["slice", "slice_rest", "review_clarity", "set_consistency", "set_standards", "questions",
                  "skeptic", "something_new"]
        got = self.run_stages(b, rec, stages)
        self.assertEqual(got, ["low", "low", "low", "high", "medium", "low", "medium", "high"])
        self.assertEqual(got[:7], [STAGE_EFFORT[s] for s in stages[:7]])
        self.assertTrue(any("по этапам" in n for n in b.notes))
        self.assertEqual(b.effort_label, "по этапам")

    def test_locked_by_env(self):
        rec = Recorder(ok())
        b = self.make(rec, {"ANTHROPIC_EFFORT": "max"})
        self.assertEqual(self.run_stages(b, rec, ["slice", "set_consistency", "review"]), ["max"] * 3)
        self.assertFalse(any("по этапам" in n for n in b.notes))
        self.assertEqual(b.effort_label, "max")

    def test_empty_env_means_map(self):
        rec = Recorder(ok())
        b = self.make(rec, {"ANTHROPIC_EFFORT": "  "})
        self.assertEqual(self.run_stages(b, rec, ["slice"]), ["low"])

    def test_stage_override_env_exact_and_family(self):
        rec = Recorder(ok())
        b = self.make(rec, {"ANTHROPIC_EFFORT_STAGES": "review=low, set_consistency=max,slice=high"})
        got = self.run_stages(b, rec, ["review_clarity", "review", "set_consistency", "set_standards", "slice_rest"])
        self.assertEqual(got, ["low", "low", "max", "medium", "high"])

    def test_bad_override_ignored_with_note(self):
        rec = Recorder(ok())
        b = self.make(rec, {"ANTHROPIC_EFFORT_STAGES": "review=turbo,junk,slice=low"})
        self.assertEqual(self.run_stages(b, rec, ["review", "set_completeness"]), ["low", "medium"])
        self.assertTrue(any("ANTHROPIC_EFFORT_STAGES" in n for n in b.notes))

    def test_parse_overrides(self):
        self.assertEqual(parse_stage_overrides("a=low;b=HIGH"), ({"a": "low", "b": "high"}, []))
        self.assertEqual(parse_stage_overrides("a=x")[1], ["a=x"])

    def test_rejected_effort_is_dropped_with_note(self):
        from tests.test_llm_anthropic import err

        rec = Recorder(err(400, "invalid_request_error", "effort is not supported for this model"), ok())
        b = self.make(rec)
        b.complete_json("s", "u", stage="slice")
        self.assertEqual(efforts(rec), ["low", None])
        self.assertTrue(any("effort" in n for n in b.notes))

    def test_truncation_retries_once_with_doubled_limit(self):
        rec = Recorder(ok('{"a": [', stop_reason="max_tokens"), ok('{"a": 1}'))
        b = self.make(rec)
        data, usage = b.complete_json("s", "u", stage="review")
        self.assertEqual(data, {"a": 1})
        self.assertEqual([r["body"]["max_tokens"] for r in rec.requests], [32000, 64000])
        self.assertEqual(usage.output_tokens, 84)  # both attempts counted
        self.assertEqual(b.stage_usage["review"].calls, 2)
        self.assertTrue(any("обрезан" in n for n in b.notes))

    def test_set_stage_truncation_retries_with_lower_effort_not_bigger_limit(self):
        rec = Recorder(ok('{"a": [', stop_reason="max_tokens"), ok('{"a": 1}'))
        b = self.make(rec)
        data, _ = b.complete_json("s", "u", stage="set_consistency", max_tokens=64000)
        self.assertEqual(data, {"a": 1})
        self.assertEqual([r["body"]["max_tokens"] for r in rec.requests], [64000, 64000])
        self.assertEqual(efforts(rec), ["high", "medium"])
        self.assertTrue(any("усилием medium" in n for n in b.notes))

    def test_truncation_with_effort_switched_off_doubles_the_limit(self):
        rec = Recorder(ok('{"a": [', stop_reason="max_tokens"), ok('{"a": 1}'))
        b = self.make(rec)
        b._use_effort = False  # the model refused the effort parameter earlier
        b.complete_json("s", "u", stage="set_consistency")
        self.assertEqual([r["body"]["max_tokens"] for r in rec.requests], [32000, 64000])

    def test_truncation_twice_raises_after_two_requests(self):
        rec = Recorder(ok('{"a": [', stop_reason="max_tokens"))
        b = self.make(rec)
        with self.assertRaises(LLMBadOutput):
            b.complete_json("s", "u", stage="review")
        self.assertEqual(len(rec.requests), 2)

    def test_no_retry_when_limit_forced(self):
        rec = Recorder(ok('{"a": [', stop_reason="max_tokens"))
        b = self.make(rec, {"LLM_MAX_TOKENS": "5000"})
        with self.assertRaises(LLMBadOutput):
            b.complete_json("s", "u", stage="review")
        self.assertEqual(len(rec.requests), 1)


class CacheAndUsageTests(unittest.TestCase):
    def test_cache_key_differs_by_effort(self):
        k = DiskCache.key
        self.assertNotEqual(k("anthropic", "m", "low", "s", "u"), k("anthropic", "m", "high", "s", "u"))

    def test_low_answer_not_served_for_high_request(self):
        class Eff(FakeBackend):
            def __init__(self, d):
                super().__init__({"a": {"v": 1}, "b": {"v": 2}}, cache_dir=d)
                self.effort = "high"
                self.effort_locked = False
                self.stage_effort = {"a": "low", "b": "high"}

        with tempfile.TemporaryDirectory() as d:
            b = Eff(d)
            b.complete_json("s", "u", stage="a")
            n = len(b.log)
            # the same prompt at another effort must not hit the cache entry written for "low"
            b.complete_json("s", "u", stage="b")
            self.assertEqual(len(b.log), n + 1)
            b.complete_json("s", "u", stage="a")  # same effort again -> cache
            self.assertEqual(len(b.log), n + 1)
            self.assertEqual(b.cache_hits, 1)

    def test_stage_usage_accumulates_under_threads(self):
        script = lambda stage, s, u: {"ok": 1}  # noqa: E731
        b = FakeBackend(script, concurrency=8)

        def work(stage):
            for _ in range(25):
                b.complete_json("sys", "user text", stage=stage)

        ts = [threading.Thread(target=work, args=(st,)) for st in ("review", "review", "slice", "set_consistency")]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        su = b.stage_usage
        self.assertEqual(su["review"].calls, 50)
        self.assertEqual(su["slice"].calls, 25)
        self.assertEqual(su["set_consistency"].calls, 25)
        self.assertEqual(sum(u.output_tokens for u in su.values()), b.totals.output_tokens)
        self.assertEqual(sum(u.calls for u in su.values()), b.calls)

    def test_stage_usage_rows_cost(self):
        b = FakeBackend({"x": {"ok": 1}})
        b.complete_json("s", "u", stage="x")
        rows = stage_usage_rows(b)
        self.assertEqual([r["stage"] for r in rows], ["x"])
        self.assertEqual(rows[0]["calls"], 1)
        self.assertEqual(rows[0]["cost_usd"], 0.0)
        self.assertEqual(set(rows[0]), {"stage", "calls", "input_tokens", "output_tokens", "cache_read",
                                        "cache_write", "seconds", "cost_usd"})
        b.provider, b.model = "anthropic", "claude-opus-5-5"
        b.stage_usage["x"] = Usage(output_tokens=1_000_000, calls=1)
        self.assertEqual(stage_usage_rows(b)[0]["cost_usd"], 20.0)
        b.model = "unknown-model"
        self.assertIsNone(stage_usage_rows(b)[0]["cost_usd"])

    def test_legacy_raw_signature_still_works(self):
        class Old(Backend):
            provider = "old"

            def _raw(self, system, user, max_tokens):
                return '{"a": 1}', Usage(output_tokens=3)

        data, _ = Old(model="m").complete_json("s", "u", stage="x")
        self.assertEqual(data, {"a": 1})


if __name__ == "__main__":
    unittest.main()
