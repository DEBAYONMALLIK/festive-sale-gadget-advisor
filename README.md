# Festive Sale Gadget Advisor

A multi-agent shopping researcher for the Indian festive sales. Describe what you want in plain words and a team of
AI agents reads Reddit owner threads and YouTube reviews, shortlists real models, then drives **real Google Chrome**
through Amazon.in and Flipkart to read the lowest in-stock price for every variant.

| | | |
|---|---|---|
| **Front end** | https://festive-sale-gadget-advisor.vercel.app | ✅ live |
| **App** | https://linkinmallik-festive-sale-gadget-advisor.hf.space | ⏳ needs the Space created — see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| **Status probe** | `status-service/` on Render | ⏳ needs this repo pushed |

The front end is live now and degrades honestly: until the Space exists it shows "backend not deployed yet" and
re-probes every 30 seconds, then fills in the embedded app by itself. Nothing to change here when the backend lands.

## Why the pieces live where they do

The app is not a stateless web request. One research run takes **8–20 minutes**, launches headed Chrome inside Xvfb,
and spawns four stdio MCP servers (two Node, two Python). That rules out most hosts:

| Layer | Host | Reason |
|---|---|---|
| `app/` — the application | **Hugging Face Space** (Docker) | Needs ~2 GB RAM for Chrome + Xvfb + Gradio + LangGraph. The free CPU tier gives 2 vCPU / 16 GB. |
| `web/` — landing page + health probe | **Vercel** | Static edge delivery, plus one serverless function that health-checks the backend *server-side* so the browser never hits CORS. |
| `status-service/` — uptime probe | **Render** | Always-on external probe, so an outage is still reportable when the app itself is down. Dependency-free, fits the 512 MB free instance. |
| this repo | **GitHub** | Single source of truth. Pushing `main` redeploys Vercel and Render; `app/` is mirrored to the Space. |

**Vercel cannot host the application itself** — no Dockerfile support, no Chrome, and function duration caps far below
an 8-minute run. It hosts the front end only. Likewise Render's *free* instance type (0.1 CPU / 512 MB) will build the
image and then OOM the moment a price check opens Chrome; moving the app to Render means a 1c-2g instance or larger.

## Layout

```
app/                      the application — this directory is the Hugging Face Space root
  Dockerfile              Chrome + Xvfb + Node 22 + two Python venvs
  app.py                  Gradio UI (2 tabs) and the /healthz route
  pipeline.py             LangGraph state machine, agents, scoring, MCP wiring
  price_agent/            browser-use worker, run as a subprocess in its own venv
  seed/                   starter knowledge graph for the memory cache
web/                      Vercel front end
  index.html              landing page with the app embedded and a live status pill
  api/health.js           server-side probe of the Space (avoids CORS, detects "asleep")
status-service/           Render uptime probe (zero dependencies)
notebook/                 the original research notebook this was ported from
docs/                     deployment guide and the key-rotation checklist
render.yaml               Render blueprint for the status service
```

## Architecture

```
                    ┌─────────────────────────┐
  browser  ────────▶│  Vercel                 │  landing page, embeds the app
                    │  /api/health ───────────┼──┐  server-side probe (no CORS)
                    └─────────────────────────┘  │
                                                 ▼
                    ┌──────────────────────────────────────────────┐
                    │  Hugging Face Space (Docker, 16 GB)          │
                    │                                              │
                    │  Gradio ──▶ LangGraph pipeline               │
                    │               ├─ MCP: tavily, memory,        │
                    │               │       reddit-rss, yt         │
                    │               └─ price_worker subprocess     │
                    │                    └─ browser-use + Chrome   │
                    │                         under Xvfb           │
                    │  /healthz ◀──────────────────────────────┐   │
                    └──────────────────────────────────────────┼───┘
                                                              │
                    ┌─────────────────────────┐               │
                    │  Render status probe    │───────────────┘
                    │  /status, /healthz      │  polls every 5 min
                    └─────────────────────────┘
```

Scoring and all price arithmetic are **plain Python**, never a model — so the numbers cannot be hallucinated.

## Local development

```bash
cp .env.example .env          # add OPENAI_API_KEY and TAVILY_API_KEY
cd app && docker build -t gadget-advisor . && \
  docker run -p 7860:7860 --env-file ../.env gadget-advisor
```

Open <http://localhost:7860>. The front end and probe run standalone too:

```bash
cd status-service && node server.js          # :10000
cd web && npx vercel dev                     # :3000
```

## Deployment

See **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** for the full walkthrough, including how to move the app onto Render
paid hardware if you would rather not use Hugging Face.

## Security

The `.env` in the original source archive contained live API keys. Treat every key that has travelled through an
archive as compromised — **[docs/KEY-ROTATION.md](docs/KEY-ROTATION.md)** is the checklist.

Secrets are never committed: `.gitignore` excludes `.env`, and `app/.dockerignore` keeps it out of the image.

## Caveats worth repeating

- Prices are read live from public pages and change constantly during sale waves. Confirm on the listing before paying.
- Bank and card offers are reported separately and never folded into the headline price.
- Marketplaces challenge shared datacenter IPs. A blocked lookup is reported as unverified rather than guessed.
  Set `PRICE_PROXY_SERVER` to a residential proxy in production.
- Daily caps (`MAX_RUNS_PER_DAY`, `MAX_PRICE_CHECKS_PER_DAY`) exist to protect your OpenAI bill. They are per process
  and reset on restart.

## Licence

MIT — see [LICENSE](LICENSE). Not affiliated with Amazon or Flipkart.
