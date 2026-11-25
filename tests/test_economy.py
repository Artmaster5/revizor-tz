"""Economy mode (scarce requests, the free Gemini tier): value order, slicing without the model, one repeat of
the steps that got no answer, and the shared waiting budget. No network, no real sleeping."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from revizor import pipeline, report_md
from revizor.llm import FakeBackend, LLMBadOutput, LLMTransient, economy_setting, make_backend
from revizor.llm.gemini_backend import OVERLOAD_WAIT_S
from revizor.prompts import GAPS_STAGE, REVIEW_LENSES, SET_STAGES

from tests import test_pipeline as tp
from tests.test_gemini_free import KEY, MAIN, REQ_DOC, SPARE, Stub, fake_model, fx, make, ok

ANT_KEY = "sk-ant-" + "TESTnotREAL0123456789"


def transient(msg: str = "Gemini: перегружена (503); шаг пропущен.") -> LLMTransient:
    e = LLMTransient(msg, kind="server")
    e.final = True  # as the Gemini backend does: no extra retries in the base loop
    return e


def flaky(fail: dict[str, int], always: tuple[str, ...] = (), bad: tuple[str, ...] = ()):
    """Fake script: ``fail[stage]`` first calls of a stage fail with a transient error, ``always`` stages always
    do, ``bad`` stages return a bad answer; everything else answers like the pipeline tests' fake model."""
    base = tp.make_script()
    seen: dict[str, int] = {}

    def script(stage, system, user):
        seen[stage] = seen.get(stage, 0) + 1
        if stage in always or seen[stage] <= fail.get(stage, 0):
            raise transient()
        if stage in bad:
            raise LLMBadOutput("ответ не по схеме")
        if stage == "tests":  # not scripted in the pipeline tests' fake: an empty, valid answer
            return {"tests": []}
        return base(stage, system, user)
    return script


def eco_fake(script, economy=True) -> FakeBackend:
    b = FakeBackend(script, concurrency=1, max_attempts=1)
    b.economy = economy
    return b


def stages(b: FakeBackend) -> list[str]:
    return [s for s, _sys, _u in b.log]


class _Run(tp._Base):
    def run_b(self, b, depth="full"):
        lines: list[str] = []
        res = pipeline.run(self.doc(), backend=b, depth=depth, progress=lines.append)
        return res, lines


class Order(unittest.TestCase):
    def jobs(self, fast=False):
        lenses = [None] if fast else list(REVIEW_LENSES)
        jobs = [("review", [b], lens) for b in ("b1", "b2") for lens in lenses]
        jobs += [("set", None, st) for st in SET_STAGES]
        jobs.append(("gaps", None, ["item"]))
        return jobs

    def test_economy_off_keeps_the_order(self):
        for fast in (False, True):
            jobs = self.jobs(fast)
            self.assertEqual(pipeline.order_jobs(jobs, False), jobs)
            self.assertEqual(pipeline.job_order(jobs, False), list(range(len(jobs))))

    def test_economy_on_most_valuable_first(self):
        got = pipeline.order_jobs(self.jobs(), True)
        self.assertEqual([pipeline.job_stage(j) for j in got],
                         ["set_consistency", GAPS_STAGE, "review_correctness", "review_correctness",
                          "review_completeness", "review_completeness", "review_clarity", "review_clarity",
                          "set_completeness", "set_standards"])
        self.assertEqual([j[1] for j in got[2:4]], [["b1"], ["b2"]])  # all batches of a lens together, in order
        fast = pipeline.order_jobs(self.jobs(fast=True), True)
        self.assertEqual([pipeline.job_stage(j) for j in fast],
                         ["set_consistency", GAPS_STAGE, "review", "review", "set_completeness", "set_standards"])


