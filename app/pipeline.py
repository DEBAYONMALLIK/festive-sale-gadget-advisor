"""Multi-agent product recommender (phones, laptops, tablets) with live Amazon.in and Flipkart prices.

A server-ready port of product_recommender_marketplace_new.ipynb. The agents, prompts, scoring and graph are the
same as in the notebook; what changed for a server:
  * configuration comes from environment variables (see the CONFIG block),
  * MCP servers are pre-installed binaries in the Docker image (no uvx/npx download per run),
  * the price worker runs from its own virtualenv and its processes are killed as a group on timeout,
  * `run_recommendation()` streams progress events so the web UI can show the agents working live.
"""
from __future__ import annotations

import asyncio
import json
import operator
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, AsyncIterator, Literal, TypedDict

import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv(override=False)

APP_DIR = Path(__file__).resolve().parent

# ============================================================== CONFIG ==============================================
def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None or v == "" else v.strip().lower() in {"1", "true", "yes", "on"}


def _data_dir() -> Path:
    # /data is Hugging Face persistent storage (if enabled for the Space); otherwise keep data next to the app.
    for cand in [os.getenv("DATA_DIR"), "/data", str(APP_DIR / "data")]:
        if not cand:
            continue
        p = Path(cand)
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".write_test"
            probe.write_text("ok")
            probe.unlink()
            return p
        except Exception:
            continue
    return APP_DIR


DATA_DIR = _data_dir()

FAST_MODEL = os.getenv("FAST_MODEL", "gpt-5-mini")      # scouts, filter, deep dives, intent parsing
SMART_MODEL = os.getenv("SMART_MODEL", "gpt-5")         # reviewer + final writer

MAX_FINALISTS = int(os.getenv("MAX_FINALISTS", "4"))
MAX_REVIEW_LOOPS = int(os.getenv("MAX_REVIEW_LOOPS", "2"))
CACHE_DAYS = int(os.getenv("CACHE_DAYS", "30"))


def today() -> str:
    return date.today().isoformat()


# ---- live marketplace prices ----
MAX_SHORTLIST = MAX_FINALISTS + 2
MARKETPLACES = ["amazon", "flipkart"]
PRICE_MODE = os.getenv("PRICE_MODE", "lowest")                 # "lowest" = every variant, "fast" = default variant only
BROWSER_MODEL = os.getenv("BROWSER_MODEL", "gpt-5-mini")
HEADLESS = _env_bool("PRICE_HEADLESS", False)                  # headed inside Xvfb when a display is available
NO_SANDBOX = _env_bool("PRICE_NO_SANDBOX", False)
PRICE_TIMEOUT = int(os.getenv("PRICE_TIMEOUT", "240"))
PRICE_CONCURRENCY = int(os.getenv("PRICE_CONCURRENCY", "3"))
PINCODE = os.getenv("PINCODE", "")
BUDGET_TOLERANCE = float(os.getenv("BUDGET_TOLERANCE", "1.05"))


def _find_chrome() -> str:
    """Locate a Chrome/Chromium binary on Windows, macOS or Linux.

    PRICE_CHROME_PATH wins when set. Otherwise look on PATH, then in the per-platform install locations, then in a
    Playwright-managed download. Returning "" lets browser-use fall back to its own detection.
    """
    explicit = os.getenv("PRICE_CHROME_PATH", "").strip()
    if explicit:
        return explicit

    for name in ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "chrome"):
        found = shutil.which(name)
        if found:
            return found

    candidates: list[Path] = []
    if os.name == "nt":
        for root in filter(None, (os.getenv("PROGRAMFILES"), os.getenv("PROGRAMFILES(X86)"), os.getenv("LOCALAPPDATA"))):
            candidates += [
                Path(root) / "Google/Chrome/Application/chrome.exe",
                Path(root) / "Chromium/Application/chrome.exe",
                Path(root) / "Microsoft/Edge/Application/msedge.exe",
            ]
    elif sys.platform == "darwin":
        candidates += [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ]
    else:
        candidates += [Path("/usr/bin/chromium"), Path("/usr/bin/chromium-browser"),
                       Path("/usr/bin/google-chrome-stable"), Path("/snap/bin/chromium")]
    for cand in candidates:
        if cand.is_file():
            return str(cand)

    for pattern in ("chromium-*/chrome-linux/chrome", "chromium-*/chrome-linux64/chrome",
                    "chromium-*/chrome-win/chrome.exe", "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"):
        for cand in sorted(Path.home().glob(f".cache/ms-playwright/{pattern}"), reverse=True):
            if cand.is_file():
                return str(cand)
    return ""


CHROME_PATH = _find_chrome()

SALES = {
    "amazon":   {"name": "Amazon Great Indian Festival", "starts": date.fromisoformat(os.getenv("AMAZON_SALE_START", "2026-10-08"))},
    "flipkart": {"name": "Flipkart Big Billion Days",    "starts": date.fromisoformat(os.getenv("FLIPKART_SALE_START", "2026-10-09"))},
}

PRICE_DIR = APP_DIR / "price_agent"
WORKER_PATH = PRICE_DIR / "price_worker.py"


def _find_worker_python() -> Path:
    """Pick the interpreter that runs the browser price worker.

    Two supported layouts:
      * separate venv  - the Docker image builds one at /opt/price-env because it pins browser-use against a
        different openai than openai-agents wants. PRICE_WORKER_PYTHON points at it.
      * single env     - the Hugging Face Gradio Space installs one compatible set (browser-use 0.11.13 +
        openai-agents 0.17.3 + openai 2.26.0), so the worker runs on this same interpreter.
    """
    explicit = os.getenv("PRICE_WORKER_PYTHON", "").strip()
    if explicit and Path(explicit).exists():
        return Path(explicit)
    local_venv = PRICE_DIR / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if local_venv.exists():
        return local_venv
    return Path(sys.executable)


WORKER_PY = _find_worker_python()
RUN_DIR = DATA_DIR / "price_runs"
RUN_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = DATA_DIR / "price_history.jsonl"

MEMORY_PATH = DATA_DIR / "memory" / "product_memory.jsonl"
MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
_seed = APP_DIR / "seed" / "product_memory.jsonl"
if not MEMORY_PATH.exists() and _seed.exists():
    shutil.copy(_seed, MEMORY_PATH)


def missing_keys() -> list[str]:
    return [k for k in ("OPENAI_API_KEY", "TAVILY_API_KEY") if not os.getenv(k)]


# The Windows fix from the notebook: a stdio MCP server started from a process whose stderr has no real file
# descriptor crashes with io.UnsupportedOperation: fileno. Harmless on Linux.
import functools  # noqa: E402

import agents.mcp.server  # noqa: E402

if os.name == "nt":
    agents.mcp.server.stdio_client = functools.partial(agents.mcp.server.stdio_client, errlog=subprocess.DEVNULL)

from agents import Agent, ModelSettings, Runner, trace  # noqa: E402
from agents.mcp import MCPServerStdio, create_static_tool_filter  # noqa: E402
from openai.types.shared import Reasoning  # noqa: E402


