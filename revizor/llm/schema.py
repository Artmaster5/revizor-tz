"""Loose JSON extraction and a small lenient validator for the JSON-Schema subset we use.

Supported keywords: type (one or a list), properties, required, items, enum, minItems,
maxItems, maxLength, default, and the flag "x-drop-invalid-items" on an array: an item that cannot be
repaired is DROPPED (and reported in the notes of ``validate_ex``) instead of failing the answer; the array fails
only when it is required, non-empty, and every item was dropped. The validator prefers repairing to failing: a model answer
that is "almost right" should still be usable.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

_FAIL = object()

# --- parse --------------------------------------------------------------------

_THINK_RE = re.compile(r"<(think|thinking|reasoning)\b[^>]*>.*?</\1\s*>", re.S | re.I)
_THINK_CLOSE_RE = re.compile(r"</(think|thinking|reasoning)\s*>", re.I)
_FENCE_RE = re.compile(r"```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n(.*?)(?:```|\Z)", re.S)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
_DECODER = json.JSONDecoder(strict=False)
_MAX_SCAN = 300


def _try_loads(s: str) -> Any:
    try:
        return _DECODER.decode(s)
    except ValueError:
        return _FAIL


def _scan(s: str, opener: str) -> Any:
    pos = s.find(opener)
    tried = 0
    while pos != -1 and tried < _MAX_SCAN:
        tried += 1
        try:
            obj, _end = _DECODER.raw_decode(s, pos)
            return obj
        except ValueError:
            pass
        pos = s.find(opener, pos + 1)
    return _FAIL


def parse_json_loose(text: str) -> Any:
    """Extract one JSON value from a model answer.

    Handles code fences, prose before/after, ``<think>…</think>`` blocks (also a lone closing
    tag), raw control characters inside strings and trailing commas. Raises ``ValueError``.
    """
    if not isinstance(text, str):
        raise ValueError("ответ не строка")
    s = text.lstrip("﻿")
    s = _THINK_RE.sub("", s)
    close = None
    for close in _THINK_CLOSE_RE.finditer(s):
        pass
    if close is not None:  # opening tag was stripped by the server
        s = s[close.end():]
    s = s.strip()
    if not s:
        raise ValueError("пустой ответ")

    r = _try_loads(s)
    if r is not _FAIL:
        return r
    for m in _FENCE_RE.finditer(s):
        inner = m.group(1).strip()
        if inner:
            r = _try_loads(inner)
            if r is not _FAIL:
                return r
            r = _scan(inner, "{")
            if r is not _FAIL:
                return r
    r = _scan(s, "{")
    if r is not _FAIL:
        return r
    r = _scan(s, "[")
    if r is not _FAIL:
        return r
    # Last resort: trailing commas inside the outermost object.
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        r = _try_loads(_TRAILING_COMMA_RE.sub(r"\1", s[start:end + 1]))
        if r is not _FAIL:
            return r
    snippet = s[:80].replace("\n", " ")
    raise ValueError("в ответе не найден разборный JSON (начало: «%s»)" % snippet)



def _salvage_shape(schema: Any) -> tuple[str, list[str], list[str]] | None:
    """(list key, item keys that start an item, other top-level keys) for a schema {"<key>": [ {…}, … ]}."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return None
    req = schema.get("required") or []
    props = schema.get("properties") or {}
    if len(req) != 1 or not isinstance(props.get(req[0]), dict):
        return None
    arr = props[req[0]]
    items = arr.get("items")
    if arr.get("type") != "array" or not isinstance(items, dict) or items.get("type") != "object":
        return None
    starts = [k for k in (items.get("required") or []) if isinstance(k, str)]
    if not starts:
        return None
    return req[0], starts, [k for k in props if k != req[0]]


def salvage_items(text: str, schema: Any) -> tuple[dict, int] | None:
    """Rescue a broken answer of the form {"<key>": [ {…}, {…}, … ]} item by item.

    A weaker model sometimes breaks the JSON inside one item: an unescaped quote copied from the document,
    a bad escape. The loose parser then finds only a nested object, and the whole answer is lost although
    every other item is fine. Here each item that starts with one of its required keys is decoded on its
    own; a broken one is skipped. Returns ({"<key>": items}, number of skipped items) or None.
    """
    shape = _salvage_shape(schema)
    if shape is None or not isinstance(text, str):
        return None
    key, starts, others = shape
    s = _THINK_RE.sub("", text)
    head = re.search(r'"%s"\s*:\s*\[' % re.escape(key), s)
    if head is None:
        return None
    start_re = re.compile(r'\{\s*"(?:%s)"\s*:' % "|".join(re.escape(k) for k in starts))
    other_re = re.compile(r'"(?:%s)"\s*:' % "|".join(re.escape(k) for k in others)) if others else None
    items: list[dict] = []
    broken = 0
    pos = head.end()
    while True:
        m = start_re.search(s, pos)
        if m is None:
            break
        if other_re is not None:  # the list is over: the next top-level field starts before the next item
            o = other_re.search(s, pos, m.start())
            if o is not None:
                break
        try:
            obj, end = _DECODER.raw_decode(s, m.start())
        except ValueError:
            broken += 1
            pos = m.start() + 1
            continue
        if not isinstance(obj, dict):
            broken += 1
            pos = m.start() + 1
            continue
        items.append(obj)
        pos = end
        rest = s[end:end + 200].lstrip()
        if rest.startswith("]"):
            break
    if not items:
        return None
    return {key: items}, broken


# --- validate -----------------------------------------------------------------

