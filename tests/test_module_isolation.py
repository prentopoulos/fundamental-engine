"""Runtime modules must depend only on this package, Python, and declared libraries."""

import ast
import sys
from pathlib import Path

import pytest

MODULES = sorted(Path("engine").rglob("*.py"))
ALLOWED_ROOTS = sys.stdlib_module_names | {
    "engine",
    "anthropic",
    "defusedxml",
    "pydantic",
    "fastapi",
    "uvicorn",
    "apscheduler",
    "jinja2",
    "yaml",
    "mcp",
}


@pytest.mark.parametrize("path", MODULES, ids=str)
def test_runtime_imports_only_declared_dependencies(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module]
        for name in names:
            assert name.split(".")[0] in ALLOWED_ROOTS, f"{path} imports {name}"
