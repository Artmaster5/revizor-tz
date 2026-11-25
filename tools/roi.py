"""Калькулятор окупаемости «Ревизора ТЗ».

Переводит вопрос «сколько экономит агент» в арифметику, которую банк может пересчитать со своими числами.
Только стандартная библиотека, без сети. Каждое значение по умолчанию печатается вместе с источником.

Примеры:
    python tools/roi.py --report reports/report.json
    python tools/roi.py --report reports/report.json --pages-per-doc 12 --hourly-rate-kzt 3000 --markdown
    python tools/roi.py --run-cost-usd 0.19 --run-seconds 710 --saved-share 0.3 --json

Коды возврата: 0 — расчёт напечатан; 2 — неверные параметры или нечитаемый отчёт.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

# --- Значения по умолчанию и их происхождение -------------------------------------------------------------

USD_KZT = 458.28
USD_KZT_ORIGIN = ("официальный курс Национального банка РК на 25.11.2026 (nationalbank.kz, первоисточник); "
                  "курс меняется каждый день")

DOCS_PER_MONTH = 20
DOCS_ORIGIN = "допущение для примера; замените числом документов вашей команды"

PAGES_PER_DOC = 5.0
PAGES_ORIGIN = "допущение для примера; страница — техническая часть без титула и оглавления, как в стандарте NASA"

REVIEWERS = 3
REVIEWERS_ORIGIN = "NASA-STD-8739.9 (2013), п. 5.2.1: в инспекции не меньше трёх человек (первоисточник)"

PACE = 15.0
PACE_ORIGIN = ("NASA-STD-8739.9 (2013), п. 7.4.5: для требований «не более 15 страниц в час» — верхняя граница "
               "(первоисточник). Другие источники называют 1–3 страницы в час (Radice, Gilb и Graham — вторично); "
               "при таком темпе ручная проверка дороже, поэтому 15 — допущение, наименее выгодное агенту")

PREP_HOURS = 0.0
PREP_ORIGIN = "допущение: подготовку не считаем (так ручная проверка дешевле, допущение невыгодно агенту)"

SALARY_KZT = 340_278
WORK_HOURS = 164
HOURLY_RATE = round(SALARY_KZT / WORK_HOURS)
RATE_ORIGIN = (f"{SALARY_KZT:,}".replace(",", " ") + f" ₸ в месяц ÷ {WORK_HOURS} рабочих часа, округлено. Зарплата — медиана предлагаемой "
               "системному аналитику в вакансиях hh.kz, итоги 2025 года (опубл. 18.02.2026, первоисточник); "
               f"{WORK_HOURS} часа — допущение о рабочем месяце. Налоги и накладные расходы не включены")

READING_MIN = 20.0
READING_ORIGIN = "допущение: столько один человек читает отчёт агента; не замер"

SCENARIOS = (0.10, 0.25, 0.50)


class RoiError(Exception):
    """Неверный ввод: печатается по-русски, код возврата 2."""


# --- Разбор параметров -----------------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message):  # noqa: D401 - argparse hook
        sys.stderr.write(f"Ошибка в параметрах: {message}\nСправка: python tools/roi.py --help\n")
        raise SystemExit(2)


def _number(text: str) -> float:
    try:
        value = float(str(text).replace(",", ".").replace(" ", ""))
    except ValueError:
        raise argparse.ArgumentTypeError(f"нужно число, получено «{text}»") from None
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"нужно конечное число, получено «{text}»")
    return value


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="python tools/roi.py",
                description="Калькулятор окупаемости Ревизора ТЗ: цена ручной проверки, цена прогона, точка "
                            "окупаемости. Все значения по умолчанию печатаются с источником.")
    p.add_argument("--report", metavar="FILE", help="JSON-отчёт агента: берутся run.cost_usd, run.duration_s и имя документа")
    p.add_argument("--run-cost-usd", type=_number, metavar="USD", help="цена одного прогона в долларах (если нет --report)")
    p.add_argument("--run-seconds", type=_number, metavar="S", help="время одного прогона в секундах (если нет --report)")
    p.add_argument("--usd-kzt", type=_number, default=None, help=f"курс тенге за доллар (по умолчанию {USD_KZT})")
    p.add_argument("--docs-per-month", type=_number, default=None, help=f"документов в месяц (по умолчанию {DOCS_PER_MONTH})")
    p.add_argument("--pages-per-doc", type=_number, default=None, help=f"страниц в документе (по умолчанию {PAGES_PER_DOC:g})")
    p.add_argument("--reviewers", type=_number, default=None, help=f"проверяющих на документ (по умолчанию {REVIEWERS})")
    p.add_argument("--pace", type=_number, default=None, help=f"страниц в час при ручной проверке (по умолчанию {PACE:g})")
    p.add_argument("--prep-hours", type=_number, default=None,
                   help="часов подготовки на проверяющего на документ (по умолчанию 0)")
    p.add_argument("--hourly-rate-kzt", type=_number, default=None, help=f"стоимость часа аналитика, ₸ (по умолчанию {HOURLY_RATE})")
    p.add_argument("--report-reading-minutes", type=_number, default=None,
                   help=f"минут на чтение отчёта агента одним человеком (по умолчанию {READING_MIN:g})")
    p.add_argument("--saved-share", type=_number, default=None,
                   help="доля ручной проверки, которую убирает агент, от 0 до 1. Значения по умолчанию нет: её никто "
                        "не измерял. Без неё печатаются три сценария")
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument("--markdown", action="store_true", help="напечатать как фрагмент Markdown")
    fmt.add_argument("--json", action="store_true", help="напечатать как JSON")
    return p


def _param(value, given, default, origin):
    if given is None:
        return {"value": default, "origin": "по умолчанию: " + origin}
    return {"value": given, "origin": "задано вами"}


def read_report(path: str) -> dict:
    """Возвращает {'cost_usd', 'seconds', 'doc', 'model', 'words', 'has_cost'} из JSON-отчёта агента."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise RoiError(f"файл отчёта не найден: {path}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RoiError(f"отчёт {path} не читается как JSON ({e.__class__.__name__})") from None
    run = data.get("run") if isinstance(data, dict) else None
    if not isinstance(run, dict):
        raise RoiError(f"в отчёте {path} нет блока run — это не JSON-отчёт Ревизора ТЗ")

    def num(key):
        v = run.get(key)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None

    cost = num("cost_usd")
    return {
        "cost_usd": cost or 0.0,
        "has_cost": bool(cost),
        "seconds": num("duration_s"),
        "doc": str(run.get("doc_title") or run.get("doc_name") or Path(path).name),
        "model": str(run.get("model") or "") or "без модели",
        "words": int(num("doc_words") or 0),
    }


