"""Gradio front end for the multi-agent festive-sale product recommender.

Two tabs:
  * Research & recommend: the full LangGraph pipeline (scouts -> filter -> live prices -> deep dives -> reviewer -> writer),
    with the agents' progress streamed live.
  * Quick price check: just the browser agents, reading the lowest in-stock price on Amazon.in and Flipkart for one product.
"""
from __future__ import annotations

import asyncio
import os
import threading
from datetime import date

import gradio as gr
import pandas as pd


def _ensure_display() -> None:
    """Give Chrome a display before the pipeline is imported.

    The Docker image wraps the process in `xvfb-run`, but a Hugging Face Gradio Space fixes the start command, so
    there the X server has to be started from here. Marketplaces block headed Chrome noticeably less than headless,
    so this is worth doing; if Xvfb is unavailable we fall back to headless rather than failing every lookup.
    """
    import shutil
    import subprocess

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

MAX_RUNS_PER_DAY = int(os.getenv("MAX_RUNS_PER_DAY", "40"))          # full research runs (each costs OpenAI tokens)
MAX_PRICE_CHECKS_PER_DAY = int(os.getenv("MAX_PRICE_CHECKS_PER_DAY", "120"))

_lock = threading.Lock()
_usage = {"day": date.today(), "runs": 0, "checks": 0}


def _take(kind: str, limit: int) -> bool:
    with _lock:
        if _usage["day"] != date.today():
            _usage.update(day=date.today(), runs=0, checks=0)
        if _usage[kind] >= limit:
            return False
        _usage[kind] += 1
        return True


def _mode(choice: str) -> str:
    return "lowest" if choice.startswith("Every") else "fast"


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


# ------------------------------------------------------------------ research tab -----------------------------------
async def research(query: str, price_choice: str):
    query = (query or "").strip()
    if len(query) < 8:
        yield "✋ Describe what you want, e.g. *phone under 30k with a great camera*.", "", "", pd.DataFrame(), EMPTY_PRICES
        return
    if len(query) > 1500:
        query = query[:1500]
    if P.missing_keys():
        yield f"❌ The server is missing {', '.join(P.missing_keys())}.", "", "", pd.DataFrame(), EMPTY_PRICES
        return
    if not _take("runs", MAX_RUNS_PER_DAY):
        yield (f"🚦 Today's limit of {MAX_RUNS_PER_DAY} research runs is used up. Try again tomorrow, "
               f"or use **Quick price check**."), "", "", pd.DataFrame(), EMPTY_PRICES
        return

    log_lines: list[str] = []
    state: dict = {}
    status = "⏳ Researching… a full run usually takes **8-20 minutes** (live prices and Reddit rate limits are the slow parts). Keep this tab open."
    yield status, "", "", pd.DataFrame(), EMPTY_PRICES

    async for ev in P.run_recommendation(query, _mode(price_choice)):
        if ev["type"] == "log":
            log_lines.append(f"[{ev['elapsed']}] {ev['node']:<18} {ev['line']}")
        elif ev["type"] == "state":
            state = ev["state"] or state
        elif ev["type"] == "done":
            state = ev["state"] or state
            status = "✅ Done."
        elif ev["type"] == "error":
            state = ev.get("state") or state
            status = f"❌ The run failed: `{ev['error'][:300]}`"
        yield (status, "\n".join(log_lines), state.get("final_answer", ""),
               _scores_frame(state.get("scores", [])), _quotes_frame(state.get("quotes", [])))


# ------------------------------------------------------------------ quick price tab --------------------------------
async def price_check(product: str, price_choice: str):
    product = (product or "").strip()
    if len(product) < 3:
        yield "✋ Type a product name, e.g. *Samsung Galaxy Tab S10 Lite*.", EMPTY_PRICES, ""
        return
    if not os.getenv("OPENAI_API_KEY"):
        yield "❌ The server is missing OPENAI_API_KEY.", EMPTY_PRICES, ""
        return
    if not _take("checks", MAX_PRICE_CHECKS_PER_DAY):
        yield f"🚦 Today's limit of {MAX_PRICE_CHECKS_PER_DAY} price checks is used up.", EMPTY_PRICES, ""
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
#log textarea {font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px;}
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
        gr.Examples(EXAMPLES, inputs=query)
        status = gr.Markdown()
        with gr.Accordion("Live agent log", open=True):
            log = gr.Textbox(show_label=False, lines=14, max_lines=14, autoscroll=True, elem_id="log", interactive=False)
        answer = gr.Markdown()
        with gr.Accordion("Live prices (Amazon.in and Flipkart)", open=True):
            prices = gr.Dataframe(value=EMPTY_PRICES, wrap=True, interactive=False)
        with gr.Accordion("Score table", open=False):
            scores = gr.Dataframe(interactive=False)
        go.click(research, [query, mode], [status, log, answer, scores, prices],
                 concurrency_limit=1, concurrency_id="research")

    with gr.Tab("💸 Quick price check"):
        gr.Markdown("Already know the model? Get its lowest in-stock price on both marketplaces, without the research.")
        with gr.Row():
            product = gr.Textbox(label="Product", placeholder="Samsung Galaxy Tab S10 Lite", scale=4)
            pmode = gr.Radio(PRICE_CHOICES, value=PRICE_CHOICES[0], label="Variants", scale=2)
        check = gr.Button("Check prices", variant="primary")
        pstatus = gr.Markdown()
        ptable = gr.Dataframe(value=EMPTY_PRICES, wrap=True, interactive=False)
        pdetail = gr.Markdown()
        check.click(price_check, [product, pmode], [pstatus, ptable, pdetail],
                    concurrency_limit=1, concurrency_id="prices")

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
        print("[health] /healthz ready", flush=True)
    except Exception as exc:  # pragma: no cover - health route is best effort
        print(f"[health] could not attach /healthz: {exc!r}", flush=True)


if __name__ == "__main__":
    fastapi_app, _local, _share = demo.queue(max_size=20).launch(
        server_name="0.0.0.0", server_port=int(os.getenv("PORT", "7860")),
        theme=gr.themes.Soft(), css=CSS, ssr_mode=False, prevent_thread_lock=True)
    _attach_healthz(fastapi_app)
    demo.block_thread()
