"""Gemini on the free tier: recorded responses (tests/fixtures/gemini/*.json) through a stub transport.

No network, no real key. The transport replaces ``request_json`` and answers by model name in the URL.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import traceback
import unittest
from pathlib import Path

from revizor import pipeline
from revizor.llm import (LLMBadOutput, LLMRefused, LLMTransient, LLMUnavailable, make_backend,
                         review_batch_size_for)
from revizor.llm._http import HttpResult
from revizor.llm.base import redact
from revizor.llm.cache import DiskCache
from revizor.llm.gemini_backend import FALLBACK_MODEL, OVERLOAD_WAIT_S, GeminiBackend

from tests import test_pipeline as tp

AGENT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures" / "gemini"
REQ_DOC = str(AGENT / "docs" / "requirements.md")
KEY = "AQ." "Ab8RN6TESTkeyNOTreal_0123456789abcdefXYZ"  # the new AI Studio key shape, not a real key
SCHEMA = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
MAIN, SPARE = "gemini-flash-latest", "gemini-flash-lite-latest"


def fx(name: str) -> HttpResult:
    d = json.loads((FIX / (name + ".json")).read_text(encoding="utf-8"))
    body = d["body"]
    return HttpResult(d["status"], body, json.dumps(body, ensure_ascii=False), {})


def ok(text: str = '{"ok": true}', version: str = "gemini-3.5-flash-lite") -> HttpResult:
    body = {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 10}, "modelVersion": version}
    return HttpResult(200, body, json.dumps(body), {})


class Stub:
    """routes: model → list of answers (fixture names, HttpResult or callables(body)); the last one repeats."""

    def __init__(self, routes: dict, models: str = "models_list"):
        self.routes = {k: list(v) if isinstance(v, (list, tuple)) else [v] for k, v in routes.items()}
        self.models = models
        self.requests: list[dict] = []
        self.pos: dict[str, int] = {}

    def __call__(self, method, url, *, headers, body=None, timeout=0.0):
        self.requests.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if method == "GET":
            return fx(self.models)
        model = re.search(r"/models/([^:/?]+):generateContent$", url).group(1)
        queue = self.routes.get(model) or self.routes.get("*") or ["404_retired"]
        i = self.pos.get(model, 0)
        self.pos[model] = i + 1
        item = queue[min(i, len(queue) - 1)]
        if callable(item) and not isinstance(item, HttpResult):
            item = item(body)
        return fx(item) if isinstance(item, str) else item

    def posts(self, model: str | None = None) -> list[dict]:
        return [r for r in self.requests if r["method"] == "POST" and (model is None or f"/models/{model}:" in r["url"])]


def make(stub: Stub, *, model=MAIN, fallback=SPARE, wait=OVERLOAD_WAIT_S, cache=None) -> GeminiBackend:
    b = GeminiBackend(KEY, model=model, base_url="https://gemini.invalid", fallback_model=fallback,
                      overload_wait_s=wait, transport=stub, cache=cache, use_cache=cache is not None)
    b.sleeps = []
    b.sleep = b.sleeps.append
    return b


def assert_no_key(tc: unittest.TestCase, *texts) -> None:
    for t in texts:
        tc.assertNotIn(KEY, str(t))
        tc.assertNotIn("Ab8RN6TESTkeyNOTreal", str(t))


def exc_texts(e: BaseException) -> list[str]:
    return [str(e), repr(e), getattr(e, "user_message", ""), getattr(e, "detail", ""),
            "".join(traceback.format_exception(type(e), e, e.__traceback__))]


class Answers(unittest.TestCase):
    def test_ok_usage_version_thought_skipped_and_request_shape(self):
        stub = Stub({MAIN: ["ok_thought"]})
        b = make(stub)
        data, usage = b.complete_json("СИСТЕМА", "задача", schema=SCHEMA, max_tokens=16000)
        self.assertEqual(data, {"ok": True})  # the thought part «{не json» was skipped
        self.assertEqual((usage.input_tokens, usage.cache_read, usage.output_tokens), (1000, 200, 100))
        self.assertEqual(b.model_version, "gemini-3.5-flash-lite")
        self.assertEqual(b.served, {MAIN: 1})
        r = stub.requests[0]
        self.assertEqual(r["url"], "https://gemini.invalid/v1beta/models/gemini-flash-latest:generateContent")
        self.assertNotIn("?", r["url"])
        self.assertNotIn(KEY, r["url"])
        self.assertEqual(r["headers"], {"x-goog-api-key": KEY})
        body = r["body"]
        self.assertEqual(body["systemInstruction"], {"parts": [{"text": "СИСТЕМА"}]})
        self.assertEqual(body["contents"], [{"role": "user", "parts": [{"text": "задача"}]}])
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertGreaterEqual(body["generationConfig"]["maxOutputTokens"], 16000)
        self.assertLessEqual(body["generationConfig"]["maxOutputTokens"], 65536)
        self.assertEqual(b.usage_note(), "")  # one model, nothing changed: no extra passport line

    def test_max_tokens_cut_is_repeated_with_more_room(self):
        stub = Stub({MAIN: ["max_tokens"]})
        b = make(stub)
        with self.assertRaises(LLMBadOutput):
            b.complete_json("s", "u", max_tokens=16000)
        limits = [p["body"]["generationConfig"]["maxOutputTokens"] for p in stub.posts()]
        self.assertEqual(len(limits), 2)
        self.assertLess(limits[0], limits[1])
        self.assertLessEqual(limits[1], 65536)
        self.assertTrue(any("обрезан" in n for n in b.notes))

    def test_review_batch_of_40_gets_a_bigger_first_budget_and_a_repeat(self):
        stub = Stub({MAIN: ["max_tokens", "ok_thought"]})
        b = make(stub)
        data, _ = b.complete_json("s", "u", schema=SCHEMA, max_tokens=16000, stage="review_clarity")
        self.assertEqual(data, {"ok": True})
        limits = [p["body"]["generationConfig"]["maxOutputTokens"] for p in stub.posts()]
        self.assertGreater(limits[0], 32768)  # a batch of 40 gets more room than the default 32 768
        self.assertLess(limits[0], 64000)  # and still leaves room for the repeat
        self.assertGreaterEqual(limits[1], 64000)

    def test_blocked_and_empty(self):
        with self.assertRaises(LLMRefused):
            make(Stub({MAIN: ["blocked"]})).complete_json("s", "u")
        with self.assertRaises(LLMBadOutput):
            make(Stub({MAIN: ["empty_candidates"]})).complete_json("s", "u")


class Quotas(unittest.TestCase):
    def test_429_per_minute_waits_retry_delay(self):
        stub = Stub({MAIN: ["429_minute", "ok_thought"]})
        b = make(stub)
        data, _ = b.complete_json("s", "u", schema=SCHEMA)
        self.assertEqual(data, {"ok": True})
        self.assertEqual(len(b.sleeps), 1)
        self.assertGreaterEqual(b.sleeps[0], 7.0)
        self.assertLess(b.sleeps[0], 8.0)
        self.assertEqual(len(stub.posts(SPARE)), 0)  # a per-minute limit does not change the model

    def test_429_per_day_moves_to_spare_for_good(self):
        stub = Stub({MAIN: ["429_day"], SPARE: ["ok_thought"]})
        b = make(stub)
        self.assertEqual(b.complete_json("s", "u", schema=SCHEMA)[0], {"ok": True})
        self.assertEqual(b.complete_json("s", "u2", schema=SCHEMA)[0], {"ok": True})
        self.assertEqual(len(stub.posts(MAIN)), 1)  # the second call went straight to the spare model
        self.assertEqual(len(stub.posts(SPARE)), 2)
        self.assertEqual(b.model, SPARE)
        self.assertIn(MAIN, b.dead)
        self.assertEqual(b.sleeps, [])
        notes = "\n".join(b.notes)
        self.assertIn("дневная квота бесплатного уровня", notes)
        self.assertIn(SPARE, notes)
        self.assertIn("gemini-flash-lite-latest (gemini-3.5-flash-lite) — 2", b.usage_note())

    def test_429_per_day_on_both_is_unavailable(self):
        for fixture in ("429_day", "429_day_text_only"):
            stub = Stub({"*": [fixture]})
            b = make(stub)
            with self.assertRaises(LLMUnavailable) as cm:
                b.complete_json("s", "u")
            self.assertIn("Дневная квота бесплатного тарифа Gemini исчерпана", cm.exception.user_message)
            self.assertEqual(len(stub.posts()), 2)
            with self.assertRaises(LLMUnavailable):  # nothing left for the rest of the run, no request sent
                b.complete_json("s", "u2")
            self.assertEqual(len(stub.posts()), 2)

    def test_day_quota_detail_keeps_quota_name_and_value(self):
        stub = Stub({"*": ["429_day"]})
        with self.assertRaises(LLMUnavailable) as cm:
            make(stub, fallback="").complete_json("s", "u")
        d = cm.exception.detail
        self.assertLessEqual(len(d), 300)
        self.assertIn("GenerateRequestsPerDayPerProjectPerModel-FreeTier", d)
        self.assertIn("quotaValue=20", d)
        self.assertIn("HTTP 429", d)


class Overload(unittest.TestCase):
    def test_503_twice_on_main_then_spare_and_later_calls_start_on_spare(self):
        stub = Stub({MAIN: ["503_high_demand"], SPARE: ["ok_thought"]})
        b = make(stub)
        self.assertEqual(b.complete_json("s", "u", schema=SCHEMA)[0], {"ok": True})
        self.assertEqual(len(stub.posts(MAIN)), 2)
        self.assertEqual(len(stub.posts(SPARE)), 1)
        self.assertEqual(b.sleeps, [2.0])
        self.assertEqual(b.calls, 3)  # every provider request is counted
        self.assertEqual(b.complete_json("s", "u2", schema=SCHEMA)[0], {"ok": True})
        self.assertEqual(len(stub.posts(MAIN)), 2)  # straight to the spare model
        self.assertEqual(len(stub.posts(SPARE)), 2)
        self.assertEqual(b.model, SPARE)
        self.assertTrue(any("резервная модель «gemini-flash-lite-latest»" in n for n in b.notes))

    def test_preferred_model_is_the_one_that_answered_last(self):
        stub = Stub({MAIN: ["503_high_demand", "503_high_demand", "ok_thought"], SPARE: ["ok_thought", "503_overloaded"]})
        b = make(stub)
        b.complete_json("s", "u1", schema=SCHEMA)  # main 503 ×2 → spare 200
        self.assertEqual(b.model, SPARE)
        b.complete_json("s", "u2", schema=SCHEMA)  # spare 503 ×2 → main 200
        self.assertEqual(b.model, MAIN)
        self.assertEqual([p["url"].split("/models/")[1].split(":")[0] for p in stub.posts()],
                         [MAIN, MAIN, SPARE, SPARE, SPARE, MAIN])

    def test_both_busy_first_round_spare_answers_second_round(self):
        stub = Stub({MAIN: ["503_high_demand"], SPARE: ["503_high_demand", "503_overloaded", "ok_thought"]})
        b = make(stub)
        self.assertEqual(b.complete_json("s", "u", schema=SCHEMA)[0], {"ok": True})
        self.assertEqual(b.sleeps, [2.0, 2.0, 8.0])
        self.assertEqual((len(stub.posts(MAIN)), len(stub.posts(SPARE))), (2, 3))
        self.assertEqual(b.overload_waited, 12.0)

    def test_both_busy_all_rounds_fails_with_plain_message(self):
        stub = Stub({"*": ["503_high_demand"]})
        b = make(stub)
        with self.assertRaises(LLMTransient) as cm:
            b.complete_json("s", "u")
        msg = cm.exception.user_message
        self.assertIn("перегружены (503), попыток 8 на двух моделях", msg)
        self.assertIn(MAIN, msg)
        self.assertIn(SPARE, msg)
        self.assertEqual(len(stub.posts()), 8)  # the base retry loop does not repeat it again
        self.assertEqual(b.sleeps, [2.0, 2.0, 8.0, 25.0])
        self.assertLessEqual(sum(b.sleeps), 75.0)

    def test_run_ceiling_spent_then_one_attempt_per_model(self):
        stub = Stub({"*": ["503_high_demand"]})
        b = make(stub, wait=4)
        with self.assertRaises(LLMTransient):
            b.complete_json("s", "u1")
        self.assertEqual(b.sleeps, [2.0, 2.0])  # the 8 s pause did not fit into the 4 s left
        self.assertEqual(len(stub.posts()), 4)
        self.assertTrue(any("GEMINI_OVERLOAD_WAIT_S" in n for n in b.notes))
        with self.assertRaises(LLMTransient) as cm:
            b.complete_json("s", "u2")
        self.assertEqual(len(stub.posts()), 6)  # one attempt per model, fails fast
        self.assertEqual(b.sleeps, [2.0, 2.0])
        self.assertIn("попыток 2 на двух моделях", cm.exception.user_message)

    def test_no_waiting_at_all(self):
        stub = Stub({MAIN: ["503_high_demand"], SPARE: ["ok_thought"]})
        b = make(stub, wait=0)
        self.assertEqual(b.complete_json("s", "u", schema=SCHEMA)[0], {"ok": True})
        self.assertEqual(b.sleeps, [])
        self.assertEqual((len(stub.posts(MAIN)), len(stub.posts(SPARE))), (1, 1))

    def test_without_spare_one_model_three_rounds(self):
        stub = Stub({MAIN: ["503_high_demand"]})
        b = make(stub, fallback="")
        with self.assertRaises(LLMTransient) as cm:
            b.complete_json("s", "u")
        self.assertIn("попыток 4 на одной модели", cm.exception.user_message)
        self.assertEqual(len(stub.posts()), 4)

    def test_spare_missing_goes_back_to_main(self):
        stub = Stub({MAIN: ["503_high_demand", "503_high_demand", "ok_thought"], SPARE: ["404_retired"]})
        b = make(stub)
        self.assertEqual(b.complete_json("s", "u", schema=SCHEMA)[0], {"ok": True})
        self.assertIn(SPARE, b.dead)
        self.assertEqual(b.model, MAIN)


class Discovery(unittest.TestCase):
    def test_retired_then_listed_but_closed_then_second_candidate(self):
        stub = Stub({"gemini-1.5-flash": ["404_retired"], "gemini-2.5-flash": ["404_new_users"],
                     "gemini-2.0-flash": ["ok_thought"]})
        b = make(stub, model="gemini-1.5-flash")
        self.assertEqual(b.complete_json("s", "u", schema=SCHEMA)[0], {"ok": True})
        self.assertEqual(b.model, "gemini-2.0-flash")
        order = [r["url"].split("/models")[1] for r in stub.requests]
        self.assertEqual(order, ["/gemini-1.5-flash:generateContent", "?pageSize=1000",
                                 "/gemini-2.5-flash:generateContent", "/gemini-2.0-flash:generateContent"])
        get = [r for r in stub.requests if r["method"] == "GET"][0]
        self.assertNotIn(KEY, get["url"])
        self.assertEqual(get["headers"], {"x-goog-api-key": KEY})
        notes = "\n".join(b.notes)
        self.assertIn("«gemini-1.5-flash» недоступна", notes)
        self.assertIn("«gemini-2.5-flash» есть в списке, но не отвечает", notes)
        self.assertIn("gemini-2.0-flash", b.usage_note())

    def test_at_most_three_candidates_never_twice(self):
        stub = Stub({"*": ["404_new_users"]})
        b = make(stub, model="gemini-1.5-flash", fallback="")
        with self.assertRaises(LLMUnavailable) as cm:
            b.complete_json("s", "u")
        posts = [p["url"] for p in stub.posts()]
        self.assertEqual(len(posts), 1 + 2)  # the list has only two suitable candidates here
        self.assertEqual(len(set(posts)), len(posts))
        self.assertIn("GEMINI_MODEL", cm.exception.user_message)


class Refusals(unittest.TestCase):
    def check(self, fixture: str, words: str):
        stub = Stub({"*": [fixture]})
        b = make(stub)
        with self.assertRaises(LLMUnavailable) as cm:
            b.complete_json("s", "u " + KEY)
        self.assertIn(words, cm.exception.user_message)
        assert_no_key(self, *exc_texts(cm.exception), *b.notes)
        self.assertEqual(len(stub.posts()), 1)

    def test_bad_key(self):
        self.check("400_key_invalid", "aistudio.google.com")

    def test_location(self):
        self.check("400_location", "из вашего региона")

    def test_403(self):
        self.check("403_denied", "нет доступа")

    def test_too_long_skips_the_step(self):
        # Per-request input limit, not per-key: only this step is skipped (LLMBadOutput), the run goes on.
        stub = Stub({"*": ["400_too_long"]})
        with self.assertRaises(LLMBadOutput) as cm:
            make(stub).complete_json("s", "u")
        msg = cm.exception.user_message
        self.assertIn("документ слишком длинный", msg)
        self.assertIn("--depth fast", msg)
        self.assertEqual(cm.exception.kind, "too_long")
        self.assertEqual(len(stub.posts()), 1)


class KeyShape(unittest.TestCase):
    def test_new_key_shape_is_masked_everywhere(self):
        other = "AQ." "Zz9TESTanotherNOTrealKEY_abcdefghijklmn"
        self.assertNotIn(other, redact("ключ " + other + " в тексте"))
        self.assertIn("***", redact("x-goog-api-key: " + other))
        echo = HttpResult(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                          "message": "API key not valid: " + KEY}}, "", {})
        stub = Stub({"*": [echo]})
        b = make(stub)
        with self.assertRaises(LLMUnavailable) as cm:
            b.complete_json("s", "u")
        assert_no_key(self, *exc_texts(cm.exception))

    def test_any_key_prefix_is_accepted(self):
        for k in (KEY, "plain-test-value-123456"):
            b = make_backend("gemini", {"GEMINI_API_KEY": k}, cache_dir=tempfile.mkdtemp())
            self.assertEqual(b.provider, "gemini")


class Settings(unittest.TestCase):
    def mk(self, **env):
        env.setdefault("GEMINI_API_KEY", KEY)
        return make_backend("gemini", env, cache_dir=tempfile.mkdtemp())

    def test_fallback_setting(self):
        self.assertEqual(self.mk().fallback_model, FALLBACK_MODEL)
        self.assertEqual(self.mk(GEMINI_FALLBACK_MODEL="").fallback_model, "")
        self.assertEqual(self.mk(GEMINI_FALLBACK_MODEL="gemini-2.5-flash-lite").fallback_model, "gemini-2.5-flash-lite")
        self.assertEqual(self.mk(GEMINI_MODEL=FALLBACK_MODEL).fallback_model, "")  # same as main: off

    def test_overload_wait_setting(self):
        self.assertEqual(self.mk().overload_wait_s, OVERLOAD_WAIT_S)
        self.assertEqual(self.mk(GEMINI_OVERLOAD_WAIT_S="0").overload_wait_s, 0)
        self.assertEqual(self.mk(GEMINI_OVERLOAD_WAIT_S="99999").overload_wait_s, 3600)

    def test_batch_size_setting_and_bounds(self):
        self.assertEqual(self.mk().review_batch_size, 40)
        self.assertEqual(self.mk(LLM_BATCH_SIZE="25").review_batch_size, 25)
        self.assertEqual(self.mk(LLM_BATCH_SIZE="0").review_batch_size, 1)
        self.assertEqual(self.mk(LLM_BATCH_SIZE="500").review_batch_size, 200)
        self.assertEqual(self.mk(LLM_BATCH_SIZE="много").review_batch_size, 40)
        ant = make_backend("anthropic", {"ANTHROPIC_API_KEY": "sk-ant-" + "TESTnotREAL0123456789"},
                           cache_dir=tempfile.mkdtemp())
        self.assertIsNone(ant.review_batch_size)
        self.assertEqual(make_backend("anthropic", {"ANTHROPIC_API_KEY": "sk-ant-" + "TESTnotREAL0123456789",
                                                    "LLM_BATCH_SIZE": "30"}, cache_dir=tempfile.mkdtemp()
                                      ).review_batch_size, 30)
        self.assertEqual(review_batch_size_for("gemini", {}), 40)
        self.assertIsNone(review_batch_size_for("anthropic", {}))
        self.assertEqual(review_batch_size_for("anthropic", {"LLM_BATCH_SIZE": "20"}), 20)

    def test_batch_counts(self):
        reqs = list(range(59))
        self.assertEqual(len(pipeline.batches(reqs)), 5)
        self.assertEqual(len(pipeline.batches(reqs, 40)), 2)
        self.assertEqual(sum(len(x) for x in pipeline.batches(reqs, 40)), 59)
        self.assertEqual(len(pipeline.batches(reqs, 200)), 1)
        self.assertEqual(pipeline.batches([], 40), [])


class EnvExample(unittest.TestCase):
    def test_env_example(self):
        text = (AGENT / ".env.example").read_text(encoding="utf-8")
        names = re.findall(r"^([A-Z][A-Z0-9_]+)=", text, flags=re.M)
        for n in ("LLM_PROVIDER", "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_FALLBACK_MODEL", "GEMINI_OVERLOAD_WAIT_S",
                  "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_EFFORT", "ANTHROPIC_EFFORT_STAGES",
                  "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL", "OLLAMA_BASE_URL", "OLLAMA_MODEL",
                  "LLM_CONCURRENCY", "LLM_TIMEOUT", "LLM_MAX_TOKENS", "LLM_BATCH_SIZE", "REVIZOR_MAX_COST_USD",
                  "REVIZOR_NO_DOTENV"):
            self.assertIn(n, names)
        self.assertEqual(len(names), len(set(names)))
        self.assertLess(names.index("GEMINI_API_KEY"), names.index("ANTHROPIC_API_KEY"))
        self.assertIn("GEMINI_MODEL=gemini-flash-latest", text)
        self.assertIn("gemini-1.5-flash", text)
        self.assertIn("aistudio.google.com", text)
        self.assertNotRegex(text, r"sk-ant-[A-Za-z0-9]|AIza[0-9A-Za-z_\-]{10,}|AQ\.[0-9A-Za-z_\-]{10,}")
        from revizor.llm import is_placeholder
        for line in text.splitlines():
            m = re.match(r"^([A-Z_]+_API_KEY)=(.*)$", line)
            if m:
                self.assertTrue(is_placeholder(m.group(2)), line)

    def test_env_example_copied_as_is_means_no_model(self):
        # A user who copies the sample and fills nothing must get the offline mode, not a dead local server.
        from revizor.llm import choose_provider
        text = (AGENT / ".env.example").read_text(encoding="utf-8")
        env = dict(re.findall(r"^([A-Z][A-Z0-9_]+)=(.*)$", text, flags=re.M))
        self.assertEqual(choose_provider(env), "offline")


class Cache(unittest.TestCase):
    def test_second_launch_finds_answers_given_by_the_spare_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Stub({MAIN: ["503_high_demand"], SPARE: ["ok_thought"]})
            b1 = make(stub, cache=DiskCache(tmp))
            b1.complete_json("s", "u1", schema=SCHEMA)
            b1.complete_json("s", "u2", schema=SCHEMA)
            stub2 = Stub({"*": ["503_high_demand"]})
            b2 = make(stub2, cache=DiskCache(tmp))
            self.assertEqual(b2.complete_json("s", "u1", schema=SCHEMA)[0], {"ok": True})
            self.assertEqual(b2.complete_json("s", "u2", schema=SCHEMA)[0], {"ok": True})
            self.assertEqual(stub2.posts(), [])
            self.assertEqual(b2.cache_hits, 2)


def fake_model(backend_ref: list):
    """Spare model that answers with the pipeline tests' fake JSON for the current stage."""
    script = tp.make_script()

    def answer(body):
        b = backend_ref[0]
        try:
            data = script(b.current_stage, body["systemInstruction"]["parts"][0]["text"],
                          body["contents"][0]["parts"][0]["text"])
        except Exception:
            return fx("empty_candidates")
        return ok(json.dumps(data, ensure_ascii=False))
    return answer


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("REVIZOR_NO_DOTENV")
        os.environ["REVIZOR_NO_DOTENV"] = "1"

    def tearDown(self):
        if self._old is None:
            os.environ.pop("REVIZOR_NO_DOTENV", None)
        else:
            os.environ["REVIZOR_NO_DOTENV"] = self._old

    def test_main_always_busy_spare_completes_the_run(self):
        ref: list = []
        stub = Stub({MAIN: ["503_high_demand"], SPARE: [fake_model(ref)]})
        b = make(stub)
        ref.append(b)
        lines: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "doc.md"
            doc.write_text(tp.DOC, encoding="utf-8")
            res = pipeline.run(str(doc), backend=b, depth="full", progress=lines.append)
        self.assertEqual(res.run.provider, "gemini")
        self.assertEqual(res.run.model, SPARE)
        self.assertTrue(any(f.source in ("model", "both") for f in res.findings))
        warn = "\n".join(res.run.warnings)
        self.assertIn("резервная модель «gemini-flash-lite-latest»", warn)
        self.assertIn("Ответы Gemini по моделям", warn)
        self.assertNotIn("Часть ответов дала запасная модель провайдера", warn)  # not the Anthropic cost warning
        self.assertEqual(len(stub.posts(MAIN)), 2)  # only the first call tried the main model
        self.assertEqual(res.run.llm_calls, len(stub.posts()))
        assert_no_key(self, json.dumps(res.to_dict() if hasattr(res, "to_dict") else res.run.to_dict(),
                                       ensure_ascii=False, default=str), "\n".join(lines), *b.notes)

    def test_request_count_on_organisers_document(self):
        """Gemini defaults, every answer at once, heuristic slicing (59 requirements): full ≤ 16, fast ≤ 11."""
        counts = {}
        for depth in ("full", "fast"):
            ref: list = []
            model = fake_model(ref)

            def route(body, model=model, ref=ref):
                if ref[0].current_stage == "slice":
                    return fx("empty_candidates")  # the run falls back to the heuristic slicer
                return model(body)
            stub = Stub({MAIN: [route]})
            b = make(stub)
            ref.append(b)
            res = pipeline.run(REQ_DOC, backend=b, depth=depth)
            self.assertGreater(len(res.requirements), 40)  # heuristic slicer: 59 on 25.11 (58–59 while it changes)
            self.assertLessEqual(len(res.requirements), 80)  # → two review batches of up to 40
            counts[depth] = len(stub.posts())
        self.assertLessEqual(counts["full"], 16)
        self.assertLessEqual(counts["fast"], 11)


if __name__ == "__main__":
    unittest.main()
