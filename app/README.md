---
title: Festive Sale Gadget Advisor
emoji: 🛍️
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
pinned: false
short_description: AI agents pick gadgets + live Amazon/Flipkart prices
---

# Festive Sale Gadget Advisor

Ask for a phone, laptop or tablet in plain words ("phone under 30k with a great camera, no Samsung").
A team of agents does the research a careful buyer would do before the Big Billion Days and the Great Indian Festival:

1. **Intent parser** turns the request into a budget and weighted priorities.
2. **Reddit scout** and **web scout** (Tavily) collect candidates in parallel.
3. **Candidate filter** verifies specs and prices and builds a shortlist.
4. **Live prices**: browser agents (browser-use + Chrome) open Amazon.in and Flipkart, read every variant, and plain Python keeps
   the lowest in-stock price. Models over budget or out of stock are dropped.
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
| `PRICE_CONCURRENCY` | no | Browsers open at once (2). Each Chrome needs roughly 500 MB |
| `PINCODE` | no | Delivery pincode for stock checks |
| `PRICE_PROXY_SERVER`, `PRICE_PROXY_USERNAME`, `PRICE_PROXY_PASSWORD` | no | Route the browser through a residential proxy (fewer captchas) |
| `RESEARCH_PROXY` | no | HTTP(S) proxy for the Reddit and YouTube MCP servers |
| `AMAZON_SALE_START` / `FLIPKART_SALE_START` | no | Sale dates shown in the app (`2026-10-08` / `2026-10-09`) |

Research memory and price history are written to `/data` when persistent storage is enabled, otherwise to the app folder
(cleared on restart).

## Endpoints

| Route | Purpose |
|---|---|
| `/` | The Gradio UI |
| `/healthz` | JSON: configured/unconfigured, missing secrets, sale status, today's usage against the daily caps |

`/healthz` is what the Vercel front end and the Render uptime probe read, so keep it available if you fork this.

## Hardware

Runs on the **free CPU tier** (2 vCPU / 16 GB). The memory headroom is the point: headed Chrome under Xvfb plus
Gradio, LangGraph and pandas sits near 2 GB with `PRICE_CONCURRENCY=2`, so 512 MB hosts are not an option.
Enable **persistent storage** in Space settings if you want the research cache and price history to survive restarts.

## Run it locally

```bash
docker build -t gadget-advisor .
docker run -p 7860:7860 -e OPENAI_API_KEY=... -e TAVILY_API_KEY=... gadget-advisor
```

Then open <http://localhost:7860>. Source and the full deployment guide:
<https://github.com/DEBAYONMALLIK/festive-sale-gadget-advisor>
