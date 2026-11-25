"""Masking of sensitive values (payment card numbers, Kazakhstan IIN/BIN, e-mail addresses, phone numbers).

The mask keeps the text length unchanged, so line numbers and character positions
stay valid for every later stage (quotes, line references, the report).
"""
from __future__ import annotations

import re

# Card numbers: 13–19 contiguous digits, or the usual groupings 4-4-4-4(-1..3) and 4-6-5 (Amex),
# separated consistently by a space or a dash.
_CARD_RE = re.compile(
    r"(?<![\w*])(?:"
    r"\d{13,19}"
    r"|\d{4}([ \-])\d{4}\1\d{4}\1\d{4}(?:\1\d{1,3})?"
    r"|\d{4}([ \-])\d{6}\2\d{5}"
    r")(?![\w*])(?![ \-]\d)"
)
_IIN_RE = re.compile(r"(?<![\w*])(?<!\d[ \-])\d{12}(?![\w*])(?![ \-]\d)")


# E-mail: the local part is masked (first character kept), the domain stays.
_EMAIL_RE = re.compile(r"(?<![\w.%+\-@])([A-Za-z0-9][A-Za-z0-9._%+\-]*)@([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,})(?![\w@])")
# Phones, only in an unmistakable form: «+7» and ten digits, «8» with the 3-3-2-2 grouping, or «+» and 10–14 digits.
_SEP = r"[ \-\u00a0]?"
_PHONE_RES = [
    re.compile(rf"(?<![\w+])\+7{_SEP}\(?\d{{3}}\)?{_SEP}\d{{3}}{_SEP}\d{{2}}{_SEP}\d{{2}}(?!\d)"),
    re.compile(r"(?<![\w+])8(?:[ \-\u00a0]?\(\d{3}\)[ \-\u00a0]?|[ \-\u00a0]\d{3}[ \-\u00a0])\d{3}[ \-\u00a0]?\d{2}[ \-\u00a0]?\d{2}(?!\d)"),
    re.compile(r"(?<![\w+])\+\d(?:[ \-\u00a0().]?\d){9,13}(?!\d)"),
]
_THOUSANDS = re.compile(r"^\+?\d{1,3}(?:[ \u00a0]\d{3})+$")  # «+1 000 000 000» is an amount, not a phone


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def iin_check_ok(digits: str) -> bool:
    """Official two-pass weighted check digit of a Kazakhstan IIN/BIN."""
    if len(digits) != 12 or not digits.isdigit():
        return False
    nums = [int(c) for c in digits]
    s = sum(w * n for w, n in zip(range(1, 12), nums[:11])) % 11
    if s == 10:
        weights = [3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2]
        s = sum(w * n for w, n in zip(weights, nums[:11])) % 11
        if s == 10:
            return False
    return s == nums[11]


def iin_prefix_plausible(digits: str) -> bool:
    """IIN starts with YYMMDD + century/sex digit; BIN starts with YYMM + entity type + attribute."""
    month = int(digits[2:4])
    if not 1 <= month <= 12:
        return False
    day = int(digits[4:6])
    as_iin = 1 <= day <= 31 and digits[6] in "0123456"
    as_bin = digits[4] in "456" and digits[5] in "0123"
    return as_iin or as_bin


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _mask_digits(fragment: str, keep_head: int, keep_tail: int) -> str:
    total = sum(c.isdigit() for c in fragment)
    out, seen = [], 0
    for c in fragment:
        if c.isdigit():
            seen += 1
            out.append(c if seen <= keep_head or seen > total - keep_tail else "*")
        else:
            out.append(c)
    return "".join(out)


def mask_sensitive(text: str) -> tuple[str, list[str]]:
    """Mask card numbers (Luhn), IIN/BIN (check digit), e-mail addresses and phone numbers in an unmistakable format.
    Same length in, same length out. Amounts, ids, dates and codes are never touched."""
    if not text:
        return text, []
    chars = list(text)
    notes: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for m in _CARD_RE.finditer(text):
        frag = m.group(0)
        digits = re.sub(r"\D", "", frag)
        if not 13 <= len(digits) <= 19 or not luhn_ok(digits) or len(set(digits)) < 2:
            continue
        masked = _mask_digits(frag, 6, 4)
        chars[m.start():m.end()] = list(masked)
        taken.append((m.start(), m.end()))
        notes.append((m.start(), f"строка {_line_of(text, m.start())}: номер карты ({len(digits)} цифр)"))
    for m in _IIN_RE.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        digits = m.group(0)
        if not iin_prefix_plausible(digits) or not iin_check_ok(digits):
            continue
        chars[m.start():m.end()] = list(_mask_digits(digits, 2, 2))
        notes.append((m.start(), f"строка {_line_of(text, m.start())}: ИИН/БИН (12 цифр)"))
    for rx in _PHONE_RES:
        for m in rx.finditer(text):
            frag = m.group(0)
            digits = re.sub(r"\D", "", frag)
            if (any(a < m.end() and m.start() < b for a, b in taken) or not 10 <= len(digits) <= 14
                    or _THOUSANDS.match(frag) or len(set(digits)) < 2):
                continue
            chars[m.start():m.end()] = list(_mask_digits(frag, 2 if frag.startswith("+7") else 1, 2))
            taken.append((m.start(), m.end()))
            notes.append((m.start(), f"строка {_line_of(text, m.start())}: номер телефона ({len(digits)} цифр)"))
    for m in _EMAIL_RE.finditer(text):
        if any(a < m.end() and m.start() < b for a, b in taken):
            continue
        local = m.group(1)
        chars[m.start() + 1:m.start() + len(local)] = ["*"] * (len(local) - 1)
        taken.append((m.start(), m.end()))
        notes.append((m.start(), f"строка {_line_of(text, m.start())}: адрес электронной почты"))
    notes.sort()
    return "".join(chars), [n for _, n in notes]
