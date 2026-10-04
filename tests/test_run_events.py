"""run_recommendation turns a LangGraph stream into the events the UI renders. The stall watchdog is
the reason a dead run now ends instead of showing a clock that counts upward forever, so it is worth
driving end to end with a graph that deliberately hangs."""
import asyncio
from contextlib import asynccontextmanager

import pytest

import pipeline as P


class FakeGraph:
    """Replays a scripted LangGraph stream, then optionally hangs like a blocked node."""

    def __init__(self, chunks, hang=False):
        self.chunks, self.hang = chunks, hang

    async def astream(self, *a, **kw):
        for chunk in self.chunks:
            yield chunk
        if self.hang:
            await asyncio.sleep(3600)


@pytest.fixture
def wire(monkeypatch):
    def _wire(graph):
        @asynccontextmanager
        async def servers(*a, **kw):
            yield {}
        monkeypatch.setattr(P, "open_servers", servers)
        monkeypatch.setattr(P, "build_agents", lambda s: {})
        monkeypatch.setattr(P, "graph", graph)
    return _wire


async def collect(timeout=10):
    out = []
    async for ev in P.run_recommendation("a phone under 50k"):
        out.append(ev)
        if ev["type"] in ("done", "error"):
            break
    return out


class TestEventStream:
    def test_a_node_start_becomes_a_step_event(self, wire):
        wire(FakeGraph([("debug", {"type": "task", "payload": {"name": "reddit_scout"}}),
                        ("values", {"query": "x"})]))
        events = asyncio.run(collect())
        steps = [e for e in events if e["type"] == "step"]
        assert "reddit_scout" in [s["node"] for s in steps]

    def test_node_log_lines_become_log_events(self, wire):
        wire(FakeGraph([("updates", {"scorer": {"log": ["scored 4 finalists"]}}),
                        ("values", {"query": "x"})]))
        events = asyncio.run(collect())
        assert "scored 4 finalists" in [e["line"] for e in events if e["type"] == "log"]

    def test_the_final_state_is_carried_on_the_done_event(self, wire):
        wire(FakeGraph([("values", {"final_answer": "buy the OnePlus"})]))
        events = asyncio.run(collect())
        assert events[-1]["type"] == "done"
        assert events[-1]["state"]["final_answer"] == "buy the OnePlus"

    def test_a_crash_becomes_an_error_event_not_an_exception(self, wire):
        class Boom(FakeGraph):
            async def astream(self, *a, **kw):
                raise RuntimeError("model refused")
                yield
        wire(Boom([]))
        events = asyncio.run(collect())
        assert events[-1]["type"] == "error" and "model refused" in events[-1]["error"]


class TestStallWatchdog:
    def test_a_hung_node_ends_the_run_instead_of_hanging_forever(self, wire, monkeypatch):
        monkeypatch.setattr(P, "STALL_LIMIT_SECONDS", 1)
        wire(FakeGraph([("values", {"finalists": ["kept"]})], hang=True))
        events = asyncio.run(collect())
        assert events[-1]["type"] == "error"
        assert "Stalled" in events[-1]["error"]

    def test_partial_results_survive_the_stall(self, wire, monkeypatch):
        monkeypatch.setattr(P, "STALL_LIMIT_SECONDS", 1)
        wire(FakeGraph([("values", {"finalists": ["kept"], "quotes": [1, 2]})], hang=True))
        events = asyncio.run(collect())
        assert events[-1]["state"]["quotes"] == [1, 2], "an hour of work must not be thrown away"

    def test_a_working_run_is_not_cut_off(self, wire, monkeypatch):
        monkeypatch.setattr(P, "STALL_LIMIT_SECONDS", 1)

        class Slow(FakeGraph):
            async def astream(self, *a, **kw):
                for _ in range(3):          # keeps reporting, just slowly
                    await asyncio.sleep(0.6)
                    yield ("updates", {"live_prices": {"log": ["still working"]}})
                yield ("values", {"final_answer": "done"})
        wire(Slow([]))
        events = asyncio.run(collect())
        assert events[-1]["type"] == "done", "progress must reset the watchdog"
