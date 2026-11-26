"""AnthropicBackend through the real `anthropic` SDK with an httpx2 mock transport: no network, no key."""
from __future__ import annotations

import json
import sys
import threading
import traceback
import unittest
from unittest import mock

try:
    import anthropic
    import httpx2
except ImportError:  # pragma: no cover
    anthropic = None

from revizor.llm import LLMBadOutput, LLMRefused, LLMTransient, LLMUnavailable, make_backend

KEY = "sk-" "ant-api03-TESTKEYnotREAL0123456789abcdef"
SCHEMA = {"type": "object", "required": ["findings"],
          "properties": {"findings": {"type": "array", "items": {"type": "object"}}}}


def sse(text: str, stop_reason: str = "end_turn", stop_details: dict | None = None, thinking: bool = True) -> bytes:
    events = [("message_start", {"type": "message_start", "message": {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 1200, "output_tokens": 1, "cache_read_input_tokens": 900,
                  "cache_creation_input_tokens": 300}}})]
    idx = 0
    if thinking:
        events += [
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "thinking_delta", "thinking": "{not json in thinking"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "signature_delta", "signature": "sig"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ]
        idx = 1
    events += [
        ("content_block_start", {"type": "content_block_start", "index": idx,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": idx,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": idx}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": stop_reason, "stop_sequence": None, "stop_details": stop_details},
                           "usage": {"output_tokens": 42}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join("event: %s\ndata: %s\n\n" % (e, json.dumps(d, ensure_ascii=False)) for e, d in events).encode("utf-8")


def ok(text: str = '{"findings": []}', **kw):
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=sse(text, **kw))


def err(status: int, etype: str, message: str, headers: dict | None = None, error_code: str = ""):
    body = {"type": "error", "error": {"type": etype, "message": message}}
    if error_code:
        body["error"]["details"] = {"error_code": error_code}
    return httpx2.Response(status, json=body, headers=headers or {})


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def __call__(self, request):
        with self.lock:
            self.requests.append({"path": request.url.path, "headers": dict(request.headers),
                                  "body": json.loads(request.content)})
            item = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item(request) if callable(item) else item


def assert_no_key(tc: unittest.TestCase, e: BaseException) -> None:
    texts = [str(e), repr(e), getattr(e, "user_message", ""), getattr(e, "detail", ""),
             "".join(traceback.format_exception(type(e), e, e.__traceback__))]
    for t in texts:
        tc.assertNotIn(KEY, t)
        tc.assertNotIn("TESTKEYnotREAL", t)


@unittest.skipIf(anthropic is None, "anthropic SDK not installed")
class AnthropicBackendTests(unittest.TestCase):
    def make(self, rec: Recorder, model: str = "claude-opus-5-5", effort: str = "high", **kw):
        from revizor.llm.anthropic_backend import AnthropicBackend

        b = AnthropicBackend(KEY, model=model, effort=effort, sdk_max_retries=0,
                             http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(rec)), **kw)
        self.sleeps: list[float] = []
        b.sleep = self.sleeps.append
        return b

    def test_request_shape_opus(self):
        rec = Recorder(ok())
        b = self.make(rec)
        data, usage = b.complete_json("СИСТЕМА + документ", "задача", schema=SCHEMA, stage="review")
        self.assertEqual(data, {"findings": []})
        self.assertEqual((usage.input_tokens, usage.output_tokens, usage.cache_read, usage.cache_write),
                         (1200, 42, 900, 300))
        r = rec.requests[0]
        body, headers = r["body"], r["headers"]
        self.assertEqual(r["path"], "/v1/messages")
        self.assertEqual(body["model"], "claude-opus-5-5")
        self.assertTrue(body["stream"])
        self.assertEqual(body["max_tokens"], 32000)
        self.assertEqual(body["system"], [{"type": "text", "text": "СИСТЕМА + документ",
                                           "cache_control": {"type": "ephemeral"}}])
        self.assertEqual(body["messages"], [{"role": "user", "content": "задача"}])
        self.assertEqual(body["output_config"], {"effort": "high"})  # no per-stage format
        self.assertEqual(body["thinking"], {"type": "adaptive"})
        self.assertEqual(body["fallbacks"], "default")
        for p in ("temperature", "top_p", "top_k"):
            self.assertNotIn(p, body)
        self.assertIn("server-side-fallback-2026-07-01", headers.get("anthropic-beta", ""))
        # The SDK chooses the auth header itself (2.14 sends the key as a bearer token).
        self.assertIn(KEY, headers.get("x-api-key", "") + headers.get("authorization", ""))
        self.assertEqual(b.calls, 1)
        self.assertEqual(b.totals.input_tokens, 1200)

    def test_haiku_has_no_fallback(self):
        rec = Recorder(ok())
        b = self.make(rec, model="claude-haiku-5-5", effort="medium")
        b.complete_json("s", "u", stage="slice")
        body, headers = rec.requests[0]["body"], rec.requests[0]["headers"]
        self.assertNotIn("fallbacks", body)
        self.assertNotIn("server-side-fallback", headers.get("anthropic-beta", ""))
        self.assertEqual(body["output_config"], {"effort": "medium"})

    def test_sonnet_5_5_and_fable_have_fallback(self):
        for model in ("claude-sonnet-5-5", "claude-fable-5-2", "claude-opus-5"):
            rec = Recorder(ok())
            self.make(rec, model=model).complete_json("s", "u")
            self.assertEqual(rec.requests[0]["body"].get("fallbacks"), "default", model)

    def test_rejected_fallback_beta_retried_without_it(self):
        rec = Recorder(err(400, "invalid_request_error",
                           "Unexpected value(s) `server-side-fallback-2026-07-01` for the `anthropic-beta` header."),
                       ok())
        b = self.make(rec)
        data, _ = b.complete_json("s", "u")
        self.assertEqual(data, {"findings": []})
        self.assertEqual(len(rec.requests), 2)
        self.assertIn("fallbacks", rec.requests[0]["body"])
        self.assertNotIn("fallbacks", rec.requests[1]["body"])
        self.assertNotIn("server-side-fallback", rec.requests[1]["headers"].get("anthropic-beta", ""))
        self.assertTrue(any("запасн" in n for n in b.notes))
        b.complete_json("s", "u2")  # stays off for later calls
        self.assertNotIn("fallbacks", rec.requests[2]["body"])

    def test_json_with_leading_prose_and_fence(self):
        rec = Recorder(ok('Вот результат:\n```json\n{"findings": [{"a": 1}]}\n```\nГотово.'))
        data, _ = self.make(rec).complete_json("s", "u", schema=SCHEMA)
        self.assertEqual(data["findings"], [{"a": 1}])

    def test_invalid_json_then_repair(self):
        rec = Recorder(ok("это не JSON"), ok('{"findings": []}'))
        b = self.make(rec)
        data, usage = b.complete_json("s", "задача", schema=SCHEMA, stage="review")
        self.assertEqual(data, {"findings": []})
        self.assertEqual(len(rec.requests), 2)
        repair = rec.requests[1]["body"]["messages"][0]["content"]
        self.assertTrue(repair.startswith("задача"))
        self.assertIn("Верни только исправленный JSON", repair)
        self.assertEqual(rec.requests[1]["body"]["system"], rec.requests[0]["body"]["system"])  # same cached prefix
        self.assertEqual(usage.output_tokens, 84)

    def test_refusal(self):
        rec = Recorder(ok("", stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber",
                                                                 "explanation": "x"}))
        with self.assertRaises(LLMRefused) as cm:
            self.make(rec).complete_json("s", "u")
        self.assertIn("отказалась", cm.exception.user_message)
        self.assertIn("кибербезопасность", cm.exception.user_message)
        self.assertEqual(len(rec.requests), 1)

    def test_max_tokens_is_bad_output_without_repair(self):
        rec = Recorder(ok('{"findings": [', stop_reason="max_tokens"))
        b = self.make(rec)
        with self.assertRaises(LLMBadOutput) as cm:
            b.complete_json("s", "u")
        self.assertIn("обрезан", cm.exception.user_message)
        self.assertEqual(len(rec.requests), 2)  # one retry with a doubled limit, no repair round-trip
        self.assertEqual([r["body"]["max_tokens"] for r in rec.requests], [32000, 64000])
        self.assertEqual(b.totals.output_tokens, 84)  # spent tokens of both attempts counted

    def test_401_unavailable_russian_and_masked(self):
        rec = Recorder(err(401, "authentication_error", "invalid x-api-key: " + KEY))
        with self.assertRaises(LLMUnavailable) as cm:
            self.make(rec).complete_json("s", "u")
        e = cm.exception
        self.assertIn("не принят", e.user_message)
        self.assertIn("console.anthropic.com", e.user_message)
        self.assertIn("offline", e.user_message)
        assert_no_key(self, e)
        self.assertEqual(len(rec.requests), 1)

    def test_403_and_404_unavailable(self):
        for status, etype, word in ((403, "permission_error", "доступа"), (404, "not_found_error", "не найдена")):
            rec = Recorder(err(status, etype, "model: claude-opus-5-5"))
            with self.assertRaises(LLMUnavailable) as cm:
                self.make(rec).complete_json("s", "u")
            self.assertIn(word, cm.exception.user_message)

    def test_429_retry_after_then_success(self):
        rec = Recorder(err(429, "rate_limit_error", "slow down", headers={"retry-after": "5"}), ok())
        b = self.make(rec)
        data, _ = b.complete_json("s", "u")
        self.assertEqual(data, {"findings": []})
        self.assertEqual(len(rec.requests), 2)
        self.assertEqual(len(self.sleeps), 1)
        self.assertGreaterEqual(self.sleeps[0], 5.0)
        self.assertLess(self.sleeps[0], 6.0)
        self.assertEqual((b.calls, b.retries), (2, 1))

    def test_spend_cap_429_not_retried(self):
        rec = Recorder(err(429, "rate_limit_error", "spend limit", error_code="enforced_spend_limit_reached"))
        with self.assertRaises(LLMUnavailable) as cm:
            self.make(rec).complete_json("s", "u")
        self.assertIn("лимит расходов", cm.exception.user_message)
        self.assertEqual(len(rec.requests), 1)

    def test_overloaded_exhausted_is_transient(self):
        rec = Recorder(err(529, "overloaded_error", "Overloaded"))
        b = self.make(rec)
        with self.assertRaises(LLMTransient) as cm:
            b.complete_json("s", "u")
        self.assertEqual(len(rec.requests), 4)
        self.assertEqual(len(self.sleeps), 3)
        self.assertIn("Попыток: 4", cm.exception.user_message)

    def test_connection_refused_is_unavailable_at_once(self):
        rec = Recorder(httpx2.ConnectError("[Errno 61] Connection refused"))
        b = self.make(rec)
        with self.assertRaises(LLMUnavailable) as cm:
            b.complete_json("s", "u")
        self.assertIn("соединение отклонено", cm.exception.user_message)
        self.assertEqual(len(rec.requests), 1)

    def test_other_network_error_retried_then_unavailable(self):
        rec = Recorder(httpx2.ConnectError("Name or service not known"))
        with self.assertRaises(LLMUnavailable) as cm:
            self.make(rec).complete_json("s", "u")
        self.assertEqual(len(rec.requests), 4)
        self.assertIn("интернет", cm.exception.user_message)

    def test_timeout_is_transient(self):
        rec = Recorder(httpx2.ReadTimeout("timed out"))
        with self.assertRaises(LLMTransient):
            self.make(rec).complete_json("s", "u")
        self.assertEqual(len(rec.requests), 4)

    def test_missing_package(self):
        from revizor.llm.anthropic_backend import AnthropicBackend

        with mock.patch.dict(sys.modules, {"anthropic": None}):
            with self.assertRaises(LLMUnavailable) as cm:
                AnthropicBackend(KEY)
        self.assertIn("pip install -r requirements.txt", cm.exception.user_message)
        with mock.patch.dict(sys.modules, {"anthropic": None}):
            with self.assertRaises(LLMUnavailable):
                make_backend("anthropic", {"ANTHROPIC_API_KEY": KEY}, cache_dir="/nonexistent-never-written")

    def test_make_backend_from_env(self):
        b = make_backend("auto", {"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_EFFORT": "max", "LLM_CONCURRENCY": "6",
                                  "ANTHROPIC_MODEL": "claude-haiku-5-5"}, cache_dir="/nonexistent-never-written")
        self.assertEqual((b.provider, b.model, b.effort, b.concurrency), ("anthropic", "claude-haiku-5-5", "max", 6))
        b2 = make_backend("anthropic", {"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_EFFORT": "turbo"},
                          cache_dir="/nonexistent-never-written")
        self.assertEqual((b2.model, b2.effort), ("claude-sonnet-5-5", "high"))
        self.assertTrue(b2.notes)
        with self.assertRaises(LLMUnavailable):
            make_backend("anthropic", {"ANTHROPIC_API_KEY": "your_anthropic_api_key_here"})


if __name__ == "__main__":
    unittest.main()
