"""Gradio front end for the multi-agent festive-sale product recommender.

Two tabs:
  * Research & recommend: the full LangGraph pipeline (scouts -> filter -> live prices -> deep dives -> reviewer -> writer),
    with the agents' progress streamed live.
  * Quick price check: just the browser agents, reading the lowest in-stock price on Amazon.in and Flipkart for one product.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import date, datetime

import gradio as gr
import pandas as pd


def _ensure_display() -> None:
    """Give Chrome a display before the pipeline is imported.

    Windows and macOS already have one, so there is nothing to do. On a headless Linux host the Docker image wraps
    the process in `xvfb-run`, but when the start command is fixed by the host the X server has to be started here
    instead. Marketplaces block headed Chrome noticeably less than headless, so this is worth doing; if Xvfb is
    unavailable we fall back to headless rather than failing every lookup.
    """
    import shutil
    import subprocess
    import sys as _sys

    if os.name == "nt" or _sys.platform == "darwin":
        return                              # a real desktop session is already present
    if os.environ.get("DISPLAY"):
        return
    if os.environ.get("PRICE_HEADLESS") == "1":
        return
    if not shutil.which("Xvfb"):
        os.environ["PRICE_HEADLESS"] = "1"
        print("[display] Xvfb not found; running Chrome headless", flush=True)
        return
    try:
        subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        os.environ["DISPLAY"] = ":99"
        print("[display] Xvfb started on :99", flush=True)
    except Exception as exc:
        os.environ["PRICE_HEADLESS"] = "1"
        print(f"[display] could not start Xvfb ({exc!r}); running Chrome headless", flush=True)


_ensure_display()

import pipeline as P  # noqa: E402  - imported after the display is set up
import trace_log as T  # noqa: E402

# A step that emits nothing for this long is almost certainly stuck rather than slow: the longest healthy
# step (live prices) reports per-product lines as it goes. Warn at the first number, give up at the second.
QUIET_WARN_SECONDS = int(os.getenv("QUIET_WARN_SECONDS", "240"))
STALL_LIMIT_SECONDS = int(os.getenv("STALL_LIMIT_SECONDS", "900"))

MAX_RUNS_PER_DAY = int(os.getenv("MAX_RUNS_PER_DAY", "10"))          # full research runs (each costs OpenAI tokens)
MAX_PRICE_CHECKS_PER_DAY = int(os.getenv("MAX_PRICE_CHECKS_PER_DAY", "40"))
MAX_RUNS_PER_IP = int(os.getenv("MAX_RUNS_PER_IP", "2"))             # so one visitor cannot drain the daily budget
MAX_CHECKS_PER_IP = int(os.getenv("MAX_CHECKS_PER_IP", "8"))

_lock = threading.Lock()
_usage = {"day": date.today().isoformat(), "runs": 0, "checks": 0, "ips": {}}


def _usage_file():
    return P.DATA_DIR / "usage.json"


def _usage_load() -> None:
    """Restore today's counters from disk.

    The budget has to survive a restart: the app is restarted every time a new tunnel is opened, and an in-memory
    counter would hand out a fresh quota each time - exactly the opposite of a daily cap.
    """
    try:
        saved = json.loads(_usage_file().read_text(encoding="utf-8"))
    except Exception:
        return
    if saved.get("day") == date.today().isoformat():
        _usage.update({k: saved.get(k, _usage[k]) for k in ("day", "runs", "checks", "ips")})
        print(f"[quota] resumed today's usage: {_usage['runs']} runs, {_usage['checks']} checks", flush=True)


def _usage_save() -> None:
    try:
        _usage_file().write_text(json.dumps(_usage), encoding="utf-8")
    except Exception as exc:
        print(f"[quota] could not save usage: {exc!r}", flush=True)


def _roll_day() -> None:
    if _usage["day"] != date.today().isoformat():
        _usage.update(day=date.today().isoformat(), runs=0, checks=0, ips={})


def _take(kind: str, limit: int, per_ip: int, ip: str) -> tuple[bool, str]:
    """Claim one unit of today's budget. Returns (allowed, reason-if-not)."""
    with _lock:
        _roll_day()
        if _usage[kind] >= limit:
            return False, (f"Today's shared limit of **{limit}** {kind} is used up. "
                           f"It resets at midnight ({date.today().isoformat()} server time).")
        seen = _usage["ips"].setdefault(ip, {"runs": 0, "checks": 0})
        if seen.get(kind, 0) >= per_ip:
            return False, (f"You have used your **{per_ip}** {kind} for today. "
                           f"This keeps one visitor from using up the shared daily budget.")
        _usage[kind] += 1
        seen[kind] = seen.get(kind, 0) + 1
        _usage_save()
        left = limit - _usage[kind]
        return True, f"{left} of {limit} {kind} left today"


