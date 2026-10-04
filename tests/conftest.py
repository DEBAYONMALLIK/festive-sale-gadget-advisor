"""Test setup.

pipeline.py imports the OpenAI Agents SDK and LangGraph at module level, but none of the logic worth
testing here touches either: scoring, price selection, budget filtering and log formatting are plain
Python. Installing those SDKs (and a browser) in CI to test arithmetic would make every push slow and
would fail whenever an unrelated pin breaks, so the heavy modules are stubbed and the real pipeline.py
is imported on top of them.

The workflow pairs this with a separate job that pip-installs the true requirements and imports the
module for real, so a genuine import error cannot hide behind these stubs.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "price_agent"))


def _module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


class _Stub:
    """Accepts any constructor arguments and any attribute access."""

    def __init__(self, *a, **kw):
        self._a, self._kw = a, kw
        for k, v in kw.items():
            setattr(self, k, v)

    def __getattr__(self, item):
        return _Stub()

    def __call__(self, *a, **kw):
        return _Stub()

    def __enter__(self):            # agents.trace() is used as a context manager
        return self

    def __exit__(self, *exc):
        return False


def _install_stubs() -> None:
    if "agents" in sys.modules:
        return

    agents = _module("agents", Agent=_Stub, ModelSettings=_Stub, Runner=_Stub,
                     trace=lambda *a, **kw: _Stub(), RunHooks=object)
    mcp = _module("agents.mcp", MCPServerStdio=_Stub,
                  create_static_tool_filter=lambda **kw: None)
    # pipeline.py wraps agents.mcp.server.stdio_client, but only under `if os.name == "nt"`, so the
    # attribute is required on Windows and unused on Linux. Registering a module in sys.modules does
    # not attach it to its parent package, so both links are made by hand - otherwise the suite passes
    # on Linux and fails on Windows, which is exactly what happened.
    server = _module("agents.mcp.server", stdio_client=lambda *a, **kw: _Stub())
    mcp.server = server
    agents.mcp = mcp

    openai = _module("openai")
    types_mod = _module("openai.types")
    shared = _module("openai.types.shared", Reasoning=_Stub)
    openai.types = types_mod
    types_mod.shared = shared

    lg = _module("langgraph")
    graph = _module("langgraph.graph", END="__end__", START="__start__", StateGraph=_Stub)
    lg_types = _module("langgraph.types", Send=_Stub)
    lg.graph, lg.types = graph, lg_types


_install_stubs()
