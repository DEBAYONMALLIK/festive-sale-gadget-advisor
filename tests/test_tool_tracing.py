"""The candidate filter fires ~8 tavily_search calls in one turn. Keyed by tool name alone, the first
completion consumed the only start time and the rest reported '-0.0s'. Same shape of bug applied to
node timings, because the deep dives fan out one task per (model, source)."""
import asyncio
import time
from collections import defaultdict, deque

import pipeline as P


class Tool:
    def __init__(self, name="tavily_search"):
        self.name = name


class Ag:
    name = "Candidate filter"


def durations(capsys_text):
    import re
    found = [float(m) for m in re.findall(r"in (-?\d+\.\d)s", capsys_text)]
    assert found, f"no duration was printed at all:\n{capsys_text}"
    return found


class TestParallelToolCallsGetRealDurations:
    def test_eight_simultaneous_calls_all_report_a_positive_duration(self, capsys):
        hooks = P._TraceHooks()

        async def go():
            await asyncio.gather(*[hooks.on_tool_start(None, Ag(), Tool()) for _ in range(8)])
            await asyncio.sleep(0.3)
            for _ in range(8):
                await hooks.on_tool_end(None, Ag(), Tool(), "x" * 5000)

        asyncio.run(go())
        times = durations(capsys.readouterr().out)
        assert len(times) == 8
        assert all(t >= 0.2 for t in times), f"parallel calls lost their start times: {times}"

    def test_a_duration_is_never_negative(self, capsys):
        hooks = P._TraceHooks()

        async def go():
            await hooks.on_tool_end(None, Ag(), Tool(), "x" * 5000)   # end with no matching start

        asyncio.run(go())
        assert all(t >= 0 for t in durations(capsys.readouterr().out))

    def test_different_tools_do_not_share_a_queue(self, capsys):
        # Both tools get a measurable sleep. The earlier version let tavily_search finish instantly
        # and asserted its duration was small, which on Windows measured exactly 0.0 - and 0.0 was
        # then not printed at all, so the test failed on timer resolution rather than on behaviour.
        hooks = P._TraceHooks()

        async def go():
            await hooks.on_tool_start(None, Ag(), Tool("search_reddit"))
            await asyncio.sleep(0.4)
            await hooks.on_tool_start(None, Ag(), Tool("tavily_search"))
            await asyncio.sleep(0.2)
            await hooks.on_tool_end(None, Ag(), Tool("tavily_search"), "y" * 5000)
            await hooks.on_tool_end(None, Ag(), Tool("search_reddit"), "y" * 5000)

        asyncio.run(go())
        tavily, reddit = durations(capsys.readouterr().out)
        assert reddit > tavily, "the tool that started first ran longer; the queues are shared"
        assert reddit >= 0.5 and tavily <= 0.4, f"tavily={tavily} reddit={reddit}"


class TestNodeFanOutTiming:
    """Mirrors the astream loop's bookkeeping for concurrently running nodes of the same name."""

    def test_concurrent_nodes_of_one_name_each_keep_a_start_time(self):
        started = defaultdict(deque)
        for _ in range(3):
            started["reddit_deep_dive"].append(time.time())
        assert len(started["reddit_deep_dive"]) == 3
        first = started["reddit_deep_dive"].popleft()
        assert len(started["reddit_deep_dive"]) == 2
        assert isinstance(first, float)

    def test_an_unmatched_completion_does_not_raise(self):
        started = defaultdict(deque)
        begun = started["scorer"]
        value = begun.popleft() if begun else time.time()
        assert value <= time.time()