def _client_ip(request) -> str:
    """Best-effort visitor identity. Behind the tunnel the real IP arrives in a forwarding header."""
    try:
        headers = dict(getattr(request, "headers", {}) or {})
        for h in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
            if headers.get(h):
                return headers[h].split(",")[0].strip()
        return getattr(getattr(request, "client", None), "host", "") or "unknown"
    except Exception:
        return "unknown"


_usage_load()


def _mode(choice: str) -> str:
    return "lowest" if choice.startswith("Every") else "fast"


# Graph node name -> what to show the user. Emitted the moment a step starts, so the log fills from the first
# second instead of staying blank until the first step happens to finish.
STEP_LABELS = {
    "_mcp_boot":        "Starting the research servers (Reddit, YouTube, web search, memory)",
    "intent_parser":    "Understanding your request (budget, priorities)",
    "reddit_scout":     "Searching Reddit for candidate models",
    "web_scout":        "Searching the web for buying guides",
    "candidate_filter": "Verifying specs and building a shortlist",
    "live_prices":      ("Opening Amazon.in and Flipkart in Chrome" if P.LIVE_PRICES
                         else "Collecting price estimates"),
    "memory_check":     "Checking the research cache",
    "reddit_deep_dive": "Reddit deep dive (owner experiences)",
    "youtube_deep_dive": "YouTube deep dive (review videos)",
    "scorer":           "Scoring the finalists",
    "reviewer":         "Reviewing the evidence for gaps",
    "memory_write":     "Saving research to the cache",
    "writer":           "Writing your answer",
}

# Extra reassurance for the steps that legitimately take a long time.
STEP_HINTS = {
    "_mcp_boot":         "First run after a restart also downloads them, which can take a few minutes.",
    "live_prices":       ("Chrome is reading every variant on both sites. This is the slowest step — several minutes."
                          if P.LIVE_PRICES else
                          "Live price checking is off, so these are search-result estimates, not verified prices."),
    "reddit_scout":      "Reddit rate-limits hard, so this can run for several minutes.",
    "reddit_deep_dive":  "Reddit rate-limits hard, so this step is slow by nature.",
    "youtube_deep_dive": "Fetching and searching video transcripts.",
}


# ----------------------------------------------------------------- progress store ----------------------------------
# Progress is published here and read by a polling timer, NOT pushed down the generator's stream.
#
# Gradio streams a generator's outputs over SSE. A proxy in front of the app - a Cloudflare quick tunnel, for
# instance - can buffer that stream and hold small events back until its buffer fills, which makes the whole run
# look frozen and then arrive at once, half an hour late. The timer below polls over ordinary request/response,
# which proxies do not buffer, so the status and log update reliably no matter what sits in between.
_PROGRESS: dict = {"headline": "", "hint": "", "log": [], "started": None, "running": False, "final": "",
                   "last_event": None}
_plock = threading.Lock()


