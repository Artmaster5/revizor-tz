"""Data contracts shared by every stage of the pipeline.

Plain dataclasses only: no logic, no I/O. Everything is JSON-serialisable through
``to_dict``. Field names are part of the public JSON report, do not rename them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# --- vocabularies -----------------------------------------------------------

SEVERITIES = ("CRITICAL", "MAJOR", "MINOR")
SEVERITY_ICON = {"CRITICAL": "🔴", "MAJOR": "🟡", "MINOR": "🟢"}

# Quality of a single requirement (ISO/IEC/IEEE 29148:2018 §5.2.5, IEEE 830-1998 §4.3).
REQ_CRITERIA = (
    "completeness",
    "unambiguity",
    "consistency",
    "verifiability",
    "feasibility",
    "necessity",
    "singularity",
)
# Quality of the whole set (ISO/IEC/IEEE 29148:2018 §5.2.6, IEEE 830-1998 §4.3).
SET_CRITERIA = (
    "set_completeness",
    "set_consistency",
    "set_feasibility",
    "comprehensibility",
    "traceability",
)
CRITERION_RU = {
    "completeness": "Полнота",
    "unambiguity": "Однозначность",
    "consistency": "Непротиворечивость",
    "verifiability": "Проверяемость",
    "feasibility": "Реализуемость",
    "necessity": "Необходимость",
    "singularity": "Атомарность",
    "set_completeness": "Полнота набора",
    "set_consistency": "Взаимная непротиворечивость",
    "set_feasibility": "Реализуемость, стандарты и безопасность",
    "comprehensibility": "Понятность и термины",
    "traceability": "Прослеживаемость",
}

BLOCK_KINDS = ("heading", "paragraph", "list_item", "table_row", "code")

REQ_KINDS = (
    "goal",
    "functional",
    "nonfunctional",
    "data",
    "integration",
    "api",
    "ui",
    "process",
    "acceptance",
    "constraint",
    "other",
)
KIND_RU = {
    "goal": "цель",
    "functional": "функциональное",
    "nonfunctional": "нефункциональное",
    "data": "данные",
    "integration": "интеграция",
    "api": "интерфейс (API)",
    "ui": "экран",
    "process": "процесс",
    "acceptance": "критерий приёмки",
    "constraint": "ограничение",
    "other": "прочее",
}

SKIP_CATEGORIES = ("heading", "meta", "explanation", "template", "placeholder", "other")
SKIP_CATEGORY_RU = {
    "heading": "заголовок",
    "meta": "сведения о документе",
    "explanation": "пояснение",
    "template": "пустой шаблон",
    "placeholder": "заглушка",
    "other": "прочее",
}

ADDRESSEES = ("business", "analyst", "architect", "security", "qa")
ADDRESSEE_RU = {
    "business": "владелец продукта (бизнес)",
    "analyst": "аналитик — автор документа",
    "architect": "архитектор или разработчик",
    "security": "информационная безопасность",
    "qa": "тестирование",
}

# What kind of gap a clarifying question closes.
GAP_TYPES = ("term", "reference", "value", "scenario", "conflict", "owner")
GAP_RU = {
    "term": "неясное слово или термин",
    "reference": "неясно, на что ссылается",
    "value": "не задано значение, единица или граница",
    "scenario": "не описан сценарий",
    "conflict": "два места говорят разное",
    "owner": "не назначен ответственный или источник",
}

# Closed list of reasons that justify CRITICAL ("none" — not a blocker). Only these make a finding a blocker.
BLOCKER_REASONS = ("contradiction", "main_flow", "protocol", "loss", "missing_part")
BLOCKER_RU = {
    "contradiction": "противоречие в документе",
    "main_flow": "не определён основной сценарий",
    "protocol": "нарушение стандарта протокола",
    "loss": "прямая потеря денег, данных или доступа",
    "missing_part": "нет обязательной части документа",
}

PRIORITIES = ("blocker", "important", "nice")
PRIORITY_RU = {"blocker": "Блокирующие", "important": "Важные", "nice": "Желательные"}

# Criterion verdict for one requirement.
#   ok        — checked, no violation
#   issue     — checked, there is a finding
#   na        — criterion does not apply to this requirement
#   unchecked — nobody checked it (offline mode or a failed model batch)
CRITERION_STATUSES = ("ok", "issue", "na", "unchecked")

VERDICTS = ("BLOCKED", "CONDITIONAL", "READY", "NOT_CHECKED")

# Loader diagnostics (the input could not be used as a requirements document).
DIAGNOSTIC_CODES = (
    "NOT_FOUND",
    "IS_DIRECTORY",
    "NO_PERMISSION",
    "READ_ERROR",
    "EMPTY_FILE",
    "ONLY_WHITESPACE",
    "TOO_SHORT",
    "TOO_LARGE",
    "BINARY",
    "WRONG_TYPE",
    "PDF_BROKEN",
    "PDF_ENCRYPTED",
    "PDF_NO_PAGES",
    "PDF_NO_TEXT",
    "DOCX_BROKEN",
    "DOC_OLD_FORMAT",
)


class _Dictable:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]


# --- source document --------------------------------------------------------


@dataclass
class Block(_Dictable):
    """One structural piece of the document, in reading order."""

    bid: str  # "B001", "B002", …
    kind: str  # one of BLOCK_KINDS
    text: str  # clean text without markup; table row → "Header1: value1 | Header2: value2"
    raw: str  # exact source lines joined with "\n"
    section: str  # heading path joined with " › " ("" before the first heading)
    line_start: int  # 1-based, inclusive, in SourceDoc.lines
    line_end: int
    page: int | None = None  # PDF page of line_start
    level: int = 0  # heading level (1..6) for kind == "heading"
    header: list[str] = field(default_factory=list)  # table_row: header cells
    cells: list[str] = field(default_factory=list)  # table_row: value cells
    is_empty: bool = False  # table_row whose value cells are all empty
    doc_id: str = ""  # identifier written in the document at the start of the block ("Ф-1", "FR-12")


@dataclass
class SourceDoc(_Dictable):
    path: str
    name: str  # file name
    fmt: str  # "md" | "txt" | "pdf" | "docx"
    size_bytes: int
    sha256: str
    text: str  # extracted text, "\n" line ends
    lines: list[str]  # text.split("\n")
    blocks: list[Block] = field(default_factory=list)
    pages: int | None = None  # PDF page count
    line_pages: list[int] | None = None  # PDF: page number for every line
    encoding: str = ""
    warnings: list[str] = field(default_factory=list)  # Russian, shown in the passport
    masked: list[str] = field(default_factory=list)  # Russian notes: which sensitive values were masked and where


@dataclass
class Diagnostic(_Dictable):
    """Why the input cannot be checked. All texts are Russian, for the report."""

    code: str  # one of DIAGNOSTIC_CODES
    title: str  # one line: what is wrong
    what_happened: str  # 1–3 sentences
    what_to_do: list[str]  # concrete steps for the user
    details: str = ""  # short technical details
    path: str = ""
    name: str = ""
    size_bytes: int | None = None
    detected_type: str = ""  # what the file really is, in Russian ("изображение PNG")


# --- results ----------------------------------------------------------------


@dataclass
class Quote(_Dictable):
    """A fragment that is guaranteed to exist in the source text."""

    text: str  # ORIGINAL source text (never the model's retyping)
    line_start: int
    line_end: int
    page: int | None = None
    section: str = ""
    rid: str = ""  # requirement that contains the fragment, if any
    exact: bool = True  # False → restored by fuzzy match


@dataclass
class Requirement(_Dictable):
    rid: str  # "REQ-001" — assigned by code in document order
    doc_id: str  # identifier in the document or ""
    section: str
    kind: str  # one of REQ_KINDS
    quote: str  # exact source text of the block(s)
    summary: str  # short wording (model) or "" (heuristic)
    block_ids: list[str]
    line_start: int
    line_end: int
    page: int | None = None
    origin: str = "model"  # "model" | "heuristic"


@dataclass
class Skipped(_Dictable):
    block_ids: list[str]
    category: str  # one of SKIP_CATEGORIES
    reason: str
    section: str
    line_start: int
    line_end: int
    text: str  # first block text (may be shortened by the renderer)
    origin: str = "model"


@dataclass
class QuestionDraft(_Dictable):
    text: str
    options: list[str] = field(default_factory=list)
    addressee: str = "analyst"  # one of ADDRESSEES
    gap: str = ""  # one of GAP_TYPES or ""


@dataclass
class Finding(_Dictable):
    scope: str  # "requirement" | "set"
    criterion: str  # one of REQ_CRITERIA (scope requirement) or SET_CRITERIA (scope set)
    severity: str  # one of SEVERITIES
    title: str
    problem: str
    recommendation: str
    quotes: list[Quote] = field(default_factory=list)  # verified; at least one for an open finding
    req_ids: list[str] = field(default_factory=list)
    question: QuestionDraft | None = None
    source: str = "model"  # "model" | "rule:<rule id>" | "both"
    rule_ids: list[str] = field(default_factory=list)
    stage: str = ""  # "rules" | "review" | "set_consistency" | "set_completeness" | "set_standards"
    fid: str = ""  # "D-001" — assigned by code after merge, in document order
    status: str = "open"  # "open" | "rejected" | "merged"
    status_reason: str = ""  # skeptic's reason for reject/merge or for a changed severity
    merged_into: str = ""
    question_id: str = ""  # "В-05" once the final questions are numbered
    closed_by_answer: bool = False  # the author's answer settles it (dialog mode); the document still has to be edited
    fixed_wording: str = ""  # corrected requirement text (from the reviewer's rewrite or from the author's answer)
    advisory: bool = False  # low-precision observation: shown separately, never counted in indices or the verdict
    blocker_reason: str = ""  # CRITICAL only: one of BLOCKER_REASONS; "" for MAJOR and MINOR
    # places of merged-away findings that lie elsewhere: shown as «см. также», never a basis for a requirement's mark
    see_also: list[Quote] = field(default_factory=list)


@dataclass
class CriterionVerdict(_Dictable):
    status: str  # one of CRITERION_STATUSES
    note: str = ""


@dataclass
class RequirementReview(_Dictable):
    rid: str
    criteria: dict[str, CriterionVerdict] = field(default_factory=dict)  # key: REQ_CRITERIA
    by_model: bool = False
    clarity: float | None = None  # clarity index 1.0–10.0, computed by code from the findings


@dataclass
class Question(_Dictable):
    qid: str  # "В-01" (Cyrillic В) — assigned by code
    text: str
    why: str
    addressee: str
    options: list[str] = field(default_factory=list)
    priority: str = "important"  # one of PRIORITIES
    finding_ids: list[str] = field(default_factory=list)
    req_ids: list[str] = field(default_factory=list)
    quote: Quote | None = None  # the place in the document the question is about
    option_effects: list[str] = field(default_factory=list)  # parallel to options: what each answer changes
    answer: str = ""  # the author's answer (dialog mode)
    answer_effect: str = ""  # what the agent did with the answer
    gap: str = ""  # one of GAP_TYPES or ""


TEST_KINDS = ("positive", "negative", "boundary")
TEST_KIND_RU = {"positive": "позитивный", "negative": "негативный", "boundary": "граничный"}


@dataclass
class TestDraft(_Dictable):
    """Draft of an acceptance test derived from the requirements (and from the gaps found)."""

    tid: str  # "Т-01" (Cyrillic Т) — assigned by code
    title: str
    kind: str  # one of TEST_KINDS
    req_ids: list[str] = field(default_factory=list)
    given: str = ""
    when: str = ""
    then: str = ""  # "" when the expected result cannot be derived from the document
    blocked_by: list[str] = field(default_factory=list)  # question ids the expected result depends on
    finding_ids: list[str] = field(default_factory=list)
    origin: str = "model"  # "model" | "rule"


@dataclass
class FixAction(_Dictable):
    action: str
    finding_ids: list[str] = field(default_factory=list)


@dataclass
class Strength(_Dictable):
    text: str
    req_ids: list[str] = field(default_factory=list)


@dataclass
class CoverageRow(_Dictable):
    acceptance_rid: str
    covers: list[str] = field(default_factory=list)


@dataclass
class DroppedQuote(_Dictable):
    """A model finding thrown away because its quote is not in the document."""

    stage: str
    title: str
    quote: str
    reason: str


@dataclass
class RunInfo(_Dictable):
    agent_name: str = "Ревизор ТЗ"
    agent_version: str = ""
    started_at: str = ""  # ISO 8601 with UTC offset
    duration_s: float = 0.0
    mode: str = "offline"  # "full" | "fast" | "offline"
    provider: str = "offline"
    model: str = ""
    effort: str = ""
    # document
    doc_name: str = ""
    doc_format: str = ""
    doc_size_bytes: int | None = None
    doc_sha256: str = ""
    doc_pages: int | None = None
    doc_lines: int = 0
    doc_chars: int = 0
    # model usage
    llm_calls: int = 0
    llm_cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None
    cost_note: str = ""
    # quality gates
    quotes_confirmed: int = 0
    quotes_restored: int = 0
    quotes_dropped: int = 0
    skeptic_kept: int = 0
    skeptic_rejected: int = 0
    skeptic_merged: int = 0
    manual_minutes_estimate: int = 0
    warnings: list[str] = field(default_factory=list)
    injection_suspected: bool = False  # the document contains text addressed to the reviewing AI
    masked_values: int = 0  # sensitive values masked before the text went anywhere
    score: int | None = None  # document readiness index 0–100, computed by code
    score_formula: str = ""  # Russian, the formula with the numbers substituted
    rules_version: str = ""  # version of the deterministic rule set used
    doc_words: int = 0
    context_files: list[str] = field(default_factory=list)  # extra context given with --context
    dup_merged: int = 0  # near-duplicate findings merged by code after the questions stage
    critical_demoted: int = 0  # model CRITICAL findings lowered to MAJOR because no valid blocker reason was given
    agent_commit: str = ""  # short git commit of the agent's folder; "" outside a git work tree
    agent_tag: str = ""  # git tag that points exactly at the agent's commit; "" when there is none
    agent_dirty: bool = False  # tracked files of the agent had uncommitted changes at run time
    doc_title: str = ""  # first heading of the document (or its first non-empty line, up to 120 characters)


CHECKLIST_VERDICTS = ("yes", "no", "unknown")
CHECKLIST_VERDICT_RU = {"yes": "Да", "no": "Нет", "unknown": "Нельзя определить по тексту"}


@dataclass
class ChecklistItem(_Dictable):
    """One item of an unfilled review checklist found inside the document, with the agent's suggested answer.

    The answer is a hint for the reviewer: it never changes findings, indices or the verdict."""

    number: int  # 1-based, in document order — assigned by code
    text: str  # the item's verbatim text from the document
    line: int  # 1-based line in SourceDoc.lines
    page: int | None = None  # PDF page of the line
    verdict: str = "unknown"  # one of CHECKLIST_VERDICTS
    basis: str = ""  # one Russian sentence: what the answer rests on
    findings: list[str] = field(default_factory=list)  # ids of open findings that support the answer
    by_model: bool = False


@dataclass
class Result(_Dictable):
    run: RunInfo
    diagnostic: Diagnostic | None = None
    requirements: list[Requirement] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    reviews: list[RequirementReview] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)  # status == "open"
    rejected: list[Finding] = field(default_factory=list)  # status "rejected" or "merged"
    dropped: list[DroppedQuote] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    fix_first: list[FixAction] = field(default_factory=list)
    strengths: list[Strength] = field(default_factory=list)
    coverage: list[CoverageRow] = field(default_factory=list)
    verdict: str = "NOT_CHECKED"  # one of VERDICTS
    verdict_text: str = ""
    verdict_reason: str = ""
    tests: list[TestDraft] = field(default_factory=list)
    verdict_after_answers: str = ""  # dialog mode: verdict once the answered findings are fixed in the document
    verdict_after_answers_text: str = ""
    audit: list[dict[str, Any]] = field(default_factory=list)  # one entry per model call and per code decision
    checklist: list[ChecklistItem] = field(default_factory=list)  # unfilled review checklist of the document, answered
