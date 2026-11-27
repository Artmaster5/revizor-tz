"""MCP-сервер «Ревизор ТЗ»: stdio, JSON-RPC 2.0, по одному JSON в строке. Без SDK и без сети.

Запуск: python mcp_server.py. В stdout уходят только ответы протокола; ход работы и сообщения — в stderr.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from . import __version__

SERVER_NAME = "revizor-tz"
KNOWN_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_VERSION = "2025-06-18"

_PROVIDERS = ["auto", "anthropic", "gemini", "openai", "ollama", "offline"]


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class ToolError(Exception):
    """Ошибка инструмента: уходит в чат как результат с isError, на русском."""


def _log(text: str) -> None:
    try:
        sys.stderr.write(text.rstrip("\n") + "\n")
        sys.stderr.flush()
    except Exception:
        pass


# ---------------------------------------------------------------- описания инструментов

_SOURCE_PROPS = {
    "path": {"type": "string", "description": "Путь к файлу с ТЗ (.md, .txt, .pdf, .docx, .html)."},
    "text": {"type": "string", "description": "Текст ТЗ, если файла нет. Нужен либо path, либо text."},
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "check_requirements",
        "description": ("Проверяет документ с техническим заданием (требованиями) и пишет отчёт на русском. "
                        "Возвращает вердикт, число замечаний по важности, индекс готовности, главные блокеры, "
                        "число вопросов к автору и список записанных файлов. Без ключей модели работает "
                        "только на автоматических правилах (provider = offline)."),
        "inputSchema": {"type": "object", "properties": {
            **_SOURCE_PROPS,
            "provider": {"type": "string", "enum": _PROVIDERS, "default": "auto",
                         "description": "Какая модель проверяет. auto — по ключам из .env; offline — без модели."},
            "depth": {"type": "string", "enum": ["full", "fast"], "default": "full",
                      "description": "full — три линзы и перепроверка; fast — один проход, быстрее "
                                     "(удобно, если чат ограничивает время ответа инструмента)."},
            "output_dir": {"type": "string",
                           "description": "Папка для отчётов. По умолчанию — временная папка revizor-mcp."},
        }},
    },
    {
        "name": "verify_quote",
        "description": ("Проверяет, есть ли цитата в документе дословно, и называет строки. Модель не нужна. "
                        "Мелкие различия в пробелах, кавычках и тире не мешают."),
        "inputSchema": {"type": "object", "properties": {
            **_SOURCE_PROPS,
            "quote": {"type": "string", "description": "Цитата, которую надо найти в документе."},
        }, "required": ["quote"]},
    },
    {
        "name": "list_checks",
        "description": ("Рассказывает, что проверяет агент: автоматические правила, критерии качества "
                        "требований и стандарт, на который опираются замечания. Модель не нужна."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "estimate_run",
        "description": ("Оценивает число вызовов модели, токены, стоимость и время проверки документа, "
                        "ничего не запуская. Модель не вызывается."),
        "inputSchema": {"type": "object", "properties": {
            **_SOURCE_PROPS,
            "provider": {"type": "string", "enum": _PROVIDERS, "default": "auto",
                         "description": "Для какой модели считать оценку."},
            "depth": {"type": "string", "enum": ["full", "fast"], "default": "full",
                      "description": "full — три линзы и перепроверка; fast — один проход."},
        }},
    },
]


# ---------------------------------------------------------------- вспомогательное

def _str_arg(args: dict, name: str) -> str | None:
    v = args.get(name)
    if v is None:
        return None
    if not isinstance(v, str):
        raise RpcError(-32602, f"Параметр «{name}» должен быть строкой.")
    return v


def _source(args: dict, tmp_dir: Path | None = None) -> tuple[str, bool]:
    """(путь к документу, временный ли он). Документ берётся из path или из text."""
    path, text = _str_arg(args, "path"), _str_arg(args, "text")
    if path:
        p = Path(path).expanduser()
        if not p.is_file():
            raise ToolError(f"Файл не найден: «{path}». Укажите полный путь к документу или передайте текст в параметре text.")
        return str(p), False
    if text and text.strip():
        d = tmp_dir or Path(tempfile.mkdtemp(prefix="revizor-mcp-"))
        d.mkdir(parents=True, exist_ok=True)
        f = d / "document.md"
        f.write_text(text, encoding="utf-8")
        return str(f), True
    raise ToolError("Не указан документ: передайте path (путь к файлу) или text (текст ТЗ).")


def _default_out_dir() -> Path:
    return Path(tempfile.gettempdir()) / "revizor-mcp" / time.strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------- инструменты

def _check_choice(name: str, value: str, allowed) -> None:
    if value not in allowed:
        raise RpcError(-32602, f"Параметр «{name}»: неизвестное значение «{value[:40]}»; допустимо: {', '.join(allowed)}.")


def tool_check_requirements(args: dict) -> str:
    from . import cli, config

    provider = _str_arg(args, "provider") or "auto"
    depth = _str_arg(args, "depth") or "full"
    _check_choice("provider", provider, _PROVIDERS)
    _check_choice("depth", depth, ("full", "fast"))
    out_dir = Path(_str_arg(args, "output_dir") or _default_out_dir()).expanduser()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ToolError(f"Не удалось создать папку для отчётов «{out_dir}»: {e.strerror or e}.")
    inp, _tmp = _source(args, out_dir / "source")
    stem = Path(inp).stem if not _tmp else "document"
    out_path = out_dir / f"{stem}.report.md"
    env = config.build_env()
    try:
        parsed = cli.build_parser().parse_args(["--input", inp, "--output", str(out_path), "--provider", provider,
                                                "--depth", depth, "--quiet"])
        with contextlib.redirect_stdout(sys.stderr):
            _code, result, written = cli.execute(parsed, env, lambda s: _log(str(s)))
    except cli.UsageError as e:
        raise ToolError(f"Ошибка в параметрах: {e}.")
    if result is None:
        raise ToolError("Отчёт не записан: проверьте, что папка для отчётов доступна для записи.")
    return _summary(result, written)


def _summary(result, written) -> str:
    lines: list[str] = []
    if result.diagnostic is not None:
        lines.append(f"Документ не проверен: {result.diagnostic.title}")
    else:
        lines.append(f"Вердикт: {result.verdict_text}")
        open_f = [f for f in result.findings if f.status == "open" and not f.advisory]
        sev = {s: sum(1 for f in open_f if f.severity == s) for s in ("CRITICAL", "MAJOR", "MINOR")}
        lines.append(f"Требований: {len(result.requirements)}. Замечаний: {len(open_f)} "
                     f"(CRITICAL {sev['CRITICAL']}, MAJOR {sev['MAJOR']}, MINOR {sev['MINOR']}).")
        if result.run.score is not None:
            lines.append(f"Индекс готовности: {result.run.score}/100.")
        blockers = [f for f in open_f if f.severity == "CRITICAL"][:5]
        if blockers:
            lines.append("Главные блокеры:")
            lines += [f"- {f.fid}: {f.title}" for f in blockers]
        lines.append(f"Вопросов автору: {len(result.questions)}.")
    lines.append("Записанные файлы:")
    lines += [f"- {Path(p).resolve()}" for p in written]
    return "\n".join(lines)


def tool_verify_quote(args: dict) -> str:
    from . import loader
    from .quotes import QuoteIndex

    quote = _str_arg(args, "quote")
    if not quote or not quote.strip():
        raise RpcError(-32602, "Не указана цитата: параметр «quote» обязателен.")
    inp, tmp = _source(args)
    try:
        doc = loader.load(inp)
    finally:
        if tmp:
            with contextlib.suppress(OSError):
                os.remove(inp)
    if not hasattr(doc, "lines"):
        raise ToolError(f"Документ не прочитан: {getattr(doc, 'title', 'неизвестная причина')}.")
    q = QuoteIndex(doc).find(quote)
    if q is None:
        return "Цитаты в документе нет: ни дословно, ни с мелкими различиями в пробелах и знаках."
    where = f"строка {q.line_start}" if q.line_start == q.line_end else f"строки {q.line_start}–{q.line_end}"
    kind = "дословно" if q.exact else "с мелкими отличиями (восстановлено по близкому совпадению)"
    extra = f", раздел «{q.section}»" if q.section else ""
    return f"Цитата найдена {kind}: {where}{extra}.\nТекст в документе: {q.text}"


def tool_list_checks(args: dict) -> str:
    from . import standard
    from .model import CRITERION_RU, REQ_CRITERIA, SET_CRITERIA
    from .rules import RULES

    main = [r for r in RULES.values() if not r.advisory]
    adv = [r for r in RULES.values() if r.advisory]
    out = [f"Агент проверяет требования по правилам кода ({len(main)}) и наблюдениям ({len(adv)}), "
           f"а при подключённой модели ещё и смыслом. Опора — {standard.NAME}.", "", "Критерии качества:"]
    for c in REQ_CRITERIA + SET_CRITERIA:
        out.append(f"- {CRITERION_RU.get(c, c)}")
    out += ["", "Автоматические правила (без модели):"]
    out += [f"- {r.id} [{r.severity}]: {r.title}" for r in main]
    if adv:
        out += ["", "Наблюдения (не влияют на вердикт):"]
        out += [f"- {r.id}: {r.title}" for r in adv]
    out += ["", "Отдельно агент находит в документе незаполненный чек-лист (ряды «Да / Нет», пустые флажки) "
            "и отвечает на его пункты как подсказку проверяющему."]
    return "\n".join(out)


def tool_estimate_run(args: dict) -> str:
    from . import cli, config

    provider = _str_arg(args, "provider") or "auto"
    depth = _str_arg(args, "depth") or "full"
    _check_choice("provider", provider, _PROVIDERS)
    _check_choice("depth", depth, ("full", "fast"))
    inp, tmp = _source(args)
    lines: list[str] = []
    parsed = cli.build_parser().parse_args(["--input", inp, "--output", "x.md", "--provider", provider,
                                            "--depth", depth, "--estimate"])
    try:
        with contextlib.redirect_stdout(sys.stderr):
            cli._estimate(inp, cli._provider_of(parsed), parsed, config.build_env(), lines.append)
    finally:
        if tmp:
            with contextlib.suppress(OSError):
                os.remove(inp)
    return "\n".join(lines)


_HANDLERS = {"check_requirements": tool_check_requirements, "verify_quote": tool_verify_quote,
             "list_checks": tool_list_checks, "estimate_run": tool_estimate_run}


# ---------------------------------------------------------------- протокол

def _result(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _call_tool(params: Any) -> dict:
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        raise RpcError(-32602, "Для tools/call нужен параметр name.")
    args = params.get("arguments", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise RpcError(-32602, "Параметр arguments должен быть объектом.")
    handler = _HANDLERS.get(params["name"])
    if handler is None:
        raise RpcError(-32602, f"Неизвестный инструмент «{params['name']}».")
    try:
        text = handler(args)
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except (RpcError, KeyboardInterrupt):
        raise
    except ToolError as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except SystemExit as e:  # чужой sys.exit внутри проверки не должен останавливать сервер
        _log(f"Инструмент {params['name']} попытался завершить процесс (код {e.code}).")
        return {"content": [{"type": "text", "text": "Проверка прервалась, сервер продолжает работу."}], "isError": True}
    except Exception as e:  # инструмент не должен ронять сервер
        _log(f"Ошибка инструмента {params['name']}: {type(e).__name__}: {e}")
        return {"content": [{"type": "text", "text": f"Не удалось выполнить инструмент: внутренняя ошибка "
                                                      f"({type(e).__name__}: {str(e)[:200]})."}], "isError": True}


def handle_message(msg: Any) -> dict | None:
    """Один разобранный JSON-запрос → ответ (dict) или None для уведомления."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        rid = msg.get("id") if isinstance(msg, dict) else None
        if isinstance(msg, dict) and ("result" in msg or "error" in msg):
            return None  # ответ клиента на наш запрос: мы запросов не шлём
        return _error(rid, -32600, "Неверный запрос JSON-RPC.")
    method, req_id = msg["method"], msg.get("id")
    is_notification = "id" not in msg
    try:
        if method == "initialize":
            params = msg.get("params") or {}
            if not isinstance(params, dict):
                raise RpcError(-32602, "Параметры initialize должны быть объектом.")
            asked = params.get("protocolVersion")
            version = asked if asked in KNOWN_VERSIONS else DEFAULT_VERSION
            res = {"protocolVersion": version, "capabilities": {"tools": {}},
                   "serverInfo": {"name": SERVER_NAME, "version": __version__}}
        elif is_notification:
            return None
        elif method == "ping":
            res = {}
        elif method == "tools/list":
            res = {"tools": TOOLS}
        elif method == "tools/call":
            res = _call_tool(msg.get("params"))
        else:
            raise RpcError(-32601, f"Неизвестный метод: {method}.")
    except RpcError as e:
        return None if is_notification else _error(req_id, e.code, e.message)
    return None if is_notification else _result(req_id, res)


def handle_line(line: str) -> dict | list | None:
    """Строка со входа → ответ. Нечитаемый JSON даёт -32700; пакет запросов обрабатывается по одному."""
    try:
        msg = json.loads(line)
    except ValueError:
        return _error(None, -32700, "Не удалось разобрать JSON.")
    if isinstance(msg, list):
        if not msg:
            return _error(None, -32600, "Пустой пакет запросов.")
        out = [r for r in (handle_message(m) for m in msg) if r is not None]
        return out or None
    return handle_message(msg)


def _force_utf8() -> None:
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(encoding="utf-8", **({"newline": "\n"} if name == "stdout" else {}))
        except Exception:
            pass


def serve(stdin=None, stdout=None) -> int:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    _log(f"Ревизор ТЗ {__version__}: MCP-сервер запущен, жду запросы.")
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        reply = handle_line(line)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


def main() -> int:
    _force_utf8()
    try:
        return serve()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
