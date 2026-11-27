"""MCP-сервер «Ревизор ТЗ» для Claude Desktop, Cursor и VS Code: python mcp_server.py (подробности: docs/MCP.md)."""
import os
import sys

if sys.version_info < (3, 10):
    sys.stderr.write("Нужен Python 3.10 или новее.\n")
    raise SystemExit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from revizor.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
