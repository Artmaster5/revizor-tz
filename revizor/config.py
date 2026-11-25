"""Settings: the environment mapping (with an own tiny .env parser) and model-name defaults.

Real environment variables always win over .env files. ``REVIZOR_NO_DOTENV=1`` switches the .env
files off completely (tests and safe manual runs use it). Values are never printed.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping

AGENT_DIR = Path(__file__).resolve().parents[1]

# Same defaults as the model layer (kept here so that --estimate needs no provider module).
DEFAULT_MODELS = {
    "anthropic": "claude-haiku-5-5",
    "gemini": "gemini-flash-latest",
    "openai": "gpt-4o-mini",
    "ollama": "",
}
MODEL_VARS = {
    "anthropic": "ANTHROPIC_MODEL",
    "gemini": "GEMINI_MODEL",
    "openai": "OPENAI_MODEL",
    "ollama": "OLLAMA_MODEL",
}
PROVIDER_RU = {
    "anthropic": "Anthropic",
    "gemini": "Google Gemini",
    "openai": "OpenAI-совместимый сервер",
    "ollama": "локальная модель (Ollama)",
    "fake": "тестовый сценарий",
    "offline": "без модели",
}

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "да", "on")


def parse_dotenv(text: str) -> dict[str, str]:
    """KEY=VALUE lines; # comments; optional quotes; ``export`` prefix. Never raises."""
    out: dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        else:
            val = re.sub(r"\s+#.*$", "", val).strip()
        out[key] = val
    return out


def read_dotenv(path: Path) -> dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return parse_dotenv(f.read())
    except (OSError, UnicodeDecodeError):
        return {}


def build_env(environ: Mapping[str, str] | None = None, *, dirs: list[Path] | None = None) -> dict[str, str]:
    """The settings mapping: .env files (current folder, then the agent folder) under the real environment."""
    real = dict(os.environ if environ is None else environ)
    if _truthy(real.get("REVIZOR_NO_DOTENV")):
        return real
    merged: dict[str, str] = {}
    seen: set[Path] = set()
    for d in dirs if dirs is not None else [Path.cwd(), AGENT_DIR]:
        p = (Path(d) / ".env").resolve()
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        for k, v in read_dotenv(p).items():
            merged.setdefault(k, v)  # the first file found wins for a key
    merged.update(real)
    return merged


def resolve_model(provider: str, env: Mapping[str, str], model: str | None = None) -> str:
    """The model name a provider will be asked for (before any server-side substitution)."""
    from .llm import is_placeholder

    if model:
        return model
    var = MODEL_VARS.get(provider, "")
    v = env.get(var, "") if var else ""
    if v and not is_placeholder(v):
        return v.strip().strip("'\"")
    return DEFAULT_MODELS.get(provider, "")


def max_cost_from(value: str | None, env: Mapping[str, str]) -> tuple[float | None, str]:
    """(cap in USD or None, error text). The command-line value wins over REVIZOR_MAX_COST_USD."""
    raw = value if value not in (None, "") else env.get("REVIZOR_MAX_COST_USD", "")
    raw = (raw or "").strip().replace(",", ".").lstrip("$")
    if not raw:
        return None, ""
    try:
        cap = float(raw)
    except ValueError:
        return None, f"ограничение стоимости «{raw[:20]}» не число; укажите сумму в долларах, например 0.50"
    if cap < 0:
        return None, "ограничение стоимости не может быть отрицательным"
    return cap, ""


def agent_version() -> str:
    """``__version__``; with a short commit (and -dirty) when the agent folder is its own git checkout."""
    from . import __version__

    if not (AGENT_DIR / ".git").exists():
        return __version__
    try:
        import subprocess

        out = subprocess.run(["git", "-C", str(AGENT_DIR), "describe", "--tags", "--always", "--dirty"],
                             capture_output=True, text=True, timeout=3)
        tag = out.stdout.strip()
        return f"{__version__} ({tag})" if out.returncode == 0 and tag else __version__
    except Exception:
        return __version__


_SHORT_SHA = re.compile(r"[0-9a-f]{4,40}")


def _inside_git(folder: Path) -> bool:
    """A ``.git`` entry (folder or file) in the folder or one of its parents. Cheap, no process is started."""
    try:
        for d in (folder, *folder.parents):
            if (d / ".git").exists():
                return True
    except OSError:
        return False
    return False


def _git(args: list[str], base: Path, runner) -> str | None:
    """stdout of a read-only ``git -C <base> …`` (2-second timeout), or None on any failure. Never raises, prints
    nothing. Without ``runner`` no process is started outside a git work tree."""
    try:
        import subprocess

        if runner is None:
            if not _inside_git(base):
                return None
            runner = subprocess.run
        out = runner(["git", "-C", str(base), *args], capture_output=True, text=True, timeout=2,
                     stdin=subprocess.DEVNULL)
        if getattr(out, "returncode", 1) != 0:
            return None
        return str(getattr(out, "stdout", "") or "")
    except Exception:
        return None


def agent_commit(folder: Path | str | None = None, runner=None) -> str:
    """Short commit hash of the agent's own folder, or "" when it is not inside a git work tree.

    Read-only ``git -C <folder> rev-parse --short HEAD`` with a 2-second timeout. Any error (no git, no work tree,
    timeout, odd output) gives "" — never an exception and nothing on the console. ``runner`` replaces
    ``subprocess.run`` in tests; with a runner the cheap ``.git`` pre-check is skipped."""
    base = Path(folder) if folder is not None else AGENT_DIR
    sha = (_git(["rev-parse", "--short", "HEAD"], base, runner) or "").strip()
    return sha if _SHORT_SHA.fullmatch(sha) else ""


_TAG = re.compile(r"[\w.+/-]{1,80}")


def agent_tag(folder: Path | str | None = None, runner=None) -> str:
    """Tag that points exactly at HEAD (``git describe --tags --exact-match HEAD``), or ""."""
    base = Path(folder) if folder is not None else AGENT_DIR
    tag = (_git(["describe", "--tags", "--exact-match", "HEAD"], base, runner) or "").strip()
    return tag if _TAG.fullmatch(tag) else ""


def agent_dirty(folder: Path | str | None = None, runner=None) -> bool:
    """True when tracked files have uncommitted changes (``git status --porcelain --untracked-files=no``)."""
    base = Path(folder) if folder is not None else AGENT_DIR
    out = _git(["--no-optional-locks", "status", "--porcelain", "--untracked-files=no"], base, runner)
    return bool(out and out.strip())


def version_label(version: str, commit: str, rules_version: str = "", *, tag: str = "", dirty: bool = False) -> str:
    """Passport text, e.g. «v1.0.0 (тег `v1.0.0`) · коммит `abc1234` · набор правил 1.0».

    The tag is shown only when HEAD carries one; « + незакоммиченные правки» follows the commit when tracked files
    were changed; outside git: «коммит не определён (папка вне git)»."""
    head = (version or "").split(" ", 1)[0] or "?"
    first = head if head.startswith("v") else "v" + head
    if tag and commit:
        first += f" (тег `{tag}`)"
    parts = [first]
    if commit:
        parts.append(f"коммит `{commit}`" + (" + незакоммиченные правки" if dirty else ""))
    else:
        parts.append("коммит не определён (папка вне git)")
    if rules_version:
        parts.append(f"набор правил {rules_version}")
    return " · ".join(parts)
