# Key rotation checklist

The `.env` file in the original `whole_code.zip` contained **live credentials**. An archive gets copied, emailed,
uploaded and unzipped in places nobody tracks, so treat every one of these as compromised and rotate it. None of
them are in this repository — `.gitignore` excludes `.env` and `app/.dockerignore` keeps it out of the Docker image —
but that does not un-expose a key that has already travelled.

Only two of these are used by this application. The rest were in the same file and should be rotated anyway.

## Used by this app — rotate, then update the hosts

| Key | Rotate at | Then update |
|---|---|---|
| `OPENAI_API_KEY` | <https://platform.openai.com/api-keys> | HF Space → Settings → Variables and secrets |
| `TAVILY_API_KEY` | <https://app.tavily.com/home> | HF Space → Settings → Variables and secrets |

After updating a secret the Space restarts itself. Confirm with:

```bash
curl -s https://linkinmallik-festive-sale-gadget-advisor.hf.space/healthz | jq '{configured, missing_secrets}'
```

`{"configured": true, "missing_secrets": []}` means both keys took.

## Not used by this app — rotate and drop

| Key | Rotate at |
|---|---|
| `HF_TOKEN`, `HF_TOKEN_2` | <https://huggingface.co/settings/tokens> — revoke both, issue one new fine-grained token |
| `GOOGLE_API_KEY` | <https://console.cloud.google.com/apis/credentials> |
| `YOUTUBE_API_KEY` | same Google Cloud console (usually the same project) |
| `SERPER_API_KEY` | <https://serper.dev/api-key> |
| `LANGSMITH_API_KEY` | <https://smith.langchain.com/settings> |
| `PUSHOVER_TOKEN`, `PUSHOVER_USER` | <https://pushover.net/apps> |
| `TWILIO_RECOVERY_CODE` | **Highest priority.** A recovery code bypasses 2FA. Regenerate it at <https://console.twilio.com> → Account → Security, and check the login history while you are there. |

## Order of work

1. **`TWILIO_RECOVERY_CODE` first** — it is an account-takeover credential, not an API key.
2. `OPENAI_API_KEY` next — it is the one attached to a metered bill. Check
   <https://platform.openai.com/usage> for spend you do not recognise.
3. Both `HF_TOKEN`s — they can push to your Spaces and models.
4. The rest, in any order.

## Keeping it clean from here

- Never commit `.env`. It is already ignored; verify with `git check-ignore -v .env`.
- Secrets live in each host's own store: HF Space → *Variables and secrets*, Vercel → *Settings → Environment
  Variables*, Render → *Environment*. Mark them as secret, not plain variables, so they are write-only afterwards.
- `.env.example` documents every variable by name with no values. Keep it current instead of sharing a real `.env`.
- If you need to hand the project to someone, send the repo or a zip built from it — not your working directory.
- Set a monthly spend limit on the OpenAI key at <https://platform.openai.com/settings/organization/limits>.
  `MAX_RUNS_PER_DAY` protects you inside the app, but it resets when the process restarts, so a hard billing cap is
  the real backstop.
