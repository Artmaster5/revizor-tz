"""Tiny JSON-over-HTTP helper on stdlib urllib, shared by the Gemini and OpenAI-compatible backends.

Never puts a key in the URL and never logs headers. Network failures become ``LLMError``s:
a refused connection is ``LLMUnavailable`` at once (nothing listens there, retrying is useless),
timeouts and other network errors are ``LLMTransient`` (retried by the backend loop).
"""
from __future__ import annotations

import errno
import http.client
import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from .base import LLMTransient, LLMUnavailable


@dataclass
class HttpResult:
    status: int
    data: Any  # parsed JSON body or None
    text: str  # raw body (for error messages; redacted by LLMError)
    headers: Any


def _host(url: str) -> str:
    parts = urlsplit(url)
    return "%s://%s" % (parts.scheme, parts.netloc)


def _is_refused(exc: BaseException | None) -> bool:
    seen = 0
    while exc is not None and seen < 6:
        if isinstance(exc, ConnectionRefusedError):
            return True
        if isinstance(exc, OSError) and exc.errno in (errno.ECONNREFUSED,):
            return True
        if "connection refused" in str(exc).lower():
            return True
        exc = getattr(exc, "reason", None) if isinstance(exc, urllib.error.URLError) else (exc.__cause__ or exc.__context__)
        seen += 1
    return False


def request_json(method: str, url: str, *, headers: Mapping[str, str], body: Any = None,
                 timeout: float = 300.0) -> HttpResult:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    hdrs = {"Accept": "application/json"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    host = _host(url)
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                status, rh = r.status, r.headers
        except urllib.error.HTTPError as e:
            try:
                raw = e.read() or b""
            except (OSError, http.client.HTTPException):
                raw = b""
            finally:
                e.close()
            status, rh = e.code, e.headers
    except urllib.error.URLError as e:
        reason = e.reason
        if _is_refused(e):
            raise LLMUnavailable("Не удалось подключиться к %s: соединение отклонено. Сервер модели не запущен "
                                 "или адрес указан неверно." % host, kind="network") from None
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise LLMTransient("Сервер %s не ответил за %d с." % (host, int(timeout)), kind="timeout") from None
        raise LLMTransient("Нет связи с %s (%s)." % (host, type(reason).__name__ if isinstance(reason, BaseException)
                                                     else str(reason)[:80]), kind="network") from None
    except (socket.timeout, TimeoutError):
        raise LLMTransient("Сервер %s не ответил за %d с." % (host, int(timeout)), kind="timeout") from None
    except ConnectionRefusedError:
        raise LLMUnavailable("Не удалось подключиться к %s: соединение отклонено." % host, kind="network") from None
    except (ConnectionError, http.client.HTTPException, OSError) as e:
        raise LLMTransient("Связь с %s прервалась (%s)." % (host, type(e).__name__), kind="network") from None

    text = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(text) if text.strip() else None
    except ValueError:
        parsed = None
    return HttpResult(status, parsed, text, rh)


def error_message(res: HttpResult) -> str:
    """Best-effort human text of an API error body (OpenAI and Google formats)."""
    d = res.data
    if isinstance(d, dict):
        err = d.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:400]
        if isinstance(err, str):
            return err[:400]
        if d.get("message"):
            return str(d["message"])[:400]
    if isinstance(d, list) and d and isinstance(d[0], dict):
        return error_message(HttpResult(res.status, d[0], "", res.headers))
    return res.text.strip()[:300]
