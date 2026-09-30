# Where this stands, and how to run it

## Live now

| | |
|---|---|
| **Front end** | https://festive-sale-gadget-advisor.vercel.app |

The page is deployed and public. It has a **Connect a backend** box: you run the app on your own machine, paste the
public URL it prints, and the page remembers it and embeds the app.

## Run the app (one command)

**Windows:**

```powershell
powershell -ExecutionPolicy Bypass -File run-local.ps1
```

**macOS / Linux:**

```bash
chmod +x run-local.sh && ./run-local.sh
```

The script checks Python 3.11+, finds Chrome, creates a virtualenv, installs everything, asks for your two API keys
once (saved to `.env`, never committed), downloads `cloudflared`, starts the app and opens a free Cloudflare tunnel.
It then prints:

```
YOUR PUBLIC URL
   https://something-random.trycloudflare.com
```

Paste that into **Connect a backend** on the Vercel page. Done — the app is live on the internet.

You need **Python 3.11+**, **Google Chrome**, and **Node.js** (for the Tavily and Memory agents). The script tells
you if any are missing.

## Why your own machine, and not a cloud host

Not a compromise — it is the better place for this app:

- **Fewer captchas.** Your own README notes that datacenter IPs get challenged by Amazon and Flipkart, and suggests
  a residential proxy. Running at home *is* a residential IP, so the app's main failure mode mostly disappears.
- **No cost, no card, no signup.** Cloudflare quick tunnels need no account.
- **Every free cloud option was worse.** Hugging Face now gates both the Docker SDK *and* CPU-basic hardware behind
  PRO — the free tier is ZeroGPU only, which is PyTorch GPU inference with a 5-minute daily quota, and this app uses
  no GPU and runs 8–20 minutes per request. Render's free instance is 0.1 CPU / 512 MB and would be killed the
  moment Chrome opens. Vercel cannot run it at all.

The trade-off: it is reachable only while your machine is awake and the script is running. The tunnel hostname also
changes on each restart, which is exactly why the page lets you paste a new one instead of needing a redeploy.

## If you later want it always-on

`Dockerfile` is still in `app/` and still works. `docs/DEPLOYMENT.md` covers Render (needs a 1c-2g instance,
~$25/mo) and Google Cloud Run (free tier covers roughly 70–80 runs/month, needs a card on file).

## Optional — put the source on GitHub

<https://github.com/new?name=festive-sale-gadget-advisor> — **public**, and leave README, `.gitignore` and licence
**unchecked** so the first push is clean.

Then from this folder:

```bash
git remote add origin https://github.com/DEBAYONMALLIK/festive-sale-gadget-advisor.git
git push -u origin main
```

## Optional — GitHub Actions / Hugging Face

Only relevant if you ever get PRO. Left in place so nothing is lost.

> **This costs nothing.** Hugging Face now gates the *Docker* SDK behind PRO, so the app was reworked to run on the
> free **Gradio** SDK instead, on free CPU-basic hardware (2 vCPU / 16 GB). See
> ["How this runs without Docker"](#how-it-was-made-free) below.

1. <https://huggingface.co/new-space> — owner `linkinmallik`, name `festive-sale-gadget-advisor`,
   SDK **Gradio → Blank**, hardware **CPU basic (free)**. Add no files.
2. Create a write token: <https://huggingface.co/settings/tokens> → fine-grained → *Write access to contents of your
   Spaces*.
3. Add it to GitHub as a secret named `HF_TOKEN`:
   *Settings → Secrets and variables → Actions → New repository secret*.
4. GitHub → **Actions** → *Mirror app/ to Hugging Face Space* → **Run workflow**.

The first Docker build takes **15–25 minutes** (Chrome, Node 22, two Python virtualenvs, four MCP servers). Watch it
under the Space's *Logs → Build*.

5. In **Space settings → Variables and secrets**, add as *secrets*:

| Name | Value |
|---|---|
| `OPENAI_API_KEY` | your rotated OpenAI key |
| `TAVILY_API_KEY` | your rotated Tavily key |

Confirm with:

```bash
curl -s https://linkinmallik-festive-sale-gadget-advisor.hf.space/healthz
```

`"configured": true` means both keys took. The Vercel page will go green by itself within 30 seconds.

## Step 3 — Render status probe

Render → **New → Blueprint** → pick the repo. `render.yaml` defines it; free instance is fine. Or by hand:

| Setting | Value |
|---|---|
| Runtime | Node |
| Root Directory | `status-service` |
| Start Command | `node server.js` |
| Health Check Path | `/healthz` |

Set `FRONTEND_URL` to the Vercel URL once it is in.

## Read this too

**[docs/KEY-ROTATION.md](docs/KEY-ROTATION.md)** — the `.env` in the archive you sent held live credentials for
OpenAI, Tavily, two Hugging Face tokens, Google, YouTube, Serper, LangSmith, Pushover **and a Twilio recovery code**,
which bypasses 2FA. Treat all of them as compromised; rotate the Twilio one first. None of them are in this
repository, but that does not un-expose a key that has already travelled through a zip.

**[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** — full deployment reference, including how to move the app onto Render
paid hardware instead of Hugging Face, and a troubleshooting table.

## How it was made free

Hugging Face gates the Docker SDK behind PRO ($9/month). The free Gradio SDK gives you **one** Python environment,
and this app appeared to need two, because its two core libraries disagreed about `openai`:

```
browser-use   0.13.10 -> openai==2.26.0   (exact pin)
openai-agents 0.22.3  -> openai>=3.0.0
```

That is why the original `Dockerfile` builds a second virtualenv at `/opt/price-env`.

The way out was version selection, not a second environment. `browser-use 0.11.13` still declares *flexible* ranges
(`openai<3,>=2.7.2`, `mcp>=1.10.1`) where every 0.12+ release hard-pins them, and `openai-agents 0.17.3` is the last
release that still accepts `openai>=2.26.0,<3` and `mcp<2`. They overlap exactly at:

```
openai==2.26.0   openai-agents==0.17.3   browser-use==0.11.13   mcp==1.26.0
```

Verified: `pip check` passes, every API the code calls exists in those versions, and the app boots and serves
`/healthz` on that stack. **Do not bump any of those four without re-checking the other three.**

Three runtime details make up for the missing Dockerfile, all in `app/`:

- `packages.txt` installs `chromium`, `xvfb` and `nodejs`/`npm` via apt.
- `pipeline.py` locates Chromium itself (`chromium`, `chromium-browser`, `google-chrome-stable`, Playwright cache),
  and runs the price worker on the current interpreter when no separate venv exists.
- `app.py` starts Xvfb before importing the pipeline, because the Gradio SDK fixes the start command so there is no
  `xvfb-run` wrapper. If Xvfb is missing it falls back to headless instead of failing every lookup.

`Dockerfile` is still in the repo and still works — use it on Render, Cloud Run, or a PRO Docker Space, where the
two-venv split applies and the newest libraries can be used.

## Why the app is not on Vercel or free Render

One research run takes 8–20 minutes, launches headed Chromium inside Xvfb, and spawns four stdio MCP servers.
Vercel has no Dockerfile support, no Chrome, and function limits far below that. Render's free instance type is
0.1 CPU / 512 MB — it would OOM the moment a price check opens Chromium. The app needs roughly 2 GB, which the
Hugging Face free CPU tier provides (2 vCPU / 16 GB).
