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

1. <https://huggingface.co/new-space> — owner `linkinmallik`, name `festive-sale-gadget-advisor`,
   SDK **Docker → Blank**. Add no files.
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

## Why the app is not on Vercel or free Render

One research run takes 8–20 minutes, launches headed Google Chrome inside Xvfb, and spawns four stdio MCP servers.
Vercel has no Dockerfile support, no Chrome, and function limits far below that. Render's free instance type is
0.1 CPU / 512 MB — it builds the image, then OOM-kills the moment a price check opens Chrome. The app needs roughly
2 GB, which the Hugging Face free CPU tier provides (2 vCPU / 16 GB). Moving it to Render means a `1c-2g` instance
or larger; `docs/DEPLOYMENT.md` has those steps.