class Slicing(_Run):
    def test_no_slicing_request_in_economy_mode(self):
        b = eco_fake(tp.make_script())
        res, lines = self.run_b(b)
        self.assertNotIn("slice", stages(b))
        self.assertIn("Нарезка на требования выполнена без модели: включён режим экономии запросов (LLM_ECONOMY).",
                      res.run.warnings)
        self.assertTrue(any("эвристика, без запроса к модели" in x for x in lines))
        self.assertTrue(res.requirements)
        self.assertEqual(res.run.mode, "full")

    def test_slicing_requested_without_economy(self):
        b = eco_fake(tp.make_script(), economy=False)
        res, _lines = self.run_b(b)
        self.assertEqual(stages(b)[0], "slice")
        self.assertFalse(any("Нарезка на требования выполнена без модели" in w for w in res.run.warnings))

    def test_send_order_in_a_run(self):
        b = eco_fake(tp.make_script())
        self.run_b(b)
        first = [s for s in stages(b) if s.startswith(("review", "set_"))]
        self.assertEqual(first[0], "set_consistency")
        self.assertEqual(first[-2:], ["set_completeness", "set_standards"])
        b2 = eco_fake(tp.make_script(), economy=False)
        self.run_b(b2)
        order = [s for s in stages(b2) if s.startswith(("review", "set_"))]
        self.assertEqual(order[:len(REVIEW_LENSES)], [lens.stage for lens in REVIEW_LENSES])  # as before

    def test_dead_provider_without_slicing_falls_back_to_rules(self):
        from revizor.llm import LLMUnavailable
        b = eco_fake(lambda st, s, u: (_ for _ in ()).throw(LLMUnavailable("Ключ не принят.")))
        res, _lines = self.run_b(b)
        self.assertEqual(res.run.mode, "offline")
        self.assertTrue(any(w.startswith("Модель недоступна: Ключ не принят.") for w in res.run.warnings))
        self.assertEqual(len(b.log), 1)


class SecondRound(_Run):
    def test_transient_failures_are_repeated_and_answered(self):
        b = eco_fake(flaky({"set_consistency": 1, "review_correctness": 1}))
        res, lines = self.run_b(b)
        st = stages(b)
        self.assertEqual(st.count("set_consistency"), 2)
        self.assertEqual(st.count("review_correctness"), 2)
        self.assertEqual(st.count("set_standards"), 1)
        self.assertIn("      Повторяю шаги, на которые модель не ответила: 2", lines)
        # the repeat runs before the pipeline moves on (skeptic comes after it)
        self.assertLess(len(st) - 1 - st[::-1].index("review_correctness"), st.index("skeptic"))
        retried = [a for a in res.audit if a.get("type") == "model_call" and a.get("retried")]
        self.assertEqual(sorted(a["stage"] for a in retried), ["review_correctness", "set_consistency"])
        self.assertEqual(sum(1 for a in res.audit if a.get("type") == "model_call" and a.get("ok") is False), 2)
        self.assertEqual(report_md.failed_steps_line(res), "")
        self.assertFalse(any("не выполнена" in w or "не проверены моделью" in w for w in res.run.warnings))

    def test_step_failing_in_both_rounds_is_listed_once(self):
        b = eco_fake(flaky({}, always=("set_standards",)))
        res, lines = self.run_b(b)
        self.assertEqual(stages(b).count("set_standards"), 2)
        self.assertIn("      Повторяю шаги, на которые модель не ответила: 1", lines)
        self.assertEqual(sum(1 for w in res.run.warnings if "Проверка набора" in w and "не выполнена" in w), 1)
        calls = [a for a in res.audit if a.get("type") == "model_call"]
        line = report_md.failed_steps_line(res)
        self.assertIn("Модель не ответила на 1 шаг из %d." % (len(calls) - 1), line)

    def test_bad_answers_are_not_repeated(self):
        b = eco_fake(flaky({}, bad=("set_standards",)))
        _res, lines = self.run_b(b)
        self.assertEqual(stages(b).count("set_standards"), 1)
        self.assertFalse(any("Повторяю шаги" in x for x in lines))

    def test_no_second_round_without_economy(self):
        b = eco_fake(flaky({"set_consistency": 1}), economy=False)
        res, lines = self.run_b(b)
        self.assertEqual(stages(b).count("set_consistency"), 1)
        self.assertFalse(any("Повторяю шаги" in x for x in lines))
        self.assertIn("не ответила на 1 шаг из", report_md.failed_steps_line(res))

    def test_no_second_round_when_the_backend_cannot_answer(self):
        b = eco_fake(flaky({"set_consistency": 1}))
        b.can_answer = lambda: False
        _res, lines = self.run_b(b)
        self.assertEqual(stages(b).count("set_consistency"), 1)
        self.assertFalse(any("Повторяю шаги" in x for x in lines))

    def test_parallel_backend_with_economy_forced_on(self):
        b = FakeBackend(flaky({"set_consistency": 1, "review_clarity": 1}), concurrency=4, max_attempts=1)
        b.economy = True  # LLM_ECONOMY=on for a provider that sends requests in parallel
        res, lines = self.run_b(b)
        self.assertEqual(stages(b).count("set_consistency"), 2)
        self.assertIn("      Повторяю шаги, на которые модель не ответила: 2", lines)
        self.assertEqual(report_md.failed_steps_line(res), "")

    def test_rounds_are_announced_to_the_backend(self):
        b = eco_fake(flaky({"set_consistency": 1}))
        seen: list[int] = []
        b.set_round = seen.append
        self.run_b(b)
        self.assertEqual(seen, [1, 2, 0])


