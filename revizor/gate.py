"""Stage 7 («перепроверка»): the policy that turns the re-check's typed decisions into statuses, and the dedup pass.

The constants at the top are the policy; the lead tunes them (and the prompts in prompts.py) without touching the code
below. Decision vocabulary of the model: ``keep``; ``drop`` + ``why``; ``same`` + ``same_as``. The words of the old
re-check are accepted too: ``reject`` ≡ ``drop`` with why «misread», ``merge`` + ``merge_into`` ≡ ``same``.
"""
from __future__ import annotations

import os
from typing import Any

from .merge import (SEV_RANK, _reason_fits, _str, absorb, blocker_code, findings_overlap, is_rule_sourced,
                    more_severe_first, note_level, same_topic, sort_key)
from .model import SEVERITIES, Finding
from .textfmt import lines_text, shorten

# --- policy (data) ------------------------------------------------------------------------------------------------

HARD_WHY = ("stated", "misread")                 # the finding is wrong
SOFT_WHY = ("scope", "design", "clear", "guess", "formal")   # the finding is a nitpick
# Measured on stored runs (fable/шум/gate, 26.11): «stated» and «misread» were wrong more often than right even with a
# verified quote, and «formal» removed defects the customer's own list counts — these three never remove a finding.
EFFECT = {"stated": "keep", "misread": "keep", "scope": "advisory", "design": "advisory", "clear": "advisory",
          "guess": "advisory", "formal": "keep"}              # "reject" | "advisory" | "keep"
STATED_NEEDS_QUOTE = True     # «stated» counts only when `answer` is a verified verbatim quote of the document
SOFT_ONLY_MINOR = True        # a soft reason removes only a finding whose level after the re-check is MINOR;
SOFT_ON_HIGHER = "keep"       #   for MAJOR/CRITICAL: "lower" → keep as MINOR, "keep" → keep with the gate's level
DROP_NEEDS_EMPTY_DIFFER = ("clear",)   # these reasons are refused when the model itself filled `differ`
# A reason must fit what the finding is about, otherwise the drop is refused: «the wording is clear» says nothing about
# a missing rule or a contradiction, «a design detail» is no answer to a finding that design leaked into requirements,
# and the operational part (rollout, storage, load) belongs to the document's subject.
REASON_NOT_FOR = {"clear": ("completeness", "feasibility", "consistency", "necessity", "correctness"),
                  "design": ("necessity",),
                  "scope": ("set_feasibility",)}
MERGE_GUARD = "topic"         # "topic" → merge only if merge.same_topic(target, f); "place_or_topic" → the old condition; "none"
DEDUP_GUARD = "topic"         # the same choice for the dedup pass

# «Только главное» — an opt-in mode (``--focus`` or REVIZOR_FOCUS=1). Only in this mode a soft reason moves a finding to
# observations. Off by default: on a document full of defects most of what the re-check calls a nitpick is a defect the
# customer counts (measured on stored runs, 26.11), so by default every finding stays in the main list.
FOCUS_ENV = "REVIZOR_FOCUS"


def focus_on() -> bool:
    return os.environ.get(FOCUS_ENV, "").strip().lower() in ("1", "true", "yes", "on")


# Short Russian label of a reason, printed in the observation's line «Почему не в основном списке: …».
WHY_RU = {"stated": "документ отвечает прямо", "misread": "проверяющий неверно прочитал текст",
          "scope": "вне предмета документа", "design": "деталь проектирования",
          "clear": "формулировка ясна из текста", "guess": "предположение без опоры на текст", "formal": "оформление"}


class GateOutcome:
    def __init__(self) -> None:
        self.kept = 0
        self.rejected = 0
        self.advisory = 0
        self.merged = 0
        self.severity_changed = 0
        self.protected = 0
        self.critical_demoted = 0
        self.refused = 0  # drops the policy did not accept (the finding stayed)


def merge_allowed(target: Finding, f: Finding, guard: str) -> bool:
    """May ``f`` be folded into ``target``? ``guard``: "topic" (one defect by words or checklist item), "place_or_topic"
    (the re-check's old condition: an overlapping quote, a shared requirement or one topic), "none" (always)."""
    if guard == "none":
        return True
    if guard == "place_or_topic":
        return findings_overlap(target, f) or bool(set(target.req_ids) & set(f.req_ids)) or same_topic(target, f)
    return same_topic(target, f)


