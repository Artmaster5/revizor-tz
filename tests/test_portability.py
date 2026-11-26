"""Runs anywhere: Windows / Linux consoles, file encodings, file names, Python 3.10–3.14 syntax. No network, no keys.

The real CLI is started in a subprocess the way a jury member on Windows would meet it: ``PYTHONUTF8`` off, the
console in cp866 / cp1251 / ASCII, ``LC_ALL=C``, an output folder with spaces and Cyrillic. The analysis itself is
not checked here (other tests do that); only that nothing crashes and the files come out right.
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import tokenize
import unittest
from argparse import Namespace
from pathlib import Path

from revizor import cli, config, console, sidefiles, webui

AGENT = Path(__file__).resolve().parents[1]
DOC = AGENT / "docs" / "requirements.md"
ENV = {"REVIZOR_NO_DOTENV": "1"}  # never a real .env
SOURCE_DIRS = ("revizor", "tools", "eval", "tests")
SUBPROCESS_CALLS = {"run", "call", "check_call", "check_output", "Popen", "runner"}

WIN_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def windows_legal(name: str) -> bool:
    """A file name Windows accepts: no reserved characters, no trailing dot or space, not a device name."""
    return (bool(name) and not WIN_ILLEGAL.search(name) and not name.endswith((".", " "))
            and name.split(".", 1)[0].rstrip(" ").upper() not in WIN_RESERVED and len(name) <= 255)


def hostile_env(**extra: str) -> dict[str, str]:
    """The current environment without any UTF-8 help, plus ``extra``. Never reads .env, never writes .pyc."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONUTF8", "PYTHONIOENCODING", "PYTHONLEGACYWINDOWSSTDIO", "LANG", "LANGUAGE")
           and not k.startswith("LC_")}
    env.update(REVIZOR_NO_DOTENV="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="0")
    env.update(extra)
    return env


def run_main(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(AGENT / "main.py"), *args], cwd=str(AGENT), env=env,
                          capture_output=True, timeout=180, stdin=subprocess.DEVNULL)


def source_files() -> list[Path]:
    files = sorted(AGENT.glob("*.py"))
    for d in SOURCE_DIRS:
        files += sorted((AGENT / d).rglob("*.py"))
    return files


class HostileConsole(unittest.TestCase):
    """cp866 / cp1251 / ASCII console with PYTHONUTF8=0 and LC_ALL=C: exit 0, the report is written."""

    def test_offline_run_in_every_console_encoding(self):
        for enc in ("cp866", "cp1251", "ascii"):
            with self.subTest(encoding=enc), tempfile.TemporaryDirectory() as d:
                out = Path(d) / "папка с пробелом" / "отчёт ревизора.md"
                r = run_main(["--input", str(DOC), "--output", str(out), "--provider", "offline"],
                             hostile_env(PYTHONIOENCODING=enc, LC_ALL="C"))
                self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace")[-2000:])
                self.assertNotIn(b"Traceback", r.stderr)
                text = r.stdout.decode(enc)  # the console got bytes it can show
                self.assertIn("$0", text)
                self.assertTrue(out.is_file() and out.stat().st_size > 1000)
                data = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
                self.assertTrue(data["findings"])
                written = sorted(p.name for p in out.parent.iterdir())
                self.assertIn("отчёт ревизора.questions.md", written)
                for name in written:
                    self.assertTrue(windows_legal(name), name)
                # the rerun command quotes the paths with spaces, so it can be pasted into cmd, PowerShell or sh
                q = out.with_name("отчёт ревизора.questions.md").read_text(encoding="utf-8")
                self.assertIn('--output "', q)

    def test_version_and_estimate_on_an_ascii_console(self):
        env = hostile_env(PYTHONIOENCODING="ascii", LC_ALL="C")
        for args in (["--version"], ["--estimate", "--input", str(DOC), "--output", "reports/estimate.md"], ["--help"]):
            with self.subTest(args=args[0]):
                r = run_main(args, env)
                self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace")[-2000:])
                self.assertTrue(r.stdout.strip())
                r.stdout.decode("ascii")  # nothing but ASCII reached the console

    def test_bad_arguments_still_print_a_message(self):
        r = run_main(["--provider", "нет-такого", str(DOC), "x.md"], hostile_env(PYTHONIOENCODING="cp866", LC_ALL="C"))
        self.assertEqual(r.returncode, 1)
        self.assertNotIn(b"Traceback", r.stderr)
        self.assertTrue(r.stdout.decode("cp866").strip())

    def test_output_piped_to_a_file_is_utf8(self):
        """``python main.py ... > log.txt`` without PYTHONIOENCODING: the log keeps Cyrillic and emoji."""
        with tempfile.TemporaryDirectory() as d:
            r = run_main(["--input", str(DOC), "--output", str(Path(d) / "r.md"), "--provider", "offline", "-q"],
                         hostile_env(LC_ALL="C"))
            self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace")[-2000:])
            self.assertIn("Итог", r.stdout.decode("utf-8"))


