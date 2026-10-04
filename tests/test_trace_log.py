"""The terminal log prints a lot, and the one thing it must never print is a usable credential.
Terminals get screenshotted, screen-recorded and pasted into chats."""
import importlib
import os

import pytest

import trace_log as T

REAL_KEY = "sk-proj-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH9876"


@pytest.fixture(autouse=True)
def _no_colour(monkeypatch):
    monkeypatch.setattr(T, "_NO_COLOUR", True)


class TestSecretsAreNeverPrintedInFull:
    def test_mask_does_not_contain_the_key(self):
        assert REAL_KEY not in T.mask(REAL_KEY)

    def test_mask_reveals_at_most_the_last_four_characters(self):
        out = T.mask(REAL_KEY)
        assert REAL_KEY[-4:] in out, "enough to tell which key is loaded"
        assert REAL_KEY[:-4] not in out
        assert REAL_KEY[8:20] not in out

    def test_mask_reports_a_missing_key_plainly(self):
        assert "missing" in T.mask(None) and "missing" in T.mask("")

    def test_a_short_secret_is_not_partially_revealed(self):
        assert "abc" not in T.mask("abc12")

    def test_the_inventory_never_prints_a_key(self, capsys, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", REAL_KEY)
        monkeypatch.setenv("TAVILY_API_KEY", "")
        T.print_key_inventory(["OPENAI_API_KEY", "TAVILY_API_KEY"])
        out = capsys.readouterr().out
        assert REAL_KEY not in out
        assert "OPENAI_API_KEY" in out and "9876" in out
        assert "missing" in out


class TestShortResultsAreSurfaced:
    """A real search result is thousands of characters. A 213-character one is an error string, and
    reporting only its length hides the reason a run produced nothing useful."""

    def test_a_short_result_is_printed_in_full(self, capsys):
        T.tool_end("Candidate filter", "tavily_search", '{"error":"usage limit exceeded"}', 0.7)
        assert "usage limit exceeded" in capsys.readouterr().out

    def test_a_short_result_is_flagged_as_suspicious(self, capsys):
        T.tool_end("Candidate filter", "tavily_search", "x" * 100, 0.7)
        assert "suspiciously short" in capsys.readouterr().out

    def test_a_normal_result_is_summarised_not_dumped(self, capsys):
        T.tool_end("Candidate filter", "tavily_search", "y" * 4000, 1.6)
        out = capsys.readouterr().out
        assert "4,000 chars" in out
        assert "yyyy" not in out, "a healthy result would flood the terminal"


class TestElapsedFormatting:
    def test_hours_minutes_seconds(self, monkeypatch):
        monkeypatch.setattr(T.time, "monotonic", lambda: T._START + 3671)
        assert "1:01:11" in T._stamp()