# ============================================================== MCP SERVERS =========================================
def _cmd(binary: str, fallback: list[str]) -> dict:
    # Use the pre-installed binary when it exists (Docker image); otherwise fall back to uvx/npx like the notebook.
    if shutil.which(binary):
        return {"command": binary, "args": []}
    return {"command": fallback[0], "args": fallback[1:]}


def server_specs() -> dict:
    youtube = _cmd("youtube-mcp-server", ["uvx", "--from", "yt-mcp-server", "youtube-mcp-server"])
    reddit = _cmd("reddit-rss-mcp", ["uvx", "reddit-rss-mcp"])
    tavily = _cmd("tavily-mcp", ["npx", "-y", "tavily-mcp@latest"]) | {"env": {"TAVILY_API_KEY": os.getenv("TAVILY_API_KEY", "")}}
    memory = _cmd("mcp-server-memory", ["npx", "-y", "@modelcontextprotocol/server-memory"]) | {"env": {"MEMORY_FILE_PATH": str(MEMORY_PATH)}}
    # Optional outbound proxy for the Reddit / YouTube servers (datacenter IPs are often rate-limited).
    proxy = os.getenv("RESEARCH_PROXY", "")
    if proxy:
        for p in (youtube, reddit):
            p["env"] = {"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy}
    return {
        "youtube": {"params": youtube, "timeout": 120,
                    "tools": ["search_videos", "get_video_info", "search_transcript", "get_comments"]},
        "reddit":  {"params": reddit, "timeout": 240, "tools": ["search_reddit", "get_post", "browse_subreddit"]},
        "tavily":  {"params": tavily, "timeout": 90, "tools": ["tavily_search", "tavily_extract"]},
        "memory":  {"params": memory, "timeout": 30, "tools": None},
    }


@asynccontextmanager
async def open_servers(*names):
    specs = server_specs()
    names = names or tuple(specs)
    async with AsyncExitStack() as stack:
        servers = {}
        for name in names:
            spec = specs[name]
            server = MCPServerStdio(
                name=name,
                params=spec["params"],
                client_session_timeout_seconds=spec["timeout"],
                cache_tools_list=True,
                max_retry_attempts=2,
                tool_filter=create_static_tool_filter(allowed_tool_names=spec["tools"]) if spec["tools"] else None,
            )
            servers[name] = await stack.enter_async_context(server)
        yield servers


# ============================================================== DATA CONTRACTS ======================================
class Priority(BaseModel):
    criterion: str = Field(description="Short lowercase name, e.g. camera, battery, performance, display")
    weight: float = Field(description="Relative importance between 0 and 1")

class UserConstraints(BaseModel):
    category: Literal["phone", "laptop", "tablet"]
    budget_min: float = Field(description="Lower bound, 0 if not given")
    budget_max: float
    currency: str = Field(description="ISO code such as INR or USD")
    region: str = Field(description="Country the user is buying in")
    priorities: list[Priority]
    must_haves: list[str]
    dealbreakers: list[str]
    use_case: str = Field(description="One sentence describing the buyer")
    user_shortlist: list[str] = Field(description="Models the shopper already named or is considering, as written; empty list if none")

class CandidateMention(BaseModel):
    name: str = Field(description="Full model name: brand + model + variant")
    mentions: int = Field(description="How many distinct posts/sources recommended it")
    why: str = Field(description="Why people recommend it, one sentence")
    approx_price: float = Field(description="Approximate price in the user's currency, 0 if unknown")
    urls: list[str]

class ScoutReport(BaseModel):
    candidates: list[CandidateMention]
    notes: str

class Finalist(BaseModel):
    name: str
    search_name: str = Field(description="Brand + model only, as typed into a shop search box: no storage, colour or brackets")
    approx_price: float
    key_specs: list[str]
    why_it_fits: str
    source_urls: list[str]

class Rejected(BaseModel):
    name: str
    reason: str

class FilterReport(BaseModel):
    finalists: list[Finalist]
    rejected: list[Rejected]

class CriterionFinding(BaseModel):
    criterion: str
    found_evidence: bool
    score: int = Field(description="1-10, or 0 when found_evidence is false")
    confidence: Literal["low", "medium", "high"]
    summary: str
    urls: list[str]

class DeepDiveReport(BaseModel):
    findings: list[CriterionFinding]
    red_flags: list[str]

class Gap(BaseModel):
    model: str = Field(description="Exact finalist name")
    source: Literal["reddit", "youtube"]
    criteria: list[str]
    instruction: str = Field(description="Concrete thing to search for")

class ReviewDecision(BaseModel):
    approved: bool
    gaps: list[Gap]
    concerns: list[str]
    reasoning: str

class Evidence(BaseModel):
    model: str
    source: Literal["reddit", "youtube"]
    criterion: str
    found: bool
    score: int
    confidence: Literal["low", "medium", "high"]
    summary: str
    urls: list[str]
    collected_on: str
    from_cache: bool = False


class RecState(TypedDict, total=False):
    query: str
    constraints: UserConstraints
    scout_reports: Annotated[list[dict], operator.add]
    finalists: list[Finalist]
    quotes: list[dict]
    rejected: list[Rejected]
    research_plan: list[dict]
    evidence: Annotated[list[Evidence], operator.add]
    scores: list[dict]
    review: ReviewDecision
    iteration: int
    final_answer: str
    log: Annotated[list[str], operator.add]


class DeepDiveTask(TypedDict):
    model: str
    source: str
    constraints: UserConstraints
    criteria: list[str]
    focus: str


# ============================================================== HELPERS =============================================
FAST_SETTINGS = ModelSettings(reasoning=Reasoning(effort="low"))


async def run_agent(agent: Agent, prompt: str, max_turns: int = 15):
    result = await Runner.run(agent, prompt, max_turns=max_turns)
    return result.final_output


def constraints_brief(c: UserConstraints) -> str:
    priorities = ", ".join(f"{p.criterion} ({p.weight:.0%})" for p in c.priorities)
    return (
        f"Category: {c.category}\n"
        f"Budget: {c.budget_min:,.0f} to {c.budget_max:,.0f} {c.currency} (buying in {c.region})\n"
        f"Priorities (weight): {priorities}\n"
        f"Must-haves: {', '.join(c.must_haves) or 'none'}\n"
        f"Deal-breakers: {', '.join(c.dealbreakers) or 'none'}\n"
        f"Already considering: {', '.join(c.user_shortlist) or 'none'}\n"
        f"Buyer: {c.use_case}\n"
        f"Today's date: {today()}"
    )


def make_config(agents_: dict, servers: dict | None = None, price_mode: str | None = None) -> dict:
    return {"configurable": {"agents": agents_, "servers": servers or {}, "price_mode": price_mode or PRICE_MODE},
            "recursion_limit": 60}


# ============================================================== INTENT PARSER =======================================
INTENT_INSTRUCTIONS = '''You convert a shopper's request for a phone, laptop or tablet into structured constraints.
- category: "phone", "laptop" or "tablet".
- budget_max: the upper limit as a number in the user's currency ("30k" -> 30000, "1.2 lakh" -> 120000).
  If no budget is given, choose a sensible mid-range value and mention that in use_case.
- budget_min: the lower bound if stated, otherwise 0.
- currency / region: infer from symbols and words (₹, "lakh", "k" with Indian context -> INR / India; $ -> USD / United States).
  If unclear, default to INR / India.
- priorities: 2-5 criteria with weights that roughly sum to 1. Prefer these names: camera, battery, performance,
  display, software, build, charging, gaming, thermals, keyboard, portability, stylus, speakers, value.
  Give the highest weight to what the user stressed most.
- must_haves: hard requirements (e.g. "5G", "16GB RAM", "OLED display").
- dealbreakers: things the user explicitly does not want.
- use_case: one sentence describing this buyer.
- user_shortlist: specific models the shopper already mentions ("I was thinking of the S10 Lite" -> ["Samsung Galaxy Tab S10 Lite"]),
  written as full product names. Empty list if none.'''


def make_intent_agent() -> Agent:
    return Agent(name="Intent parser", instructions=INTENT_INSTRUCTIONS, model=FAST_MODEL,
                 model_settings=FAST_SETTINGS, output_type=UserConstraints)


def normalize_constraints(c: UserConstraints) -> UserConstraints:
    priorities = [p for p in c.priorities if p.weight > 0]
    if not any(p.criterion == "reliability" for p in priorities):
        total = sum(p.weight for p in priorities) or 1
        priorities = [Priority(criterion=p.criterion, weight=p.weight / total * 0.9) for p in priorities]
        priorities.append(Priority(criterion="reliability", weight=0.1))
    total = sum(p.weight for p in priorities)
    priorities = [Priority(criterion=p.criterion.lower().strip(), weight=round(p.weight / total, 3)) for p in priorities]
    return c.model_copy(update={"priorities": sorted(priorities, key=lambda p: -p.weight)})


async def intent_parser(state: RecState, config) -> dict:
    agent = config["configurable"]["agents"]["intent"]
    constraints = normalize_constraints(await run_agent(agent, state["query"]))
    return {"constraints": constraints, "iteration": 0,
            "log": [f"parsed: {constraints.category}, max {constraints.budget_max:,.0f} {constraints.currency}, "
                    + ", ".join(f"{p.criterion}={p.weight}" for p in constraints.priorities)]}


# ============================================================== SCOUTS ==============================================
REDDIT_SCOUT_INSTRUCTIONS = '''You are a Reddit research scout. Find which specific models real users recommend for the shopper.
1. Run 3-4 search_reddit queries phrased the way people ask (e.g. "best phone under 30000", "camera phone under 30k").
   Use time_filter="year" so results are current. Useful subreddits:
   phones in India: IndiaTech, GadgetsIndia, PickAnAndroidForMe, Android;
   laptops: SuggestALaptop, IndianGaming, laptops; tablets: tablets, GalaxyTab, iPad, IndiaTech, GadgetsIndia. You can also search without a subreddit.
2. Recommendations live in comments, so open the 2-4 most relevant threads with get_post (comment_limit=40).
3. Count how many distinct posts/comments recommend each model.
Return 5-12 candidates with full model names (brand + model + variant) that plausibly fit the budget.
Never invent models or counts. If the feeds return little, return fewer candidates and say so in notes.'''

WEB_SCOUT_INSTRUCTIONS = '''You are a web research scout. Use tavily_search to find which models current buying guides and
review sites recommend for the shopper's budget and priorities.
1. Run 2-3 searches, e.g. "best camera phone under 30000 India <month> <year>" and "new <category> launches under <budget>".
   Use max_results=8. Prefer sources such as gsmarena, 91mobiles, smartprix, notebookcheck, rtings, the verge.
2. Note each model's approximate current price in the user's currency.
Return 5-12 candidates with full model names; mentions = number of sources recommending it; put the source URLs in urls.
Don't invent anything.'''


def make_reddit_scout(reddit) -> Agent:
    return Agent(name="Reddit scout", instructions=REDDIT_SCOUT_INSTRUCTIONS, model=FAST_MODEL,
                 model_settings=FAST_SETTINGS, mcp_servers=[reddit], output_type=ScoutReport)

def make_web_scout(tavily) -> Agent:
    return Agent(name="Web scout", instructions=WEB_SCOUT_INSTRUCTIONS, model=FAST_MODEL,
                 model_settings=FAST_SETTINGS, mcp_servers=[tavily], output_type=ScoutReport)


async def _scout(key: str, source: str, state: RecState, config) -> dict:
    agent = config["configurable"]["agents"][key]
    try:
        report = await run_agent(agent, "Shopper:\n" + constraints_brief(state["constraints"]), max_turns=20)
    except Exception as e:  # one scout failing shouldn't kill the run
        report = ScoutReport(candidates=[], notes=f"{source} scout failed: {e}")
    names = ", ".join(c.name for c in report.candidates)
    return {"scout_reports": [{"source": source, "report": report.model_dump()}],
            "log": [f"{source} scout found {len(report.candidates)}: {names}"]}

async def reddit_scout(state: RecState, config) -> dict:
    return await _scout("reddit_scout", "reddit", state, config)

async def web_scout(state: RecState, config) -> dict:
    return await _scout("web_scout", "web", state, config)


# ============================================================== CANDIDATE FILTER ====================================
FILTER_INSTRUCTIONS = f'''You are the candidate filter. You receive raw candidates from a Reddit scout and a web scout.
1. Merge only pure naming variants of the SAME product ("Galaxy Tab S10 Lite" = "Samsung Galaxy Tab S10 Lite 5G"). Use the
   full official name. NEVER merge different generations or tiers (S9 FE and S10 Lite, Pad 2 and Pad 3, Pro and base).
   If unsure, keep them as separate candidates.
2. Check the ~8 most promising with tavily_search (one query per model, e.g. "<model> price India specifications") for key
   specs and a rough price. Search-result prices are only approximate: the NEXT step reads live prices from Amazon and
   Flipkart, so do not reject a model just because a search price is up to 15% over budget.
3. Reject ONLY for hard facts: clearly far over budget, missing a must-have, hits a deal-breaker, or discontinued / not sold in
   the region. Never reject a model because it seems older, weaker or less ideal: ranking is done later by the scorer.
4. Keep every model that BOTH scouts recommended. Also keep up to 2 models that only one scout recommended when that scout's
   evidence is strong (Reddit: several owners praising it; web: several review sites).
5. Any model in "Already considering" MUST stay on the list unless it breaks a must-have or deal-breaker.
6. Rank the rest by fit to the weighted priorities and by how often the scouts recommended them
   (recommended by both scouts is a strong signal). Mention counts are rough estimates, so don't over-trust small differences.
7. For every model set search_name = brand + model only, the way you would type it into a shop search box, with no storage,
   colour or brackets (for example "Samsung Galaxy Tab S10 Lite").
Return at most {MAX_SHORTLIST} finalists, best first, plus every rejected model with a short reason.'''


def make_filter_agent(tavily) -> Agent:
    return Agent(name="Candidate filter", instructions=FILTER_INSTRUCTIONS, model=FAST_MODEL,
                 model_settings=FAST_SETTINGS, mcp_servers=[tavily], output_type=FilterReport)


async def candidate_filter(state: RecState, config) -> dict:
    agent = config["configurable"]["agents"]["filter"]
    prompt = (f"Shopper:\n{constraints_brief(state['constraints'])}\n\n"
              f"Scout reports:\n{json.dumps(state['scout_reports'], indent=1, ensure_ascii=False)}")
    report = await run_agent(agent, prompt, max_turns=25)
    shortlist = report.finalists[:MAX_SHORTLIST]
    return {"finalists": shortlist, "rejected": report.rejected,
            "log": [f"shortlist: {', '.join(f.name for f in shortlist)} | rejected {len(report.rejected)}"]}


# ============================================================== LIVE PRICES (browser worker) ========================
sys.path.insert(0, str(PRICE_DIR))
from price_worker import Listing, Variant  # noqa: E402,F401  (shared contract with the worker)

_price_sem: asyncio.Semaphore | None = None


def _sem() -> asyncio.Semaphore:
    global _price_sem
    if _price_sem is None:
        _price_sem = asyncio.Semaphore(PRICE_CONCURRENCY)
    return _price_sem


def _worker_env():
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "ANONYMIZED_TELEMETRY": "false",
            "BROWSER_USE_SETUP_LOGGING": "true"}


