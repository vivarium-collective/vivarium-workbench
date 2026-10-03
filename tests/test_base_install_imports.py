"""The app must import and build on a BASE install (no ``[chat]`` extra).

``pydantic-ai`` and ``mcp`` are optional (the ``chat`` extra). The Claude Code MCP mount (``lib/claude_mcp.py``) is
wired into ``create_app``, so a careless top-level import of ``ai_tools`` (which needs pydantic-ai) or of ``mcp`` there
would stop the server from even starting for anyone without the extra. Each test runs the import in a fresh
interpreter that really cannot import the blocked packages — the only way to know it works without them.
"""
import subprocess
import sys

import pytest

_CODE = """
import importlib.abc, sys
BLOCKED = {blocked!r}
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError("blocked for the test: " + name)
sys.meta_path.insert(0, Block())
from vivarium_workbench.api import app as appmod
from vivarium_workbench.lib import claude_mcp
app = appmod.create_app()
assert claude_mcp.available() is False or "mcp" not in BLOCKED
assert (claude_mcp.build() is None) == ("mcp" in BLOCKED)
print("OK")
"""


@pytest.mark.parametrize("blocked", [("pydantic_ai", "mcp", "mcp_types"), ("mcp", "mcp_types")],
                         ids=["no chat extra at all", "pydantic-ai present, mcp missing"])
def test_the_app_imports_and_builds_without_the_optional_chat_packages(blocked):
    out = subprocess.run([sys.executable, "-c", _CODE.format(blocked=blocked)], capture_output=True, text=True, timeout=240)
    assert out.returncode == 0 and out.stdout.strip().endswith("OK"), out.stderr[-800:]