class Waiting(unittest.TestCase):
    def test_default_budget(self):
        self.assertEqual(OVERLOAD_WAIT_S, 600.0)

    def test_first_round_call_cannot_take_the_whole_budget(self):
        b = make(Stub({"*": ["503_high_demand"]}), wait=60)
        b.set_round(1)
        with self.assertRaises(LLMTransient):
            b.complete_json("s", "u")
        self.assertLessEqual(sum(b.sleeps), 10.0)  # 1/6 of 60 s
        self.assertEqual(b.sleeps, [2.0, 2.0])
        self.assertGreaterEqual(b.overload_wait_s - b.overload_waited, 50.0)
        b.set_round(2)  # the repeat may use what is left
        with self.assertRaises(LLMTransient) as cm:
            b.complete_json("s", "u2")
        self.assertGreater(sum(b.sleeps), 40.0)
        self.assertLessEqual(b.overload_waited, 60.0)
        self.assertIn("перегрузка на стороне Google, не ошибка ключа", cm.exception.user_message)

    def test_shares_shrink_but_never_starve(self):
        b = make(Stub({"*": ["503_high_demand"]}), wait=600)
        b.set_round(1)
        for i in range(12):
            with self.assertRaises(LLMTransient):
                b.complete_json("s", "u%d" % i)
        self.assertGreater(b.overload_wait_s - b.overload_waited, 100.0)

    def test_outside_rounds_the_old_sequence(self):
        b = make(Stub({"*": ["503_high_demand"]}))
        with self.assertRaises(LLMTransient):
            b.complete_json("s", "u")
        self.assertEqual(b.sleeps, [2.0, 2.0, 8.0, 25.0])

    def test_repeat_round_waits_longer(self):
        b = make(Stub({"*": ["503_high_demand"]}))
        b.set_round(2)
        with self.assertRaises(LLMTransient):
            b.complete_json("s", "u")
        self.assertEqual(b.sleeps, [2.0, 2.0, 8.0, 25.0, 45.0, 60.0])

    def test_zero_means_no_waiting(self):
        b = make(Stub({MAIN: ["503_high_demand"], SPARE: ["ok_thought"]}), wait=0)
        b.set_round(1)
        b.complete_json("s", "u")
        self.assertEqual(b.sleeps, [])

    def test_wait_status_is_visible_while_sleeping(self):
        b = make(Stub({MAIN: ["503_high_demand"], SPARE: ["503_high_demand", "ok_thought"]}))
        seen: list[str] = []
        b.sleep = lambda s: seen.append(b.wait_status)
        b.complete_json("s", "u")
        self.assertEqual(seen[0], "Gemini перегружен (503): жду 2 с, попытка 2")
        self.assertEqual(b.wait_status, "")

    def test_heartbeat_shows_the_wait(self):
        b = make(Stub({"*": ["ok_thought"]}))
        gate = pipeline.Gate(b, "s", None, [], heartbeat_s=15.0)
        now = time.monotonic()
        gate.in_flight[1] = ("set_consistency", now - 40)
        gate.last_print = now - 16
        self.assertIn("жду ответа модели: проверка набора", gate.idle_line(now))
        b.wait_status = "Gemini перегружен (503): жду 25 с, попытка 3"
        self.assertEqual(gate.idle_line(now), "  … Gemini перегружен (503): жду 25 с, попытка 3 — проверка набора, "
                                              "шаг идёт 40 с")
        gate.last_print = now - 5
        self.assertEqual(gate.idle_line(now), "")  # not more often than the heartbeat


