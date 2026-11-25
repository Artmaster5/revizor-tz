"""Model-layer core without any provider: JSON parsing, schema repairs, FakeBackend, retries,
repair round-trip, disk cache, thread safety, provider choice, pricing, key masking."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from revizor.llm import (FakeBackend, LLMBadOutput, LLMError, LLMRefused, LLMTransient, LLMUnavailable, Usage,
                         choose_provider, is_placeholder, make_backend)
from revizor.llm.base import parse_retry_after, redact, register_secret
from revizor.llm.cache import DiskCache
from revizor.llm.pricing import cost_note, cost_usd
from revizor.llm.schema import parse_json_loose, validate

SEV = {"type": "string", "enum": ["CRITICAL", "MAJOR", "MINOR"]}
FINDINGS = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {"type": "array", "maxItems": 3, "items": {
            "type": "object",
            "required": ["severity", "title"],
            "properties": {
                "severity": SEV,
                "title": {"type": "string", "maxLength": 10},
                "criterion": {"type": "string", "enum": ["completeness", "set_consistency", "other"], "default": "other"},
                "quote": {"type": "string"},
                "req_ids": {"type": "array", "items": {"type": "string"}},
                "question": {"type": ["object", "null"]},
                "confidence": {"type": "number"},
            }}},
        "summary": {"type": "string", "default": ""},
    },
}


class ParseJsonLooseTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(parse_json_loose('{"a": 1}'), {"a": 1})

    def test_code_fence(self):
        self.assertEqual(parse_json_loose('```json\n{"a": [1, 2]}\n```'), {"a": [1, 2]})
        self.assertEqual(parse_json_loose('```\n{"a": 1}\n```'), {"a": 1})

    def test_unclosed_fence(self):
        self.assertEqual(parse_json_loose('```json\n{"a": 1}\n'), {"a": 1})

    def test_leading_and_trailing_prose(self):
        self.assertEqual(parse_json_loose('Конечно! Вот ответ (см. {ниже}):\n{"a": "x"}\nНадеюсь, помог.'), {"a": "x"})

    def test_think_blocks(self):
        self.assertEqual(parse_json_loose('<think>план: {"b": 2}</think>\n{"a": 1}'), {"a": 1})
        self.assertEqual(parse_json_loose('<thinking>\n…\n</thinking>```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json_loose('рассуждение без открывающего тега {"x": 0}</think>{"a": 1}'), {"a": 1})

    def test_raw_newline_in_string_and_bom(self):
        self.assertEqual(parse_json_loose('﻿{"a": "строка\nвторая"}'), {"a": "строка\nвторая"})

    def test_trailing_comma(self):
        self.assertEqual(parse_json_loose('{"a": [1, 2,], "b": 3,}'), {"a": [1, 2], "b": 3})

    def test_failures(self):
        for bad in ("", "   ", "просто текст", "{нет: кавычек", None):
            with self.assertRaises(ValueError):
                parse_json_loose(bad)  # type: ignore[arg-type]


class ValidateTests(unittest.TestCase):
    def test_valid_passes_unchanged(self):
        data = {"findings": [{"severity": "MAJOR", "title": "Коротко", "criterion": "other"}], "summary": "ok"}
        out, errors = validate(data, FINDINGS)
        self.assertEqual(errors, [])
        self.assertEqual(out, data)

    def test_lenient_repairs(self):
        data = {"findings": {"severity": " major ", "title": "Очень длинный заголовок", "criterion": "SET-CONSISTENCY",
                             "quote": None, "req_ids": None, "question": None, "confidence": "0.8", "extra": 1}}
        out, errors = validate(data, FINDINGS)
        self.assertEqual(errors, [])
        f = out["findings"][0]  # single object wrapped into a list
        self.assertEqual(f["severity"], "MAJOR")  # case/space-insensitive enum
        self.assertEqual(f["criterion"], "set_consistency")
        self.assertEqual(len(f["title"]), 10)  # cut to maxLength
        self.assertTrue(f["title"].endswith("…"))
        self.assertEqual(f["quote"], "")  # null optional string
        self.assertEqual(f["req_ids"], [])  # null optional array
        self.assertIsNone(f["question"])  # null allowed by type list
        self.assertEqual(f["confidence"], 0.8)
        self.assertEqual(f["extra"], 1)  # unknown keys kept
        self.assertEqual(out["summary"], "")  # missing optional with default filled
        self.assertNotIn("severity", data["findings"]["title"])  # input not modified
        self.assertEqual(data["findings"]["severity"], " major ")

    def test_enum_default_and_errors(self):
        out, errors = validate({"findings": [{"severity": "BLOCKER", "title": "x", "criterion": "нечто"}]}, FINDINGS)
        self.assertEqual(out["findings"][0]["criterion"], "other")  # unknown → schema default
        self.assertEqual(len(errors), 1)
        self.assertIn("$.findings[0].severity", errors[0])

    def test_missing_required_and_wrong_type(self):
        _, errors = validate({"findings": [{"title": 5}]}, FINDINGS)
        self.assertTrue(any("$.findings[0].severity" in e for e in errors))
        _, errors = validate({"summary": "x"}, FINDINGS)
        self.assertTrue(any("$.findings" in e for e in errors))
        _, errors = validate({"findings": "строка"}, {"type": "object", "properties": {
            "findings": {"type": "array", "items": {"type": "object"}}}})
        self.assertTrue(errors)
        _, errors = validate([1, 2], {"type": "object"})
        self.assertTrue(errors)

    def test_max_items_truncates_min_items_errors(self):
        out, errors = validate({"findings": [{"severity": "MINOR", "title": str(i)} for i in range(5)]}, FINDINGS)
        self.assertEqual((len(out["findings"]), errors), (3, []))
        _, errors = validate({"o": []}, {"type": "object", "properties": {"o": {"type": "array", "minItems": 2}}})
        self.assertEqual(len(errors), 1)

    def test_integer_and_boolean_coercion(self):
        s = {"type": "object", "properties": {"n": {"type": "integer"}, "b": {"type": "boolean"}, "s": {"type": "string"}}}
        out, errors = validate({"n": "12", "b": "да", "s": 7}, s)
        self.assertEqual((out, errors), ({"n": 12, "b": True, "s": "7"}, []))
        _, errors = validate({"n": "1.5"}, s)
        self.assertTrue(errors)


class FakeBackendTests(unittest.TestCase):
    def test_queue_last_repeats_and_log(self):
        b = FakeBackend({"review": [{"n": 1}, {"n": 2}]})
        got = [b.complete_json("S", "U%d" % i, stage="review")[0]["n"] for i in range(4)]
        self.assertEqual(got, [1, 2, 2, 2])
        self.assertEqual(b.log[0], ("review", "S", "U0"))
        self.assertEqual(b.calls, 4)
        self.assertEqual(b.provider, "fake")

    def test_single_item_str_callable_and_exception(self):
        b = FakeBackend({"a": {"x": 1}, "b": lambda s, u: {"echo": u}, "c": LLMRefused("нет"),
                         "d": '```json\n{"y": 2}\n```'})
        self.assertEqual(b.complete_json("s", "u", stage="a")[0], {"x": 1})
        self.assertEqual(b.complete_json("s", "привет", stage="b")[0], {"echo": "привет"})
        with self.assertRaises(LLMRefused):
            b.complete_json("s", "u", stage="c")
        self.assertEqual(b.complete_json("s", "u", stage="d")[0], {"y": 2})

    def test_missing_stage(self):
        with self.assertRaises(LLMBadOutput) as cm:
            FakeBackend({}).complete_json("s", "u", stage="skeptic")
        self.assertIn("нет сценария для этапа", cm.exception.user_message)

    def test_whole_script_callable(self):
        b = FakeBackend(lambda stage, s, u: {"stage": stage})
        self.assertEqual(b.complete_json("s", "u", stage="questions")[0], {"stage": "questions"})

    def test_usage_totals(self):
        b = FakeBackend({"x": {"a": 1}})
        _, u = b.complete_json("s" * 40, "u" * 40, stage="x")
        self.assertEqual(u.input_tokens, 20)
        self.assertEqual(b.totals.input_tokens, 20)
        self.assertFalse(u.from_cache)


class RepairTests(unittest.TestCase):
    def test_invalid_once_then_repair(self):
        b = FakeBackend({"review": ["это не JSON вовсе", {"findings": []}]})
        data, usage = b.complete_json("SYS", "ЗАДАЧА", schema=FINDINGS, stage="review")
        self.assertEqual(data, {"findings": [], "summary": ""})
        self.assertEqual(len(b.log), 2)
        stage, system, repair_user = b.log[1]
        self.assertEqual((stage, system), ("review", "SYS"))
        self.assertTrue(repair_user.startswith("ЗАДАЧА"))
        self.assertIn("это не JSON вовсе", repair_user)
        self.assertIn("Верни только исправленный JSON", repair_user)
        self.assertGreater(usage.output_tokens, 0)

    def test_schema_error_repair_lists_errors(self):
        b = FakeBackend({"review": [{"findings": [{"severity": "BLOCKER", "title": "x"}]},
                                    {"findings": [{"severity": "MAJOR", "title": "x"}]}]})
        data, _ = b.complete_json("s", "u", schema=FINDINGS, stage="review")
        self.assertEqual(data["findings"][0]["severity"], "MAJOR")
        self.assertIn("$.findings[0].severity", b.log[1][2])

    def test_invalid_twice(self):
        b = FakeBackend({"review": ["мусор", "снова мусор", {"findings": []}]})
        with self.assertRaises(LLMBadOutput) as cm:
            b.complete_json("s", "u", schema=FINDINGS, stage="review")
        self.assertIn("review", cm.exception.user_message)
        self.assertEqual(len(b.log), 2)  # exactly one repair

    def test_top_level_must_be_object(self):
        b = FakeBackend({"x": [[1, 2], [3]]})
        with self.assertRaises(LLMBadOutput):
            b.complete_json("s", "u", stage="x")


class RetryTests(unittest.TestCase):
    def test_transient_retried_then_success(self):
        sleeps = []
        b = FakeBackend({"x": [LLMTransient("429", retry_after=3.0), LLMTransient("500"), {"ok": 1}]})
        b.sleep = sleeps.append
        b.jitter = lambda: 0.0
        data, _ = b.complete_json("s", "u", stage="x")
        self.assertEqual(data, {"ok": 1})
        self.assertEqual(sleeps, [3.0, 4.0])  # retry-after honoured, then exponential backoff
        self.assertEqual((b.calls, b.retries), (3, 2))

    def test_exhausted(self):
        b = FakeBackend({"x": LLMTransient("перегрузка", kind="server")})
        with self.assertRaises(LLMTransient) as cm:
            b.complete_json("s", "u", stage="x")
        self.assertIn("Попыток: 4", cm.exception.user_message)
        self.assertEqual(b.calls, 4)

    def test_network_exhausted_becomes_unavailable(self):
        b = FakeBackend({"x": LLMTransient("нет связи", kind="network")})
        with self.assertRaises(LLMUnavailable):
            b.complete_json("s", "u", stage="x")

    def test_too_long_retry_after_gives_up(self):
        b = FakeBackend({"x": [LLMTransient("квота", retry_after=3600.0), {"ok": 1}]})
        with self.assertRaises(LLMTransient):
            b.complete_json("s", "u", stage="x")
        self.assertEqual(b.calls, 1)

    def test_unavailable_not_retried(self):
        b = FakeBackend({"x": [LLMUnavailable("ключ"), {"ok": 1}]})
        with self.assertRaises(LLMUnavailable):
            b.complete_json("s", "u", stage="x")
        self.assertEqual(b.calls, 1)

    def test_parse_retry_after(self):
        self.assertEqual(parse_retry_after({"retry-after": "12"}), 12.0)
        self.assertEqual(parse_retry_after({"retry-after-ms": "1500"}), 1.5)
        self.assertIsNone(parse_retry_after({}))
        self.assertGreater(parse_retry_after({"retry-after": "Wed, 21 Oct 2099 07:28:00 GMT"}), 1000)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_hit_sets_from_cache_and_counts(self):
        b = FakeBackend({"x": [{"v": 1}, {"v": 2}]}, cache_dir=self.tmp.name)
        d1, u1 = b.complete_json("S", "U", stage="x")
        d2, u2 = b.complete_json("S", "U", stage="x")
        self.assertEqual(d1, d2)
        self.assertFalse(u1.from_cache)
        self.assertTrue(u2.from_cache)
        self.assertEqual((u2.input_tokens, u2.output_tokens), (0, 0))
        self.assertEqual((b.calls, b.cache_hits), (1, 1))
        files = list(Path(self.tmp.name).glob("*.json"))
        self.assertEqual(len(files), 1)
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual((payload["provider"], payload["model"], payload["stage"], payload["data"]),
                         ("fake", "fake-1", "x", {"v": 1}))
        self.assertFalse(list(Path(self.tmp.name).glob("*.tmp")))

    def test_key_depends_on_inputs(self):
        k = DiskCache.key("p", "m", "high", "s", "u")
        self.assertEqual(len(k), 64)
        self.assertNotEqual(k, DiskCache.key("p", "m", "low", "s", "u"))
        self.assertNotEqual(k, DiskCache.key("p", "m2", "high", "s", "u"))
        self.assertNotEqual(k, DiskCache.key("p", "m", "high", "s", "u2"))
        self.assertNotEqual(DiskCache.key("p", "m", "", "ab", "c"), DiskCache.key("p", "m", "", "a", "bc"))

    def test_no_cache_skips_reading_but_writes(self):
        FakeBackend({"x": {"v": 1}}, cache_dir=self.tmp.name).complete_json("S", "U", stage="x")
        b = FakeBackend({"x": {"v": 2}}, cache_dir=self.tmp.name, use_cache=False)
        d, u = b.complete_json("S", "U", stage="x")
        self.assertEqual((d, u.from_cache, b.cache_hits), ({"v": 2}, False, 0))
        b3 = FakeBackend({"x": {"v": 3}}, cache_dir=self.tmp.name)
        self.assertEqual(b3.complete_json("S", "U", stage="x")[0], {"v": 2})  # overwritten by the no-cache run

    def test_failure_not_cached(self):
        b = FakeBackend({"x": ["мусор", "мусор"]}, cache_dir=self.tmp.name)
        with self.assertRaises(LLMBadOutput):
            b.complete_json("S", "U", stage="x")
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_stale_entry_failing_schema_is_a_miss(self):
        FakeBackend({"x": {"other": 1}}, cache_dir=self.tmp.name).complete_json("S", "U", stage="x")
        b = FakeBackend({"x": {"findings": []}}, cache_dir=self.tmp.name)
        d, u = b.complete_json("S", "U", schema=FINDINGS, stage="x")
        self.assertFalse(u.from_cache)
        self.assertEqual(d["findings"], [])

    def test_corrupt_entry_is_a_miss_and_unwritable_dir_is_a_note(self):
        c = DiskCache(self.tmp.name)
        key = c.key("fake", "fake-1", "", "S", "U")
        Path(self.tmp.name, key + ".json").write_text("{битый", encoding="utf-8")
        b = FakeBackend({"x": {"v": 1}}, cache_dir=self.tmp.name)
        self.assertFalse(b.complete_json("S", "U", stage="x")[1].from_cache)
        blocker = Path(self.tmp.name, "file")
        blocker.write_text("x")
        b2 = FakeBackend({"x": {"v": 1}}, cache_dir=str(blocker / "sub"))
        self.assertEqual(b2.complete_json("S", "U", stage="x")[0], {"v": 1})
        self.assertTrue(any("Кэш" in n for n in b2.notes))


class ThreadSafetyTests(unittest.TestCase):
    def test_eight_threads(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        b = FakeBackend(lambda stage, s, u: {"u": u}, cache_dir=tmp.name)
        errors: list[BaseException] = []
        results: dict[str, dict] = {}
        lock = threading.Lock()
        start = threading.Barrier(8)

        def worker(i: int) -> None:
            try:
                start.wait()
                for j in range(25):
                    user = "U-%d-%d" % (i, j % 20)  # repeats → cache hits
                    d, _ = b.complete_json("S", user, stage="review")
                    with lock:
                        results[user] = d
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(b.calls + b.cache_hits, 200)
        self.assertEqual(b.calls, len(b.log))
        self.assertEqual(b.calls, 160)
        self.assertEqual(b.cache_hits, 40)
        self.assertTrue(all(results[k] == {"u": k} for k in results))
        self.assertEqual(len(list(Path(tmp.name).glob("*.json"))), 160)
        self.assertEqual(b.totals.input_tokens, sum(max(1, (1 + len(u)) // 4) for _, _, u in b.log))


class ProviderChoiceTests(unittest.TestCase):
    def test_order(self):
        self.assertEqual(choose_provider({}), "offline")
        self.assertEqual(choose_provider({"OLLAMA_BASE_URL": "http://localhost:11434"}), "ollama")
        self.assertEqual(choose_provider({"OPENAI_API_KEY": "sk-real-looking-123", "OLLAMA_BASE_URL": "http://x"}), "openai")
        self.assertEqual(choose_provider({"GOOGLE_API_KEY": "AI" "zaReal", "OPENAI_API_KEY": "sk-1"}), "gemini")
        self.assertEqual(choose_provider({"GEMINI_API_KEY": "AI" "zaReal", "ANTHROPIC_API_KEY": "sk-" "ant-1"}), "anthropic")

    def test_forced_and_aliases(self):
        env = {"ANTHROPIC_API_KEY": "sk-" "ant-1"}
        self.assertEqual(choose_provider(dict(env, LLM_PROVIDER="gemini")), "gemini")
        self.assertEqual(choose_provider(dict(env, LLM_PROVIDER=" Offline ")), "offline")
        self.assertEqual(choose_provider(dict(env, LLM_PROVIDER="auto")), "anthropic")
        self.assertEqual(choose_provider(dict(env, LLM_PROVIDER="claude")), "anthropic")
        self.assertEqual(choose_provider({"LLM_PROVIDER": "mistral"}), "mistral")

    def test_placeholders_count_as_absent(self):
        for v in ("", "  ", "your_anthropic_api_key_here", "your-key", "YOUR_GEMINI_API_KEY_HERE", "<ключ>", "...",
                  "xxx", "changeme", "sk-...", "'your_api_key_here'", "вставьте ключ сюда"):
            self.assertTrue(is_placeholder(v), v)
        for v in ("sk-" "ant-api03-abc", "AI" "zaSyabc", "http://localhost:11434", "sk-proj-123"):
            self.assertFalse(is_placeholder(v), v)
        env = {"ANTHROPIC_API_KEY": "your_anthropic_api_key_here", "GEMINI_API_KEY": "your_gemini_api_key_here",
               "OPENAI_API_KEY": "", "OLLAMA_BASE_URL": ""}
        self.assertEqual(choose_provider(env), "offline")

    def test_make_backend_errors(self):
        with self.assertRaises(LLMUnavailable):
            make_backend("offline", {})
        with self.assertRaises(LLMUnavailable) as cm:
            make_backend("mistral", {})
        self.assertIn("Неизвестный провайдер", cm.exception.user_message)
        with self.assertRaises(LLMUnavailable) as cm:
            make_backend("gemini", {"GEMINI_API_KEY": "your_gemini_api_key_here"})
        self.assertIn("aistudio.google.com", cm.exception.user_message)
        with self.assertRaises(LLMUnavailable):
            make_backend("auto", {})


class PricingTests(unittest.TestCase):
    def test_known_models(self):
        u = Usage(input_tokens=1_000_000, output_tokens=1_000_000, cache_read=1_000_000, cache_write=1_000_000)
        self.assertAlmostEqual(cost_usd("anthropic", "claude-opus-5-5", u), 4 + 20 + 0.20 + 5)
        self.assertAlmostEqual(cost_usd("anthropic", "claude-sonnet-5-5", u), 2 + 10 + 0.10 + 2.5)
        self.assertAlmostEqual(cost_usd("anthropic", "claude-haiku-5-5", u), 0.10 + 0.50 + 0.01 + 0.125)
        self.assertAlmostEqual(cost_usd("anthropic", "claude-fable-5-2", u), 8 + 40 + 0.20 + 10)
        self.assertAlmostEqual(cost_usd("anthropic", "claude-opus-5-5-20261101", u), 29.2)
        self.assertAlmostEqual(cost_usd("anthropic", "claude-opus-5-5", Usage(input_tokens=1000, output_tokens=500)),
                               0.014)

    def test_free_local_unknown(self):
        u = Usage(input_tokens=10_000, output_tokens=10_000)
        self.assertEqual(cost_usd("gemini", "gemini-flash-latest", u), 0.0)
        self.assertEqual(cost_note("gemini", "gemini-flash-latest"), "бесплатный тариф")
        self.assertEqual(cost_usd("ollama", "qwen3:8b", u), 0.0)
        self.assertEqual(cost_note("ollama", "qwen3:8b"), "локальная модель")
        self.assertIsNone(cost_usd("anthropic", "claude-unknown-9", u))
        self.assertIsNone(cost_usd("openai", "gpt-9-turbo", u))
        self.assertEqual(cost_note("openai", "gpt-9-turbo"), "цена модели неизвестна")
        self.assertTrue(cost_note("anthropic", "claude-opus-5-5").startswith("по тарифу claude-opus-5-5"))
        self.assertIsNotNone(cost_usd("openai", "gpt-4o-mini", u))


class SecretMaskingTests(unittest.TestCase):
    def test_registered_and_pattern_keys_masked(self):
        register_secret("my-custom-secret-value-42")
        e = LLMUnavailable("ошибка my-custom-secret-value-42 и sk-" "ant-api03-AAAAAAAAAAAA и AI" "zaSyBBBBBBBBBBBBBBBBBBBBBBBB",
                           detail="Authorization: Bearer abcdefghijklmnop123 key=ZZZZZZZZZZZZZZZZZZZZ")
        for text in (str(e), e.user_message, e.detail):
            for secret in ("my-custom-secret-value-42", "AAAAAAAAAAAA", "BBBBBBBBBBBB", "abcdefghijklmnop123",
                           "ZZZZZZZZZZZZZZZZZZZZ"):
                self.assertNotIn(secret, text)
        self.assertIn("ошибка", e.user_message)
        self.assertIsInstance(e, LLMError)

    def test_plain_text_untouched(self):
        self.assertEqual(redact("Модель вернула task-1234567890abcdefghij"), "Модель вернула task-1234567890abcdefghij")


if __name__ == "__main__":
    unittest.main()
