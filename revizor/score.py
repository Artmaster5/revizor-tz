"""Clarity index of a requirement (1–10) and readiness index of the document (0–100). Pure code."""
from __future__ import annotations

from .model import Finding, Requirement
from .textfmt import dec

REQ_WEIGHT = {"CRITICAL": 6.5, "MAJOR": 2.5, "MINOR": 0.75}
SET_PENALTY = {"CRITICAL": 8, "MAJOR": 3, "MINOR": 1}
SET_PENALTY_CAP = 40
NO_BLOCKER_FLOOR = 4.0  # the lowest band is reserved for requirements with an open blocker linked to them

CLARITY_BANDS = (  # (lower bound, label)
    (9.0, "ясное"),
    (7.0, "есть замечания"),
    (4.0, "нужна доработка"),
    (0.0, "в таком виде использовать нельзя"),
)
READINESS_BANDS = (
    (85, "готов"),
    (60, "готов после правок"),
    (40, "нужна существенная доработка"),
    (0, "не готов"),
)


def counted(findings: list[Finding]) -> list[Finding]:
    """Findings that count: open and not advisory."""
    return [f for f in findings if f.status == "open" and not f.advisory]


def clarity_of(rid: str, findings: list[Finding]) -> float:
    """10 − weights of the findings linked to the requirement (set level at half weight), not below 1;
    without an open CRITICAL linked to it, not below 4 (the lowest band needs a blocker)."""
    penalty = 0.0
    blocker = False
    for f in counted(findings):
        if rid not in f.req_ids:
            continue
        w = REQ_WEIGHT.get(f.severity, 0.0)
        penalty += w if f.scope == "requirement" else w / 2
        blocker = blocker or f.severity == "CRITICAL"
    return round(max(1.0 if blocker else NO_BLOCKER_FLOOR, 10.0 - penalty), 1)


def clarity_band(value: float | None) -> str:
    if value is None:
        return ""
    for lo, label in CLARITY_BANDS:
        if value >= lo:
            return label
    return CLARITY_BANDS[-1][1]


def readiness_band(value: int | None) -> str:
    if value is None:
        return ""
    for lo, label in READINESS_BANDS:
        if value >= lo:
            return label
    return READINESS_BANDS[-1][1]


def clarities(requirements: list[Requirement], findings: list[Finding]) -> dict[str, float]:
    return {r.rid: clarity_of(r.rid, findings) for r in requirements}


def readiness(clarity: dict[str, float], findings: list[Finding]) -> tuple[int | None, str]:
    """(index 0–100, Russian formula with the numbers substituted)."""
    set_f = [f for f in counted(findings) if f.scope == "set"]
    c = sum(1 for f in set_f if f.severity == "CRITICAL")
    m = sum(1 for f in set_f if f.severity == "MAJOR")
    n = sum(1 for f in set_f if f.severity == "MINOR")
    raw_pen = SET_PENALTY["CRITICAL"] * c + SET_PENALTY["MAJOR"] * m + SET_PENALTY["MINOR"] * n
    pen = min(SET_PENALTY_CAP, raw_pen)
    if not clarity:
        return None, "индекс не считается: в документе не найдено требований"
    mean = sum(clarity.values()) / len(clarity)
    value = int(round(max(0.0, 10 * mean - pen)))
    pen_text = f"8×{c} + 3×{m} + 1×{n} = {raw_pen}"
    if raw_pen > SET_PENALTY_CAP:
        pen_text += f", но не больше {SET_PENALTY_CAP}"
    formula = (f"10 × {dec(mean, 2)} (средняя ясность {len(clarity)} треб.) − {pen} "
               f"(штраф за замечания к набору: {pen_text}) = {value}")
    return value, formula


def band_distribution(clarity: dict[str, float]) -> list[tuple[str, int]]:
    out = []
    for _lo, label in CLARITY_BANDS:
        out.append((label, sum(1 for v in clarity.values() if clarity_band(v) == label)))
    return out