def collect(args) -> dict:
    """Проверяет ввод и собирает параметры с указанием происхождения."""
    params: dict[str, dict] = {}
    rep = read_report(args.report) if args.report else None

    if args.run_cost_usd is not None:
        params["run_cost_usd"] = {"value": args.run_cost_usd, "origin": "задано вами"}
    elif rep is not None:
        params["run_cost_usd"] = {"value": rep["cost_usd"],
                                  "origin": f"замер: run.cost_usd из {args.report}" if rep["has_cost"]
                                  else f"в {args.report} цены нет или она 0: прогон без модели бесплатен"}
    else:
        raise RoiError("укажите --report <отчёт.json> или --run-cost-usd <цена прогона в долларах>")

    if args.run_seconds is not None:
        params["run_seconds"] = {"value": args.run_seconds, "origin": "задано вами"}
    elif rep is not None and rep["seconds"] is not None:
        params["run_seconds"] = {"value": rep["seconds"], "origin": f"замер: run.duration_s из {args.report}"}
    else:
        params["run_seconds"] = {"value": None, "origin": "не задано"}

    params["usd_kzt"] = _param(None, args.usd_kzt, USD_KZT, USD_KZT_ORIGIN)
    params["docs_per_month"] = _param(None, args.docs_per_month, DOCS_PER_MONTH, DOCS_ORIGIN)
    params["pages_per_doc"] = _param(None, args.pages_per_doc, PAGES_PER_DOC, PAGES_ORIGIN)
    params["reviewers"] = _param(None, args.reviewers, REVIEWERS, REVIEWERS_ORIGIN)
    params["pace"] = _param(None, args.pace, PACE, PACE_ORIGIN)
    params["prep_hours"] = _param(None, args.prep_hours, PREP_HOURS, PREP_ORIGIN)
    params["hourly_rate_kzt"] = _param(None, args.hourly_rate_kzt, HOURLY_RATE, RATE_ORIGIN)
    params["report_reading_minutes"] = _param(None, args.report_reading_minutes, READING_MIN, READING_ORIGIN)
    if args.saved_share is not None:
        params["saved_share"] = {"value": args.saved_share, "origin": "задано вами"}

    names = {"usd_kzt": "--usd-kzt", "docs_per_month": "--docs-per-month", "pages_per_doc": "--pages-per-doc",
             "reviewers": "--reviewers", "pace": "--pace", "hourly_rate_kzt": "--hourly-rate-kzt"}
    for key, flag in names.items():
        if not params[key]["value"] > 0:
            raise RoiError(f"{flag} должен быть больше нуля, получено {params[key]['value']:g}")
    for key, flag in (("prep_hours", "--prep-hours"), ("report_reading_minutes", "--report-reading-minutes"),
                      ("run_cost_usd", "--run-cost-usd")):
        if params[key]["value"] < 0:
            raise RoiError(f"{flag} не может быть отрицательным, получено {params[key]['value']:g}")
    if params["run_seconds"]["value"] is not None and params["run_seconds"]["value"] < 0:
        raise RoiError(f"--run-seconds не может быть отрицательным, получено {params['run_seconds']['value']:g}")
    if "saved_share" in params and not 0 < params["saved_share"]["value"] <= 1:
        raise RoiError("--saved-share — доля от 0 до 1 (например, 0.25 — четверть ручной проверки), "
                       f"получено {params['saved_share']['value']:g}")
    return {"params": params, "report": rep, "report_path": args.report or ""}


