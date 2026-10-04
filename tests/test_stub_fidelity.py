"""conftest.py stubs the OpenAI Agents SDK. A stub that is missing something the real code touches
turns into a failure only on the platform that touches it.

That is not hypothetical: pipeline.py wraps `agents.mcp.server.stdio_client` inside
`if os.name == "nt"`, so the first version of these stubs passed on Linux and CI and failed on every
Windows machine. These tests assert the stub surface on every platform."""
import functools
import subprocess
import sys

import pytest


class TestAgentsStubSurface:
    def test_agents_mcp_server_is_reachable_by_attribute(self):
        # Registering a module in sys.modules does not attach it to its parent package, so
        # `import agents.mcp.server` can succeed while `agents.mcp.server` raises AttributeError.
        import agents
        assert hasattr(agents, "mcp")
        assert hasattr(agents.mcp, "server"), "agents.mcp.server must resolve as an attribute"

    def test_stdio_client_exists_for_the_windows_branch(self):
        import agents.mcp.server as server
        assert hasattr(server, "stdio_client")

    def test_the_windows_branch_can_actually_wrap_stdio_client(self):
        import agents.mcp.server as server
        wrapped = functools.partial(server.stdio_client, errlog=subprocess.DEVNULL)
        assert wrapped.keywords["errlog"] is subprocess.DEVNULL
        wrapped()          # must stay callable after wrapping

    @pytest.mark.parametrize("path", [
        "agents.Agent", "agents.ModelSettings", "agents.Runner", "agents.trace", "agents.RunHooks",
        "agents.mcp.MCPServerStdio", "agents.mcp.create_static_tool_filter",
        "openai.types.shared.Reasoning", "langgraph.graph.StateGraph", "langgraph.graph.START",
        "langgraph.graph.END", "langgraph.types.Send",
    ])
    def test_every_name_pipeline_imports_is_stubbed(self, path):
        module, _, attr = path.rpartition(".")
        __import__(module)
        assert hasattr(sys.modules[module], attr), f"{path} is imported by pipeline.py but not stubbed"

    def test_trace_works_as_a_context_manager(self):
        import agents
        with agents.trace("run", metadata={"query": "x"}):
            pass