def decision_of(d: dict) -> tuple[str, str, str]:
    """(decision, why, target fid) of one answer item, the old vocabulary mapped onto the new one."""
    dec = str(d.get("decision") or "keep").strip().lower()
    why = str(d.get("why") or "").strip().lower()
    into = str(d.get("same_as") or "").strip() or str(d.get("merge_into") or "").strip()
    if dec == "reject":
        dec, why = "drop", "misread"
    elif dec == "merge":
        dec = "same"
    if dec not in ("keep", "drop", "same"):
        dec = "keep"
    return dec, why, into


def verified_answer(qi: Any, d: dict):
    """The ``answer`` of a decision as a verbatim quote of the document (a ``Quote``), or None."""
    ans = str(d.get("answer") or "").strip()
    if not ans or qi is None:
        return None
    q = qi.find(ans)
    return q if q is not None and q.exact else None


def _next_observation_no(findings: list[Finding]) -> int:
    n = 0
    for f in findings:
        if f.fid.startswith("Н-") and f.fid[2:].isdigit():
            n = max(n, int(f.fid[2:]))
    return n


def apply_gate(findings: list[Finding], decisions: list[dict], *, qi: Any = None, injection: bool,
               audit: list[dict]) -> GateOutcome:
    """Apply the re-check's decisions in place. Findings absent from the answer stay unchanged.

    Two passes: first every keep and drop (with its change of level), then the merges, so that a merged card takes the
    level of its members AS THEY STAND AFTER THE RE-CHECK; in a merge the more severe finding survives
    (``more_severe_first``). A rule-sourced finding is never removed (drop and, under a suspected injection, merge are
    ignored); under a suspected injection its severity cannot be lowered either. CRITICAL survives «keep» only with a
    valid ``blocker`` (a rule-sourced finding keeps its rule's reason); otherwise it is lowered to MAJOR.
    A finding moved to the observations gets the next free «Н-NN» id (its former id is in the audit)."""
    out = GateOutcome()
    by_id = {f.fid: f for f in findings if f.status == "open" and not f.advisory}
    reviewed: set[str] = set()
    merges: list[tuple[Finding, dict, str, str]] = []
    observations: list[tuple[Finding, dict]] = []

    def resolve(fid: str) -> Finding | None:
        seen = set()
        f = by_id.get(fid)
        while f is not None and f.status == "merged" and f.merged_into and f.merged_into not in seen:
            seen.add(f.merged_into)
            f = by_id.get(f.merged_into)
        return f if f is not None and f.status == "open" and not f.advisory else None

    def keep(f: Finding, d: dict, reason: str) -> None:
        protected = is_rule_sourced(f)
        new_sev = d.get("severity") if d.get("severity") in SEVERITIES else f.severity
        if SEV_RANK[new_sev] < SEV_RANK[f.severity] and not _reason_fits(f, reason):
            # the re-check judges the findings it was given; a higher level argued with another defect («новое
            # замечание…», a neighbouring problem at the same place) would put a blocker's label on the wrong card
            audit.append({"type": "decision", "what": "skeptic_raise_refused", "fid": f.fid, "from": f.severity,
                          "to": new_sev, "reason": reason})
            new_sev = f.severity
        blocker = blocker_code(d.get("blocker"))
        no_reason = False
        if new_sev == "CRITICAL" and not blocker:
            if protected and f.severity == "CRITICAL" and f.blocker_reason:
                blocker = f.blocker_reason  # a rule's reason stands on its own
            else:
                new_sev = "MAJOR" if f.severity == "CRITICAL" else f.severity
                no_reason = f.severity == "CRITICAL"
        if new_sev != f.severity:
            lowering = SEV_RANK[new_sev] > SEV_RANK[f.severity]
            if protected and injection and lowering:
                out.protected += 1
                audit.append({"type": "decision", "what": "rule_finding_protected", "fid": f.fid,
                              "attempt": f"severity {f.severity}→{new_sev}", "reason": reason})
            else:
                why = "не названо основание блокера" if no_reason else reason
                f.status_reason = f"уровень изменён перепроверкой: {f.severity} → {new_sev}" + (f". {why}" if why else "")
                audit.append({"type": "decision", "what": "skeptic_severity", "fid": f.fid, "from": f.severity,
                              "to": new_sev, "reason": reason})
                if no_reason:
                    audit.append({"type": "decision", "what": "critical_without_reason", "stage": "skeptic",
                                  "fid": f.fid, "title": f.title, "to": new_sev})
                    out.critical_demoted += 1
                note_level(f, "skeptic", new_sev, why)
                f.severity = new_sev
                out.severity_changed += 1
        if f.severity == "CRITICAL":
            f.blocker_reason = blocker or f.blocker_reason
        else:
            f.blocker_reason = ""
        out.kept += 1

    def refuse(f: Finding, d: dict, why: str, cause: str, reason: str, severity: str = "") -> None:
        out.refused += 1
        audit.append({"type": "decision", "what": "gate_drop_refused", "fid": f.fid, "why": why, "cause": cause,
                      "reason": reason})
        keep(f, {**d, "severity": severity} if severity else d, reason)

    def drop(f: Finding, d: dict, why: str, reason: str) -> None:
        if is_rule_sourced(f):
            out.protected += 1
            audit.append({"type": "decision", "what": "rule_finding_protected", "fid": f.fid,
                          "attempt": f"drop ({why or 'без причины'})", "reason": reason})
            keep(f, d, reason)
            return
        if why not in HARD_WHY + SOFT_WHY:
            refuse(f, d, why, "no_reason", reason)
            return
        effect = EFFECT.get(why, "keep")
        if effect not in ("reject", "advisory"):
            refuse(f, d, why, "policy_keep", reason)
            return
        if effect == "advisory" and not focus_on():
            refuse(f, d, why, "focus_off", reason)
            return
        if f.criterion in REASON_NOT_FOR.get(why, ()):
            refuse(f, d, why, "reason_does_not_fit", reason)
            return
        if why in DROP_NEEDS_EMPTY_DIFFER and str(d.get("differ") or "").strip():
            refuse(f, d, why, "differ_filled", reason)
            return
        answer = verified_answer(qi, d) if why == "stated" else None
        if why == "stated" and STATED_NEEDS_QUOTE and answer is None:
            refuse(f, d, why, "answer_not_verified", reason)
            return
        level = d.get("severity") if d.get("severity") in SEVERITIES else f.severity
        if why in SOFT_WHY and SOFT_ONLY_MINOR and level != "MINOR":
            if SOFT_ON_HIGHER == "lower":
                refuse(f, d, why, "level_lowered", reason, severity="MINOR")
            else:
                refuse(f, d, why, "level", reason)
            return
        if effect == "reject":
            f.status = "rejected"
            f.status_reason = reason or "перепроверка не подтвердила дефект"
            if answer is not None:
                f.status_reason += (f" Ответ в документе, {lines_text(answer.line_start, answer.line_end)}: "
                                    f"«{shorten(answer.text, 300)}»")
            out.rejected += 1
            audit.append({"type": "decision", "what": "skeptic_rejected", "fid": f.fid, "why": why,
                          "reason": f.status_reason})
            return
        # advisory: the finding stays open as an observation, outside the count
        if level != f.severity:
            note_level(f, "skeptic", level, reason)
            f.severity = level
            out.severity_changed += 1
        if f.severity != "CRITICAL":
            f.blocker_reason = ""
        f.advisory = True
        f.advisory_reason = WHY_RU.get(why, why) + (f" — {reason}" if reason else "")
        out.advisory += 1
        entry = {"type": "decision", "what": "gate_advisory", "fid": f.fid, "why": why, "reason": reason}
        audit.append(entry)
        observations.append((f, entry))

    # pass 1: keep and drop, with the levels
    for d in decisions:
        if not isinstance(d, dict):
            continue
        fid = str(d.get("fid") or "").strip()
        f = by_id.get(fid)
        if f is None or f.status != "open" or fid in reviewed:
            continue
        reviewed.add(fid)
        dec, why, into = decision_of(d)
        reason = _str(d.get("reason"), 400)
        if dec == "drop":
            drop(f, d, why, reason)
        elif dec == "same":
            merges.append((f, d, reason, into))
        else:
            keep(f, d, reason)

    # pass 2: merges, on the levels the re-check has just set
    for f, d, reason, into in merges:
        if f.status != "open":
            continue
        target = resolve(into)
        if target is None or target is f:
            gone_to = by_id.get(into)
            if gone_to is not None and gone_to.advisory and gone_to is not f:
                # a counted finding never disappears into an observation
                audit.append({"type": "decision", "what": "gate_merge_refused", "fid": f.fid, "into": into,
                              "cause": "target_observation", "reason": reason})
            keep(f, d, reason)
            continue
        if is_rule_sourced(f) and injection:
            out.protected += 1
            audit.append({"type": "decision", "what": "rule_finding_protected", "fid": f.fid, "attempt": "merge",
                          "reason": reason})
            keep(f, d, reason)
            continue
        if not merge_allowed(target, f, MERGE_GUARD):
            # a merge of findings about different things erases a real defect from the report: both stay
            audit.append({"type": "decision", "what": "gate_merge_refused", "fid": f.fid, "into": target.fid,
                          "cause": "guard_" + MERGE_GUARD, "reason": reason})
            keep(f, d, reason)
            continue
        survivor, gone = more_severe_first(target, f)
        if gone is target and injection and is_rule_sourced(target):
            survivor, gone = target, f  # under a suspected injection a rule's finding is never the one that goes
        if gone is target:
            audit.append({"type": "decision", "what": "skeptic_merge_swapped", "fid": f.fid, "into": target.fid,
                          "survivor": f.fid})
        absorb(survivor, gone)
        gone.status = "merged"
        gone.merged_into = survivor.fid
        gone.status_reason = reason or f"то же, что {survivor.fid}"
        out.merged += 1
        audit.append({"type": "decision", "what": "skeptic_merged", "fid": gone.fid, "into": survivor.fid,
                      "reason": gone.status_reason})
    out.kept += sum(1 for fid, f in by_id.items() if fid not in reviewed and f.status == "open" and not f.advisory)

    # observations continue the «Н-NN» numbering, in document order; the former id stays in the audit
    n = _next_observation_no(findings)
    for f, entry in sorted(observations, key=lambda x: sort_key(x[0])):
        n += 1
        f.fid = f"Н-{n:02d}"
        entry["observation"] = f.fid
    return out