# --- Расчёт ----------------------------------------------------------------------------------------------

def compute(params: dict) -> dict:
    """Вся арифметика. На вход — словарь {имя: {'value': ...}}; деления на ноль нет: делители проверены в collect."""
    v = {k: p["value"] for k, p in params.items()}
    rate = v["hourly_rate_kzt"]
    hours_per_reviewer = v["pages_per_doc"] / v["pace"] + v["prep_hours"]
    manual_person_hours = v["reviewers"] * hours_per_reviewer
    manual_kzt = manual_person_hours * rate
    manual_minutes = manual_person_hours * 60

    run_kzt = v["run_cost_usd"] * v["usd_kzt"]
    run_in_analyst_minutes = run_kzt / rate * 60
    reading_kzt = v["report_reading_minutes"] / 60 * rate
    agent_kzt = run_kzt + reading_kzt

    break_even_minutes = run_in_analyst_minutes + v["report_reading_minutes"]
    break_even_share = break_even_minutes / manual_minutes  # manual_minutes > 0: все множители > 0

    docs = v["docs_per_month"]
    shares = [v["saved_share"]] if "saved_share" in v else list(SCENARIOS)
    scenarios = []
    for s in shares:
        saved_kzt = s * manual_kzt
        net_doc = saved_kzt - agent_kzt
        scenarios.append({"share": s, "saved_minutes_per_doc": s * manual_minutes, "net_kzt_per_doc": net_doc,
                          "net_kzt_month": net_doc * docs, "net_kzt_year": net_doc * docs * 12,
                          "net_hours_month": net_doc * docs / rate})
    return {
        "hours_per_reviewer": hours_per_reviewer,
        "manual_person_hours": manual_person_hours,
        "manual_minutes": manual_minutes,
        "manual_kzt": manual_kzt,
        "manual_elapsed_minutes": v["pages_per_doc"] / v["pace"] * 60,
        "run_kzt": run_kzt,
        "run_in_analyst_minutes": run_in_analyst_minutes,
        "reading_kzt": reading_kzt,
        "agent_kzt_per_doc": agent_kzt,
        "break_even_minutes": break_even_minutes,
        "break_even_share": break_even_share,
        "pays_back_possible": break_even_share <= 1,
        "month": {"manual_kzt": manual_kzt * docs, "agent_kzt": agent_kzt * docs, "run_kzt": run_kzt * docs},
        "year": {"manual_kzt": manual_kzt * docs * 12, "agent_kzt": agent_kzt * docs * 12, "run_kzt": run_kzt * docs * 12},
        "scenarios": scenarios,
        "scenarios_are_facts": "saved_share" in v,
    }


# --- Вывод -----------------------------------------------------------------------------------------------

def num(x: float, digits: int = 0) -> str:
    """Русская запись числа: пробел между тысячами, запятая в дробной части."""
    s = f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")
    return s.replace("-", "−")