def _run_worker(cmd: list[str], log_path: Path, timeout: int):
    # Runs the worker in its own process group so Chrome is killed too if it hangs past the timeout.
    posix = os.name != "nt"
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, env=_worker_env(),
                                start_new_session=posix)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                if posix:
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            except Exception:
                proc.kill()
            proc.wait()


def _prune_logs(keep: int = 60):
    logs = sorted(RUN_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in logs[keep:]:
        try:
            p.unlink()
        except Exception:
            pass


async def fetch_listing(query: str, site: str, mode: str | None = None, extra_rules: str = "") -> dict:
    tag = uuid.uuid4().hex[:8]
    out_file, log_file = RUN_DIR / f"{site}-{tag}.json", RUN_DIR / f"{site}-{tag}.log"
    cmd = [str(WORKER_PY), str(WORKER_PATH), "--site", site, "--query", query, "--mode", mode or PRICE_MODE,
           "--model", BROWSER_MODEL, "--timeout", str(PRICE_TIMEOUT), "--out", str(out_file)]
    if PINCODE:
        cmd += ["--pincode", PINCODE]
    if extra_rules:
        cmd += ["--extra-rules", extra_rules]
    if HEADLESS:
        cmd += ["--headless"]
    if NO_SANDBOX:
        cmd += ["--no-sandbox"]
    if CHROME_PATH:
        cmd += ["--chrome", CHROME_PATH]

    started = time.time()
    async with _sem():
        await asyncio.to_thread(_run_worker, cmd, log_file, PRICE_TIMEOUT + 90)
    seconds = round(time.time() - started, 1)
    try:
        listing = Listing.model_validate_json(out_file.read_text(encoding="utf-8"))
    except Exception as e:
        listing = Listing(site=site, found=False, note=f"worker produced no valid result ({type(e).__name__})")
    finally:
        out_file.unlink(missing_ok=True)
    if listing.found and not listing.blocked:
        log_file.unlink(missing_ok=True)       # keep logs only for failures, for debugging
    _prune_logs()
    return {"listing": listing, "seconds": seconds, "log": log_file}


async def browser_selftest() -> tuple[bool, str]:
    out_file = RUN_DIR / "selftest.json"
    cmd = [str(WORKER_PY), str(WORKER_PATH), "--selftest", "--out", str(out_file)]
    cmd += (["--headless"] if HEADLESS else []) + (["--no-sandbox"] if NO_SANDBOX else []) + (["--chrome", CHROME_PATH] if CHROME_PATH else [])
    r = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", env=_worker_env(), timeout=180)
    ok = out_file.exists() and json.loads(out_file.read_text()).get("ok")
    return bool(ok), (r.stdout + r.stderr)[-1500:]


# ---- lowest in-stock price (plain Python) ----
MIN_PLAUSIBLE_PRICE = 1000


class PriceQuote(BaseModel):
    model: str
    site: str
    ok: bool = False
    blocked: bool = False
    price: float = 0
    mrp: float | None = None
    storage: str | None = None
    color: str | None = None
    connectivity: str | None = None
    seller: str | None = None
    url: str | None = None
    deal_label: str | None = None
    bank_offers: list[str] = []
    variants_seen: int = 0
    in_stock_variants: int = 0
    seconds: float = 0
    fetched_at: str = ""
    warnings: list[str] = []
    reason: str = ""


def _gb(text: str | None) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(tb|gb)", (text or "").lower())
    return float(m.group(1)) * (1024 if m and m.group(2) == "tb" else 1) if m else 0


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


FILLER_WORDS = {"the", "new", "and", "with", "for", "galaxy", "tablet", "phone", "smartphone", "mobile", "laptop",
                "5g", "4g", "wifi", "wi", "fi", "lte"}


def title_matches(query: str, title: str | None) -> tuple[bool, str]:
    if not title:
        return True, ""
    need = [w for w in _tokens(query) if w not in FILLER_WORDS]
    have = _tokens(title)
    missing = [w for w in need if w not in have]
    if missing and "".join(need) not in "".join(have):
        return False, f"title '{title}' does not match '{query}' (missing: {', '.join(missing)})"
    return True, ""


def build_quote(model: str, query: str, site: str, listing: Listing, seconds: float = 0, ref_price: float = 0) -> PriceQuote:
    q = PriceQuote(model=model, site=site, seconds=seconds, url=listing.url, seller=listing.seller,
                   deal_label=listing.deal_label, bank_offers=listing.bank_offers[:3],
                   variants_seen=len(listing.variants), blocked=listing.blocked,
                   fetched_at=datetime.now().strftime("%Y-%m-%d %H:%M"))
    if listing.blocked:
        q.reason = f"blocked (captcha or login wall). {listing.note}"[:200]
        return q
    if not listing.found:
        q.reason = f"not found. {listing.note}"[:200]
        return q
    matches, why = title_matches(query, listing.matched_title)
    if not matches:
        q.reason = why[:200]
        return q

    ceiling = max([v.price or 0 for v in listing.variants] + [v.mrp or 0 for v in listing.variants] + [ref_price or 0])
    floor = max(MIN_PLAUSIBLE_PRICE, 0.35 * ceiling)
    usable = []
    for v in listing.variants:
        if not v.in_stock or not v.price:
            continue
        if v.price < floor:
            q.warnings.append(f"ignored {v.price:g} ({v.storage}/{v.color}): far below the listing's other prices, probably EMI or an accessory")
            continue
        usable.append(v)
    q.in_stock_variants = len(usable)
    if not usable:
        q.reason = "no in-stock variant with a price" + (f" ({len(listing.variants)} variants seen)" if listing.variants else "")
        return q

    best = min(usable, key=lambda v: (v.price, -_gb(v.storage)))
    q.ok, q.price, q.mrp = True, best.price, best.mrp
    q.storage, q.color, q.connectivity = best.storage, best.color, best.connectivity
    if best.mrp and best.price > best.mrp * 1.02:
        q.warnings.append(f"price {best.price:g} is above the MRP {best.mrp:g}")
    if ref_price and not (0.4 * ref_price <= best.price <= 2.0 * ref_price):
        q.warnings.append(f"price is far from the expected ~{ref_price:,.0f}: check the listing")
    return q


def cheapest_quote(quotes: list[PriceQuote]) -> PriceQuote | None:
    ok = [q for q in quotes if q.ok]
    return min(ok, key=lambda q: q.price) if ok else None


def variants_frame(listing: Listing) -> pd.DataFrame:
    rows = [v.model_dump(exclude={"note"}) for v in listing.variants]
    return pd.DataFrame(rows).sort_values(["in_stock", "price"], ascending=[False, True]) if rows else pd.DataFrame()


# ---- price history and sale status ----
def log_quotes(quotes: list[PriceQuote]):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        for q in quotes:
            f.write(json.dumps(q.model_dump(exclude={"warnings", "reason"}), ensure_ascii=False) + "\n")


def history_frame() -> pd.DataFrame:
    if not HISTORY_FILE.exists():
        return pd.DataFrame()
    rows = [json.loads(line) for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    df = pd.DataFrame(rows)
    return df[df["ok"]].sort_values("fetched_at") if not df.empty else df


def sale_status() -> str:
    lines = []
    for s in SALES.values():
        days = (s["starts"] - date.today()).days
        when = f"starts {s['starts']:%d %b} (in {days} days)" if days > 0 else ("starts today" if days == 0 else "should be live now")
        lines.append(f"{s['name']} {when}")
    return "; ".join(lines)


# ---- the live_prices graph node ----
def _strip_brackets(name: str) -> str:
    return re.sub(r"\s*[\(\[].*?[\)\]]", "", name).strip()


BRAND_WORDS = {"samsung", "apple", "xiaomi", "oneplus", "lenovo", "motorola", "moto", "google", "nothing", "realme", "vivo",
               "oppo", "iqoo", "asus", "hp", "dell", "acer", "msi", "honor", "huawei", "microsoft"}


def model_key(text: str) -> frozenset:
    words = [w for w in _tokens(text) if w not in FILLER_WORDS | BRAND_WORDS | {"gb", "tb", "ram"} and not re.fullmatch(r"\d+(gb|tb)", w)]
    return frozenset(words)


def same_model(a: str, b: str) -> bool:
    return model_key(a) == model_key(b)


def is_user_model(user_models: list[str], f: Finalist) -> bool:
    return any(same_model(name, f.search_name) or same_model(name, f.name) for name in user_models)


def add_user_models(user_models: list[str], shortlist: list[Finalist]) -> list[Finalist]:
    out = list(shortlist)
    for name in user_models:
        if not any(same_model(name, f.search_name) or same_model(name, f.name) for f in out):
            out.append(Finalist(name=name, search_name=name, approx_price=0, key_specs=[],
                                why_it_fits="You asked about this model", source_urls=[]))
    return out


async def fetch_prices_for(items: list[dict], sites: list[str], mode: str | None = None) -> list[PriceQuote]:
    async def one(item, site):
        query = item.get("search_name") or _strip_brackets(item["name"])
        res = await fetch_listing(query, site, mode)
        return build_quote(item["name"], query, site, res["listing"], res["seconds"], item.get("ref", 0))
    return await asyncio.gather(*[one(it, s) for it in items for s in sites])


async def live_prices(state: RecState, config) -> dict:
    c = state["constraints"]
    mode = (config or {}).get("configurable", {}).get("price_mode") or PRICE_MODE
    shortlist = add_user_models(c.user_shortlist, state["finalists"])
    items = [{"name": f.name, "search_name": f.search_name, "ref": f.approx_price} for f in shortlist]
    quotes = await fetch_prices_for(items, MARKETPLACES, mode)
    log_quotes(quotes)

    by_model = defaultdict(list)
    for q in quotes:
        by_model[q.model].append(q)

    kept, dropped, notes = [], [], []
    for f in shortlist:
        qs = by_model[f.name]
        best = cheapest_quote(qs)
        if best:
            if best.price > c.budget_max * BUDGET_TOLERANCE or best.price < c.budget_min:
                dropped.append(Rejected(name=f.name, reason=f"live price ₹{best.price:,.0f} on {best.site} is outside the budget "
                                                            f"₹{c.budget_min:,.0f}-{c.budget_max:,.0f}"))
            else:
                kept.append(f.model_copy(update={"approx_price": best.price}))
        elif any(q.blocked or "worker" in q.reason or "no valid result" in q.reason for q in qs):
            kept.append(f)
            notes.append(f"{f.name}: price NOT verified ({'; '.join(q.reason[:70] for q in qs)})")
        else:
            why = "; ".join(f"{q.site}: {q.reason[:80]}" for q in qs)
            dropped.append(Rejected(name=f.name, reason=f"not available to buy: {why}"))

    if not kept and shortlist:
        cheap = sorted([f for f in shortlist if cheapest_quote(by_model[f.name])], key=lambda f: cheapest_quote(by_model[f.name]).price)[:2]
        kept = [f.model_copy(update={"approx_price": cheapest_quote(by_model[f.name]).price}) for f in cheap] or shortlist[:2]
        notes.append("WARNING: every model is over budget or unavailable; keeping the cheapest so the answer can say so")

    kept = sorted(kept, key=lambda f: not is_user_model(c.user_shortlist, f))[:MAX_FINALISTS]
    lines = [f"{q.model} @ {q.site}: " + (f"₹{q.price:,.0f} ({q.storage}, {q.color}) {q.seconds:.0f}s" if q.ok else f"no price ({q.reason[:60]})") for q in quotes]
    return {"finalists": kept, "rejected": state.get("rejected", []) + dropped,
            "quotes": [q.model_dump() for q in quotes],
            "log": lines + notes + [f"live prices: kept {len(kept)}, dropped {len(dropped)}"]}


def price_digest(state: RecState) -> str:
    quotes = [PriceQuote.model_validate(q) for q in state.get("quotes", [])]
    lines = []
    for f in state["finalists"]:
        qs = [q for q in quotes if q.model == f.name]
        ok = sorted([q for q in qs if q.ok], key=lambda q: q.price)
        if not ok:
            why = "; ".join(f"{q.site}: {q.reason[:70]}" for q in qs) or "no live check"
            lines.append(f"- {f.name}: live price NOT verified ({why}); ~₹{f.approx_price:,.0f} is only a search-result estimate")
            continue
        for q in ok:
            lines.append(f"- {f.name} on {q.site}: ₹{q.price:,.0f}{' <- LOWEST' if q is ok[0] else ''} | {q.storage}, {q.color} | "
                         f"MRP {q.mrp or 'n/a'} | deal: {q.deal_label or 'none'} | offers: {'; '.join(q.bank_offers) or 'none'} | "
                         f"{q.url} | checked {q.fetched_at}")
    return "\n".join(lines) or "no live prices"


# ============================================================== MEMORY CACHE ========================================
SOURCES = ("reddit", "youtube")


def _tool_json(result):
    try:
        return json.loads(result.content[0].text)
    except Exception:
        return getattr(result, "structured_content", None) or getattr(result, "structuredContent", None) or {}


async def memory_load(memory, names: list[str]) -> list[Evidence]:
    data = _tool_json(await memory.call_tool("open_nodes", {"names": names}))
    out = []
    for entity in data.get("entities", []):
        for obs in entity.get("observations", []):
            if not obs.startswith("EVIDENCE "):
                continue
            try:
                ev = Evidence.model_validate_json(obs[len("EVIDENCE "):])
            except Exception:
                continue
            age = (date.today() - date.fromisoformat(ev.collected_on)).days
            if age <= CACHE_DAYS:
                out.append(ev.model_copy(update={"from_cache": True}))
    return out


async def memory_save(memory, constraints: UserConstraints, finalists: list[Finalist], evidence: list[Evidence]):
    new = [e for e in evidence if e.found and not e.from_cache]
    await memory.call_tool("create_entities", {"entities": [
        {"name": f.name, "entityType": constraints.category, "observations": []} for f in finalists]})
    by_model = {}
    for f in finalists:
        by_model[f.name] = [f"[{today()}] ~{f.approx_price:,.0f} {constraints.currency}; specs: {'; '.join(f.key_specs)}"]
    for e in new:
        by_model.setdefault(e.model, []).append("EVIDENCE " + e.model_dump_json(exclude={"from_cache"}))
    await memory.call_tool("add_observations", {"observations": [
        {"entityName": m, "contents": obs} for m, obs in by_model.items()]})
    names = [f.name for f in finalists]
    await memory.call_tool("create_relations", {"relations": [
        {"from": a, "to": b, "relationType": "competes_with"} for a in names for b in names if a != b]})
    return len(new)


async def memory_check(state: RecState, config) -> dict:
    memory = config["configurable"]["servers"]["memory"]
    criteria = [p.criterion for p in state["constraints"].priorities]
    finalists = [f.name for f in state["finalists"]]
    try:
        cached = [e for e in await memory_load(memory, finalists) if e.criterion in criteria + ["red_flags"]]
    except Exception as e:
        cached = []
        state_note = f" (memory read failed: {type(e).__name__})"
    else:
        state_note = ""

    plan, reused = [], []
    for model in finalists:
        for source in SOURCES:
            covered = {e.criterion for e in cached if e.model == model and e.source == source and e.found}
            if set(criteria) <= covered:
                reused += [e for e in cached if e.model == model and e.source == source]
            else:
                plan.append({"model": model, "source": source})
    return {"research_plan": plan, "evidence": reused,
            "log": [f"memory: reused {len(reused)} evidence items, {len(plan)} research tasks to run{state_note}"]}


# ============================================================== DEEP DIVES ==========================================
REDDIT_DEEP_INSTRUCTIONS = '''You investigate ONE product on Reddit to learn how it performs for real owners.
1. search_reddit with 2-3 queries such as "<model> review", "<model> problems", "<model> <criterion>". Use time_filter="year".
2. Open the 2-3 most relevant threads with get_post (comment_limit=40). Owners' experiences after weeks of use matter most.
3. For EVERY criterion you are given, return one finding: score 1-10 from owner sentiment; confidence high (many consistent
   owner reports), medium (a few) or low (one, or indirect); a 1-2 sentence summary; and thread URLs.
   If you found nothing for a criterion, set found_evidence=false and score=0. Never guess.
4. red_flags: recurring problems (heating, bugs, display defects, poor service, update complaints). Empty list if none.
If a focus instruction is given, prioritise it.'''

YOUTUBE_DEEP_INSTRUCTIONS = '''You investigate ONE product on YouTube using review videos and their comments.
1. search_videos "<model> review" (limit 6). Pick the 2 most relevant, in-depth reviews whose title names this exact model
   (skip shorts, unboxings, and videos about other models).
2. For each chosen video and each criterion, call search_transcript with a short keyword (e.g. "battery", "screen on time",
   "low light", "thermal") to read what the reviewer actually said. Don't try to read whole transcripts.
3. Call get_comments (limit 40) on the best video to capture owner feedback and complaints.
4. For EVERY criterion return one finding: score 1-10 from the reviewers' verdicts, confidence, a 1-2 sentence summary,
   and the video URLs. If nothing covers a criterion, set found_evidence=false and score=0. Never guess.
5. red_flags: problems that reviewers or commenters mention repeatedly.
If a focus instruction is given, prioritise it.'''


def make_reddit_deep(reddit) -> Agent:
    return Agent(name="Reddit deep dive", instructions=REDDIT_DEEP_INSTRUCTIONS, model=FAST_MODEL,
                 model_settings=FAST_SETTINGS, mcp_servers=[reddit], output_type=DeepDiveReport)

def make_youtube_deep(youtube) -> Agent:
    return Agent(name="YouTube deep dive", instructions=YOUTUBE_DEEP_INSTRUCTIONS, model=FAST_MODEL,
                 model_settings=FAST_SETTINGS, mcp_servers=[youtube], output_type=DeepDiveReport)


def _no_evidence(task: DeepDiveTask, criterion: str, why: str) -> Evidence:
    return Evidence(model=task["model"], source=task["source"], criterion=criterion, found=False, score=0,
                    confidence="low", summary=why, urls=[], collected_on=today())


async def _deep_dive(task: DeepDiveTask, config) -> dict:
    agent = config["configurable"]["agents"][f"{task['source']}_deep"]
    prompt = (f"Product: {task['model']}\nCriteria to assess: {', '.join(task['criteria'])}\n"
              f"Focus: {task['focus'] or 'general assessment'}\n\nShopper context:\n{constraints_brief(task['constraints'])}")
    try:
        report: DeepDiveReport = await run_agent(agent, prompt, max_turns=20)
    except Exception as e:
        evidence = [_no_evidence(task, c, f"agent failed: {type(e).__name__}") for c in task["criteria"]]
        return {"evidence": evidence, "log": [f"{task['source']} x {task['model']}: FAILED ({type(e).__name__})"]}

    by_criterion = {f.criterion.lower().strip(): f for f in report.findings}
    evidence = []
    for c in task["criteria"]:
        f = by_criterion.get(c)
        if f is None:
            evidence.append(_no_evidence(task, c, "not covered by agent"))
        else:
            evidence.append(Evidence(model=task["model"], source=task["source"], criterion=c,
                                     found=f.found_evidence and f.score > 0, score=f.score,
                                     confidence=f.confidence, summary=f.summary, urls=f.urls, collected_on=today()))
    if report.red_flags:
        evidence.append(Evidence(model=task["model"], source=task["source"], criterion="red_flags", found=True, score=0,
                                 confidence="medium", summary="; ".join(report.red_flags), urls=[], collected_on=today()))
    found = sum(e.found for e in evidence if e.criterion != "red_flags")
    return {"evidence": evidence,
            "log": [f"{task['source']} x {task['model']}: evidence for {found}/{len(task['criteria'])} criteria"]}


async def reddit_deep_dive(task: DeepDiveTask, config) -> dict:
    return await _deep_dive(task, config)

async def youtube_deep_dive(task: DeepDiveTask, config) -> dict:
    return await _deep_dive(task, config)


def make_task(state_or_constraints, model: str, source: str, criteria=None, focus: str = "") -> DeepDiveTask:
    c = state_or_constraints["constraints"] if isinstance(state_or_constraints, dict) else state_or_constraints
    return {"model": model, "source": source, "constraints": c,
            "criteria": criteria or [p.criterion for p in c.priorities], "focus": focus}


# ============================================================== SCORER ==============================================
CONF_WEIGHT = {"high": 1.0, "medium": 0.7, "low": 0.4}


def latest_evidence(evidence: list[Evidence]) -> dict:
    latest = {}
    for e in evidence:
        key = (e.model, e.source, e.criterion)
        if e.found or key not in latest or not latest[key].found:
            latest[key] = e
    return latest


def compute_scores(constraints: UserConstraints, finalists: list[Finalist], evidence: list[Evidence], quotes: list[dict] | None = None) -> list[dict]:
    latest = latest_evidence(evidence)
    cheapest = {}
    for q in quotes or []:
        if q.get("ok") and (q["model"] not in cheapest or q["price"] < cheapest[q["model"]][0]):
            cheapest[q["model"]] = (q["price"], q["site"])
    rows = []
    for f in finalists:
        row, weighted, covered = {"model": f.name, "price": f.approx_price, "buy_at": cheapest.get(f.name, (0, "unverified"))[1]}, 0.0, 0.0
        for p in constraints.priorities:
            evs = [e for (m, s, c), e in latest.items() if m == f.name and c == p.criterion and e.found]
            if evs:
                w = [CONF_WEIGHT[e.confidence] for e in evs]
                s = sum(e.score * wi for e, wi in zip(evs, w)) / sum(w)
                row[p.criterion] = round(s, 1)
                weighted += p.weight * s
                covered += p.weight
            else:
                row[p.criterion] = None
        base = weighted / covered if covered else 0.0
        row["coverage"] = round(covered, 2)
        row["score"] = round(base * (0.75 + 0.25 * covered), 2)
        rows.append(row)
    return sorted(rows, key=lambda r: -r["score"])


async def scorer(state: RecState, config) -> dict:
    scores = compute_scores(state["constraints"], state["finalists"], state.get("evidence", []), state.get("quotes"))
    return {"scores": scores, "log": ["scores: " + ", ".join(f"{r['model']}={r['score']}" for r in scores)]}


# ============================================================== REVIEWER ============================================
REVIEWER_INSTRUCTIONS = '''You are the senior reviewer of a product research team. You get the shopper's constraints, a score table,
and evidence per model and source (reddit = owner experience, youtube = expert reviews).
Decide whether the research is good enough to recommend confidently.
Approve when every model in the top 3 has evidence for each criterion with weight >= 0.2 from at least one source,
and there are no unresolved contradictions (e.g. reviewers praise the battery but owners report heavy drain).
Otherwise list gaps. Each gap targets ONE model and ONE source ("reddit" or "youtube"), names the criteria to re-check,
and gives a concrete search instruction (e.g. "look for screen-on-time reports after the latest update").
At most 4 gaps; prioritise top-ranked models and high-weight criteria. Use the exact model names from the table.
If this is the final allowed round, approve and put remaining uncertainty in concerns instead of listing gaps.'''


def make_reviewer_agent() -> Agent:
    return Agent(name="Reviewer", instructions=REVIEWER_INSTRUCTIONS, model=SMART_MODEL, output_type=ReviewDecision)


def evidence_digest(finalists: list[Finalist], evidence: list[Evidence], max_urls: int = 2) -> str:
    latest = latest_evidence(evidence)
    lines = []
    for f in finalists:
        lines.append(f"\n### {f.name} (~{f.approx_price:,.0f}) — {'; '.join(f.key_specs)}")
        for (m, s, c), e in sorted(latest.items()):
            if m != f.name:
                continue
            tag = f"{e.score}/10 ({e.confidence})" if e.found and c != "red_flags" else ("" if e.found else "NO EVIDENCE")
            cached = " [cached]" if e.from_cache else ""
            lines.append(f"- [{s}] {c}: {tag}{cached} {e.summary} {' '.join(e.urls[:max_urls])}")
    return "\n".join(lines)


async def reviewer(state: RecState, config) -> dict:
    agent = config["configurable"]["agents"]["reviewer"]
    round_no = state.get("iteration", 0) + 1
    final_round = round_no > MAX_REVIEW_LOOPS
    prompt = (f"Shopper:\n{constraints_brief(state['constraints'])}\n\n"
              f"Review round {round_no} of {MAX_REVIEW_LOOPS + 1}."
              f"{' This is the FINAL allowed round.' if final_round else ''}\n\n"
              f"Score table:\n{pd.DataFrame(state['scores']).to_string(index=False)}\n\n"
              f"Evidence:{evidence_digest(state['finalists'], state.get('evidence', []))}")
    decision: ReviewDecision = await run_agent(agent, prompt)
    verdict = "APPROVED" if decision.approved else f"{len(decision.gaps)} gaps"
    return {"review": decision, "iteration": round_no,
            "log": [f"review round {round_no}: {verdict}. {decision.reasoning[:200]}"]}


# ============================================================== MEMORY WRITE + WRITER ===============================
WRITER_INSTRUCTIONS = '''You write the final recommendation for the shopper, in Markdown.
1. Top pick and 1-2 runners-up (max 3, ranked by the score table). For each: a one-line verdict, pros and cons tied
   to the shopper's priorities, and 2-4 source links (Reddit threads / YouTube videos) inline.
   Add a "Where to buy" line from the LIVE PRICES: the lowest price, the marketplace, the exact variant (storage, colour),
   the sale badge if any, and the product link. If a price says NOT verified, say so instead of quoting it as current.
2. A compact comparison table: model, price, score, and one column per priority.
3. "Who should pick what": one line per model.
4. "Also considered": rejected models with the reason.
5. Caveats: the reviewer's concerns; that prices were read at the time shown and can move during the sales, so mention the
   upcoming sale dates from the Sales line and suggest re-checking then; and that bank offers are not included in the prices.
Use only facts from the provided research. Be direct and concise.'''


def make_writer_agent() -> Agent:
    return Agent(name="Writer", instructions=WRITER_INSTRUCTIONS, model=SMART_MODEL)


async def memory_write(state: RecState, config) -> dict:
    memory = config["configurable"]["servers"]["memory"]
    try:
        n = await memory_save(memory, state["constraints"], state["finalists"], state.get("evidence", []))
        return {"log": [f"memory: saved {n} new evidence items"]}
    except Exception as e:           # a cache write failure must never cost the user their answer
        return {"log": [f"memory: save failed ({type(e).__name__}), continuing"]}


async def writer(state: RecState, config) -> dict:
    agent = config["configurable"]["agents"]["writer"]
    review = state.get("review")
    rejected = "\n".join(f"- {r.name}: {r.reason}" for r in state.get("rejected", []))
    prompt = (f"Shopper request: {state['query']}\n\n{constraints_brief(state['constraints'])}\n\n"
              f"Score table:\n{pd.DataFrame(state['scores']).to_string(index=False)}\n\n"
              f"Evidence:{evidence_digest(state['finalists'], state.get('evidence', []), max_urls=3)}\n\n"
              f"LIVE PRICES (lowest in-stock variant per marketplace):\n{price_digest(state)}\n\n"
              f"Sales: {sale_status()}\n\n"
              f"Rejected:\n{rejected or 'none'}\n\n"
              f"Reviewer concerns: {'; '.join(review.concerns) if review else 'none'}")
    answer = await run_agent(agent, prompt)
    return {"final_answer": answer, "log": ["final answer written"]}


# ============================================================== GRAPH ===============================================
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Send  # noqa: E402


def route_research(state: RecState):
    plan = state.get("research_plan", [])
    if not plan:
        return "scorer"
    return [Send(f"{t['source']}_deep_dive", make_task(state, t["model"], t["source"])) for t in plan]


def route_review(state: RecState):
    review = state["review"]
    if review.approved or not review.gaps or state["iteration"] > MAX_REVIEW_LOOPS:
        return "memory_write"
    names = {f.name.lower(): f.name for f in state["finalists"]}
    valid = {p.criterion for p in state["constraints"].priorities}
    sends = []
    for g in review.gaps:
        model = names.get(g.model.lower().strip())
        criteria = [c for c in (c.lower().strip() for c in g.criteria) if c in valid]
        if model:
            sends.append(Send(f"{g.source}_deep_dive", make_task(state, model, g.source, criteria or None, g.instruction)))
    return sends or "memory_write"


def build_graph():
    builder = StateGraph(RecState)
    builder.add_node("intent_parser", intent_parser)
    builder.add_node("reddit_scout", reddit_scout)
    builder.add_node("web_scout", web_scout)
    builder.add_node("candidate_filter", candidate_filter)
    builder.add_node("live_prices", live_prices)
    builder.add_node("memory_check", memory_check)
    builder.add_node("reddit_deep_dive", reddit_deep_dive)
    builder.add_node("youtube_deep_dive", youtube_deep_dive)
    builder.add_node("scorer", scorer)
    builder.add_node("reviewer", reviewer)
    builder.add_node("memory_write", memory_write)
    builder.add_node("writer", writer)

    builder.add_edge(START, "intent_parser")
    builder.add_edge("intent_parser", "reddit_scout")
    builder.add_edge("intent_parser", "web_scout")
    builder.add_edge(["reddit_scout", "web_scout"], "candidate_filter")
    builder.add_edge("candidate_filter", "live_prices")
    builder.add_edge("live_prices", "memory_check")
    builder.add_conditional_edges("memory_check", route_research, ["reddit_deep_dive", "youtube_deep_dive", "scorer"])
    builder.add_edge("reddit_deep_dive", "scorer")
    builder.add_edge("youtube_deep_dive", "scorer")
    builder.add_edge("scorer", "reviewer")
    builder.add_conditional_edges("reviewer", route_review, ["reddit_deep_dive", "youtube_deep_dive", "memory_write"])
    builder.add_edge("memory_write", "writer")
    builder.add_edge("writer", END)
    return builder.compile()


graph = build_graph()


def build_agents(servers: dict) -> dict:
    return {
        "intent": make_intent_agent(),
        "reddit_scout": make_reddit_scout(servers["reddit"]),
        "web_scout": make_web_scout(servers["tavily"]),
        "filter": make_filter_agent(servers["tavily"]),
        "reddit_deep": make_reddit_deep(servers["reddit"]),
        "youtube_deep": make_youtube_deep(servers["youtube"]),
        "reviewer": make_reviewer_agent(),
        "writer": make_writer_agent(),
    }


# ============================================================== ENTRY POINTS ========================================
async def run_recommendation(query: str, price_mode: str | None = None) -> AsyncIterator[dict]:
    """Run the whole graph and stream events:
         {"type": "log", "node": str, "line": str, "elapsed": "0:01:23"}
         {"type": "state", "state": dict}     (latest full state, after every step)
         {"type": "done", "state": dict} or {"type": "error", "error": str}

    The graph runs in its own asyncio task, which owns the MCP connections from start to finish (anyio cancel scopes must
    be opened and closed in the same task). Events are handed over through a queue, so the consumer can be any task.
    """
    queue: asyncio.Queue = asyncio.Queue()
    started = datetime.now()

    async def worker():
        final_state: dict = {}
        try:
            async with open_servers() as servers:
                config = make_config(build_agents(servers), servers, price_mode)
                with trace("Product recommender (HF Space)", metadata={"query": query[:200]}):
                    async for mode, chunk in graph.astream({"query": query}, config, stream_mode=["updates", "values"]):
                        if mode == "updates":
                            for node, update in chunk.items():
                                elapsed = str(datetime.now() - started).split(".")[0]
                                for line in (update or {}).get("log", []):
                                    await queue.put({"type": "log", "node": node, "line": line, "elapsed": elapsed})
                        else:
                            final_state = chunk
                            await queue.put({"type": "state", "state": chunk})
            await queue.put({"type": "done", "state": final_state})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await queue.put({"type": "error", "error": f"{type(e).__name__}: {e}", "state": final_state})

    task = asyncio.create_task(worker())
    try:
        while True:
            event = await queue.get()
            yield event
            if event["type"] in ("done", "error"):
                break
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass


async def quick_price_check(product: str, mode: str | None = None) -> tuple[list[PriceQuote], dict[str, Listing]]:
    """Read one product's live price on Amazon.in and Flipkart (no research). Returns quotes and the raw listings."""
    query = _strip_brackets(product.strip())
    results = await asyncio.gather(*[fetch_listing(query, site, mode) for site in MARKETPLACES])
    quotes, listings = [], {}
    for site, res in zip(MARKETPLACES, results):
        listings[site] = res["listing"]
        quotes.append(build_quote(product.strip(), query, site, res["listing"], res["seconds"]))
    log_quotes(quotes)
    return quotes, listings