def apply_dedup(findings: list[Finding], groups: list[dict], *, injection: bool, audit: list[dict]) -> int:
    """Merge the dedup call's groups in place: every member of ``same`` into ``keep``, the more severe one surviving.
    Unknown ids, findings that are not open counted ones and self-references are ignored; the guard is
    ``DEDUP_GUARD``; under a suspected injection a rule-sourced finding is never the one that goes. Returns the count."""
    by_id = {f.fid: f for f in findings if f.status == "open" and not f.advisory}

    def resolve(fid: str) -> Finding | None:
        seen = set()
        f = by_id.get(fid)
        while f is not None and f.status == "merged" and f.merged_into and f.merged_into not in seen:
            seen.add(f.merged_into)
            f = by_id.get(f.merged_into)
        return f if f is not None and f.status == "open" and not f.advisory else None

    n = 0
    for g in groups:
        if not isinstance(g, dict):
            continue
        keep_id = str(g.get("keep") or "").strip()
        why = _str(g.get("why"), 300)
        same = g.get("same") if isinstance(g.get("same"), list) else []
        for sid in same:
            target = resolve(keep_id)
            f = resolve(str(sid or "").strip())
            if target is None or f is None or f is target:
                continue
            if injection and is_rule_sourced(f) and is_rule_sourced(target):
                audit.append({"type": "decision", "what": "rule_finding_protected", "fid": f.fid, "attempt": "dedup",
                              "reason": why})
                continue
            if not merge_allowed(target, f, DEDUP_GUARD):
                audit.append({"type": "decision", "what": "dedup_merge_refused", "fid": f.fid, "into": target.fid,
                              "cause": "guard_" + DEDUP_GUARD, "reason": why})
                continue
            survivor, gone = more_severe_first(target, f)
            if injection and is_rule_sourced(gone):
                survivor, gone = gone, survivor
            absorb(survivor, gone)
            gone.status = "merged"
            gone.merged_into = survivor.fid
            gone.status_reason = (f"повтор: {why}" if why else f"повтор: то же, что {survivor.fid}")
            n += 1
            audit.append({"type": "decision", "what": "dedup_merged", "fid": gone.fid, "into": survivor.fid,
                          "reason": gone.status_reason})
    return n


__all__ = ["HARD_WHY", "SOFT_WHY", "EFFECT", "REASON_NOT_FOR", "FOCUS_ENV", "focus_on", "STATED_NEEDS_QUOTE", "SOFT_ONLY_MINOR", "SOFT_ON_HIGHER",
           "DROP_NEEDS_EMPTY_DIFFER", "MERGE_GUARD", "DEDUP_GUARD", "WHY_RU", "GateOutcome", "apply_gate",
           "apply_dedup", "merge_allowed", "decision_of", "verified_answer"]
