/**
 * Server-side health probe for the backend Space.
 *
 * Runs on Vercel rather than in the browser for two reasons: the backend sets no CORS headers on its Gradio
 * routes, and the Hugging Face API tells us whether a Space is merely asleep (worth waiting for) as opposed to
 * genuinely broken — a distinction the page uses to decide between "waking up" and "unreachable".
 */

const BACKEND = (process.env.BACKEND_URL || 'https://linkinmallik-festive-sale-gadget-advisor.hf.space').replace(/\/+$/, '');
const SPACE_ID = process.env.HF_SPACE_ID || 'linkinmallik/festive-sale-gadget-advisor';
const REPO_URL = process.env.REPO_URL || 'https://github.com/DEBAYONMALLIK/festive-sale-gadget-advisor';

/** fetch with a hard deadline, so a hanging backend cannot hold the function open. */
async function withTimeout(url, ms) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), ms);
  try {
    return await fetch(url, { signal: ac.signal, cache: 'no-store' });
  } finally {
    clearTimeout(timer);
  }
}

/** Space lifecycle stage from the Hub API: RUNNING | SLEEPING | BUILDING | RUNTIME_ERROR | ... */
async function spaceStage() {
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
async function appHealth() {
  try {
    const r = await withTimeout(BACKEND + '/healthz', 8000);
    if (!r.ok) return { reachable: false, httpStatus: r.status };
    const ct = r.headers.get('content-type') || '';
    if (!ct.includes('application/json')) return { reachable: true, health: null, note: 'non-json /healthz' };
    return { reachable: true, health: await r.json() };
  } catch (e) {
    return { reachable: false, error: e && e.name === 'AbortError' ? 'timeout' : 'unreachable' };
  }
}

export default async function handler(req, res) {
  const [stage, app] = await Promise.all([spaceStage(), appHealth()]);

  const building = stage === 'BUILDING' || stage === 'APP_STARTING';
  const sleeping = stage === 'SLEEPING' || stage === 'PAUSED';
  const reachable = Boolean(app.reachable);
  const configured = Boolean(app.health && app.health.configured);

  const body = {
    ok: reachable && configured,
    reachable,
    configured,
    sleeping: !reachable && (sleeping || building),
    stage: stage || 'not_found',
    backend: BACKEND,
    repo: REPO_URL,
    health: app.health || null,
    checkedAt: new Date().toISOString(),
  };
  if (app.error) body.error = app.error;
  if (app.httpStatus) body.httpStatus = app.httpStatus;
  if (app.note) body.note = app.note;

  res.setHeader('Cache-Control', 'no-store, max-age=0');
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.status(reachable ? 200 : 503).json(body);
}
