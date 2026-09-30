# Deployment

Three hosts, one repository. GitHub is the source of truth; Vercel and Render auto-deploy from `main`, and `app/` is
mirrored to the Hugging Face Space.

```
GitHub  DEBAYONMALLIK/festive-sale-gadget-advisor
  ├─ push main ──▶ Vercel   (rootDir: web)          landing page + /api/health
  ├─ push main ──▶ Render   (rootDir: status-service) uptime probe
  └─ mirror app/ ─▶ HF Space linkinmallik/festive-sale-gadget-advisor
```

## 1. The application — Hugging Face Space

The Space's git root is the contents of `app/`, not the repo root, because Hugging Face requires `Dockerfile` and the
README card at the top level. `.github/workflows/sync-hf-space.yml` handles that: it publishes the `app/` subtree as
the Space's root commit on every push that touches `app/`.

**One-time setup:**

1. Create an empty Space at <https://huggingface.co/new-space> — owner `linkinmallik`, name
   `festive-sale-gadget-advisor`, SDK **Docker → Blank**. Do not add any files.
2. Create a write token at <https://huggingface.co/settings/tokens> (fine-grained, *Write access to contents of your
   Spaces*).
3. Add it to GitHub as a repository secret named `HF_TOKEN`:
   *Settings → Secrets and variables → Actions → New repository secret*.

After that, every push to `app/` redeploys the Space. To deploy without a code change, run the workflow manually from
the Actions tab (*Mirror app/ to Hugging Face Space → Run workflow*).

The workflow refuses to publish if a `.env` has slipped into `app/`, and force-pushes a single clean commit — the
Space's history is disposable because this repo is the source of truth.

**Manual fallback**, if you would rather not use the Action:

```bash
git remote add space https://USER:HF_TOKEN@huggingface.co/spaces/linkinmallik/festive-sale-gadget-advisor
git subtree push --prefix=app space main
```

Then in **Space settings → Variables and secrets**, add as *secrets*:

| Name | Value |
|---|---|
| `OPENAI_API_KEY` | your rotated OpenAI key |
| `TAVILY_API_KEY` | your rotated Tavily key |

Optional but recommended:

| Name | Value | Why |
|---|---|---|
| `PRICE_CONCURRENCY` | `2` | Each Chrome needs ~500 MB. Raise only if you upgrade hardware. |
| `PRICE_PROXY_SERVER` | a residential proxy URL | Datacenter IPs get far more captchas from Amazon and Flipkart. |
| `MAX_RUNS_PER_DAY` | `40` | Bill protection. |

Enable **persistent storage** if you want the memory graph and price history to survive restarts; without it the app
falls back to the container filesystem and resets on every rebuild.

The first build takes **15–25 minutes** — it installs Chrome, Node 22, two Python virtualenvs and four MCP servers.
Watch it under the Space's *Logs → Build* tab.

### Verify

```bash
curl -s https://linkinmallik-festive-sale-gadget-advisor.hf.space/healthz | jq
```

Expect `"status": "ok"` and `"missing_secrets": []`.

## 2. Front end — Vercel

Project settings that matter:

| Setting | Value |
|---|---|
| Root Directory | `web` |
| Framework Preset | Other / none |
| Build Command | *(empty)* |
| Output Directory | *(empty)* |

Environment variables (Production + Preview):

| Name | Value |
|---|---|
| `BACKEND_URL` | `https://linkinmallik-festive-sale-gadget-advisor.hf.space` |
| `HF_SPACE_ID` | `linkinmallik/festive-sale-gadget-advisor` |
| `REPO_URL` | `https://github.com/DEBAYONMALLIK/festive-sale-gadget-advisor` |

None of these are secret — they are public URLs, and the page shows them anyway. There is deliberately **no API key
on the front end**; `api/health.js` only reads public endpoints.

### Verify

```bash
curl -s https://<your-vercel-domain>/api/health | jq '{ok, reachable, configured, stage}'
```

## 3. Uptime probe — Render

Either apply `render.yaml` as a Blueprint, or create a web service by hand:

| Setting | Value |
|---|---|
| Runtime | Node |
| Root Directory | `status-service` |
| Build Command | *(none needed — zero dependencies)* |
| Start Command | `node server.js` |
| Health Check Path | `/healthz` |
| Instance Type | Free is fine |

Environment: `BACKEND_URL`, `HF_SPACE_ID`, `REPO_URL`, and `FRONTEND_URL` once the Vercel domain exists.

Free instances spin down after 15 minutes idle, so the probe history has gaps. That is acceptable for a status page —
the first request wakes it and it probes immediately. For gapless history, use a paid instance.

## Moving the app onto Render instead of Hugging Face

Possible, but it is not free. The app needs roughly 2 GB of RAM: headed Chrome under Xvfb is ~500 MB per instance
(`PRICE_CONCURRENCY=2` means two), and Gradio + LangGraph + pandas is another ~400 MB. Render's free instance type is
**0.1 CPU / 512 MB** — the image builds and the UI loads, then the first price check OOM-kills the instance.

To do it anyway:

1. New → Web Service → connect the repo.
2. **Runtime: Docker**, **Root Directory: `app`** (so the `Dockerfile` is found).
3. **Instance type `1c-2g` or larger.** `2c-4g` if you want `PRICE_CONCURRENCY` above 2.
4. Add a **disk** mounted at `/data`, 1 GB, and set `DATA_DIR=/data`.
5. Environment: `OPENAI_API_KEY`, `TAVILY_API_KEY`, `PORT=7860`,
   `PRICE_WORKER_PYTHON=/opt/price-env/bin/python`, `PRICE_CHROME_PATH=/usr/bin/google-chrome-stable`,
   `PRICE_NO_SANDBOX=1`, `PRICE_HEADLESS=0`.
6. Health Check Path `/healthz`.

Note the Dockerfile's `CMD` wraps the app in `xvfb-run`; keep it, or Chrome has no display and every price lookup fails.

Then point `BACKEND_URL` on Vercel and Render at the new service and the front end follows automatically.

## Why not Vercel for the app

Asked often, so for the record: Vercel has no Dockerfile support, no Chrome in the function runtime, and function
duration limits well under the 8–20 minutes one research run takes. It also cannot host the four long-lived stdio MCP
subprocesses the pipeline spawns. The front end is the right job for it.

## Routine operations

| Task | How |
|---|---|
| Deploy a front-end change | push to `main` — Vercel builds automatically |
| Deploy an app change | `git subtree push --prefix=app space main` |
| Restart the app | Space → Settings → Factory rebuild (or Restart for a soft bounce) |
| Read app logs | Space → Logs → Container |
| Check daily usage | `curl .../healthz \| jq .usage_today` |
| Raise the daily caps | `MAX_RUNS_PER_DAY` / `MAX_PRICE_CHECKS_PER_DAY` in Space secrets |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Front end says "backend unreachable" | Space asleep or build failed | Open the Space directly; check Build logs |
| `"configured": false` in `/healthz` | secrets missing or misnamed | Re-add in Space settings, exact names |
| Every price says "blocked by captcha" | datacenter IP | set `PRICE_PROXY_SERVER` to a residential proxy |
| App restarts mid-run | out of memory | lower `PRICE_CONCURRENCY` to 1, or upgrade hardware |
| Prices read but research never finishes | Reddit rate limits | expected; runs legitimately take 8–20 min |
| Memory cache empty after restart | no persistent storage | enable it in Space settings |
