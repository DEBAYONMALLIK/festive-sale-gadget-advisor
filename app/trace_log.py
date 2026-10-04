"""Terminal tracing for the research pipeline.

Everything the pipeline does is printed to the console that started it, because the web UI only ever
shows a summarised log and a run that goes quiet there is indistinguishable from one that has died.
Writes go to stdout with flush=True: PowerShell buffers aggressively otherwise and the output arrives
in a lump minutes later, which defeats the purpose.

Secrets are never printed in full. print_key_inventory shows whether a key is loaded and its last four
characters, which is enough to tell "wrong key" from "no key" without putting a usable credential in a
terminal, a screen recording or a pasted log.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

# ---- colour -------------------------------------------------------------------------------------
_NO_COLOUR = os.getenv("NO_COLOR") is not None or os.getenv("LOG_COLOR", "1") == "0"

if os.name == "nt" and not _NO_COLOUR:        # Windows needs VT processing turned on explicitly
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetConsoleMode(k.GetStdHandle(-11), 7)
    except Exception:
        _NO_COLOUR = True


def _c(code: str, text: str) -> str:
    return text if _NO_COLOUR else f"\033[{code}m{text}\033[0m"


DIM, BOLD = lambda s: _c("2", s), lambda s: _c("1", s)
CYAN, GREEN = lambda s: _c("36", s), lambda s: _c("32", s)
YELLOW, RED = lambda s: _c("33", s), lambda s: _c("31", s)
MAGENTA, BLUE = lambda s: _c("35", s), lambda s: _c("34", s)

VERBOSE = os.getenv("LOG_VERBOSE", "1") != "0"
_START = time.monotonic()


def _stamp() -> str:
    now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    total = int(time.monotonic() - _START)
    return f"{DIM(now)} {DIM(f'+{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}')}"


def _emit(icon: str, body: str, indent: int = 0) -> None:
    print(f"{_stamp()} {' ' * indent}{icon} {body}", flush=True)


# ---- public API ---------------------------------------------------------------------------------
def node(name: str, label: str = "") -> None:
    """A graph node is starting."""
    _emit(CYAN("▶"), f"{BOLD(name)}" + (f"  {DIM(label)}" if label else ""))


def node_done(name: str, seconds: float, detail: str = "") -> None:
    _emit(GREEN("✓"), f"{BOLD(name)} {DIM(f'{seconds:.1f}s')}" + (f"  {detail}" if detail else ""))


def info(msg: str, indent: int = 2) -> None:
    _emit(DIM("·"), msg, indent)


def ok(msg: str, indent: int = 2) -> None:
    _emit(GREEN("✓"), msg, indent)


def warn(msg: str, indent: int = 2) -> None:
    _emit(YELLOW("!"), YELLOW(msg), indent)


def error(msg: str, indent: int = 0) -> None:
    _emit(RED("✖"), RED(msg), indent)


def agent_start(name: str, model: str, prompt_chars: int) -> None:
    if VERBOSE:
        _emit(MAGENTA("🤖"), f"{BOLD(name)} {DIM(f'({model}, prompt {prompt_chars:,} chars)')}", 2)


def agent_done(name: str, seconds: float, out_chars: int, turns: int = 0) -> None:
    if VERBOSE:
        extra = f", {turns} turns" if turns else ""
        _emit(MAGENTA("🤖"), f"{BOLD(name)} done {DIM(f'{seconds:.1f}s, output {out_chars:,} chars{extra}')}", 2)


def tool_start(agent: str, tool: str) -> None:
    if VERBOSE:
        _emit(BLUE("→"), f"{DIM(agent)} calls {BOLD(tool)}", 4)


# A real search result runs to thousands of characters. Anything this short is almost always an error
# string - a rate limit, a bad key, no results - so print it rather than reporting a meaningless size.
SHORT_RESULT_CHARS = int(os.getenv("LOG_SHORT_RESULT_CHARS", "300"))
SHOW_RESULTS = os.getenv("LOG_TOOL_RESULT", "0") == "1"


def tool_end(agent: str, tool: str, result: object, seconds: float = 0.0) -> None:
    if not VERBOSE:
        return
    text = "" if result is None else str(result)
    size = f"{len(text):,} chars" if text else "empty"
    _emit(BLUE("←"), f"{DIM(agent)} {BOLD(tool)} → {size}"
                     + (DIM(f" in {seconds:.1f}s") if seconds else ""), 4)
    if text and (SHOW_RESULTS or len(text) <= SHORT_RESULT_CHARS):
        preview = " ".join(text.split())[:400]
        tag = YELLOW("suspiciously short:") if len(text) <= SHORT_RESULT_CHARS else DIM("result:")
        _emit(" ", f"{tag} {DIM(preview)}", 6)


def llm_call(agent: str, n_items: int) -> None:
    if VERBOSE and os.getenv("LOG_LLM", "0") == "1":     # very chatty; off unless asked for
        _emit(DIM("~"), DIM(f"{agent} → model ({n_items} input items)"), 4)


def banner(title: str) -> None:
    line = "─" * max(8, 68 - len(title))
    print(f"\n{BOLD(CYAN(f'── {title} '))}{DIM(line)}", flush=True)


def mask(value: str | None) -> str:
    """Show only enough of a secret to tell which key is loaded."""
    if not value:
        return RED("missing")
    v = value.strip()
    return GREEN(f"set ({len(v)} chars, …{v[-4:]})") if len(v) > 8 else GREEN(f"set ({len(v)} chars)")


def print_key_inventory(names: list[str]) -> None:
    """Report which credentials are loaded, without printing any of them in full."""
    banner("Credentials")
    for n in names:
        print(f"   {n:<24} {mask(os.getenv(n))}", flush=True)
    print(f"   {DIM('(values are masked on purpose - a terminal is not a safe place for a usable key)')}",
          flush=True)


def print_settings(settings: dict) -> None:
    banner("Settings")
    for k, v in settings.items():
        print(f"   {k:<24} {v}", flush=True)
