"""Every outbound `requests` call carries a timeout: the scheduler runs one job at a time, so one hung
connection with no timeout stalls every tenant's jobs."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
VERBS = {"get", "post", "put", "patch", "delete", "head", "request"}


def test_every_requests_call_sets_a_timeout():
    missing = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in VERBS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "requests"
                and not any(kw.arg == "timeout" for kw in node.keywords)
            ):
                missing.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not missing, f"requests calls without a timeout: {missing}"