def _p_reset() -> None:
    with _plock:
        _PROGRESS.update(headline="Starting the agents", hint="", log=[], started=time.monotonic(),
                         running=True, final="", last_event=time.monotonic())


def _p_step(headline: str, hint: str = "") -> None:
    with _plock:
        _PROGRESS["headline"], _PROGRESS["hint"] = headline, hint
        _PROGRESS["last_event"] = time.monotonic()


def _p_log(line: str) -> None:
    with _plock:
        _PROGRESS["log"].append(line)
        _PROGRESS["last_event"] = time.monotonic()


def _p_final(text: str) -> None:
    with _plock:
        _PROGRESS.update(running=False, final=text)


def _elapsed(since: float) -> str:
    total = int(max(0.0, time.monotonic() - since))
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def poll_progress():
    """Called by the timer a few times a second's worth of seconds. Returns (status markdown, log text)."""
    with _plock:
        headline, hint = _PROGRESS["headline"], _PROGRESS["hint"]
        log_text = "\n".join(_PROGRESS["log"])
        started, running, final = _PROGRESS["started"], _PROGRESS["running"], _PROGRESS["final"]
        last_event = _PROGRESS["last_event"]

    if not running and not final and not log_text:
        return gr.skip(), gr.skip()            # nothing has ever run; leave the UI alone
    if not running:
        return final or "✅ Done.", log_text

    status = f"⏳ **{headline}…** — {_elapsed(started)} elapsed of a typical 40–50 minutes."
    # The elapsed clock above is computed in this poll, so it keeps counting even if the pipeline has stopped
    # emitting anything. Without the line below a stall is indistinguishable from a slow step.
    quiet = (time.monotonic() - last_event) if last_event else 0.0
    if quiet > QUIET_WARN_SECONDS:
        status += (f"\n\n⚠️ **No activity for {int(quiet // 60)} minutes.** The step above may be stuck. "
                   f"The run gives up on its own after {STALL_LIMIT_SECONDS // 60} quiet minutes; "
                   f"the PowerShell window shows what it is waiting on.")
    elif hint:
        status += f"\n\n<small>{hint}</small>"
    return status, log_text

LAST_RUN = P.DATA_DIR / "last_run.json"


