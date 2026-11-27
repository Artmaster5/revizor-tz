"""Deterministic checks. Generic: nothing here knows any particular document."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..merge import absorbed_record, rule_blocker
from ..model import SEVERITIES, Finding, Requirement, Skipped, SourceDoc
from ._common import Ctx
from .advisory_rules import ADVISORY_RULES
from .api_rules import API_RULES
from .composite_rules import COMPOSITE_RULES
from .crosscheck_rules import CROSSCHECK_RULES
from .glossary_rules import GLOSSARY_RULES
from .safety_rules import SAFETY_RULES
from .set_rules import SET_RULES
from .text_rules import TEXT_RULES
from .time_rules import TIME_RULES
from .value_rules import VALUE_RULES

RULESET_VERSION = "1.2"


@dataclass
class RuleInfo:
    id: str
    criterion: str
    severity: str  # default severity (a rule may raise or lower it for a particular finding)
    scope: str  # "requirement" | "set"
    title: str  # Russian, what the rule checks
    advisory: bool = False


_R = RuleInfo
RULES: dict[str, RuleInfo] = {r.id: r for r in [
    # wording of a single requirement
    _R("vague-word", "unambiguity", "MAJOR", "requirement", "Размытые слова без измеримого значения"),
    _R("open-list", "completeness", "MINOR", "requirement", "Незакрытые перечни («и т.д.», «и другие»)"),
    _R("placeholder", "completeness", "MINOR", "requirement", "Заглушки вместо значений (TBD, «уточнить», «—»)"),
    _R("time-without-conditions", "verifiability", "MAJOR", "requirement", "Срок без доли случаев и нагрузки"),
    _R("non-atomic", "singularity", "MINOR", "requirement", "Несколько требований в одном пункте"),
    _R("incomplete-reference", "unambiguity", "MINOR", "requirement", "Ссылка на документ без точного адреса"),
    _R("number-without-unit", "unambiguity", "MAJOR", "requirement", "Время или срок без единицы измерения"),
    # whole document
    _R("missing-ids", "traceability", "MINOR", "set", "Требования без номеров"),
    _R("unit-mix", "set_consistency", "MAJOR", "set", "Одна величина в разных единицах без правила пересчёта"),
    _R("empty-section", "set_completeness", "MAJOR", "set", "Пустые разделы и разделы-заглушки"),
    _R("empty-table", "set_completeness", "MINOR", "set", "Незаполненные таблицы"),
    _R("unfilled-checklist", "set_completeness", "MINOR", "set", "Незаполненный чек-лист ревью"),
    _R("no-version", "traceability", "MINOR", "set", "Нет версии и даты документа"),
    _R("dangling-reference", "set_consistency", "MINOR", "set", "Ссылки на несуществующие разделы, таблицы, требования"),
    _R("duplicate", "set_consistency", "MINOR", "set", "Повторяющиеся требования"),
    _R("flow-happy-path", "set_completeness", "MAJOR", "set", "Схема процесса только с успешным путём"),
    _R("title-mismatch", "comprehensibility", "MINOR", "set", "Название в заголовке и в сведениях о документе расходятся"),
    # HTTP API
    _R("get-mutation", "set_feasibility", "CRITICAL", "set", "GET для изменяющего запроса (RFC 9110 §9.2.1)"),
    _R("error-in-success", "set_consistency", "CRITICAL", "set", "Ответ с ошибкой под успешным кодом 2xx"),
    _R("status-mismatch", "set_consistency", "MAJOR", "set", "Коды ответов: упомянутые, но не описанные или перепутанные"),
    _R("param-type-mismatch", "set_consistency", "MAJOR", "set", "Один параметр с разными типами в разных таблицах"),
    _R("content-type-without-body", "set_consistency", "MINOR", "set", "Content-Type у запроса без тела"),
    _R("sensitive-in-query", "set_feasibility", "MAJOR", "set", "Чувствительные данные в строке запроса"),
    _R("api-reliability", "set_completeness", "MINOR", "set", "Интерфейс без лимитов, тайм-аутов, идемпотентности или версии"),
    # defects made of several parts (one finding names every missing part)
    _R("refund-checklist", "set_completeness", "MAJOR", "set", "Возврат меняет считаемую величину, а правила возврата описаны не полностью"),
    _R("error-body-shapes", "set_consistency", "MAJOR", "set", "Тело ошибки в разных примерах устроено по-разному"),
    _R("error-code-unlinked", "set_consistency", "MINOR", "set", "Коды ошибок без условия или не сходятся с методами"),
    _R("api-access-codes", "set_completeness", "MINOR", "set", "Интерфейс с токеном и идентификатором в пути без кодов 401/403/404"),
    _R("api-ownership", "set_feasibility", "MAJOR", "set", "Метод по идентификатору объекта без проверки права вызывающего"),
    _R("list-no-order-limit", "set_completeness", "MINOR", "set", "Метод возвращает список без порядка и ограничения размера"),
    _R("notification-checklist", "set_completeness", "MINOR", "set", "Уведомления без текста, языка, сбоя доставки, повторов, времени или отключения"),
    _R("enum-no-values", "set_completeness", "MINOR", "set", "«Одно из значений» без перечня значений"),
    _R("param-table-no-required", "set_completeness", "MINOR", "set", "Таблица параметров запроса без признака «обязательный»"),
    # manipulation and data protection
    _R("prompt-injection", "set_feasibility", "MAJOR", "set", "Текст, обращённый к проверяющему или ИИ"),
    _R("hidden-text", "set_feasibility", "MINOR", "set", "Скрытый текст: HTML-комментарии и невидимые символы"),
    _R("sensitive-data", "set_feasibility", "MAJOR", "set", "Похожие на настоящие номера карт и ИИН"),
    # time, periods, quantities and thresholds (one grouped finding per rule per document)
    _R("time-zone-missing", "set_completeness", "MINOR", "set", "Время или граница дня без часового пояса"),
    _R("period-boundary", "set_completeness", "MINOR", "set", "Срок или период без «включительно / не включая»"),
    _R("days-without-kind", "set_completeness", "MINOR", "set", "Срок в днях без «календарных / рабочих»"),
    _R("month-end-day", "set_completeness", "MINOR", "set", "Ежемесячное действие в день 29–31 без правила для коротких месяцев"),
    _R("period-undefined", "set_completeness", "MINOR", "set", "Лимит «в месяц» или «в сутки» без определения периода"),
    _R("relative-period", "set_completeness", "MINOR", "set", "«Прошлый», «закрытый», «отчётный» период без определения"),
    _R("rounding-missing", "set_completeness", "MAJOR", "set", "Расчёт денег (процент, доля, курс) без правила округления"),
    _R("value-without-unit", "set_completeness", "MINOR", "set", "Число без единицы: сумма без валюты, срок без единицы, размер без единицы"),
    _R("step-missing", "set_completeness", "MINOR", "set", "Настраиваемое значение в диапазоне без шага"),
    _R("threshold-conflict", "set_consistency", "MAJOR", "set", "Одна граница описана по-разному («более N» и «от N»)"),
    _R("limit-no-behaviour", "set_completeness", "MINOR", "set", "Лимит без описания, что происходит при превышении"),
    # terms (one grouped finding per rule per document)
    _R("abbreviation-undefined", "comprehensibility", "MINOR", "set", "Сокращения без расшифровки"),
    _R("term-defined-twice", "set_consistency", "MINOR", "set", "Один термин определён в двух местах по-разному"),
    _R("condition-undefined", "comprehensibility", "MINOR", "set", "Условие зависит от понятия, которое нигде не определено"),
    # one document against itself: value sets, examples against tables, numbers against rules
    _R("set-members-differ", "set_consistency", "MAJOR", "set", "Один перечень (статусы, роли, коды) в двух местах с разным составом"),
    _R("example-field-mismatch", "set_consistency", "MINOR", "set", "Поля в примере и в таблице полей не совпадают"),
    _R("parameter-two-values", "set_consistency", "MAJOR", "set", "Один параметр назван двумя разными числами"),
    _R("example-arithmetic", "set_consistency", "MAJOR", "set", "Расчёт в примере не сходится при пересчёте"),
    _R("example-out-of-range", "set_consistency", "MINOR", "set", "Пример нарушает диапазон или формат своего параметра"),
    _R("requirement-without-criterion", "traceability", "MINOR", "set", "Требования без критерия приёмки, когда критерии ссылаются на номера"),
    # advisory observations (low precision, never affect the verdict)
    _R("negative-wording", "unambiguity", "MINOR", "requirement", "Отрицательная формулировка", True),
    _R("vague-pronoun", "unambiguity", "MINOR", "requirement", "Местоимение без ясного предмета", True),
    _R("comparative", "verifiability", "MINOR", "requirement", "Сравнение без точки отсчёта", True),
    _R("passive-no-actor", "unambiguity", "MINOR", "requirement", "Страдательный залог без исполнителя", True),
    _R("universal-quantifier", "verifiability", "MINOR", "requirement", "Обобщения «все», «всегда», «любой»", True),
    _R("and-or", "unambiguity", "MINOR", "requirement", "Двусмысленное «и/или»", True),
    _R("weak-modal", "unambiguity", "MINOR", "requirement", "Неясно, обязательно ли («может», «желательно»)", True),
    _R("example-in-requirement", "completeness", "MINOR", "requirement", "Пример вместо полного перечня", True),
]}

_IMPL = {**TEXT_RULES, **SET_RULES, **API_RULES, **COMPOSITE_RULES, **SAFETY_RULES, **TIME_RULES, **VALUE_RULES, **GLOSSARY_RULES, **CROSSCHECK_RULES, **ADVISORY_RULES}
assert set(_IMPL) == set(RULES), set(_IMPL) ^ set(RULES)

last_errors: list[str] = []


def rule_counts() -> tuple[int, int]:
    """(main rules, advisory observations), counted from the registry."""
    adv = sum(1 for r in RULES.values() if r.advisory)
    return len(RULES) - adv, adv


def run_rules(doc: SourceDoc, requirements: list[Requirement], skipped: list[Skipped]) -> list[Finding]:
    """Run every rule. Never raises; a failing rule is skipped and recorded in ``last_errors``."""
    last_errors.clear()
    try:
        ctx = Ctx.build(doc, requirements, skipped)
    except Exception as e:  # pragma: no cover - defensive
        last_errors.append(f"правила не запущены: {type(e).__name__}: {e}")
        return []
    order = {rid: k for k, rid in enumerate(RULES)}
    found: list[Finding] = []
    for rid, info in RULES.items():
        try:
            res = _IMPL[rid](ctx, info) or []
        except Exception as e:
            last_errors.append(f"правило {rid} не выполнено: {type(e).__name__}: {e}")
            continue
        for f in res:
            if not f.quotes or f.severity not in SEVERITIES:
                continue
            f.advisory = f.advisory or info.advisory  # a rule may mark a single weak finding as an observation
            f.blocker_reason = rule_blocker(f) if f.severity == "CRITICAL" else ""
            found.append(f)
    found = _drop_echoes(found)
    found.sort(key=lambda f: (f.quotes[0].line_start, order.get(f.rule_ids[0], 99)))
    return found


def _drop_echoes(found: list[Finding]) -> list[Finding]:
    """Drop an advisory observation that repeats a normal finding on the same words («всегда» inside «всегда доступен»)."""
    normal = [f for f in found if not f.advisory]
    out = []
    for f in found:
        if f.advisory:
            m = re.search(r"«([^»]+)»", f.title)
            word = m.group(1).lower() if m else ""
            twin = next((n for n in normal if word and word in n.title.lower()
                         and any(q.line_start <= fq.line_end and fq.line_start <= q.line_end
                                 for q in n.quotes for fq in f.quotes)), None)
            if twin is not None:
                rec = absorbed_record(f, "code")  # the dropped observation stays visible on the survivor
                if not any(x.get("title") == rec["title"] and x.get("by") == "code" for x in twin.absorbed):
                    twin.absorbed.append(rec)
                continue
        out.append(f)
    return out


__all__ = ["RULES", "RULESET_VERSION", "RuleInfo", "run_rules", "last_errors", "rule_counts"]