def pct(x: float) -> str:
    return num(x * 100, 0 if abs(x * 100 - round(x * 100)) < 0.05 else 1) + " %"


def minutes(m: float) -> str:
    if m < 60:
        return f"{num(m, 1 if m < 10 else 0)} мин"
    h, rest = divmod(round(m), 60)
    return f"{h} ч {rest} мин" if rest else f"{h} ч"


PARAM_TITLES = (
    ("run_cost_usd", "Цена прогона, $"), ("run_seconds", "Время прогона, с"), ("usd_kzt", "Курс, ₸ за $"),
    ("docs_per_month", "Документов в месяц"), ("pages_per_doc", "Страниц в документе"),
    ("reviewers", "Проверяющих"), ("pace", "Темп ручной проверки, стр/ч"),
    ("prep_hours", "Подготовка, ч на проверяющего"), ("hourly_rate_kzt", "Час аналитика, ₸"),
    ("report_reading_minutes", "Чтение отчёта агента, мин"), ("saved_share", "Доля сэкономленной проверки"),
)


def _fmt_param(key, value) -> str:
    if value is None:
        return "—"
    if key == "run_cost_usd":
        return f"{value:.4g}".replace(".", ",")
    if key == "saved_share":
        return pct(value)
    return num(value, 2 if key == "usd_kzt" else (0 if float(value).is_integer() else 1))


def sections(data: dict, res: dict) -> list[tuple[str, list[str]]]:
    """Содержимое отчёта: [(заголовок, строки)], общее для текста и Markdown."""
    p = {k: x["value"] for k, x in data["params"].items()}
    rep = data["report"]
    out: list[tuple[str, list[str]]] = []

    head = []
    if rep:
        head.append(f"Отчёт: {data['report_path']} — документ «{rep['doc']}», модель: {rep['model']}.")
        if rep["words"]:
            head.append(f"Слов в документе: {num(rep['words'])}. Для сравнения: при 300 словах на страницу (оценка "
                        f"практика, вторично) это ≈ {num(rep['words'] / 300, 1)} стр. Число страниц в расчёте "
                        "задаёт --pages-per-doc.")
    if head:
        out.append(("Отчёт агента", head))
    out.append(("Параметры", [f"{title}: {_fmt_param(k, p.get(k))} — {data['params'][k]['origin']}"
                              for k, title in PARAM_TITLES if k in data["params"]]))

    out.append(("1. Ручная проверка одного документа", [
        f"Часов на проверяющего = страницы ÷ темп + подготовка = {num(p['pages_per_doc'], 1)} ÷ {num(p['pace'], 1)}"
        f" + {num(p['prep_hours'], 1)} = {num(res['hours_per_reviewer'], 2)} ч",
        f"Человеко-часы = проверяющие × часы = {num(p['reviewers'])} × {num(res['hours_per_reviewer'], 2)} = "
        f"{num(res['manual_person_hours'], 2)} ч ({minutes(res['manual_minutes'])})",
        f"Стоимость = человеко-часы × час аналитика = {num(res['manual_person_hours'], 2)} × "
        f"{num(p['hourly_rate_kzt'])} ₸ = {num(res['manual_kzt'])} ₸",
        f"Длительность встречи по темпу: {minutes(res['manual_elapsed_minutes'])}.",
    ]))

    run_lines = []
    if p["run_cost_usd"] == 0:
        run_lines.append("Прогон без модели бесплатен: $0.")
    run_lines += [
        f"Прогон: ${p['run_cost_usd']:.4f} × {num(p['usd_kzt'], 2)} = {num(res['run_kzt'])} ₸".replace(".", ","),
        f"Прогон стоит как {minutes(res['run_in_analyst_minutes'])} работы аналитика "
        f"({num(res['run_kzt'])} ₸ ÷ {num(p['hourly_rate_kzt'])} ₸ × 60).",
        f"Чтение отчёта: {minutes(p['report_reading_minutes'])} = {num(res['reading_kzt'])} ₸.",
        f"Итого расход с агентом на документ: {num(res['agent_kzt_per_doc'])} ₸.",
    ]
    if p.get("run_seconds") is not None:
        run_lines.append(f"Время прогона (машинное, человек в это время свободен): {minutes(p['run_seconds'] / 60)}.")
    out.append(("2. Прогон агента", run_lines))

    be = [
        "Сколько человеко-времени агент должен сэкономить на одном документе, чтобы окупить прогон и чтение отчёта:",
        f"порог = прогон в минутах аналитика + чтение отчёта = {minutes(res['run_in_analyst_minutes'])} + "
        f"{minutes(p['report_reading_minutes'])} = {minutes(res['break_even_minutes'])} на документ",
        f"это {pct(res['break_even_share'])} ручной проверки ({minutes(res['break_even_minutes'])} из "
        f"{minutes(res['manual_minutes'])}).",
    ]
    if not res["pays_back_possible"]:
        be.append("При этих параметрах агент не окупается, даже если уберёт всю ручную проверку: она дешевле, "
                  "чем прогон плюс чтение отчёта. Проверьте число страниц, темп и цену модели.")
    be.append("Порог не опирается ни на какое допущение об экономии: это минимум, который покажет или опровергнет пилот.")
    out.append(("3. Точка окупаемости", be))

    m, y = res["month"], res["year"]
    period = [
        f"Ручная проверка: {num(m['manual_kzt'])} ₸ в месяц, {num(y['manual_kzt'])} ₸ в год.",
        f"Агент (прогоны и чтение отчётов): {num(m['agent_kzt'])} ₸ в месяц, {num(y['agent_kzt'])} ₸ в год; "
        f"из них прогоны — {num(m['run_kzt'])} ₸ в месяц.",
        "Экономия = доля × ручная проверка − расход с агентом:" if res["scenarios_are_facts"]
        else "Экономия = доля × ручная проверка − расход с агентом. Сценарии, не факты: доля никем не измерена.",
    ]
    for s in res["scenarios"]:
        period.append(f"  {pct(s['share'])}: на документ {num(s['net_kzt_per_doc'])} ₸; "
                      f"в месяц {num(s['net_kzt_month'])} ₸ (≈ {num(s['net_hours_month'], 1)} ч аналитика); "
                      f"в год {num(s['net_kzt_year'])} ₸")
    period.append("Отрицательное число — убыток: при такой доле агент обходится дороже, чем экономит.")
    out.append((f"4. Месяц и год ({num(p['docs_per_month'])} документов в месяц)", period))

    measured = "цена и время прогона (паспорт отчёта)" if rep and rep["has_cost"] else (
        "время прогона (паспорт отчёта); прогон без модели бесплатен" if rep else "ничего: цена прогона задана вами")
    out.append(("Что здесь что", [
        f"Измерено: {measured}.",
        "Параметры (меняйте своими числами): курс, объём, страницы, проверяющие, темп, подготовка, час аналитика, "
        "время чтения отчёта.",
        "Неизвестно до пилота: какую долю ручной проверки агент убирает на самом деле. План — docs/PILOT.md.",
    ]))
    return out