def _save_last_run(query: str, panes: tuple) -> None:
    """Persist a finished run so a dropped connection does not throw away 30+ minutes of work.

    The browser receives the result panes over the streamed response, so if the tunnel drops near the end they are
    gone from the page even though the work completed. Writing them to disk makes the run recoverable.
    """
    answer, scores_df, prices_df, sources = panes
    try:
        LAST_RUN.write_text(json.dumps({
            "query": query,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "answer": answer,
            "sources": sources,
            "scores": scores_df.to_dict("records") if hasattr(scores_df, "to_dict") else [],
            "prices": prices_df.to_dict("records") if hasattr(prices_df, "to_dict") else [],
        }, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:                      # never let saving break a finished run
        print(f"[last-run] could not save: {exc!r}", flush=True)


def _load_last_run():
    """Return the four result panes from the most recent finished run."""
    try:
        d = json.loads(LAST_RUN.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ("_No finished run saved yet._", pd.DataFrame(), EMPTY_PRICES, "")
    except Exception as exc:
        return (f"_Could not read the saved run: {exc}_", pd.DataFrame(), EMPTY_PRICES, "")
    header = (f"> Recovered run from **{d.get('finished_at', '?')}**  \n"
              f"> Query: _{d.get('query', '')}_\n\n")
    scores = pd.DataFrame(d.get("scores") or [])
    prices = pd.DataFrame(d.get("prices") or [], columns=PRICE_COLUMNS) if d.get("prices") else EMPTY_PRICES
    return (header + (d.get("answer") or "_(no answer was produced)_"), scores, prices, d.get("sources") or "")


PRICE_CHOICES = ["Every variant (finds the true lowest, slower)", "Default variant only (faster)"]
PRICE_COLUMNS = ["model", "site", "price (₹)", "variant", "deal", "status", "link"]
EMPTY_PRICES = pd.DataFrame(columns=PRICE_COLUMNS)


def _quotes_frame(quotes: list[dict]) -> pd.DataFrame:
    rows = []
    for q in quotes or []:
        variant = ", ".join(x for x in [q.get("storage"), q.get("color"), q.get("connectivity")] if x)
        rows.append({
            "model": q["model"],
            "site": q["site"].title(),
            "price (₹)": f"{q['price']:,.0f}" if q.get("ok") else "—",
            "variant": variant or "—",
            "deal": q.get("deal_label") or "",
            "status": "in stock" if q.get("ok") else ("blocked by captcha" if q.get("blocked") else (q.get("reason") or "no price")[:90]),
            "link": q.get("url") or "",
        })
    return pd.DataFrame(rows, columns=PRICE_COLUMNS) if rows else EMPTY_PRICES


def _sources_md(state: dict) -> str:
    """Build the source list straight from the evidence, grouped by model.

    The writer is asked for inline links, but it is a language model and sometimes renders a source as bare text.
    These links come from the data itself, so they cannot be lost in the write-up.
    """
    evidence = state.get("evidence") or []
    by_model: dict[str, dict[str, set]] = {}
    for e in evidence:
        get = (lambda k: getattr(e, k, None)) if not isinstance(e, dict) else e.get
        urls = get("urls") or []
        if not urls:
            continue
        clean = {u for u in urls if isinstance(u, str) and u.startswith("http")}
        if not clean:
            continue                        # nothing usable, so do not create an empty heading for this model
        model, source = get("model") or "—", get("source") or "other"
        by_model.setdefault(model, {}).setdefault(source, set()).update(clean)
    if not by_model:
        return ("_No source links were captured for this run._ The deep-dive agents return URLs with their findings; "
                "an empty list usually means the Reddit or YouTube MCP server failed to start.")
    out = []
    for model, sources in by_model.items():
        total = sum(len(v) for v in sources.values())
        out.append(f"**{model}** — {total} link{'s' if total != 1 else ''}")
        for source in ("reddit", "youtube"):
            for i, url in enumerate(sorted(sources.get(source, [])), 1):
                out.append(f"- {'🟠' if source == 'reddit' else '🔴'} [{source} {i}]({url})")
        out.append("")
    return "\n".join(out)


def _scores_frame(scores: list[dict]) -> pd.DataFrame:
    if not scores:
        return pd.DataFrame()
    df = pd.DataFrame(scores)
    if "price" in df:
        df["price"] = df["price"].map(lambda v: f"₹{v:,.0f}" if v else "—")
    return df.fillna("—")


def _banner() -> str:
    missing = P.missing_keys()
    warn = f"\n\n> ⚠️ **Server not configured:** missing secrets {', '.join(missing)}. Add them in the Space settings." if missing else ""
    return (f"### 🛍️ Festive Sale Gadget Advisor\n"
            f"Tell it what phone, laptop or tablet you want. A team of AI agents reads **Reddit** owner threads, **YouTube** "
            f"reviews and **buying guides**, then opens **Amazon.in** and **Flipkart** in a real browser to get the lowest "
            f"in-stock price for every shortlisted model.\n\n"
            f"🗓️ **{P.sale_status()}**{warn}")


async def _guarded(stream):
    """Turn a crash inside the pipeline into a normal error event.

    Without this an exception propagates into Gradio and the browser shows a bare "Connection errored out", which
    looks like the server died even though it is still running and still reachable.
    """
    try:
        async for ev in stream:
            yield ev
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        yield {"type": "error", "error": f"{type(exc).__name__}: {exc}", "state": None}


# ------------------------------------------------------------------ research tab -----------------------------------
async def research(query: str, price_choice: str, request: gr.Request = None):
    """Run the pipeline. Progress goes to the shared store (the timer renders it); this yields only the results."""
    EMPTY = ("", pd.DataFrame(), EMPTY_PRICES, "")
    query = (query or "").strip()
    _p_reset()

    if len(query) < 8:
        _p_final("\u270b Describe what you want, e.g. *phone under 30k with a great camera*.")
        yield EMPTY
        return
    if len(query) > 1500:
        query = query[:1500]
    if P.missing_keys():
        _p_final(f"\u274c The server is missing {', '.join(P.missing_keys())}.")
        yield EMPTY
        return
    allowed, note = _take("runs", MAX_RUNS_PER_DAY, MAX_RUNS_PER_IP, _client_ip(request))
    if not allowed:
        _p_final(f"\U0001f6a6 {note}\n\n<small>A full run costs real OpenAI tokens, which is why there is a cap. "
                 f"**Quick price check** is much cheaper and has its own allowance.</small>")
        yield EMPTY
        return
    print(f"[quota] run accepted - {note}", flush=True)

    _p_log("[0:00:00] \u25b6 Starting the agents\u2026")
    state: dict = {}
    yield EMPTY

    async for ev in _guarded(P.run_recommendation(query, _mode(price_choice))):
        kind = ev["type"]
        if kind == "step":
            label = STEP_LABELS.get(ev["node"], ev["node"])
            _p_step(label, STEP_HINTS.get(ev["node"], ""))
            _p_log(f"[{ev['elapsed']}] \u25b6 {label}")
            continue                                   # nothing in the result panes changed yet
        if kind == "log":
            _p_log(f"[{ev['elapsed']}]    \u21b3 {ev['line']}")
            continue
        if kind == "tick":
            continue                                   # the timer keeps its own clock
        if kind == "state":
            state = ev["state"] or state
        elif kind == "done":
            state = ev["state"] or state
            _p_final("\u2705 **Done.**")
        elif kind == "error":
            state = ev.get("state") or state
            _p_log(f"[\u2014] \u2716 {ev['error'][:200]}")
            _p_final(f"\u274c **The run stopped.** `{ev['error'][:300]}`\n\n"
                     f"<small>The server is still running \u2014 press the button to try again. "
                     f"Partial results are kept below.</small>")
        panes = (state.get("final_answer", ""),
                 _scores_frame(state.get("scores", [])),
                 _quotes_frame(state.get("quotes", [])),
                 _sources_md(state))
        if kind in ("done", "error"):
            _save_last_run(query, panes)
        yield panes


# ------------------------------------------------------------------ quick price tab --------------------------------
async def price_check(product: str, price_choice: str, request: gr.Request = None):
    product = (product or "").strip()
    if len(product) < 3:
        yield "✋ Type a product name, e.g. *Samsung Galaxy Tab S10 Lite*.", EMPTY_PRICES, ""
        return
    if not os.getenv("OPENAI_API_KEY"):
        yield "❌ The server is missing OPENAI_API_KEY.", EMPTY_PRICES, ""
        return
    allowed, note = _take("checks", MAX_PRICE_CHECKS_PER_DAY, MAX_CHECKS_PER_IP, _client_ip(request))
    if not allowed:
        yield f"🚦 {note}", EMPTY_PRICES, ""
        return
    yield "⏳ Opening Amazon.in and Flipkart… this takes 1-5 minutes.", EMPTY_PRICES, ""
    quotes, listings = await P.quick_price_check(product[:120], _mode(price_choice))
    best = P.cheapest_quote(quotes)
    if best:
        head = (f"✅ **Lowest in-stock: ₹{best.price:,.0f} on {best.site.title()}** "
                f"({', '.join(x for x in [best.storage, best.color] if x) or 'default variant'})"
                f"{' · ' + best.deal_label if best.deal_label else ''}  \n[Open listing]({best.url})")
    else:
        head = "⚠️ No in-stock price could be read. See the status column; a captcha on a shared server is the usual cause."
    detail = []
    for site, listing in listings.items():
        if listing.variants:
            df = P.variants_frame(listing)
            detail.append(f"**{site.title()}**: {listing.matched_title or ''}\n\n{df.to_markdown(index=False)}")
        if listing.bank_offers:
            detail.append(f"{site.title()} bank offers (not included in the price): " + "; ".join(listing.bank_offers))
    yield head, _quotes_frame([q.model_dump() for q in quotes]), "\n\n".join(detail)


# ------------------------------------------------------------------ UI ---------------------------------------------
EXAMPLES = [
    "Phone under 30k in India with a really good camera and solid battery life. I don't game much. No Samsung please.",
    "Tablet under 40k for note taking, drawing and a great display. I was thinking of the Galaxy Tab S10 Lite.",
    "Laptop under 70000 for college and coding, light weight and long battery, 16GB RAM must.",
    "Gaming phone around 40k with good thermals and fast charging.",
]

CSS = """
.gradio-container {max-width: 1150px !important; margin: auto;}

/* ---------- live agent log: terminal-like, so the eye tracks new lines ---------- */
#log textarea {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px; line-height: 1.65;
  background: var(--neutral-950, #0f1117); color: var(--neutral-200, #d7dae0);
  border-radius: 10px;
}

/* ---------- status line ---------- */
#status {
  padding: 11px 15px; margin: 6px 0 2px;
  border-left: 3px solid var(--primary-500, #f97316);
  background: var(--background-fill-secondary); border-radius: 0 10px 10px 0;
  font-size: 14.5px;
}
#status p {margin: 0;}

/* ---------- the answer: the thing people actually read ---------- */
#answer {
  background: var(--background-fill-primary);
  border: 1px solid var(--border-color-primary);
  border-radius: 16px;
  padding: 26px 30px;
  margin-top: 14px;
  box-shadow: 0 1px 2px rgba(0,0,0,.04), 0 12px 32px -16px rgba(0,0,0,.18);
  line-height: 1.72;
  font-size: 15.5px;
}
#answer > *:first-child {margin-top: 0;}
#answer > *:last-child {margin-bottom: 0;}

#answer h1, #answer h2 {
  font-size: 1.32em; font-weight: 700; letter-spacing: -.02em;
  margin: 1.5em 0 .5em; padding-bottom: .34em;
  border-bottom: 1px solid var(--border-color-primary);
}
#answer h3 {
  font-size: 1.1em; font-weight: 650; margin: 1.5em 0 .4em;
  color: var(--primary-600, #ea580c);
}
#answer h3:first-of-type {margin-top: .2em;}

#answer ul, #answer ol {padding-left: 1.35em; margin: .5em 0;}
#answer li {margin: .32em 0;}
#answer li::marker {color: var(--primary-500, #f97316);}

#answer strong {font-weight: 650;}
#answer a {
  color: var(--primary-600, #ea580c); text-decoration: underline;
  text-underline-offset: 2px; text-decoration-thickness: 1px;
  overflow-wrap: anywhere;
}
#answer a:hover {text-decoration-thickness: 2px;}

#answer blockquote {
  margin: .9em 0; padding: .6em 1em;
  border-left: 3px solid var(--primary-400, #fb923c);
  background: var(--background-fill-secondary); border-radius: 0 8px 8px 0;
}

/* comparison tables inside the answer */
#answer table {
  width: 100%; border-collapse: collapse; margin: 1em 0;
  font-size: .93em; display: block; overflow-x: auto;
}
#answer thead th {
  text-align: left; font-weight: 650; font-size: .86em;
  text-transform: uppercase; letter-spacing: .05em;
  padding: 9px 12px; white-space: nowrap;
  background: var(--background-fill-secondary);
  border-bottom: 2px solid var(--border-color-primary);
}
#answer tbody td {padding: 9px 12px; border-bottom: 1px solid var(--border-color-primary); vertical-align: top;}
#answer tbody tr:last-child td {border-bottom: 0;}
#answer tbody tr:first-child {font-weight: 600;}   /* the top pick */

/* ---------- sources panel ---------- */
#sources {font-size: 14px; line-height: 1.6;}
#sources ul {padding-left: 1.2em;}
#sources a {overflow-wrap: anywhere;}
"""

with gr.Blocks(title="Festive Sale Gadget Advisor") as demo:
    gr.Markdown(_banner())
    with gr.Tab("🔎 Research & recommend"):
        with gr.Row():
            query = gr.Textbox(label="What are you looking for?", lines=3, scale=4,
                               placeholder="e.g. phone under ₹30k with a great camera and good battery, no Samsung")
            with gr.Column(scale=1, min_width=220):
                mode = gr.Radio(PRICE_CHOICES, value=PRICE_CHOICES[0], label="Live price check")
                go = gr.Button("Find the best buy", variant="primary")
                recover = gr.Button("↻ Show last finished run", size="sm")
        gr.Examples(EXAMPLES, inputs=query)
        status = gr.Markdown(elem_id="status")
        with gr.Accordion("Live agent log", open=True):
            log = gr.Textbox(show_label=False, lines=14, max_lines=14, autoscroll=True, elem_id="log", interactive=False)
        answer = gr.Markdown(elem_id="answer")
        with gr.Accordion("📎 Sources — every link the agents used", open=False):
            sources = gr.Markdown(elem_id="sources")
        with gr.Accordion("Live prices (Amazon.in and Flipkart)", open=True):
            prices = gr.Dataframe(value=EMPTY_PRICES, wrap=True, interactive=False)
        with gr.Accordion("Score table", open=False):
            scores = gr.Dataframe(interactive=False)
        # Lock the button for the whole run. .then() runs whether the run succeeded, failed or was cancelled, so
        # the button always comes back; queue=False keeps the lock instant instead of waiting behind the queue.
        # show_progress="hidden" is essential, not cosmetic: the default "full" lays a loading overlay over every
        # output component for the whole event, so the streamed log sat underneath it invisibly for the entire run
        # and only appeared once the event finished. The status line and log below report progress instead.
        # The status line and log are driven by the timer, not by this event, so they survive a proxy that
        # buffers the SSE stream. This event only carries the result panes.
        timer = gr.Timer(2.0)
        timer.tick(poll_progress, outputs=[status, log], show_progress="hidden", queue=False)

        go.click(lambda: gr.update(interactive=False, value="Researching…"),
                 outputs=go, queue=False) \
          .then(research, [query, mode], [answer, scores, prices, sources],
                concurrency_limit=1, concurrency_id="research", show_progress="hidden") \
          .then(lambda: gr.update(interactive=True, value="Find the best buy"),
                outputs=go, queue=False)

        # A long run's results arrive over the streamed response, so a tunnel drop near the end loses them even
        # though the work finished. Every completed run is written to disk; this reads the last one back.
        recover.click(_load_last_run, outputs=[answer, scores, prices, sources],
                      queue=False, show_progress="hidden")

    with gr.Tab("💸 Quick price check"):
        gr.Markdown("Already know the model? Get its lowest in-stock price on both marketplaces, without the research.")
        with gr.Row():
            product = gr.Textbox(label="Product", placeholder="Samsung Galaxy Tab S10 Lite", scale=4)
            pmode = gr.Radio(PRICE_CHOICES, value=PRICE_CHOICES[0], label="Variants", scale=2)
        check = gr.Button("Check prices", variant="primary")
        pstatus = gr.Markdown()
        ptable = gr.Dataframe(value=EMPTY_PRICES, wrap=True, interactive=False)
        pdetail = gr.Markdown()
        check.click(lambda: gr.update(interactive=False, value="Checking…"), outputs=check, queue=False) \
             .then(price_check, [product, pmode], [pstatus, ptable, pdetail],
                   concurrency_limit=1, concurrency_id="prices", show_progress="hidden") \
             .then(lambda: gr.update(interactive=True, value="Check prices"), outputs=check, queue=False)

    gr.Markdown(
        "<small>Prices are read live from public product pages and can change at any moment, especially during the sales. "
        "Bank and card offers are not included. Captchas on a shared server can stop a lookup; the answer then says the price "
        "is not verified. Recommendations cite their Reddit and YouTube sources so you can check every claim.</small>")


def _attach_healthz(fastapi_app) -> None:
    """Expose /healthz as JSON so the Vercel front end and the Render status service can report on this backend.

    Registered on the FastAPI app that Gradio builds, after launch, so the Gradio mount, queue, theme and CSS are
    untouched. Never allowed to take the server down: a failure here just means no health route.
    """
    try:
        from fastapi.responses import JSONResponse

        async def healthz(_request):
            with _lock:
                usage = dict(_usage, day=str(_usage["day"]))
            missing = P.missing_keys()
            return JSONResponse({
                "status": "ok" if not missing else "unconfigured",
                "app": "festive-sale-gadget-advisor",
                "configured": not missing,
                "missing_secrets": missing,
                "sale_status": P.sale_status(),
                "limits": {"runs_per_day": MAX_RUNS_PER_DAY, "price_checks_per_day": MAX_PRICE_CHECKS_PER_DAY},
                "usage_today": usage,
                "price_mode_default": P.PRICE_MODE,
            }, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})

        fastapi_app.add_route("/healthz", healthz, methods=["GET"])
        # No anti-buffering middleware here: FastAPI refuses to add middleware once the app has started, and the
        # progress timer polls over ordinary requests anyway, so a buffered event stream no longer matters.
        print("[health] /healthz ready", flush=True)
    except Exception as exc:  # pragma: no cover - health route is best effort
        print(f"[health] could not attach /healthz: {exc!r}", flush=True)


KEY_NAMES = ["OPENAI_API_KEY", "TAVILY_API_KEY", "YOUTUBE_API_KEY", "GOOGLE_API_KEY",
             "SERPER_API_KEY", "LANGSMITH_API_KEY"]


def _startup_report() -> None:
    """Print what the server is about to run with, so a misconfiguration is obvious before the first
    run rather than forty minutes into one."""
    T.banner("Festive Sale Gadget Advisor")
    T.print_key_inventory(KEY_NAMES)
    T.print_settings({
        "live price check": "ON (real Chrome)" if P.LIVE_PRICES else "OFF - estimates only",
        "fast model": P.FAST_MODEL,
        "smart model": P.SMART_MODEL,
        "review loops": P.MAX_REVIEW_LOOPS,
        "finalists": P.MAX_FINALISTS,
        "runs/day": MAX_RUNS_PER_DAY,
        "price checks/day": MAX_PRICE_CHECKS_PER_DAY,
        "stall limit": f"{STALL_LIMIT_SECONDS // 60} min of silence",
        "verbose tool logs": "on" if T.VERBOSE else "off (LOG_VERBOSE=0)",
        "port": os.getenv("PORT", "7860"),
    })
    missing = [n for n in ("OPENAI_API_KEY",) if not os.getenv(n)]
    if missing:
        T.error(f"{', '.join(missing)} is not set - runs will fail immediately")
    T.banner("Ready - waiting for a request")


if __name__ == "__main__":
    _startup_report()
    fastapi_app, _local, _share = demo.queue(max_size=20).launch(
        server_name="0.0.0.0", server_port=int(os.getenv("PORT", "7860")),
        theme=gr.themes.Soft(), css=CSS, ssr_mode=False, prevent_thread_lock=True)
    _attach_healthz(fastapi_app)
    demo.block_thread()