class Setting(unittest.TestCase):
    def test_parsing(self):
        for v, want in (("on", True), ("ON", True), ("1", True), ("off", False), ("0", False), ("auto", None),
                        ("", None), ("что-то", None)):
            self.assertEqual(economy_setting({"LLM_ECONOMY": v}), want, v)
        self.assertIsNone(economy_setting({}))

    def test_defaults_and_overrides(self):
        def mk(p, **env):
            env.update({"GEMINI_API_KEY": KEY} if p == "gemini" else {"ANTHROPIC_API_KEY": ANT_KEY} if p == "anthropic"
                       else {"OPENAI_API_KEY": "sk-" + "TESTnotREAL0123456789abcd"})
            return make_backend(p, env, cache_dir=tempfile.mkdtemp())
        self.assertTrue(mk("gemini").economy)
        self.assertTrue(mk("gemini", LLM_ECONOMY="auto").economy)
        self.assertFalse(mk("gemini", LLM_ECONOMY="off").economy)
        self.assertFalse(mk("anthropic").economy)
        self.assertFalse(mk("openai").economy)
        self.assertTrue(mk("anthropic", LLM_ECONOMY="on").economy)
        self.assertTrue(mk("openai", LLM_ECONOMY="on").economy)
        self.assertFalse(FakeBackend({}).economy)


class EndToEnd(tp._Base):
    def test_overloaded_steps_answered_in_the_second_round(self):
        """Gemini stub: every attempt of the first call of three valuable steps answers 503, later ones succeed."""
        ref: list = []
        model = fake_model(ref)
        busy = {"set_consistency", "review_correctness", GAPS_STAGE}
        first_call: dict[str, int] = {}

        def route(body):
            st = ref[0].current_stage
            if st in busy and ref[0].round == 1:
                first_call[st] = first_call.get(st, 0) + 1
                return fx("503_high_demand")
            if st == "tests":
                return ok('{"tests": []}')
            return model(body)
        stub = Stub({"*": [route]})
        b = make(stub)
        ref.append(b)
        lines: list[str] = []
        res = pipeline.run(self.doc(), backend=b, depth="full", progress=lines.append)
        self.assertEqual(set(first_call), busy & {a["stage"] for a in res.audit if a.get("type") == "model_call"})
        self.assertTrue(all(n == 8 for n in first_call.values()), first_call)  # the whole per-call plan
        self.assertTrue(any(x.startswith("      Повторяю шаги, на которые модель не ответила: ") for x in lines))
        self.assertEqual(report_md.failed_steps_line(res), "")
        self.assertFalse(any("не выполнен" in w or "не проверены моделью" in w for w in res.run.warnings))
        self.assertTrue(any(f.source in ("model", "both") for f in res.findings))
        self.assertIn("Нарезка на требования выполнена без модели: бесплатный уровень Gemini считает запросы, "
                      "а они нужнее для проверки.", res.run.warnings)
        self.assertNotIn("slice", [a["stage"] for a in res.audit if a.get("type") == "model_call"])
        self.assertLessEqual(b.overload_waited, OVERLOAD_WAIT_S)
        self.assertNotIn("Модель не ответила на", report_md.render(res))

    def test_request_count_in_economy_mode(self):
        """Every answer at once on the organisers' document: no slicing request."""
        counts = {}
        for depth in ("full", "fast"):
            ref: list = []
            stub = Stub({MAIN: [fake_model(ref)]})
            b = make(stub)
            ref.append(b)
            res = pipeline.run(REQ_DOC, backend=b, depth=depth)
            self.assertGreater(len(res.requirements), 40)
            self.assertNotIn("slice", [a["stage"] for a in res.audit if a.get("type") == "model_call"])
            counts[depth] = len(stub.posts())
        # before economy mode: 16 and 10 (one slicing request more); measured 26.11.2026: 15 and 9
        self.assertLessEqual(counts["full"], 15)
        self.assertLessEqual(counts["fast"], 9)


if __name__ == "__main__":
    unittest.main()
