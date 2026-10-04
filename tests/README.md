# Tests

```bash
pip install -r requirements-dev.txt
pytest                       # 54 python tests
node --test tests/*.test.mjs # 18 tests for the Vercel function
```

No test needs an API key, a network connection or a browser. CI runs both suites on every push and
pull request (`.github/workflows/tests.yml`).

## What is covered

| File | What it protects |
|------|------------------|
| `test_price_selection.py` | Picking the cheapest **in-stock** variant, discarding EMI/accessory prices, refusing to quote a different product, reporting captchas as blocked rather than guessing |
| `test_price_honesty.py` | With `LIVE_PRICES=0`, every price is labelled an unverified estimate and no marketplace is opened |
| `test_model_matching.py` | The shopper's own model is never dropped or duplicated; generations and tiers (S23/S24, base/Pro, FE/Lite) stay distinct |
| `test_run_events.py` | The event stream the UI renders, and the stall watchdog that ends a hung run while keeping partial results |
| `test_trace_log.py` | **No credential is ever printed in full**; suspiciously short tool results are surfaced |
| `test_tool_tracing.py` | Parallel tool calls and fanned-out nodes each keep a real duration |
| `health-api.test.mjs` | The SSRF allowlist on the public Vercel function |

## Why the SDKs are stubbed

`pipeline.py` imports the OpenAI Agents SDK and LangGraph at module level, but the logic worth testing
— scoring, price selection, budget filtering, log formatting — is plain Python. `tests/conftest.py`
stubs those two packages so the suite runs in seconds with no heavy install.

Stubs can hide a real import error, so the `real-imports` CI job installs the genuine packages and
imports `pipeline` for real. Fast feedback from one job, honest verification from the other.

## One known gap

`test_model_matching.py` has an `xfail` recording that `model_key` does not strip bracketed text, so
`"Galaxy S24 (256 GB, Violet)"` will not match `"Samsung Galaxy S24"`. It is harmless while the filter
agent obeys *"search_name = brand + model only, no brackets"*, and it becomes a duplicate entry when it
does not. If you fix it, that test starts passing and pytest will tell you to remove the marker.