class ConsoleUnit(unittest.TestCase):
    def test_forced_encoding_is_kept_but_never_raises(self):
        raw = io.BytesIO()
        s = io.TextIOWrapper(raw, encoding="cp866")
        console.prepare_stream(s, forced=True)
        s.write("Итог: ⛔ блокер · 1 000 ₸ → ✅\n")
        s.flush()
        self.assertEqual(s.encoding, "cp866")
        self.assertIn("Итог".encode("cp866"), raw.getvalue())

    def test_a_pipe_becomes_utf8(self):
        raw = io.BytesIO()
        s = io.TextIOWrapper(raw, encoding="cp1251")
        console.prepare_stream(s)
        s.write("⛔ ₸\n")
        s.flush()
        self.assertEqual(raw.getvalue().decode("utf-8").strip(), "⛔ ₸")

    def test_missing_streams_are_fine(self):
        console.prepare_stream(None)
        console.prepare_stream(object())


class FilesFromWindows(unittest.TestCase):
    """Documents, answers and settings files saved on Windows: BOM, CRLF, UTF-16."""

    def _run(self, doc: Path, out: Path):
        args = cli.build_parser().parse_args(["--input", str(doc), "--output", str(out), "--provider", "offline", "-q"])
        code, result, written = cli.execute(args, config.build_env(ENV), lambda _s: None)
        self.assertEqual(code, 0)
        return result, json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))

    def test_document_with_bom_and_crlf_gives_the_same_findings(self):
        raw = DOC.read_bytes()
        self.assertNotIn(b"\r", raw)
        with tempfile.TemporaryDirectory() as d:
            win = Path(d) / "requirements.md"
            win.write_bytes(b"\xef\xbb\xbf" + raw.replace(b"\n", b"\r\n"))
            _, a = self._run(DOC, Path(d) / "lf" / "r.md")
            _, b = self._run(win, Path(d) / "crlf" / "r.md")
        for key in ("requirements", "findings", "questions", "verdict", "summary"):
            self.assertEqual(a[key], b[key], key)
        lines = raw.decode("utf-8").split("\n")
        quotes = [q for f in b["findings"] for q in f["quotes"]]
        self.assertTrue(quotes)
        for q in quotes:
            self.assertTrue(q["exact"])
            span = "\n".join(lines[q["line_start"] - 1:q["line_end"]])
            self.assertIn(" ".join(q["text"].split()), " ".join(span.split()))
            self.assertNotIn("\ufeff", q["text"])
            self.assertNotIn("\r", q["text"])

    def test_answers_file_in_utf16_with_bom_and_crlf(self):
        """PowerShell 5 «>» and «Out-File» write UTF-16 with a BOM; Notepad may add a BOM to UTF-8."""
        with tempfile.TemporaryDirectory() as d:
            result, _ = self._run(DOC, Path(d) / "r.md")
            self.assertTrue(result.questions)
            qtext = (Path(d) / "r.questions.md").read_text(encoding="utf-8")
            for enc, bom in (("utf-16-le", b"\xff\xfe"), ("utf-8", b"\xef\xbb\xbf")):
                with self.subTest(encoding=enc):
                    path = Path(d) / f"answers-{enc}.md"
                    path.write_bytes(bom + qtext.replace("\n", "\r\n").encode(enc))
                    fn, _ = cli._answer_provider(Namespace(answers=str(path), interactive=False), None, None, True)
                    parsed = fn(result.questions)
                    self.assertFalse([w for w in parsed.warnings if "не найдено" in w or "не прочитан" in w],
                                     parsed.warnings)

    def test_dotenv_with_bom_and_crlf(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_bytes(b"\xef\xbb\xbfANTHROPIC_MODEL=claude-x\r\n# comment\r\nexport GEMINI_API_KEY=\"abc def\"\r\n"
                          b"REVIZOR_MAX_COST_USD=0.5 # note\r\nLLM_PROVIDER=offline\r\n")
            got = config.read_dotenv(p)
            self.assertEqual(got, {"ANTHROPIC_MODEL": "claude-x", "GEMINI_API_KEY": "abc def",
                                   "REVIZOR_MAX_COST_USD": "0.5", "LLM_PROVIDER": "offline"})
            env = config.build_env({}, dirs=[Path(d)])
            self.assertEqual(env["ANTHROPIC_MODEL"], "claude-x")
            self.assertFalse([k for k in env if "\ufeff" in k or "\r" in k or "\r" in env[k]])

    @unittest.skipIf(os.name == "nt", "Windows passes file names as UTF-16, never as escaped bytes")
    def test_cyrillic_output_name_under_an_ascii_file_system_encoding(self):
        """Linux with LC_ALL=C and PYTHONUTF8=0: «отчёт.md» from the command line is the str '\\udcd0\\udcbe…'."""
        with tempfile.TemporaryDirectory() as d:
            name = os.fsencode("отчёт").decode("ascii", "surrogateescape") + ".md"
            out = Path(d) / name
            self._run(DOC, out)
            self.assertTrue((Path(d) / "отчёт.md").is_file())
            self.assertIn("отчёт_answered.md", (Path(d) / "отчёт.questions.md").read_text(encoding="utf-8"))

    def test_report_files_are_written_with_lf_on_every_system(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "a.md"
            cli._write_atomic(out, "строка 1\nстрока 2\n")
            self.assertEqual(out.read_bytes(), "строка 1\nстрока 2\n".encode("utf-8"))
            # LC_ALL=C with PYTHONUTF8=0 on Linux: a Cyrillic name from argv arrives as escaped bytes
            esc = os.fsdecode(b"\xd0\xb0") if sys.getfilesystemencoding() == "ascii" else "\udcd0\udcb0"
            cli._write_atomic(out, "x " + esc)
            self.assertEqual(out.read_bytes(), b"x \xd0\xb0")


class FileNames(unittest.TestCase):
    def test_upload_names_are_legal_on_windows(self):
        for raw in ("CON.md", "nul.txt", "com1.MD", "Lpt9.pdf", 'a:b*c?"d<e>f|g.md', "отчёт. .md", "..  .md",
                    "C:\\Users\\Имя\\Мой документ.docx", "x" * 400 + ".md", "trailing .md", "a\x00b.md"):
            with self.subTest(raw=raw):
                name = webui.safe_name(raw, webui.DOC_FORMATS)
                self.assertTrue(windows_legal(name), name)
        self.assertEqual(webui.safe_name("requirements.md", webui.DOC_FORMATS), "requirements.md")
        self.assertEqual(webui.safe_name("CON.md", webui.DOC_FORMATS), "_CON.md")

    def test_paths_in_printed_commands(self):
        self.assertEqual(sidefiles.command_path("C:\\Users\\Имя Ф\\d.md", sep="\\"), '"C:/Users/Имя Ф/d.md"')
        self.assertEqual(sidefiles.command_path("docs\\req.md", sep="\\"), "docs/req.md")
        self.assertEqual(sidefiles.command_path("docs/req.md", sep="/"), "docs/req.md")
        self.assertEqual(sidefiles.command_path("a\\b.md", sep="/"), "a\\b.md")  # a legal POSIX name stays
        self.assertIn('--input "my docs/r.md"', sidefiles.rerun_line("my docs/r.md", "o.md", "o.questions.md"))

    def test_tracked_file_names_are_legal_on_windows(self):
        try:
            names = subprocess.run(["git", "ls-files", "-z"], cwd=str(AGENT), capture_output=True, timeout=20,
                                   check=True).stdout.decode("utf-8").split("\0")
        except (OSError, subprocess.SubprocessError):
            self.skipTest("нет git")
        names = [n for n in names if n]
        bad = [n for n in names if not all(windows_legal(part) for part in n.split("/"))]
        self.assertEqual(bad, [])
        lower = [n.lower() for n in names]
        self.assertEqual(len(lower), len(set(lower)), "имена, различающиеся только регистром")

    def test_headless_linux_does_not_start_a_text_browser(self):
        self.assertFalse(webui._has_display({}, "linux"))
        self.assertTrue(webui._has_display({"DISPLAY": ":0"}, "linux"))
        self.assertTrue(webui._has_display({}, "win32"))
        self.assertTrue(webui._has_display({}, "darwin"))


class SourceRules(unittest.TestCase):
    """Rules that keep the code portable; they fail on the first file that breaks them."""

    def test_text_io_always_names_the_encoding(self):
        """open()/read_text()/write_text()/subprocess(text=True) without encoding= use cp1251 on Windows."""
        bad = []
        for path in source_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call):
                    continue
                f = n.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                owner = getattr(f.value, "id", "") if isinstance(f, ast.Attribute) else ""
                kw = {k.arg for k in n.keywords}
                if "encoding" in kw or None in kw:
                    continue
                where = f"{path.relative_to(AGENT).as_posix()}:{n.lineno}"
                if name in ("read_text", "write_text"):
                    bad.append(f"{where} {name}")
                elif name in ("open", "fdopen") and owner not in ("webbrowser", "zipfile", "zf", "tarfile"):
                    mode = next((k.value for k in n.keywords if k.arg == "mode"), None)
                    if mode is None and len(n.args) >= 2:
                        mode = n.args[1]
                    m = mode.value if isinstance(mode, ast.Constant) else ("r" if mode is None else "?")
                    if not (isinstance(m, str) and "b" in m):
                        bad.append(f"{where} {name}({m!r})")
                elif kw & {"text", "universal_newlines"} and name in SUBPROCESS_CALLS:
                    bad.append(f"{where} {name}(text=True)")
                elif name in ("NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile"):
                    mode = next((k.value for k in n.keywords if k.arg == "mode"), None)
                    if isinstance(mode, ast.Constant) and "b" not in str(mode.value):
                        bad.append(f"{where} {name}")
        self.assertEqual(bad, [], "укажите encoding=\"utf-8\"")

    def test_no_posix_only_calls_or_fixed_temp_folders(self):
        pattern = re.compile(r"\bos\.(getuid|geteuid|fork|setsid|killpg|symlink|chown)\b|\bimport (fcntl|pwd|grp|termios)\b"
                             r"|\bsignal\.(SIGKILL|SIGALRM|SIGHUP|SIGUSR1|alarm)\b|[\"']/tmp/|%-[dmHMjI]|\bshlex\.")
        bad = []
        for path in source_files():
            if path.name == "test_portability.py":
                continue
            for k, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    bad.append(f"{path.relative_to(AGENT).as_posix()}:{k}: {line.strip()[:100]}")
        self.assertEqual(bad, [])

    def test_sources_parse_as_python_3_10(self):
        new_names = {"tomllib", "Self", "StrEnum", "batched", "UTC", "TaskGroup", "ExceptionGroup", "LiteralString",
                     "Never", "override", "assert_never", "reveal_type", "TypeIs", "ReadOnly"}
        bad = []
        for path in source_files():
            src = path.read_text(encoding="utf-8")
            where = path.relative_to(AGENT).as_posix()
            try:
                tree = ast.parse(src, filename=where, feature_version=(3, 10))
            except SyntaxError as e:
                bad.append(f"{where}:{e.lineno} {e.msg}")
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    bad += [f"{where}:{n.lineno} import {a.name}" for a in n.names if a.name in new_names]
                elif isinstance(n, ast.ImportFrom):
                    bad += [f"{where}:{n.lineno} from {n.module} import {a.name}" for a in n.names
                            if a.name in new_names]
                elif isinstance(n, (ast.TryStar,) if hasattr(ast, "TryStar") else ()):
                    bad.append(f"{where}:{n.lineno} except*")
                elif hasattr(ast, "TypeAlias") and isinstance(n, ast.TypeAlias):
                    bad.append(f"{where}:{n.lineno} type X = …")
            bad += [f"{where}:{line} {why}" for line, why in pep701_problems(src)]
        self.assertEqual(bad, [])

    def test_main_guard_parses_on_old_python(self):
        """main.py must reach its «Нужен Python 3.10» line on any Python 3: no «from __future__», no new syntax."""
        src = (AGENT / "main.py").read_text(encoding="utf-8")
        head = src.split("from revizor", 1)[0]
        tree = ast.parse(head, feature_version=(3, 4))
        self.assertFalse([n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "__future__"])
        self.assertIn("sys.version_info < (3, 10)", head)


def pep701_problems(src: str) -> list[tuple[int, str]]:
    """f-strings that only Python 3.12+ accepts (PEP 701): the enclosing quote reused inside {…}, a backslash or a
    comment inside {…}. Needs the 3.12+ tokenizer; on older Pythons the compiler itself already refuses them."""
    if not hasattr(tokenize, "FSTRING_START"):
        return []
    out: list[tuple[int, str]] = []
    stack: list[str] = []  # quote of each open f-string
    passthrough = {tokenize.FSTRING_MIDDLE, tokenize.FSTRING_END, tokenize.FSTRING_START}
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.FSTRING_START:
            quote = tok.string.lstrip("rRfFbBuU")
            if stack and _same_quote(quote, stack):
                out.append((tok.start[0], "f-строка внутри f-строки с той же кавычкой"))
            stack.append(quote)
            continue
        if tok.type == tokenize.FSTRING_END:
            if stack:
                stack.pop()
            continue
        if not stack or tok.type in passthrough:
            continue
        if tok.type == tokenize.STRING:
            quote = re.match(r"[rRbBuU]*('''|\"\"\"|'|\")", tok.string)
            if quote and _same_quote(quote.group(1), stack):
                out.append((tok.start[0], "та же кавычка внутри {…} f-строки"))
            if "\\" in tok.string:
                out.append((tok.start[0], "обратная косая черта внутри {…} f-строки"))
        elif tok.type == tokenize.COMMENT:
            out.append((tok.start[0], "комментарий внутри {…} f-строки"))
    return out


def _same_quote(quote: str, stack: list[str]) -> bool:
    return any(quote == q or (len(q) == 1 and quote.startswith(q)) or (len(q) == 3 and quote.startswith(q))
               for q in stack)


if __name__ == "__main__":
    unittest.main()
