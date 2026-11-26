"""The line of ISO/IEC/IEEE 29148:2018 behind each finding.

One table: every criterion of ``model.REQ_CRITERIA`` and ``model.SET_CRITERIA`` is tied to one characteristic from the
standard's own lists: clause 5.2.5 (characteristics of an individual requirement), 5.2.6 (characteristics of a set of
requirements) or 5.2.7 (criteria for requirement language). Nothing else is quoted: no other norms, no law articles.
A rule that is about the wording of a requirement (a vague word, an open list, a comparative…) points at 5.2.7 instead
of its criterion's clause.
"""
from __future__ import annotations

from .model import Finding

NAME = "ISO/IEC/IEEE 29148:2018"

# The characteristics the standard lists, in the words a reader of the report gets.
# 5.2.5: necessary, appropriate, unambiguous, complete, singular, feasible, verifiable, correct, conforming.
# 5.2.6: complete, consistent, feasible, comprehensible, able to be validated.
# 5.2.7: vague and subjective terms, open-ended wording, comparatives, loopholes, incomplete references,
#        negative statements.
_C525 = "5.2.5"
_C526 = "5.2.6"
_C527 = "5.2.7"

# criterion → (clause, what the standard asks)
CRITERION_STANDARD: dict[str, tuple[str, str]] = {
    "completeness": (_C525, "требование должно быть полным"),
    "unambiguity": (_C525, "требование должно быть однозначным"),
    "consistency": (_C526, "набор требований должен быть непротиворечивым"),
    "verifiability": (_C525, "требование должно быть проверяемым"),
    "feasibility": (_C525, "требование должно быть реализуемым"),
    "necessity": (_C525, "требование должно быть необходимым и уместным"),
    "singularity": (_C525, "требование должно быть атомарным: одно требование — одно утверждение"),
    "set_completeness": (_C526, "набор требований должен быть полным"),
    "set_consistency": (_C526, "набор требований должен быть непротиворечивым"),
    "set_feasibility": (_C526, "набор требований должен быть реализуемым"),
    "comprehensibility": (_C526, "набор требований должен быть понятным"),
    "traceability": (_C526, "набор требований должен допускать проверку на соответствие потребности"),
}

# rule id → (clause, what the standard asks); only rules about the language of a requirement or about one named
# characteristic of the individual requirement. Every other rule follows its criterion.
RULE_STANDARD: dict[str, tuple[str, str]] = {
    "vague-word": (_C527, "в требовании не должно быть расплывчатых и субъективных слов"),
    "open-list": (_C527, "в требовании не должно быть открытых формулировок («и т. д.», «и другие»)"),
    "comparative": (_C527, "в требовании не должно быть сравнений без точки отсчёта"),
    "negative-wording": (_C527, "в требовании следует избегать отрицательных формулировок"),
    "incomplete-reference": (_C527, "в требовании не должно быть неполных ссылок"),
    "weak-modal": (_C527, "в требовании не должно быть лазеек («может», «желательно», «по возможности»)"),
    "requirement-without-criterion": (_C525, "требование должно быть проверяемым: для него нужен критерий приёмки"),
}


def line(clause: str, what: str) -> str:
    return f"{NAME}, п. {clause} — {what}"


def standard_for(criterion: str, rule_ids: list[str] | tuple[str, ...] = ()) -> str:
    """The one Russian line for a criterion (and the rules that fired); "" when nothing is known about it."""
    for r in rule_ids:
        if r in RULE_STANDARD:
            return line(*RULE_STANDARD[r])
    if criterion in CRITERION_STANDARD:
        return line(*CRITERION_STANDARD[criterion])
    return ""


def standard_of(f: Finding) -> str:
    """The line for a finding: the rule's own clause when the rule is about wording, else the criterion's."""
    ids = list(f.rule_ids) or ([f.source[5:]] if f.source.startswith("rule:") else [])
    return standard_for(f.criterion, ids)


def apply(findings: list[Finding]) -> None:
    for f in findings:
        f.standard = standard_of(f)
