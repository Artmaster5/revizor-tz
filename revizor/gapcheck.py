"""The critic pass «чего не хватает»: a fixed checklist the model must answer item by item, so that no typical gap
is skipped silently. The checklist is data; the model answers every item (na / covered / gap); code checks the answer,
turns gaps into ordinary findings and later links every cell to the finding that survived merging and the skeptic.

Generic: no item names any particular document. The map never changes indices or the verdict by itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .merge import QuoteStats, model_finding
from .model import DroppedQuote, Finding, GapCell, SourceDoc
from .prompts import GAPS_NAME, GAPS_STAGE
from .quotes import QuoteIndex
from .textfmt import shorten


@dataclass(frozen=True)
class GapItem:
    id: str
    group: str
    title: str  # short, for the report table
    ask: str  # the full question to the document, for the prompt
    criterion: str  # one of SET_CRITERIA
    max_severity: str  # "MAJOR" | "MINOR"
    rule_ids: tuple[str, ...] = ()  # deterministic rules that answer the same item
    needs: str = ""  # "" | "api": applies only when the document describes a program interface


G_STATE = "Состояния"
G_PATHS = "Отказы и альтернативные пути"
G_VALUES = "Значения, деньги и время"
G_IFACE = "Интерфейсы между системами"
G_DATA = "Данные и экран"
G_DOC = "Согласованность документа"
G_TRACE = "Адресуемость и проверяемость"
G_PERSONAL = "Данные клиентов и карт"
GROUPS = (G_STATE, G_PATHS, G_VALUES, G_IFACE, G_DATA, G_DOC, G_TRACE, G_PERSONAL)
GROUP_NOTE = {
    G_PERSONAL: ("Вопросы для службы безопасности и комплаенса по Закону РК «О персональных данных и их защите» и "
                 "стандарту PCI DSS. Это не юридическое заключение."),
}

_I = GapItem
ITEMS: tuple[GapItem, ...] = (
    # Состояния
    _I("ST1", G_STATE, "Состояния переключателей и статусов",
       "Перечисли все переключатели, режимы, статусы, признаки и флаги. Для каждого: описано ли поведение системы в каждом "
       "его состоянии — в том числе что система продолжает и что перестаёт делать, пока функция выключена или объект в "
       "нерабочем статусе.", "set_completeness", "MAJOR"),
    _I("ST2", G_STATE, "Смена состояния посреди периода",
       "Что происходит с уже накопленными данными, счётчиками и признаками, когда настройку включают, выключают или меняют "
       "посреди периода или процесса; когда признак «уже сделано» сбрасывается; что при повторном сохранении того же "
       "значения.", "set_completeness", "MAJOR"),
    _I("ST3", G_STATE, "Начальное состояние",
       "Значения по умолчанию для нового и для существующего клиента или объекта; первое включение; случай, когда условие "
       "правила уже выполнено в момент включения.", "set_completeness", "MAJOR"),
    # Отказы и альтернативные пути
    _I("NP1", G_PATHS, "Ошибки на шагах основного сценария",
       "Для каждого шага основного сценария: что происходит при ошибке, отказе, отмене, тайм-ауте, пустом или неверном "
       "вводе.", "set_completeness", "MAJOR"),
    _I("NP2", G_PATHS, "Ветки ошибок на схемах",
       "Схемы и диаграммы процессов и взаимодействий (BPMN, sequence, потоки): показаны ли ветки ошибок и альтернативные "
       "ветки, а не только успешный путь; не подменена ли схема одной строкой.", "set_completeness", "MAJOR",
       ("flow-happy-path",)),
    _I("NP3", G_PATHS, "Недоступность соседних систем",
       "Что делает система и что видит пользователь, когда соседняя или внешняя система недоступна или отвечает с "
       "опозданием: повтор, очередь, отказ; что с событием, пришедшим дважды или потерянным.", "set_completeness", "MAJOR"),
    _I("NP4", G_PATHS, "Сообщения клиенту",
       "Тексты и язык сообщений, уведомлений и ошибок для клиента и что в них подставляется; что происходит, если канал "
       "доставки выключен или получатель недоступен.", "set_completeness", "MAJOR"),
    # Значения, деньги и время
    _I("VL1", G_VALUES, "Числовые параметры: единицы и границы",
       "Перечисли параметры с числами. Для каждого: единица измерения, нижняя и верхняя граница и входят ли сами "
       "границы в допустимые значения.", "set_completeness", "MAJOR"),
    _I("VL2", G_VALUES, "Валюта и округление",
       "Дробные единицы и правило округления; операции в другой валюте: по какому курсу и на какую дату пересчитываются.",
       "set_completeness", "MAJOR"),
    _I("VL3", G_VALUES, "Время и периоды",
       "Часовой пояс; границы суток, месяца и других периодов; какая дата события берётся (совершения, обработки, "
       "списания).", "set_completeness", "MAJOR"),
    _I("VL4", G_VALUES, "Полнота перечней",
       "Если правило перечисляет виды объектов или операций, которые учитываются или исключаются, — сказано ли, что делать "
       "со всеми остальными видами, которые в перечень не попали.", "set_completeness", "MAJOR"),
    _I("VL5", G_VALUES, "Изменения задним числом",
       "Отмена, возврат, частичный возврат и корректировка операции, которая относится к уже закрытому периоду.",
       "set_completeness", "MAJOR"),
    _I("VL6", G_VALUES, "Несколько однотипных объектов",
       "Если у клиента несколько карт, счетов, устройств или пользователей: настройка и подсчёт общие или отдельные для "
       "каждого.", "set_completeness", "MAJOR"),
    # VL7 and VL8 were one question with VL1; the model answered one aspect and skipped the others, so each aspect
    # is asked separately.
    _I("VL7", G_VALUES, "Шаг и точность чисел",
       "Для каждого числа, которое вводит человек или передаёт другая система: с каким шагом (кратностью) оно "
       "принимается и с какой точностью хранится и сравнивается. Если сказано только «целое», а значения идут на "
       "тысячи и больше, проверь, сказано ли, принимается любое целое или только кратное круглому числу.",
       "set_completeness", "MAJOR"),
    _I("VL8", G_VALUES, "Значение по умолчанию",
       "Для каждой настройки и каждого поля ввода: какое значение действует, пока пользователь ничего не задал, и что "
       "показано в поле при первом открытии.", "set_completeness", "MINOR"),
    # Интерфейсы между системами
    _I("IF1", G_IFACE, "Надёжность интерфейса",
       "Ограничение частоты запросов (код 429), тайм-ауты и повторы, одновременные изменения одного объекта, "
       "идемпотентность изменяющих запросов, версия интерфейса. В checked перечисли, что из этого описано; в замечании — "
       "одним списком, чего нет.", "set_completeness", "MINOR", ("api-reliability",), "api"),
    _I("IF2", G_IFACE, "Ошибки интерфейса",
       "Единый формат ошибок, машинный код ошибки, полный и закрытый перечень кодов ответа.", "set_consistency", "MINOR",
       (), "api"),
    _I("IF3", G_IFACE, "Права доступа в интерфейсе",
       "Кто может вызвать метод; проверяется ли, что объект принадлежит вызывающему; что возвращается для чужого или "
       "несуществующего объекта.", "set_feasibility", "MAJOR", (), "api"),
    _I("IF4", G_IFACE, "Интеграции",
       "Описаны ли все взаимодействия между системами, без которых требования не выполнить: откуда приходят события и "
       "данные, куда уходят, ждёт ли вызывающая сторона результат.", "set_completeness", "MAJOR"),
    # Данные и экран
    _I("DT1", G_DATA, "Модель данных",
       "Перечисли сущности и поля, на которые опираются правила. Для каждого: есть ли оно в описании данных; названы ли "
       "тип, формат, длина, обязательность; где хранится признак, по которому система решает, что действие уже "
       "выполнено.", "set_completeness", "MAJOR"),
    _I("UI1", G_DATA, "Экран",
       "Состояния элементов экрана, в том числе недоступные; тексты ошибок ввода; подтверждение действия; поведение при "
       "сбое сети или сервиса; есть ли макеты или описание экрана, достаточное для вёрстки.", "set_completeness", "MAJOR"),
    # Согласованность документа
    _I("CN1", G_DOC, "Название задачи",
       "Задача, функция или документ называются одинаково во всех местах: в заголовке, в служебных полях, в ссылках.",
       "comprehensibility", "MINOR", ("title-mismatch",)),
    _I("CN2", G_DOC, "Перечни систем и участников",
       "Перечисли системы и участников из каждого места, где они названы (общие сведения, архитектура, схемы, интеграции, "
       "шаги). Совпадают ли перечни; кого нет в перечне затронутых систем.", "set_consistency", "MINOR"),
    _I("CN3", G_DOC, "Термины",
       "Одно понятие названо одним словом; термины, от которых зависит результат, определены.", "comprehensibility",
       "MAJOR"),
    _I("CN4", G_DOC, "Цель и требования",
       "Каждое обещание из цели обеспечено требованием; формулировка цели не обещает больше или иное, чем правила.",
       "set_consistency", "MAJOR"),
    _I("CN5", G_DOC, "Экран, функции и интерфейс",
       "Каждое поле, действие и сообщение экрана обеспечено требованием и методом интерфейса, и наоборот.", "traceability",
       "MAJOR"),
    # Адресуемость и проверяемость
    _I("TR1", G_TRACE, "Номера требований",
       "У каждого требования во всех разделах есть свой номер, на который можно сослаться; перечисли разделы без номеров.",
       "traceability", "MINOR", ("missing-ids",)),
    _I("TR2", G_TRACE, "Критерии приёмки",
       "На каждое требование есть критерий приёмки; на правила с границей — проверка границы; на отказы — проверка отказа.",
       "traceability", "MAJOR"),
    _I("TR3", G_TRACE, "Проверяемость через действие системы",
       "Требование сформулировано через то, что делает и может проверить сама система (отправила, показала, записала), а "
       "не через результат у внешнего участника, который система не контролирует («клиент получил», «пользователь "
       "увидел»).", "set_feasibility", "MAJOR"),
    _I("TR4", G_TRACE, "Нефункциональные требования",
       "Нагрузка, доступность, время ответа, сроки хранения заданы числами с условиями измерения.", "set_completeness",
       "MAJOR"),
    # Данные клиентов и карт
    _I("PD1", G_PERSONAL, "Персональные данные",
       "Какие данные клиента обрабатываются и зачем; есть ли основание или согласие; срок хранения и удаление.",
       "set_feasibility", "MINOR"),
    _I("PD2", G_PERSONAL, "Где хранятся и кому передаются данные",
       "Место хранения; передача внешним сервисам, третьим лицам и за пределы страны.", "set_feasibility", "MINOR"),
    _I("PD3", G_PERSONAL, "Данные карт",
       "Полный номер карты, срок действия и код проверки не хранятся, не показываются целиком и не попадают в журналы, "
       "адреса запросов и уведомления; номер карты маскируется.", "set_feasibility", "MAJOR"),
    _I("PD4", G_PERSONAL, "Что видно посторонним",
       "Какие суммы и данные клиента видны в уведомлении, на заблокированном экране, в SMS или письме.", "set_feasibility",
       "MAJOR"),
    _I("PD5", G_PERSONAL, "Журнал аудита и откат",
       "Кто, когда и что изменил; что в журнал не пишется; есть ли выключатель функции и порядок отката.",
       "set_completeness", "MINOR"),
)
BY_ID = {it.id: it for it in ITEMS}
assert len(BY_ID) == len(ITEMS)
assert all(it.group in GROUPS for it in ITEMS)

NO_API_NOTE = "в документе нет описания программного интерфейса"
OFFLINE_NOTE = "без модели не оценивается"
NO_ANSWER_NOTE = "модель не ответила на этот вопрос"
UNVERIFIED_QUOTE_NOTE = "цитата модели не найдена в документе"
UNVERIFIED_GAP_NOTE = "цитаты пробела не найдены в документе"
REJECTED_NOTE = "пробел не подтвердился при перепроверке"
RULE_NOTE = "ответ дало автоматическое правило"
OBSERVATION_NOTE = "перепроверка вынесла замечание в наблюдения"
MAX_CHECKED = 8
MAX_FINDINGS = 2


# --- before the model --------------------------------------------------------------------------------------


def describes_api(doc: SourceDoc) -> bool:
    """The detection the API rules use: the document describes an HTTP interface."""
    from .rules._common import describes_http_api

    try:
        return describes_http_api(doc)
    except Exception:  # pragma: no cover - defensive
        return False


def initial_cells(doc: SourceDoc) -> tuple[list[GapCell], list[GapItem]]:
    """All cells «unchecked» (API items «na» when the document has no interface) and the items to send to the model."""
    api = describes_api(doc)
    cells, send = [], []
    for it in ITEMS:
        c = GapCell(id=it.id, group=it.group, title=it.title, status="unchecked", note=OFFLINE_NOTE)
        if it.needs == "api" and not api:
            c.status, c.note = "na", NO_API_NOTE
        else:
            send.append(it)
        cells.append(c)
    return cells, send


def item_lines(items: list[GapItem]) -> list[str]:
    return [f"{it.id} · {it.group} · {it.title}: {it.ask}" for it in items]


def answered_ids(ans: Any) -> set[str]:
    out: set[str] = set()
    raw = ans.get("items") if isinstance(ans, dict) else None
    for x in raw if isinstance(raw, list) else []:
        if isinstance(x, dict) and x.get("status") in ("na", "covered", "gap"):
            out.add(str(x.get("id") or "").strip().upper())
    return out


# --- the model's answer ----------------------------------------------------------------------------------


def _cap(sev: Any, item: GapItem) -> str:
    s = str(sev or "").upper()
    s = "MAJOR" if s == "CRITICAL" else (s if s in ("MAJOR", "MINOR") else "MINOR")
    if item.max_severity == "MINOR":
        s = "MINOR"
    return s


def apply_model(ans: dict, cells: list[GapCell], sent: list[GapItem], *, qi: QuoteIndex, stats: QuoteStats,
                known: set[str], audit: list[dict], dropped: list[DroppedQuote]) -> list[Finding]:
    """Fill the cells of the sent items from the model's answer; return the gap findings (quotes verified).

    Unknown ids are dropped; an item that did not come back is «unchecked»; «covered» with an unconfirmed quote and
    «gap» whose findings all lost their quotes are «unverified». Findings get the item's criterion, a severity capped by
    the item (never CRITICAL), no blocker reason, and the item id in ``gap_ids``."""
    by_id = {c.id: c for c in cells}
    sent_ids = {it.id for it in sent}
    seen: set[str] = set()
    out: list[Finding] = []
    for x in (ans or {}).get("items") or []:
        if not isinstance(x, dict):
            continue
        iid = str(x.get("id") or "").strip().upper()
        if iid not in sent_ids:
            audit.append({"type": "decision", "what": "gap_unknown_id", "id": iid[:20]})
            continue
        if iid in seen:
            continue
        status = x.get("status")
        if status not in ("na", "covered", "gap"):
            continue
        seen.add(iid)
        item, cell = BY_ID[iid], by_id[iid]
        cell.checked = [shorten(str(v).strip(), 60) for v in (x.get("checked") or []) if str(v).strip()][:MAX_CHECKED]
        cell.note = shorten(str(x.get("note") or "").strip(), 300)
        if status == "na":
            cell.status = "na"
        elif status == "covered":
            quote = str(x.get("quote") or "").strip()
            q = qi.find(quote) if quote else None
            if q is None:
                cell.status = "unverified"
                cell.note = cell.note or UNVERIFIED_QUOTE_NOTE
                audit.append({"type": "decision", "what": "gap_quote_unverified", "id": iid})
            else:
                stats.confirmed += 1
                if not q.exact:
                    stats.restored += 1
                cell.status, cell.line = "covered", q.line_start
        else:
            got: list[Finding] = []
            for raw in [r for r in (x.get("findings") or []) if isinstance(r, dict)][:MAX_FINDINGS]:
                raw = dict(raw, severity=_cap(raw.get("severity"), item), blocker="none", criterion=item.criterion)
                f, dq = model_finding(raw, scope="set", stage=GAPS_STAGE, qi=qi, stats=stats, known_rids=known,
                                      audit=audit)
                if f is not None:
                    f.criterion = item.criterion
                    f.gap_ids = [iid]
                    got.append(f)
                elif dq is not None:
                    dropped.append(dq)
                    audit.append({"type": "decision", "what": "quote_dropped", "stage": GAPS_STAGE, "title": dq.title})
            if got:
                cell.status = "gap"
                out += got
            else:
                cell.status = "unverified"
                cell.note = UNVERIFIED_GAP_NOTE + (f"; {cell.note}" if cell.note else "")
    for it in sent:
        if it.id not in seen:
            c = by_id[it.id]
            c.status, c.note = "unchecked", NO_ANSWER_NOTE
    return out


# --- after the skeptic and the merge of duplicates ---------------------------------------------------------


def resolve(cells: list[GapCell], findings: list[Finding]) -> None:
    """Link each cell to the open finding that carries its gap (merges carry ``gap_ids`` to the survivor); a gap all of
    whose findings were rejected becomes «rejected». A cell answered by a rule is a gap whenever that rule produced an
    open finding, whatever the model said."""
    open_c = [f for f in findings if f.status == "open" and not f.advisory]
    for cell in cells:
        item = BY_ID.get(cell.id)
        if item is None:
            continue
        if cell.status == "gap":
            linked = [f.fid for f in open_c if cell.id in f.gap_ids]
            observed = [f.fid for f in findings if f.status == "open" and f.advisory and cell.id in f.gap_ids]
            if linked:
                cell.finding_ids = linked
            elif observed:  # the re-check moved the gap's finding to the observations: the link follows it there
                cell.finding_ids, cell.note = observed, OBSERVATION_NOTE
            elif any(cell.id in f.gap_ids for f in findings):
                cell.status, cell.note, cell.finding_ids = "rejected", REJECTED_NOTE, []
            else:
                cell.status, cell.note = "unverified", UNVERIFIED_GAP_NOTE
        if item.rule_ids:
            by_rule = [f.fid for f in open_c if set(f.rule_ids) & set(item.rule_ids)]
            if by_rule:
                if cell.status != "gap":
                    cell.note = RULE_NOTE
                cell.status = "gap"
                cell.finding_ids = list(dict.fromkeys(cell.finding_ids + by_rule))
        cell.finding_ids = sorted(cell.finding_ids)


def totals(cells: list[GapCell]) -> dict[str, int]:
    return {
        "covered": sum(1 for c in cells if c.status in ("covered", "rejected")),
        "gap": sum(1 for c in cells if c.status == "gap"),
        "na": sum(1 for c in cells if c.status == "na"),
        "unchecked": sum(1 for c in cells if c.status in ("unchecked", "unverified")),
    }


__all__ = ["GAPS_STAGE", "GAPS_NAME", "GapItem", "ITEMS", "BY_ID", "GROUPS", "GROUP_NOTE", "initial_cells",
           "item_lines", "answered_ids", "apply_model", "resolve", "totals", "describes_api"]
