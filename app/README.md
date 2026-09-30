---
title: Festive Sale Gadget Advisor
emoji: 🛍️
colorFrom: yellow
colorTo: red
sdk: gradio
sdk_version: 6.29.0
app_file: app.py
python_version: "3.11"
pinned: false
short_description: AI agents pick gadgets + live Amazon/Flipkart prices
---

# Festive Sale Gadget Advisor

Ask for a phone, laptop or tablet in plain words ("phone under 30k with a great camera, no Samsung").
A team of agents does the research a careful buyer would do before the Big Billion Days and the Great Indian Festival:

1. **Intent parser** turns the request into a budget and weighted priorities.
2. **Reddit scout** and **web scout** (Tavily) collect candidates in parallel.
3. **Candidate filter** verifies specs and prices and builds a shortlist.
4. **Live prices**: browser agents (browser-use + Chromium) open Amazon.in and Flipkart, read every variant, and plain
   Python keeps the lowest in-stock price. Models over budget or out of stock are dropped.
5. **Memory check** reuses recent research from a knowledge-graph cache (MCP Memory server).
6. **Reddit and YouTube deep dives** run in parallel, one task per model per source.
7. **Scorer** (plain Python) ranks by the shopper's weights; a **reviewer** sends agents back to fill gaps (max 2 loops).
8. **Writer** produces the answer with sources, a comparison table, and where to buy.

The **Quick price check** tab runs only step 4 for one product.

## Configuration (Space settings → Variables and secrets)

| Name | Required | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | yes (secret) | All agents and the browser agent |
| `TAVILY_API_KEY` | yes (secret) | Web scout and candidate filter |
| `FAST_MODEL` / `SMART_MODEL` / `BROWSER_MODEL` | no | Defaults `gpt-5-mini` / `gpt-5` / `gpt-5-mini` |
| `MAX_RUNS_PER_DAY` / `MAX_PRICE_CHECKS_PER_DAY` | no | Daily caps to protect your OpenAI bill (40 / 120) |
| `PRICE_CONCURRENCY` | no | Browsers open at once (2). Each Chromium needs roughly 500 MB |
| `PRICE_HEADLESS` | no | `1` forces headless. Left unset the app starts Xvfb and runs headed, which is blocked less |
| `PINCODE` | no | Delivery pincode for stock checks |
| `PRICE_PROXY_SERVER`, `PRICE_PROXY_USERNAME`, `PRICE_PROXY_PASSWORD` | no | Route the browser through a residential proxy (fewer captchas) |
| `RESEARCH_PROXY` | no | HTTP(S) proxy for the Reddit and YouTube MCP servers |
| `AMAZON_SALE_START` / `FLIPKART_SALE_START` | no | Sale dates shown in the app (`2026-10-08` / `2026-10-09`) |

Research memory and price history are written to `/data` when persistent storage is enabled, otherwise to the app
folder (cleared on restart).

## Endpoints

| Route | Purpose |
|---|---|
| `/` | The Gradio UI |
| `/healthz` | JSON: configured/missing secrets, sale status, today's usage against the daily caps |

## How this runs without Docker

The Docker SDK needs a PRO subscription, so this Space uses the **free Gradio SDK** instead. That normally cannot work
here, because the app needs two libraries that disagree about `openai`:

```
browser-use   0.13.10 -> openai==2.26.0  (exact)
openai-agents 0.22.3  -> openai>=3.0.0
```

The fix is version selection rather than a second virtualenv. `browser-use 0.11.13` keeps *flexible* ranges
(`openai<3,>=2.7.2`, `mcp>=1.10.1`) where 0.12+ hard-pin them, and `openai-agents 0.17.3` is the newest release still
accepting `openai>=2.26.0,<3` and `mcp<2`. They intersect at `openai==2.26.0` and `mcp==1.26.0`, so everything fits in
one environment. `requirements.txt` documents this; do not bump those four without re-checking all of them.

The rest is handled at runtime:

- **Chromium** comes from `packages.txt`. `pipeline.py` finds it by searching `chromium`, `chromium-browser`,
  `google-chrome-stable` and a Playwright cache, so the same code runs locally and in Docker.
- **Xvfb** is started by `app.py` before the pipeline imports, since the Gradio SDK fixes the start command and there
  is no `xvfb-run` wrapper. Headed Chrome gets challenged less than headless; if Xvfb is missing it falls back to
  headless rather than failing.
- **MCP servers**: the YouTube and Reddit ones are installed as Python packages; Tavily and Memory are Node and run
  through `npx` (`nodejs` and `npm` are in `packages.txt`).

`Dockerfile` is kept for running this on Docker hosts (Render, Cloud Run, or a PRO Docker Space), where the two-venv
split is used instead and the newest libraries apply.

## Hardware

Free CPU basic (2 vCPU / 16 GB) is enough. Chromium plus Gradio and LangGraph sits near 2 GB with
`PRICE_CONCURRENCY=2`. Enable **persistent storage** if you want the research cache and price history to survive
restarts.

Source and full deployment guide: <https://github.com/DEBAYONMALLIK/festive-sale-gadget-advisor>
