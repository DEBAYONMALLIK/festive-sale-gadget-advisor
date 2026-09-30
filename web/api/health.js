/**
 * Server-side health probe for the backend.
 *
 * Runs on Vercel rather than in the browser because the backend sets no CORS headers on its Gradio routes, and the
 * Hugging Face API can say whether a Space is merely asleep as opposed to genuinely broken.
 *
 * The backend may be supplied per-request (`?backend=`) because a Cloudflare quick tunnel gets a fresh hostname on
 * every run, so pinning one at build time would mean redeploying each time the laptop restarts. Accepting a URL
 * from the client makes this function an SSRF vector, so the host is checked against ALLOWED_SUFFIXES and the
 * scheme must be https - it will not fetch arbitrary addresses, internal hostnames or private IPs.
 */

const DEFAULT_BACKEND = (process.env.BACKEND_URL || '').replace(/\/+$/, '');
const SPACE_ID = process.env.HF_SPACE_ID || '';
const REPO_URL = process.env.REPO_URL || 'https://github.com/DEBAYONMALLIK/festive-sale-gadget-advisor';

// Public tunnel and PaaS domains only. No bare IPs, no internal names, no http.
const ALLOWED_SUFFIXES = [
  '.trycloudflare.com',
  '.cfargotunnel.com',
  '.hf.space',
  '.onrender.com',
  '.run.app',
  '.ngrok-free.app',
  '.ngrok.io',
  '.loca.lt',
  '.serveo.net',
];

/** Validate and normalise a candidate backend URL. Returns "" when it is not acceptable. */
function safeBackend(raw) {
  if (!raw || typeof raw !== 'string' || raw.length > 300) return '';
  let u;
  try {
    u = new URL(raw.trim());
  } catch {
    return '';
  }
  if (u.protocol !== 'https:') return '';
  const host = u.hostname.toLowerCase();
  if (!ALLOWED_SUFFIXES.some((s) => host.endsWith(s))) return '';
  return `${u.protocol}//${u.host}`;
}

async function withTimeout(url, ms) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), ms);
  try {
    return await fetch(url, { signal: ac.signal, cache: 'no-store', redirect: 'follow' });
  } finally {
    clearTimeout(timer);
  }
}

/** Space lifecycle stage from the Hub API: RUNNING | SLEEPING | BUILDING | RUNTIME_ERROR | ... */
async function spaceStage() {
  if (!SPACE_ID) return null;
  try {
    const r = await withTimeout('https://huggingface.co/api/spaces/' + SPACE_ID, 6000);
    if (!r.ok) return null;
    const j = await r.json();
    return (j && j.runtime && j.runtime.stage) || null;
  } catch {
    return null;
  }
}

/** The app's own /healthz, which also reports whether its secrets are configured. */
async function appHealth(backend) {
  if (!backend) return { reachable: false, error: 'no backend configured' };
  try {
    const r = await withTimeout(backend + '/healthz', 8000);
    if (!r.ok) return { reachable: false, httpStatus: r.status };
    const ct = r.headers.get('content-type') || '';
    if (!ct.includes('application/json')) return { reachable: true, health: null, note: 'non-json /healthz' };
    return { reachable: true, health: await r.json() };
  } catch (e) {
    return { reachable: false, error: e && e.name === 'AbortError' ? 'timeout' : 'unreachable' };
  }
}

export default async function handler(req, res) {
  const requested = (req.query && req.query.backend) || '';
  const fromClient = safeBackend(Array.isArray(requested) ? requested[0] : requested);
  const backend = fromClient || DEFAULT_BACKEND;
  const rejected = Boolean(requested && !fromClient);

  const [stage, app] = await Promise.all([spaceStage(), appHealth(backend)]);

  const building = stage === 'BUILDING' || stage === 'APP_STARTING';
  const sleeping = stage === 'SLEEPING' || stage === 'PAUSED';
  const reachable = Boolean(app.reachable);
  const configured = Boolean(app.health && app.health.configured);

  const body = {
    ok: reachable && configured,
    reachable,
    configured,
    sleeping: !reachable && (sleeping || building),
    stage: stage || 'n/a',
    backend,
    backendSource: fromClient ? 'client' : (DEFAULT_BACKEND ? 'env' : 'none'),
    repo: REPO_URL,
    health: app.health || null,
    checkedAt: new Date().toISOString(),
  };
  if (rejected) {
    body.rejected = 'That URL was not accepted. It must be https and on a public tunnel or PaaS domain.';
    body.allowed = ALLOWED_SUFFIXES;
  }
  if (app.error) body.error = app.error;
  if (app.httpStatus) body.httpStatus = app.httpStatus;
  if (app.note) body.note = app.note;

  res.setHeader('Cache-Control', 'no-store, max-age=0');
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.status(reachable ? 200 : 503).json(body);
}
