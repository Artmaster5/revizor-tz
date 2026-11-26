"""Speed: the same result with requests in flight side by side, and the seconds of every stage written down.

Test doubles with artificial delays stand in for the model; nothing here touches the network."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from revizor import estimate, pipeline, report_json
from revizor.llm import FakeBackend, LLMBadOutput, make_backend
from revizor.llm.pricing import cost_usd

from tests import test_pipeline as tp
from tests.test_wave2 import DOC_CL, checklist_answer


def setUpModule() -> None:
    tp.setUpModule()


def tearDownModule() -> None:
    tp.tearDownModule()


def slow_script(delays: dict[str, float], gauge: dict | None = None, **over):
    """The standard test script, but every request first waits ``delays[stage]`` seconds. ``gauge`` (optional) keeps
    the highest number of requests that were in flight at once, per stage group and overall."""
    inner = tp.make_script(**over)
    lock = threading.Lock()
    live: dict[str, int] = {}

    def script(stage, system, user):
        if gauge is not None:
            with lock:
                live[stage] = live.get(stage, 0) + 1
                gauge["peak"] = max(gauge.get("peak", 0), sum(live.values()))
                gauge.setdefault("by_stage", {})
                gauge["by_stage"][stage] = max(gauge["by_stage"].get(stage, 0), live[stage])
        try:
            time.sleep(delays.get(stage, 0.0))
            return inner(stage, system, user)
        finally:
            if gauge is not None:
                with lock:
                    live[stage] -= 1
    return script


def shape(res) -> dict:
    """Everything of a result that must not depend on the speed: the report data without the run's times, and the
    audit without the per-request lines (their order is the order of arrival, by nature)."""
    data = json.loads(report_json.render(res))
    data["run"] = {k: v for k, v in data["run"].items()
                   if k not in ("duration_s", "stage_seconds", "stage_usage", "started_at")}
    if isinstance(data.get("passport"), dict):
        data["passport"].pop("duration_s", None)
    data["audit"] = [a for a in data.get("audit", []) if a.get("type") not in ("model_call", "stage_usage")]
    return data


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._old = os.environ.get("REVIZOR_NO_DOTENV")
        os.environ["REVIZOR_NO_DOTENV"] = "1"
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("REVIZOR_NO_DOTENV", None)
        else:
            os.environ["REVIZOR_NO_DOTENV"] = self._old
        self.tmp.cleanup()

    def doc(self, text: str = tp.DOC) -> str:
        p = self.dir / "doc.md"
        p.write_text(text, encoding="utf-8")
        return str(p)

    def run_with(self, workers: int, delays: dict[str, float], text: str = tp.DOC, gauge: dict | None = None,
                 lines: list | None = None, **over):
        b = FakeBackend(slow_script(delays, gauge, **over), concurrency=workers)
        t0 = time.monotonic()
        res = pipeline.run(self.doc(text), backend=b, depth="full", progress=(lines.append if lines is not None else None))
        return res, b, time.monotonic() - t0


class Determinism(_Base):
    # The long requests are the ones that would finish last in a sequential run: with workers they finish first.
    DELAYS = {"review_clarity": 0.05, "review_completeness": 0.02, "review_correctness": 0.01, "set_consistency": 0.01,
              "set_completeness": 0.03, "set_standards": 0.0, "skeptic": 0.02, "tests": 0.05, "checklist": 0.02}

    def test_same_report_with_one_and_many_workers(self):
        over = dict(checklist=checklist_answer)
        a, _, _ = self.run_with(1, self.DELAYS, text=DOC_CL, **over)
        b, _, _ = self.run_with(8, self.DELAYS, text=DOC_CL, **over)
        self.assertEqual([(f.fid, f.severity, f.title) for f in a.findings],
                         [(f.fid, f.severity, f.title) for f in b.findings])
        self.assertEqual(shape(a), shape(b))
        self.assertTrue(a.findings)

    def test_numbering_follows_job_order_not_arrival(self):
        slow_first = dict(self.DELAYS, review_clarity=0.12, set_consistency=0.1)
        slow_last = dict(self.DELAYS, review_correctness=0.12, set_completeness=0.1)
        a, _, _ = self.run_with(6, slow_first)
        b, _, _ = self.run_with(6, slow_last)
        self.assertEqual([(f.fid, f.title) for f in a.findings], [(f.fid, f.title) for f in b.findings])

    def test_checklist_beside_answers_keeps_audit_and_warnings(self):
        for over in (dict(checklist=checklist_answer), dict(checklist=LLMBadOutput("нет ответа"))):
            a, _, _ = self.run_with(1, self.DELAYS, text=DOC_CL, **over)
            b, _, _ = self.run_with(6, self.DELAYS, text=DOC_CL, **over)
            self.assertEqual(a.run.warnings, b.run.warnings)
            self.assertEqual([i.verdict for i in a.checklist], [i.verdict for i in b.checklist])
            # the checklist's own entries sit after the tests' entries, as in a sequential run
            kinds = lambda r: [x.get("what") or x.get("type") for x in r.audit if x.get("type") != "model_call"]
            self.assertEqual(kinds(a), kinds(b))

    def test_dedup_chunks_side_by_side_same_groups(self):
        def groups(user):
            ids = re.findall(r"^(D-\d{3}) \|", user, re.M)
            return {"groups": [{"fids": ids[:2], "keep": ids[0], "reason": "повтор"}]} if len(ids) >= 2 else {"groups": []}
        gauge: dict = {}
        with mock.patch.object(pipeline, "DEDUP_CHUNK", 2):
            a, _, _ = self.run_with(1, self.DELAYS, dedup=groups)
            b, _, _ = self.run_with(6, dict(self.DELAYS, dedup=0.05), gauge=gauge, dedup=groups)
        self.assertEqual([(f.fid, f.title) for f in a.findings], [(f.fid, f.title) for f in b.findings])
        self.assertEqual(shape(a)["run"], shape(b)["run"])
        if a.run.skeptic_dedup or b.run.skeptic_dedup:
            self.assertEqual(a.run.skeptic_dedup, b.run.skeptic_dedup)


class Concurrency(_Base):
    def test_review_and_set_requests_run_together(self):
        gauge: dict = {}
        delays = {s: 0.05 for s in ("review_clarity", "review_completeness", "review_correctness", "set_consistency",
                                    "set_completeness", "set_standards")}
        res, _, _ = self.run_with(6, delays, gauge=gauge)
        self.assertGreaterEqual(gauge["peak"], 4)
        self.assertLessEqual(gauge["peak"], 6)
        self.assertIsNone(res.diagnostic)

    def test_wall_time_shrinks(self):
        delays = {s: 0.15 for s in ("review_clarity", "review_completeness", "review_correctness", "set_consistency",
                                    "set_completeness", "set_standards")}
        _, _, one = self.run_with(1, delays)
        _, _, many = self.run_with(6, delays)
        self.assertLess(many, one * 0.6)

    def test_checklist_request_goes_beside_the_tests(self):
        delays = {"tests": 0.4, "checklist": 0.4}
        _, _, many = self.run_with(6, delays, text=DOC_CL, checklist=checklist_answer)
        _, _, one = self.run_with(1, delays, text=DOC_CL, checklist=checklist_answer)
        self.assertLess(many, one - 0.3)

    def test_sequential_when_one_worker(self):
        gauge: dict = {}
        self.run_with(1, {"review_clarity": 0.01, "set_consistency": 0.01}, gauge=gauge, text=DOC_CL,
                      checklist=checklist_answer)
        self.assertEqual(gauge["peak"], 1)

    def test_cost_cap_holds_with_workers(self):
        """No request STARTS after the cap is reached; the ones already in flight may finish."""
        script = tp.make_script()
        b = tp.PricedFake(lambda s, sy, u: (time.sleep(0.03), script(s, sy, u))[1], model="claude-opus-5-5",
                          concurrency=4)
        gate = pipeline.Gate(b, "система " * 200, None, [])
        # price of one request, then a cap of about two and a half of them
        gate.call("slice", "ЗАДАНИЕ\nБЛОКИ:\n")
        one = gate.cost()
        self.assertTrue(one and one > 0)
        gate.max_cost = one * 2.5
        started = len(b.log)

        def go(_):
            try:
                gate.call("slice", "ЗАДАНИЕ\nБЛОКИ:\n")
                return True
            except pipeline.StageSkipped:
                return False
        with ThreadPoolExecutor(max_workers=4) as ex:
            ok = list(ex.map(go, range(24)))
        sent = len(b.log) - started
        self.assertLess(sent, 24)
        self.assertLessEqual(sent, 3 + 3)  # about two and a half before the cap, plus at most 3 already in flight
        self.assertEqual(sum(ok), sent)
        self.assertTrue(gate.budget_hit)
        self.assertFalse(gate.can_call())

    def test_full_run_with_workers_stops_at_the_cap(self):
        b = tp.PricedFake(tp.make_script(), model="claude-opus-5-5", concurrency=6)
        res = pipeline.run(self.doc(), backend=b, depth="full", max_cost_usd=0.000001)
        self.assertEqual([s for s, _, _ in b.log], ["slice"])
        self.assertTrue(any("лимит стоимости" in w for w in res.run.warnings))

    def test_default_workers_by_provider(self):
        from revizor.llm import DEFAULT_CONCURRENCY
        self.assertEqual(DEFAULT_CONCURRENCY, {"anthropic": 6, "openai": 4, "gemini": 1, "ollama": 1})
        b = make_backend("anthropic", {"ANTHROPIC_API_KEY": "sk-ant-test-key-1234567890"}, cache_dir=str(self.dir))
        self.assertEqual(b.concurrency, 6)
        b = make_backend("anthropic", {"ANTHROPIC_API_KEY": "sk-ant-test-key-1234567890", "LLM_CONCURRENCY": "3"},
                         cache_dir=str(self.dir))
        self.assertEqual(b.concurrency, 3)
        b = make_backend("gemini", {"GEMINI_API_KEY": "AIzaTestKey1234567890"}, cache_dir=str(self.dir))
        self.assertEqual(b.concurrency, 1)


class StageSeconds(_Base):
    def test_nine_numbers_in_the_run_info_and_json(self):
        res, _, wall = self.run_with(4, {"set_completeness": 0.1, "skeptic": 0.05})
        sec = res.run.stage_seconds
        self.assertEqual(len(sec), 9)
        self.assertTrue(all(isinstance(x, float) and x >= 0 for x in sec))
        self.assertGreaterEqual(sec[4], 0.09)  # stage 5 held by its slowest request
        self.assertGreaterEqual(sec[6], 0.04)  # stage 7: the re-check
        self.assertLessEqual(max(sec), wall + 0.5)
        data = json.loads(report_json.render(res))
        self.assertEqual(data["run"]["stage_seconds"], sec)
        back = report_json.from_data(data)
        self.assertEqual(back.run.stage_seconds, sec)

    def test_stage_lines_end_with_seconds_and_keep_the_prefix(self):
        lines: list[str] = []
        res, _, _ = self.run_with(4, {"set_completeness": 0.05}, lines=lines)
        stage = [ln for ln in lines if re.match(r"^\[\d/9\] ", ln)]
        self.assertEqual([ln[:5] for ln in stage], [f"[{n}/9]" for n in range(1, 10)])
        for ln, sec in zip(stage, res.run.stage_seconds):
            self.assertRegex(ln, r" · \d+ с$")
            self.assertEqual(int(re.search(r" · (\d+) с$", ln).group(1)), int(round(sec)))

    def test_stages_four_and_five_have_their_own_spans(self):
        res, _, _ = self.run_with(6, {"review_clarity": 0.05, "set_completeness": 0.25})
        sec = res.run.stage_seconds
        self.assertGreaterEqual(sec[4], 0.24)
        self.assertLess(sec[3], sec[4])

    def test_skipped_stage_has_no_seconds_text_and_offline_works(self):
        lines: list[str] = []
        res = pipeline.run(self.doc(), backend=None, progress=lines.append)
        self.assertEqual(len(res.run.stage_seconds), 9)
        skipped = [ln for ln in lines if "… пропущено" in ln]
        self.assertEqual(len(skipped), 3)
        for ln in skipped:
            self.assertNotRegex(ln, r" с$")
        done = [ln for ln in lines if ln.startswith(("[1/9]", "[2/9]", "[3/9]", "[6/9]", "[8/9]", "[9/9]"))]
        for ln in done:
            self.assertRegex(ln, r" · \d+ с$")

    def test_step_lines_of_the_window_still_parse(self):
        from revizor import webui
        lines: list[str] = []
        self.run_with(2, {}, lines=lines)
        for ln in lines:
            m = webui._STAGE_LINE.match(ln)
            if m:
                self.assertTrue(1 <= int(m.group(1)) <= 9)

    def test_early_failure_leaves_no_numbers(self):
        res = pipeline.run(str(self.dir / "нет.md"))
        self.assertEqual(res.run.stage_seconds, [])


class Estimate(_Base):
    def test_more_workers_never_slower_and_checklist_is_beside_tests(self):
        est = estimate.build(self.doc(DOC_CL), depth="full")
        for provider in ("anthropic", "openai"):
            one = estimate.expected_seconds(est, provider, {"LLM_CONCURRENCY": "1"})
            four = estimate.expected_seconds(est, provider, {"LLM_CONCURRENCY": "4"})
            eight = estimate.expected_seconds(est, provider, {"LLM_CONCURRENCY": "8"})
            self.assertGreater(one, four)
            self.assertGreaterEqual(four, eight)

    def test_default_workers_per_provider(self):
        est = estimate.build(self.doc(DOC_CL), depth="full")
        self.assertEqual(estimate.expected_seconds(est, "anthropic", {}),
                         estimate.expected_seconds(est, "anthropic", {"LLM_CONCURRENCY": "6"}))
        self.assertEqual(estimate.expected_seconds(est, "gemini", {}),
                         estimate.expected_seconds(est, "gemini", {"LLM_CONCURRENCY": "1"}))
        self.assertEqual(estimate.expected_seconds(est, "openai", {}),
                         estimate.expected_seconds(est, "openai", {"LLM_CONCURRENCY": "4"}))

    def test_range_is_printed(self):
        est = estimate.build(self.doc(DOC_CL), depth="full")
        text = "\n".join(estimate.report(est, provider="anthropic", model="claude-haiku-5-5", depth="full"))
        self.assertRegex(text, r"Время: ≈ \d+ – \d+ мин")


if __name__ == "__main__":
    unittest.main()
