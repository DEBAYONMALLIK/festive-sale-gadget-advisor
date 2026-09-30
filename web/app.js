(function(){
  "use strict";

  var BACKEND_KEY = "fsga-backend";
  var THEME_KEY = "fsga-theme";

  // ---- theme ----
  var root = document.documentElement;
  try { var saved = localStorage.getItem(THEME_KEY); if (saved) root.setAttribute("data-theme", saved); } catch (e) {}
  document.getElementById("theme-toggle").addEventListener("click", function(){
    var dark = getComputedStyle(root).getPropertyValue("--bg").trim().indexOf("#15") === 0;
    var next = dark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
  });

  var backend = "";
  var repo = "";
  var timer = null;

  function stored(){ try { return localStorage.getItem(BACKEND_KEY) || ""; } catch (e) { return ""; } }
  function store(v){ try { v ? localStorage.setItem(BACKEND_KEY, v) : localStorage.removeItem(BACKEND_KEY); } catch (e) {} }

  function setLinks(){
    ["open-direct","open-direct-2"].forEach(function(id){
      var el = document.getElementById(id); if (el && backend) el.href = backend;
    });
    var fs = document.getElementById("footer-space"); if (fs && backend) fs.href = backend;
    ["repo-link","footer-repo"].forEach(function(id){
      var el = document.getElementById(id); if (el && repo) el.href = repo;
    });
    var fu = document.getElementById("frame-url");
    if (fu) fu.textContent = backend ? backend.replace(/^https?:\/\//, "") : "no backend connected";
  }

  function pill(kind, text, live){
    var p = document.getElementById("status");
    p.className = "pill " + kind + (live ? " live" : "");
    document.getElementById("status-text").textContent = text;
  }

  function msg(text, cls){
    var m = document.getElementById("connect-msg");
    m.textContent = text || "";
    m.className = "connect-msg" + (cls ? " " + cls : "");
  }

  function slot(html){ document.getElementById("frame-slot").innerHTML = html; }

  function mountFrame(){
    if (!backend) return;
    var host = document.getElementById("frame-slot");
    var existing = host.querySelector("iframe");
    if (existing && existing.src.indexOf(backend) === 0) return;   // already showing this backend
    var f = document.createElement("iframe");
    f.src = backend;
    f.title = "Festive Sale Gadget Advisor application";
    f.loading = "lazy";
    f.allow = "clipboard-write";
    f.referrerPolicy = "no-referrer-when-downgrade";
    host.innerHTML = "";
    host.appendChild(f);
  }

  function renderSales(h){
    if (!h || !h.sale_status) return;
    // "Amazon Great Indian Festival starts 08 Oct (in 8 days); Flipkart Big Billion Days starts 09 Oct (in 9 days)"
    var parts = String(h.sale_status).split(";");
    function tidy(s, name){
      s = String(s || "").replace(name, "").trim();
      s = s.replace(/^(Great Indian Festival|Big Billion Days)\s*/, "").trim();
      return s || "—";
    }
    document.getElementById("amz-val").textContent = tidy(parts[0], "Amazon");
    document.getElementById("fk-val").textContent  = tidy(parts[1], "Flipkart");
    if (h.limits && h.usage_today){
      var runsLeft = Math.max(0, (h.limits.runs_per_day || 0) - (h.usage_today.runs || 0));
      var chkLeft  = Math.max(0, (h.limits.price_checks_per_day || 0) - (h.usage_today.checks || 0));
      document.getElementById("cap-val").textContent = runsLeft + " runs · " + chkLeft + " checks";
    }
    document.getElementById("sales").hidden = false;
  }

  function again(ms){ clearTimeout(timer); timer = setTimeout(probe, ms); }

  function probe(){
    var url = "/api/health" + (backend ? "?backend=" + encodeURIComponent(backend) : "");
    fetch(url, { cache: "no-store" })
      .then(function(r){ return r.json(); })
      .then(function(d){
        if (d && d.repo) { repo = d.repo; }
        if (d && d.backend && d.backendSource === "env" && !backend) { backend = d.backend; }
        setLinks();

        if (d && d.rejected){
          pill("bad", "backend URL rejected", false);
          msg(d.rejected, "err");
          return;
        }

        if (d && d.reachable && d.configured) {
          pill("ok", "backend live", true);
          msg(backend ? "Connected." : "", "good");
          renderSales(d.health);
          mountFrame();
          again(60000);
        } else if (d && d.reachable && !d.configured) {
          var miss = (d.health && d.health.missing_secrets || []).join(", ");
          pill("warn", miss ? "backend up · missing " + miss : "backend up · not configured", false);
          msg(miss ? "The backend is running but missing: " + miss : "", "err");
          renderSales(d.health);
          mountFrame();
          again(30000);
        } else if (d && d.sleeping) {
          pill("warn", "backend waking up…", true);
          slot('<div class="boot">The backend is starting. This takes a minute or two on a cold start — the page picks it up automatically.</div>');
          again(15000);
        } else if (!backend) {
          pill("bad", "no backend connected", false);
          slot('<div class="boot">No backend connected yet. Start the app on your machine and paste its public URL below.</div>');
        } else {
          pill("bad", "backend unreachable", false);
          msg("Could not reach that URL. Is the app still running?", "err");
          slot('<div class="boot">That backend is not responding. If your machine went to sleep or the tunnel restarted, the URL will have changed — paste the new one below.</div>');
          again(30000);
        }
      })
      .catch(function(){
        pill("bad", "status check failed", false);
        again(30000);
      });
  }

  // ---- connect form ----
  var input = document.getElementById("backend-input");
  input.value = stored();
  document.getElementById("connect-form").addEventListener("submit", function(ev){
    ev.preventDefault();
    var v = input.value.trim().replace(/\/+$/, "");
    if (!v) { msg("Paste the URL the script printed.", "err"); return; }
    if (!/^https:\/\//i.test(v)) { msg("The URL must start with https://", "err"); return; }
    backend = v;
    store(v);
    msg("Checking…");
    pill("warn", "connecting…", true);
    slot('<div class="boot">Connecting…</div>');
    probe();
  });
  document.getElementById("backend-clear").addEventListener("click", function(){
    backend = ""; store(""); input.value = "";
    msg("Forgotten.");
    setLinks();
    probe();
  });

  backend = stored();
  setLinks();
  probe();
})();