def render_text(data: dict, res: dict) -> str:
    lines = ["Ревизор ТЗ — расчёт окупаемости", ""]
    for title, rows in sections(data, res):
        lines += [title, "-" * len(title)] + rows + [""]
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(data: dict, res: dict) -> str:
    lines = ["### Расчёт окупаемости", ""]
    for title, rows in sections(data, res):
        lines += [f"#### {title}", ""]
        if title == "Параметры":
            lines += ["| Параметр | Значение | Откуда |", "| :--- | :--- | :--- |"]
            for row in rows:
                name_val, origin = row.split(" — ", 1)
                name, val = name_val.split(": ", 1)
                lines.append(f"| {name} | {val} | {origin.replace('|', '/')} |")
        else:
            lines += [("- " + r.strip()) for r in rows]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_json(data: dict, res: dict) -> str:
    rep = data["report"]
    doc = {"params": data["params"], "report": ({"path": data["report_path"], **rep} if rep else None),
           "result": res,
           "notes": {"measured": ["run_cost_usd", "run_seconds"] if rep else [],
                     "unknown_until_pilot": "saved_share",
                     "scenarios_are_facts": res["scenarios_are_facts"]}}
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None, out=None) -> int:
    write = out or sys.stdout.write
    args = build_parser().parse_args(argv)
    try:
        data = collect(args)
    except RoiError as e:
        sys.stderr.write(f"Ошибка: {e}\n")
        return 2
    res = compute(data["params"])
    if args.json:
        write(render_json(data, res))
    elif args.markdown:
        write(render_markdown(data, res))
    else:
        write(render_text(data, res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
