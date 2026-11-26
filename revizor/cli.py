"""Command line: python main.py --input docs/requirements.md --output reports/report.md

Exit codes: 0 — a report was written (normal or diagnostic); 1 — no report could be written or bad arguments;
3 — the --fail-on threshold was reached (the report is still written);
4 — with --fail-on critical|major the document could not be read (scan without text, broken or empty file):
a gate must not pass what was not reviewed (the diagnostic report is still written).
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Mapping

from . import (__version__, answers as ans_mod, config, exports, pipeline, report_json, report_md, sidefiles,
               verdict)
from .llm import LLMUnavailable, make_backend, normalize_provider
from .llm import choose_provider
from .model import Result
from .textfmt import count, num, usd, W_DEFECT, W_REQ

PROVIDER_CHOICES = ("auto", "anthropic", "gemini", "openai", "ollama", "offline")


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # Russian message, no SystemExit(2)
        raise UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="python main.py", add_help=True,
                description="Ревизор ТЗ — статическая проверка документа с требованиями. Пишет отчёт на русском.")
    p.add_argument("paths", nargs="*", help="вход и выход без ключей: <документ> <отчёт.md>")
    p.add_argument("--input", "-i", help="документ с требованиями (.md, .txt, .pdf, .docx, .html)")
    p.add_argument("--output", "-o", help="куда записать отчёт (.md или .json); рядом появятся .json, файл подробностей (.details.md), анкета вопросов и другие файлы")
    p.add_argument("--provider", default="auto",
                   help="auto | anthropic | gemini | openai | ollama | offline. По умолчанию auto: берётся первый найденный ключ в "
                        "порядке ANTHROPIC_API_KEY, GEMINI_API_KEY (или GOOGLE_API_KEY), OPENAI_API_KEY, OLLAMA_BASE_URL; "
                        "если ключей нет — режим без модели. LLM_PROVIDER в .env задаёт провайдера явно")
    p.add_argument("--model", help="имя модели (по умолчанию из .env или встроенное)")
    p.add_argument("--depth", choices=("full", "fast"), default="full",
                   help="full — три линзы и перепроверка (по умолчанию); fast — один проход без перепроверки")
    p.add_argument("--context", action="append", default=[], metavar="FILE",
                   help="доп. материалы: глоссарий, связанная спецификация (можно несколько раз)")
    p.add_argument("--answers", metavar="FILE",
                   help="файл-анкета с ответами автора (<отчёт>.questions.md): агент учтёт ответы и перепишет отчёт")
    p.add_argument("--interactive", action="store_true",
                   help="задать до 7 вопросов прямо в терминале (сначала блокирующие), затем учесть ответы")
    p.add_argument("--baseline", metavar="OLD.json",
                   help="JSON-отчёт прошлой проверки: показать, что исправлено, что осталось и что появилось")
    p.add_argument("--no-cache", action="store_true", help="не брать ответы модели из кэша (запись в кэш остаётся)")
    p.add_argument("--fail-on", choices=("never", "critical", "major"), default="never",
                   help="для CI: вернуть код 3, если есть дефекты этого уровня; код 4, если документ не удалось прочитать "
                        "(скан без текста, битый или пустой файл), чтобы такой документ не прошёл проверку незамеченным. "
                        "При never (по умолчанию) нечитаемый документ даёт код 0 и диагностический отчёт")
    p.add_argument("--max-cost", metavar="USD", help="потолок стоимости прогона в долларах (или REVIZOR_MAX_COST_USD)")
    p.add_argument("--estimate", action="store_true", help="только оценить число вызовов, токены и стоимость; модель не вызывается")
    p.add_argument("--quiet", "-q", action="store_true", help="печатать только итог")
    p.add_argument("--version", action="version", version=f"Ревизор ТЗ {__version__}")
    return p


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _paths(args) -> tuple[str, str]:
    inp, out = args.input, args.output
    pos = list(args.paths or [])
    if inp is None and pos:
        inp = pos.pop(0)
    if out is None and pos:
        out = pos.pop(0)
    if pos:
        raise UsageError("лишние параметры: " + " ".join(pos))
    if not inp:
        raise UsageError("не указан документ: --input путь/к/документу.md")
    if not out:
        raise UsageError("не указан файл отчёта: --output reports/report.md")
    return inp, out


AUTO_ORDER = (("anthropic", ("ANTHROPIC_API_KEY",)), ("gemini", ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
              ("openai", ("OPENAI_API_KEY",)), ("ollama", ("OLLAMA_BASE_URL",)))


def auto_choice_line(env: Mapping[str, str], backend) -> str:
    """One line for ``--provider auto``: which provider and model were taken and how to take another."""
    from .llm import is_placeholder
    found = [name for name, keys in AUTO_ORDER
             if any((env.get(k) or "").strip() and not is_placeholder(env.get(k)) for k in keys)]
    also = [n for n in found if n != backend.provider]
    forced = (env.get("LLM_PROVIDER") or "").strip().lower() not in ("", "auto")
    why = "задан в LLM_PROVIDER" if forced else "первый найденный ключ"
    return (f"Провайдер выбран автоматически ({why}): {backend.provider} / {backend.model}"
            + (f"; найдены также ключи: {', '.join(also)}" if also else "")
            + ". Другой — параметр --provider (anthropic, gemini, openai, ollama, offline).")


def no_model_call(result: Result) -> bool:
    r = result.run
    return r.provider in ("offline", "") or (not r.llm_calls and not r.llm_cache_hits)


def cost_text(result: Result) -> str:
    """Cost for the terminal: «$0 (модель не вызывалась)» whenever no model call was made."""
    return "$0 (модель не вызывалась)" if no_model_call(result) else usd(result.run.cost_usd)


def ledger_line(result: Result) -> str:
    r = result.run
    if no_model_call(result):
        return "Расход: модель не вызывалась — вызовов 0, вход 0, выход 0, чтение кэша 0, запись кэша 0, $0"
    cost = "цена неизвестна" if r.cost_usd is None else f"${r.cost_usd:.4f}"
    return (f"Расход: {r.model} ({r.provider}) — вызовов {r.llm_calls}, из кэша агента {r.llm_cache_hits}, "
            f"вход {r.input_tokens}, выход {r.output_tokens}, чтение кэша {r.cache_read_tokens}, "
            f"запись кэша {r.cache_write_tokens}, {cost}")


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None,
         out: Callable[[str], None] | None = None, backend=None,
         input_fn: Callable[[str], str] | None = None) -> int:
    """``env`` replaces os.environ (tests pass it explicitly); ``backend`` injects a ready backend (tests);
    ``input_fn`` replaces the terminal for ``--interactive`` (tests)."""
    printer = out or (lambda s: print(s, flush=True))
    try:
        return _main(argv, env, printer, backend, input_fn)
    except KeyboardInterrupt:
        printer("Прервано пользователем. Отчёт не записан.")
        return 1
    except UsageError as e:
        printer(f"Ошибка в параметрах: {e}. Справка: python main.py --help")
        return 1


def _main(argv, env_in, say, injected_backend, input_fn=None) -> int:
    args = build_parser().parse_args(argv)
    inp, outp = _paths(args)
    env = config.build_env(env_in)
    t0 = time.monotonic()
    quiet = args.quiet
    progress = (lambda _s: None) if quiet else say

    provider = normalize_provider(args.provider or "auto")
    if provider not in PROVIDER_CHOICES:
        raise UsageError(f"неизвестный провайдер «{args.provider}»; допустимо: {', '.join(PROVIDER_CHOICES)}")
    cap, cap_err = config.max_cost_from(args.max_cost, env)
    if cap_err:
        raise UsageError(cap_err)

    if args.estimate:
        return _estimate(inp, provider, args, env, say)

    backend = injected_backend
    reason = ""
    if backend is None and provider != "offline":
        try:
            backend = make_backend(provider, env, model=args.model, use_cache=not args.no_cache)
        except LLMUnavailable as e:
            chosen = provider if provider != "auto" else choose_provider(env)
            if chosen != "offline":
                reason = e.user_message
            backend = None
    if not quiet:
        if backend is None:
            say("Режим: без модели — только автоматические правила" + (f" (модель недоступна: {reason})" if reason else ""))
        else:
            say(f"Режим: {backend.provider} / {backend.model}, глубина {args.depth}"
                + (f", потолок стоимости ${cap:g}" if cap is not None else ""))
            if provider == "auto" and injected_backend is None:
                say(auto_choice_line(env, backend))

    provider_fn, interactive_note = _answer_provider(args, input_fn, say, quiet)
    result = pipeline.run(inp, backend=backend, depth=args.depth, progress=progress, contexts=args.context,
                          max_cost_usd=cap, unavailable_reason=reason, answers=provider_fn,
                          baseline_path=args.baseline or "")
    if interactive_note:
        result.run.warnings.append(interactive_note)

    # the gate for proposed rewrites now runs at the end of pipeline.run, so every path that renders cards gets it
    written = _write_all(result, inp, outp, say)
    if written is None:
        return 1
    _final_lines(result, written, time.monotonic() - t0, say)
    if result.diagnostic is not None and args.fail_on != "never":
        say(f"Документ не прочитан и не проверен, а задан --fail-on {args.fail_on}: код возврата 4, чтобы проверка не прошла.")
        return 4
    if result.diagnostic is None and verdict.fail_threshold_reached(result.findings, args.fail_on):
        say(f"Порог --fail-on {args.fail_on} достигнут: код возврата 3.")
        return 3
    return 0


def _answer_provider(args, input_fn, say, quiet):
    """(callable(questions) -> Parsed or None, note for the passport). Never raises."""
    if args.answers:
        path = Path(args.answers)

        def from_file(questions):
            try:
                if path.stat().st_size > 2_000_000:
                    raise OSError("файл больше 2 МБ")
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                parsed = ans_mod.Parsed()
                parsed.warnings.append(f"Файл ответов «{path.name}» не прочитан ({getattr(e, 'strerror', None) or e}): "
                                       "ответы не применены.")
                return parsed
            return ans_mod.parse(text, questions)

        return from_file, ""
    if args.interactive:
        if input_fn is None and not (sys.stdin and sys.stdin.isatty()):
            say("Интерактивный режим недоступен: нет терминала. Продолжаю обычную проверку; ответы можно дать "
                "позже через --answers.")
            return None, "Интерактивный режим недоступен: нет терминала."
        ask = input_fn or input

        def interactive(questions):
            return ans_mod.ask(questions, input_fn=ask, print_fn=say)

        return interactive, ""
    return None, ""


def _write_all(result: Result, inp: str, outp: str, say) -> list[Path] | None:
    out = Path(outp)
    ext = out.suffix.lower()
    files: list[tuple[Path, str]] = []
    md_text: str | None = None
    side: list[tuple[str, str]] = []  # (suffix, text) built before the report, so a failure becomes a note in it
    try:
        csv_text = sidefiles.findings_csv(result)
        if csv_text:
            side.append((".findings.csv", csv_text))
    except Exception as e:  # optional export: never breaks the run
        result.run.warnings.append(f"Таблица замечаний в CSV не записана: внутренняя ошибка ({type(e).__name__}).")
    try:
        if ext == ".json":
            files.append((out, report_json.render(result)))
        else:
            main_path = out
            if ext in (".html", ".htm"):
                main_path = out.with_suffix(".md")
                say(f"Основной отчёт записываю в Markdown: {main_path}. HTML-страница с отчётом будет рядом: {main_path.with_suffix('.html')}.")
            md_text = report_md.render(result, main_path.name)
            files.append((main_path, md_text))
            files.append((main_path.with_suffix(".json"), report_json.render(result)))
        base = files[0][0]
        if result.questions:
            qpath = base.with_name(base.stem + ".questions.md")
            answered = base.with_name(base.stem + "_answered" + base.suffix)  # the rerun must not overwrite this report
            files.append((qpath, sidefiles.questions_md(result, input_path=inp, output_path=str(answered),
                                                         questions_path=str(qpath))))
        fixed = sidefiles.fixed_md(result)
        if fixed:
            files.append((base.with_name(base.stem + ".fixed.md"), fixed))
        tests = sidefiles.tests_md(result)
        if tests:
            files.append((base.with_name(base.stem + ".tests.md"), tests))
        for suffix, text in side:
            files.append((base.with_name(base.stem + suffix), text))
        # details of the review, one-page summary, tracker import, run details, HTML page: optional, never break the run
        files += exports.extra_files(result, base, md_text)
    except Exception as e:  # rendering bug: say so plainly
        say(f"Не удалось собрать отчёт: внутренняя ошибка ({type(e).__name__}: {str(e)[:200]}).")
        return None
    written: list[Path] = []
    for path, text in files:
        try:
            _write_atomic(path, text)
            written.append(path)
        except OSError as e:
            if not written:
                say(f"Не удалось записать отчёт в «{path}»: {e.strerror or e}. Проверьте, что папка существует "
                    "и доступна для записи, или укажите другой --output.")
                return None
            say(f"Не удалось записать дополнительный файл «{path}»: {e.strerror or e}.")
    return written


def _final_lines(result: Result, written: list[Path], seconds: float, say) -> None:
    r = result.run
    say("")
    if result.diagnostic is not None:
        say(f"Итог: документ не проверен — {result.diagnostic.title}")
    else:
        c = [f for f in result.findings if f.status == "open" and not f.advisory]
        sev = {s: sum(1 for f in c if f.severity == s) for s in ("CRITICAL", "MAJOR", "MINOR")}
        say(f"Итог: {result.verdict_text}")
        say(f"Требований: {num(len(result.requirements))} · Замечаний: {num(len(c))} (CRITICAL {sev['CRITICAL']} · "
            f"MAJOR {sev['MAJOR']} · MINOR {sev['MINOR']}) · Вопросов: {len(result.questions)}"
            + (f" · Индекс готовности: {r.score}/100" if r.score is not None else ""))
    say("Записано: " + ", ".join(str(p) for p in written))
    say(f"Время: {seconds:.1f} с · Стоимость: {cost_text(result)}")
    say(ledger_line(result))


def _estimate(inp: str, provider: str, args, env, say) -> int:
    from . import estimate

    chosen = choose_provider(env) if provider == "auto" else provider
    note = ""
    if chosen == "offline":
        chosen = "anthropic"
        note = "провайдер не настроен — оценка для модели по умолчанию"
    model = config.resolve_model(chosen, env, args.model)
    from .llm import review_batch_size_for

    est = estimate.build(inp, depth=args.depth, contexts=args.context,
                         batch_size=review_batch_size_for(chosen, env))
    for line in estimate.report(est, provider=chosen, model=model, depth=args.depth, provider_note=note, env=env):
        say(line)
    return 0
