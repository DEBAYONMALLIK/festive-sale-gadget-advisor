(function(){
  "use strict";

  var root = document.documentElement, KEY = "fsga-theme";
  try { var saved = localStorage.getItem(KEY); if (saved) root.setAttribute("data-theme", saved); } catch (e) {}
  document.getElementById("theme-toggle").addEventListener("click", function(){
    var dark = getComputedStyle(root).getPropertyValue("--bg").trim().indexOf("#15") === 0;
    var next = dark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem(KEY, next); } catch (e) {}
  });

  var backend = "";
  var repo = "";

  function setLinks(){
    ["open-direct","open-direct-2"].forEach(function(id){
      var el = document.getElementById(id); if (el && backend) el.href = backend;
    });
    var fs = document.getElementById("footer-space"); if (fs && backend) fs.href = backend;
    ["repo-link","footer-repo"].forEach(function(id){
      var el = document.getElementById(id); if (el && repo) el.href = repo;
    });
    var fu = document.getElementById("frame-url");
    if (fu && backend) fu.textContent = backend.replace(/^https?:\/\//, "");
  }

  function pill(kind, text, live){
    var p = document.getElementById("status");
    p.className = "pill " + kind + (live ? " live" : "");
    document.getElementById("status-text").textContent = text;
  }

  function mountFrame(){
    if (!backend) return;
    var slot = document.getElementById("frame-slot");
    var f = document.createElement("iframe");
    f.src = backend;
    f.title = "Festive Sale Gadget Advisor application";
    f.loading = "lazy";
    f.allow = "clipboard-write";
    f.referrerPolicy = "no-referrer-when-downgrade";
    slot.innerHTML = "";
    slot.appendChild(f);
  }

  function renderSales(h){
    if (!h || !h.sale_status) return;
    // sale_status: "Amazon Great Indian Festival starts 08 Oct (in 8 days); Flipkart Big Billion Days starts 09 Oct (in 9 days)"
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

  function probe(){
    fetch("/api/health", { cache: "no-store" })
      .then(function(r){ return r.json(); })
      .then(function(d){
        if (d && d.backend) { backend = d.backend; setLinks(); }
        if (d && d.repo) { repo = d.repo; setLinks(); }

        if (d && d.reachable && d.configured) {
          pill("ok", "backend live", true);
          renderSales(d.health);
          mountFrame();
        } else if (d && d.reachable && !d.configured) {
          var miss = (d.health && d.health.missing_secrets || []).join(", ");
          pill("warn", miss ? "backend up · missing " + miss : "backend up · not configured", false);
          renderSales(d.health);
          mountFrame();
        } else if (d && d.sleeping) {
          pill("warn", "backend waking up…", true);
          document.getElementById("frame-slot").innerHTML =
            '<div class="boot">The Space is asleep and is starting now. This takes a minute or two on a cold start — the page will pick it up automatically.</div>';
          setTimeout(probe, 15000);
        } else {
          pill("bad", "backend not deployed yet", false);
          document.getElementById("frame-slot").innerHTML =
            '<div class="boot">The backend Space is not reachable yet. Once it is deployed this panel fills in automatically — no change needed here.</div>';
          setTimeout(probe, 30000);
        }
      })
      .catch(function(){
        pill("bad", "status check failed", false);
      });
  }

  setLinks();
  probe();
})();