_TYPE_RU = {
    "object": "объект",
    "array": "список",
    "string": "строка",
    "integer": "целое число",
    "number": "число",
    "boolean": "логическое значение",
    "null": "null",
}


def _type_name(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def _matches(v: Any, t: str) -> bool:
    if t == "object":
        return isinstance(v, dict)
    if t == "array":
        return isinstance(v, list)
    if t == "string":
        return isinstance(v, str)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "null":
        return v is None
    return True  # unknown type keyword: do not judge


def _coerce(v: Any, types: list[str]) -> Any:
    for t in types:
        if t == "array" and not isinstance(v, list):
            return [v]  # a single object (or scalar) where a list is expected
        if t == "object" and isinstance(v, list) and len(v) == 1 and isinstance(v[0], dict):
            return v[0]
        if t == "string" and isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
        if t == "string" and isinstance(v, bool):
            return "true" if v else "false"
        if t in ("integer", "number") and isinstance(v, str):
            try:
                f = float(v.strip().replace(",", "."))
            except ValueError:
                continue
            if t == "integer":
                if f.is_integer():
                    return int(f)
                continue
            return int(f) if f.is_integer() and "." not in v and "," not in v else f
        if t == "integer" and isinstance(v, float) and v.is_integer():
            return int(v)
        if t == "boolean" and isinstance(v, str):
            low = v.strip().lower()
            if low in ("true", "да", "yes"):
                return True
            if low in ("false", "нет", "no"):
                return False
    return _FAIL


def _enum_key(v: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(v)).lower()


def _v(value: Any, schema: Any, path: str, errors: list[str], required: bool,
       notes: list[dict[str, str]] | None = None) -> Any:
    if not isinstance(schema, dict) or not schema:
        return value
    t = schema.get("type")
    types = [t] if isinstance(t, str) else [x for x in (t or []) if isinstance(x, str)]

    if value is None and types and "null" not in types:
        if "default" in schema:
            return copy.deepcopy(schema["default"])
        if not required and "string" in types:
            return ""
        if not required and "array" in types:
            return []
        errors.append("%s: пустое значение (null), ожидался тип %s" % (path, "/".join(types)))
        return None

    if types and not any(_matches(value, x) for x in types):
        c = _coerce(value, types)
        if c is _FAIL:
            errors.append("%s: ожидался тип %s, получено %s"
                          % (path, "/".join(_TYPE_RU.get(x, x) for x in types), _TYPE_RU.get(_type_name(value), _type_name(value))))
            return value
        value = c

    enum = schema.get("enum")
    if isinstance(enum, list) and enum and value not in enum:
        key = _enum_key(value)
        match = next((e for e in enum if _enum_key(e) == key), _FAIL)
        if match is not _FAIL:
            value = match
        elif "default" in schema:
            value = copy.deepcopy(schema["default"])
        else:
            shown = str(value)[:40]
            errors.append("%s: значение «%s» не из списка допустимых: %s"
                          % (path, shown, ", ".join(str(e) for e in enum[:12])))
            return value

    if isinstance(value, dict):
        props = schema.get("properties") or {}
        req = schema.get("required") or []
        out: dict[str, Any] = {}
        for k, v in value.items():
            sub = props.get(k)
            out[k] = _v(v, sub, "%s.%s" % (path, k), errors, k in req, notes) if sub is not None else v
        for k, sub in props.items():
            if k in out:
                continue
            if isinstance(sub, dict) and "default" in sub:
                out[k] = copy.deepcopy(sub["default"])
            elif k in req:
                errors.append("%s.%s: нет обязательного поля" % (path, k))
        for k in req:
            if k not in props and k not in out:
                errors.append("%s.%s: нет обязательного поля" % (path, k))
        return out

    if isinstance(value, list):
        items = schema.get("items")
        if items and schema.get("x-drop-invalid-items"):
            lst = []
            for i, x in enumerate(value):
                item_errors: list[str] = []
                item_notes: list[dict[str, str]] = []
                got = _v(x, items, "%s[%d]" % (path, i), item_errors, True, item_notes)
                if item_errors:
                    if notes is not None:
                        notes.append({"path": "%s[%d]" % (path, i), "reason": item_errors[0][:160]})
                    continue
                if notes is not None:
                    notes.extend(item_notes)
                lst.append(got)
            if value and not lst and required:
                errors.append("%s: ни один из %d элементов не годится" % (path, len(value)))
        else:
            lst = [_v(x, items, "%s[%d]" % (path, i), errors, True, notes) for i, x in enumerate(value)] if items else list(value)
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and max_items >= 0 and len(lst) > max_items:
            lst = lst[:max_items]
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(lst) < min_items:
            errors.append("%s: элементов %d, нужно не меньше %d" % (path, len(lst), min_items))
        return lst

    if isinstance(value, str):
        max_len = schema.get("maxLength")
        if isinstance(max_len, int) and max_len > 0 and len(value) > max_len:
            value = value[: max_len - 1] + "…" if max_len > 1 else value[:max_len]
        return value

    return value


def validate_ex(data: Any, schema: dict) -> tuple[Any, list[str], list[dict[str, str]]]:
    """Like ``validate`` plus the notes about dropped items: [{"path": "$.reviews[2]", "reason": "…"}]."""
    errors: list[str] = []
    notes: list[dict[str, str]] = []
    out = _v(data, schema or {}, "$", errors, True, notes)
    return out, errors, notes


def validate(data: Any, schema: dict) -> tuple[Any, list[str]]:
    """Return (repaired data, errors). Never raises; the input is not modified. Dropped items are not reported here."""
    out, errors, _notes = validate_ex(data, schema)
    return out, errors
