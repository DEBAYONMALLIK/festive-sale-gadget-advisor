/**
 * Uptime probe for the Festive Sale Gadget Advisor backend.
 *
 * Deliberately dependency-free (Node 22 built-ins only) so it builds in seconds and fits the 512 MB free instance.
 * It exists so an outage is still reportable when the app itself is down: it polls the Space from outside and keeps
 * a rolling history in memory.
 *
 *   GET /           human-readable status page
 *   GET /status     JSON: current state plus recent probe history
 *   GET /healthz    liveness of this probe service itself
 */

import http from 'node:http';

const PORT = Number(process.env.PORT || 10000);
const BACKEND = (process.env.BACKEND_URL || 'https://linkinmallik-festive-sale-gadget-advisor.hf.space').replace(/\/+$/, '');
const SPACE_ID = process.env.HF_SPACE_ID || 'linkinmallik/festive-sale-gadget-advisor';
const FRONTEND = process.env.FRONTEND_URL || '';
const REPO_URL = process.env.REPO_URL || 'https://github.com/DEBAYONMALLIK/festive-sale-gadget-advisor';
const INTERVAL_MS = Number(process.env.PROBE_INTERVAL_MS || 5 * 60 * 1000);
const HISTORY_MAX = 288; // 24h at a 5-minute interval

/** @type {{at:string, up:boolean, configured:boolean, stage:string, ms:number}[]} */
const history = [];
let latest = { at: null, up: false, configured: false, stage: 'unknown', ms: 0, detail: 'no probe yet' };

async function withTimeout(url, ms) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), ms);
  try {
    return await fetch(url, { signal: ac.signal, cache: 'no-store' });
  } finally {
    clearTimeout(timer);
  }
}

async function probe() {
  const started = Date.now();
  let stage = 'unknown';
  let up = false;
  let configured = false;
  let detail = '';

  try {
    const r = await withTimeout(`https://huggingface.co/api/spaces/${SPACE_ID}`, 6000);
    if (r.ok) {
      const j = await r.json();
      stage = (j && j.runtime && j.runtime.stage) || 'unknown';
    }
  } catch {
    /* Hub lookup is advisory only */
  }

  try {
    const r = await withTimeout(`${BACKEND}/healthz`, 10000);
    up = r.ok;
    if (r.ok) {
      const j = await r.json().catch(() => null);
      configured = Boolean(j && j.configured);
      detail = j && j.missing_secrets && j.missing_secrets.length
        ? `missing secrets: ${j.missing_secrets.join(', ')}`
        : 'ok';
    } else {
      detail = `HTTP ${r.status}`;
    }
  } catch (e) {
    detail = e && e.name === 'AbortError' ? 'timeout' : 'unreachable';
  }

  const entry = { at: new Date().toISOString(), up, configured, stage, ms: Date.now() - started };
  latest = { ...entry, detail };
  history.push(entry);
  if (history.length > HISTORY_MAX) history.shift();
  console.log(`[probe] up=${up} configured=${configured} stage=${stage} ${entry.ms}ms ${detail}`);
}

function uptimePct() {
  if (!history.length) return null;
  return Math.round((history.filter((h) => h.up).length / history.length) * 1000) / 10;
}

function statusPayload() {
  return {
    service: 'festive-sale-gadget-advisor-status',
    backend: BACKEND,
    frontend: FRONTEND || null,
    repo: REPO_URL,
    spaceId: SPACE_ID,
    latest,
    uptimePercent: uptimePct(),
    probes: history.length,
    probeIntervalMs: INTERVAL_MS,
    history: history.slice(-60),
  };
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function page() {
  const up = latest.up;
  const colour = up ? (latest.configured ? '#1f7a4d' : '#8a6100') : '#a32218';
  const label = up ? (latest.configured ? 'Operational' : 'Up, not fully configured') : 'Down';
  const pct = uptimePct();
  const bars = history
    .slice(-60)
    .map((h) => `<i title="${esc(h.at)} · ${h.up ? 'up' : 'down'} · ${h.ms}ms" style="background:${h.up ? '#1f7a4d' : '#a32218'}"></i>`)
    .join('');

  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backend status · Festive Sale Gadget Advisor</title>
<style>
:root{color-scheme:light dark;--bg:#fbfaf8;--fg:#1a1814;--mut:#6b6459;--card:#fff;--bd:#e5e1d8}
@media(prefers-color-scheme:dark){:root{--bg:#15140f;--fg:#f2efe8;--mut:#a39b8d;--card:#1e1c17;--bd:#37332c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.w{max-width:640px;margin:0 auto;padding:44px 16px}
h1{font-size:22px;letter-spacing:-.02em;margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:20px;margin-bottom:14px}
.row{display:flex;align-items:center;gap:10px;font-size:19px;font-weight:650;letter-spacing:-.01em}
.dot{width:11px;height:11px;border-radius:50%;background:${colour};flex:none}
dl{display:grid;grid-template-columns:auto 1fr;gap:6px 16px;margin:16px 0 0;font-size:14px}
dt{color:var(--mut)}dd{margin:0;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;word-break:break-all}
.spark{display:flex;gap:2px;height:34px;align-items:flex-end;margin-top:14px}
.spark i{flex:1;min-width:2px;height:100%;border-radius:2px;opacity:.85}
a{color:inherit}
p.n{color:var(--mut);font-size:13.5px;margin:14px 0 0}
</style></head><body><div class="w">
<h1>Backend status</h1>
<div class="card">
  <div class="row"><span class="dot"></span>${esc(label)}</div>
  <div class="spark">${bars || '<span style="color:var(--mut);font-size:13px">no probes yet</span>'}</div>
  <dl>
    <dt>Backend</dt><dd><a href="${esc(BACKEND)}">${esc(BACKEND)}</a></dd>
    ${FRONTEND ? `<dt>Front end</dt><dd><a href="${esc(FRONTEND)}">${esc(FRONTEND)}</a></dd>` : ''}
    <dt>Space stage</dt><dd>${esc(latest.stage)}</dd>
    <dt>Detail</dt><dd>${esc(latest.detail || '—')}</dd>
    <dt>Latency</dt><dd>${latest.ms} ms</dd>
    <dt>Last probe</dt><dd>${esc(latest.at || 'pending')}</dd>
    ${pct === null ? '' : `<dt>Uptime</dt><dd>${pct}% over ${history.length} probes</dd>`}
  </dl>
  <p class="n">Probed every ${Math.round(INTERVAL_MS / 60000)} min from outside the app. History is in memory, so it
  resets when this service restarts. Machine-readable: <a href="/status">/status</a></p>
</div>
</div></body></html>`;
}

const server = http.createServer((req, res) => {
  const path = (req.url || '/').split('?')[0];
  const json = (code, obj) => {
    res.writeHead(code, {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
      'access-control-allow-origin': '*',
    });
    res.end(JSON.stringify(obj, null, 2));
  };

  if (path === '/status') return json(latest.up ? 200 : 503, statusPayload());
  if (path === '/healthz') return json(200, { ok: true, service: 'status-probe', uptimeSec: Math.round(process.uptime()) });
  if (path === '/' || path === '/index.html') {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
    return res.end(page());
  }
  return json(404, { error: 'not found', routes: ['/', '/status', '/healthz'] });
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`[status] listening on :${PORT} watching ${BACKEND}`);
  probe();
  setInterval(probe, INTERVAL_MS);
});

for (const sig of ['SIGTERM', 'SIGINT']) {
  process.on(sig, () => server.close(() => process.exit(0)));
}
