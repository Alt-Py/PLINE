(function () {
  "use strict";
  var REPO = document.body.getAttribute("data-repo"), ISSUE = document.body.getAttribute("data-issue");
  function load(key, fallback) { try { var v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch (e) { return fallback; } }
  function store(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) {} }
  var read = load("pline.read", []), readSet = {};
  read.forEach(function (id) { readSet[id] = 1; });
  var votes = load("pline.votes", {});       // id -> {vote, topic, source, title, at}
  var pending = load("pline.pending", []);   // ids waiting to be sent
  var token = load("pline.token", "");

  // ── Read state ──
  function syncGrid(grid) {
    var cards = grid.querySelectorAll(".card"), unread = 0;
    cards.forEach(function (c) { if (!c.classList.contains("read")) unread++; });
    grid.classList.toggle("all-read", cards.length > 0 && unread === 0);
  }
  function markRead(card) {
    var id = card.getAttribute("data-id");
    if (readSet[id]) return;
    readSet[id] = 1; read.push(id); store("pline.read", read.slice(-2000));
    card.classList.add("read"); syncGrid(card.closest(".grid"));
  }

  // ── Votes ──
  function paint(card) {
    var v = (votes[card.getAttribute("data-id")] || {}).vote || 0;
    card.querySelector(".vote.up").setAttribute("aria-pressed", String(v === 1));
    card.querySelector(".vote.down").setAttribute("aria-pressed", String(v === -1));
  }
  function cast(card, value) {
    var id = card.getAttribute("data-id"), cur = (votes[id] || {}).vote || 0;
    var next = cur === value ? 0 : value;
    votes[id] = { vote: next, topic: card.getAttribute("data-topic"), source: card.getAttribute("data-source"), title: card.getAttribute("data-title"), at: new Date().toISOString() };
    if (pending.indexOf(id) < 0) pending.push(id);
    store("pline.votes", votes); store("pline.pending", pending);
    paint(card); updateBadge(); scheduleSend();
  }

  document.querySelectorAll(".card").forEach(function (card) {
    if (readSet[card.getAttribute("data-id")]) card.classList.add("read");
    paint(card);
    card.querySelectorAll("a").forEach(function (a) { a.addEventListener("click", function () { markRead(card); }); });
    card.querySelector(".vote.up").addEventListener("click", function () { cast(card, 1); });
    card.querySelector(".vote.down").addEventListener("click", function () { cast(card, -1); });
  });
  document.querySelectorAll(".grid").forEach(syncGrid);

  // ── Hide read ──
  var hideBtn = document.getElementById("hideRead"), hide = load("pline.hideRead", false) === true;
  function applyHide() { document.body.classList.toggle("hide-read", hide); hideBtn.setAttribute("aria-pressed", String(hide)); }
  hideBtn.addEventListener("click", function () { hide = !hide; store("pline.hideRead", hide); applyHide(); });
  applyHide();

  // ── Sending votes to the locked GitHub issue ──
  var dialog = document.getElementById("likesDialog"), statusEl = document.getElementById("likesStatus");
  function setStatus(text, kind) { statusEl.textContent = text; statusEl.className = "status" + (kind ? " " + kind : ""); }
  function updateBadge() {
    document.getElementById("pendingDot").hidden = pending.length === 0;
    var all = Object.keys(votes).map(function (k) { return votes[k].vote; });
    var up = all.filter(function (v) { return v === 1; }).length, down = all.filter(function (v) { return v === -1; }).length;
    document.getElementById("likesCount").textContent = (up || down)
      ? up + " liked, " + down + " less like this on this device" + (pending.length ? ", " + pending.length + " waiting to be sent." : ", all sent.")
      : "No votes yet.";
  }
  var timer = null, sending = false;
  function scheduleSend() { if (!token) return; clearTimeout(timer); timer = setTimeout(send, 4000); }
  function send(keepalive) {
    if (!token || sending || !pending.length) return Promise.resolve(false);
    sending = true;
    var batch = pending.slice(0, 200);
    var payload = { votes: batch.map(function (id) { var v = votes[id] || {}; return { id: id, vote: v.vote || 0, topic: v.topic, source: v.source, title: v.title, at: v.at }; }) };
    var body = "Pline votes\n\n```json\n" + JSON.stringify(payload) + "\n```";
    return fetch("https://api.github.com/repos/" + REPO + "/issues/" + ISSUE + "/comments", {
      method: "POST",
      keepalive: !!keepalive,
      headers: { "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "Content-Type": "application/json" },
      body: JSON.stringify({ body: body })
    }).then(function (r) {
      sending = false;
      if (r.status === 201) {
        pending = pending.filter(function (id) { return batch.indexOf(id) < 0; });
        store("pline.pending", pending); updateBadge();
        setStatus("Votes sent. Tomorrow's edition will take them into account.", "ok");
        if (pending.length) scheduleSend();
        return true;
      }
      setStatus(r.status === 401 ? "GitHub refused the token. It may have expired: create a new one and connect again."
        : r.status === 403 || r.status === 404 ? "This token can't write to PLINE issues. Check it has Issues › Read and write on PLINE."
        : "GitHub answered " + r.status + ". Votes are kept and will be retried.", "err");
      return false;
    }).catch(function () { sending = false; setStatus("No connection. Votes are kept and will be sent later.", "err"); return false; });
  }

  document.getElementById("likesBtn").addEventListener("click", function () {
    document.getElementById("tokenInput").value = "";
    document.getElementById("tokenInput").placeholder = token ? "Connected · paste a new token to replace it" : "github_pat_…";
    setStatus(token ? "This device is connected." : "This device isn't connected yet.");
    updateBadge();
    if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", "");
  });
  document.getElementById("saveToken").addEventListener("click", function () {
    var t = document.getElementById("tokenInput").value.trim();
    if (!/^(github_pat_|ghp_)[A-Za-z0-9_]{20,}$/.test(t)) { setStatus("That doesn't look like a GitHub token. It starts with github_pat_.", "err"); return; }
    token = t; store("pline.token", token);
    document.getElementById("tokenInput").value = "";
    setStatus("Connected." + (pending.length ? " Sending your votes…" : ""), "ok");
    send();
  });
  document.getElementById("sendNow").addEventListener("click", function () {
    if (!token) { setStatus("Connect a token first.", "err"); return; }
    if (!pending.length) { setStatus("Nothing waiting. All votes are sent.", "ok"); return; }
    setStatus("Sending…"); send();
  });
  document.getElementById("forgetToken").addEventListener("click", function () {
    token = ""; try { localStorage.removeItem("pline.token"); } catch (e) {}
    setStatus("Disconnected. Votes stay on this device.");
  });
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden" && pending.length) send(true); });
  updateBadge();
  if (pending.length) scheduleSend();
})();
