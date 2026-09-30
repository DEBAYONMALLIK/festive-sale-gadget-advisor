# Where this stands, and the three things left

## Live now

| | |
|---|---|
| **Front end** | https://festive-sale-gadget-advisor.vercel.app |
| Vercel project | `festive-sale-gadget-advisor` (`prj_jOMaeAW2X320FmKjAazUT4TsB4yj`) |

Verified: the page renders, `/styles.css` and `/app.js` serve correctly, and the `/api/health` function runs.
Vercel's deployment authentication was **on by default** and has been turned off, so the page is publicly reachable.

Until the backend exists the status pill reads *"backend not deployed yet"* and the page re-probes every 30 seconds.
It fills in the embedded app on its own once the Space is up — **nothing needs changing on the front end.**

## Not yet deployed, and why

The session that built this could not reach Hugging Face: `huggingface.co` is blocked by network policy (403 at the
egress proxy), and the attached Hugging Face connector is read-only — it can list and read repositories but not
create or upload. Its GitHub credential was also scoped to pre-existing repositories, so it could not create a new
one. Everything below is therefore set up and committed, but needs you to open two doors.

## Step 1 — Create the GitHub repository

<https://github.com/new?name=festive-sale-gadget-advisor> — **public**, and leave README, `.gitignore` and licence
**unchecked** so the first push is clean.

Then from this folder:

```bash
git remote add origin https://github.com/DEBAYONMALLIK/festive-sale-gadget-advisor.git
git push -u origin main
```

## Step 2 — Create the Space and let the Action fill it

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
