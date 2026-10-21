(function () {
  "use strict";
  var REPO = document.body.getAttribute("data-repo"), ISSUE = document.body.getAttribute("data-issue");
  var PAGE = document.body.getAttribute("data-page");
  var API = "https://api.github.com/repos/" + REPO + "/issues/" + ISSUE + "/comments";
  var TRUSTED = { OWNER: 1, MEMBER: 1, COLLABORATOR: 1 };
  var IMG_OK = /^thumbs\/[0-9a-f]{16}\.webp$/, URL_OK = /^https?:\/\//i;

  function load(key, fallback) { try { var v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch (e) { return fallback; } }
  function store(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) {} }
  function $(id) { return document.getElementById(id); }

  var read = load("pline.read", []), readSet = {};
  read.forEach(function (id) { readSet[id] = 1; });
  var votes = load("pline.votes", {});            // id -> {vote, topic, source, title, at}
  var pending = load("pline.pending", []);        // vote ids waiting to be sent
  var saves = load("pline.saves", {});            // id -> {saved, at, story}  (this device's own actions)
  var pendingSaves = load("pline.pendingSaves", []);
  var token = load("pline.token", "");
  var synced = {};                                // id -> {saved, at, story}  (from saved.json + GitHub)

  // Stories of this edition, for saving them with their full card.
  var stories = {};
  try { JSON.parse(($("pline-stories") || {}).textContent || "[]").forEach(function (s) { stories[s.id] = s; }); } catch (e) {}

  // ── Saved state: merge everything, newest action wins ──
  function merged() {
    var out = {}, k;
    for (k in synced) out[k] = synced[k];
    for (k in saves) if (!out[k] || (saves[k].at || "") >= (out[k].at || "")) out[k] = saves[k];
    return out;
  }
  function isSaved(id) { var m = merged()[id]; return !!(m && m.saved); }
  function savedCount() { var m = merged(), n = 0; for (var k in m) if (m[k].saved) n++; return n; }
  function paintSavedCount() { var n = savedCount(); $("savedCount").textContent = n ? String(n) : ""; }

  // ── Read state ──
  function syncGrid(grid) {
    if (!grid) return;
    var cards = grid.querySelectorAll(".card:not([hidden])"), unread = 0;
    cards.forEach(function (c) { if (!c.classList.contains("read")) unread++; });
    grid.classList.toggle("all-read", cards.length > 0 && unread === 0);
  }
  function markRead(card) {
    var id = card.getAttribute("data-id");
    if (readSet[id]) return;
    readSet[id] = 1; read.push(id); store("pline.read", read.slice(-2000));
    if (PAGE === "edition") { card.classList.add("read"); syncGrid(card.closest(".grid")); }
  }

  // ── Votes and saves on a card ──
  function paint(card) {
    var id = card.getAttribute("data-id"), v = (votes[id] || {}).vote || 0;
    card.querySelector(".vote.up").setAttribute("aria-pressed", String(v === 1));
    card.querySelector(".vote.down").setAttribute("aria-pressed", String(v === -1));
    var sv = card.querySelector(".vote.save"), on = isSaved(id);
    sv.setAttribute("aria-pressed", String(on));
    sv.setAttribute("aria-label", on ? "Remove from saved" : "Save for later");
    sv.title = on ? "Remove from saved" : "Save for later";
    if (PAGE === "saved") card.classList.toggle("unsaved", !on);
  }
  function storyFor(card) {
    var id = card.getAttribute("data-id");
    var m = merged()[id];
    return stories[id] || (m && m.story) || { id: id, topic: card.getAttribute("data-topic"), source: card.getAttribute("data-source"), title: card.getAttribute("data-title") };
  }
  function cast(card, value) {
    var id = card.getAttribute("data-id"), cur = (votes[id] || {}).vote || 0, s = storyFor(card);
    var next = cur === value ? 0 : value;
    votes[id] = { vote: next, topic: s.topic, source: s.source, title: s.title, at: new Date().toISOString() };
    if (pending.indexOf(id) < 0) pending.push(id);
    store("pline.votes", votes); store("pline.pending", pending);
    paint(card); updateBadge(); scheduleSend();
  }
  function toggleSave(card) {
    var id = card.getAttribute("data-id");
    saves[id] = { saved: !isSaved(id), at: new Date().toISOString(), story: storyFor(card) };
    if (pendingSaves.indexOf(id) < 0) pendingSaves.push(id);
    store("pline.saves", saves); store("pline.pendingSaves", pendingSaves);
    paint(card); paintSavedCount(); updateBadge(); scheduleSend();
  }
  function wire(card) {
    if (PAGE === "edition" && readSet[card.getAttribute("data-id")]) card.classList.add("read");
    paint(card);
    card.querySelectorAll("a").forEach(function (a) { a.addEventListener("click", function () { markRead(card); }); });
    card.querySelector(".vote.up").addEventListener("click", function () { cast(card, 1); });
    card.querySelector(".vote.down").addEventListener("click", function () { cast(card, -1); });
    card.querySelector(".vote.save").addEventListener("click", function () { toggleSave(card); });
    var img = card.querySelector(".media img");
    if (img) img.addEventListener("error", function () { var m = card.querySelector(".media"); if (m) m.remove(); card.classList.remove("has-img"); });
  }

  document.querySelectorAll(".card").forEach(wire);
  document.querySelectorAll(".grid").forEach(syncGrid);
  paintSavedCount();

  // ── Show more: reveal the section's reserve stories ──
  document.querySelectorAll(".pill.more").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var grid = btn.closest(".tile").querySelector(".grid");
      grid.querySelectorAll(".card.extra[hidden]").forEach(function (c) { c.hidden = false; c.classList.add("revealed"); });
      btn.closest(".more-row").hidden = true;
      syncGrid(grid);
      var first = grid.querySelector(".card.extra.revealed a");
      if (first) first.focus({ preventScroll: true });
    });
  });

  // ── Hide read ──
  var hideBtn = $("hideRead");
  if (hideBtn) {
    var hide = load("pline.hideRead", false) === true;
    var applyHide = function () { document.body.classList.toggle("hide-read", hide); hideBtn.setAttribute("aria-pressed", String(hide)); };
    hideBtn.addEventListener("click", function () { hide = !hide; store("pline.hideRead", hide); applyHide(); });
    applyHide();
  }

  // ── Pull saves made on other devices: saved.json (daily) + newer GitHub comments ──
  function applyEvents(target, events) {
    (events || []).forEach(function (e) {
      if (!e || typeof e.id !== "string") return;
      var prev = target[e.id];
      if (prev && (prev.at || "") > (e.at || "")) return;
      target[e.id] = { saved: !!e.saved, at: e.at || "", story: e.story || (prev && prev.story) };
    });
  }
  function eventsFromComments(list) {
    var out = [];
    list.forEach(function (c) {
      if (!TRUSTED[c.author_association]) return;
      var re = /```json\s*([\s\S]*?)```/g, m;
      while ((m = re.exec(c.body || ""))) {
        try { var p = JSON.parse(m[1]); if (p && p.saves) out = out.concat(p.saves); } catch (e) {}
      }
    });
    return out;
  }
  function pull() {
    return fetch("saved.json", { cache: "no-store" }).then(function (r) { return r.ok ? r.json() : { items: [] }; }).catch(function () { return { items: [] }; })
      .then(function (base) {
        (base.items || []).forEach(function (it) { synced[it.id] = { saved: true, at: it.at || "", story: it }; });
        var cacheKey = "pline.comments." + (base.updated || "all");
        var cached = null;
        try { cached = JSON.parse(sessionStorage.getItem(cacheKey) || "null"); } catch (e) {}
        if (cached && Date.now() - cached.t < 120000) return cached.events;
        var url = API + "?per_page=100" + (base.updated ? "&since=" + encodeURIComponent(base.updated) : "");
        var headers = { "Accept": "application/vnd.github+json" };
        if (token) headers.Authorization = "Bearer " + token;
        return fetch(url, { headers: headers }).then(function (r) { return r.ok ? r.json() : []; }).then(function (list) {
          var events = eventsFromComments(Array.isArray(list) ? list : []);
          try { sessionStorage.setItem(cacheKey, JSON.stringify({ t: Date.now(), events: events })); } catch (e) {}
          return events;
        }).catch(function () { return []; });
      })
      .then(function (events) { applyEvents(synced, events); });
  }

  // ── Saved page ──
  var filter = "";
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function svgUse(id, cls) {
    var ns = "http://www.w3.org/2000/svg", s = document.createElementNS(ns, "svg"), u = document.createElementNS(ns, "use");
    s.setAttribute("aria-hidden", "true"); if (cls) s.setAttribute("class", cls);
    u.setAttribute("href", "#" + id); s.appendChild(u); return s;
  }
  function savedAgo(iso) {
    var t = Date.parse(iso); if (!t) return "";
    var d = Math.floor((Date.now() - t) / 86400000);
    return d <= 0 ? "Saved today" : d === 1 ? "Saved yesterday" : d < 30 ? "Saved " + d + " days ago" : "Saved " + new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }
  function buildCard(entry) {
    var s = entry.story || {};
    var card = el("article", "card");
    card.setAttribute("data-id", s.id); card.setAttribute("data-topic", s.topic || "");
    card.setAttribute("data-source", s.source || ""); card.setAttribute("data-title", s.title || "");
    var url = URL_OK.test(s.url || "") ? s.url : null;
    if (s.image && IMG_OK.test(s.image)) {
      card.classList.add("has-img");
      var media = el(url ? "a" : "div", "media");
      if (url) { media.href = url; media.target = "_blank"; media.rel = "noopener"; media.tabIndex = -1; media.setAttribute("aria-hidden", "true"); }
      var img = el("img"); img.src = s.image; img.alt = ""; img.loading = "lazy"; img.decoding = "async";
      media.appendChild(img);
      if (s.kind === "video") { var play = el("span", "play"); play.appendChild(svgUse("i-play")); media.appendChild(play); }
      card.appendChild(media);
    }
    var body = el("div", "body"), meta = el("div", "meta");
    meta.appendChild(el("span", "src", s.source || ""));
    meta.appendChild(el("span", "saved-at", savedAgo(entry.at)));
    if (s.kind === "video") meta.appendChild(el("span", "kind", "Video"));
    if (s.kind === "reddit") meta.appendChild(el("span", "kind", "Discussion"));
    body.appendChild(meta);
    var h3 = el("h3");
    if (url) { var a = el("a", null, s.title || ""); a.href = url; a.target = "_blank"; a.rel = "noopener"; h3.appendChild(a); } else h3.textContent = s.title || "";
    body.appendChild(h3);
    if (s.title_ja) { var jp = el("div", "jp-title", s.title_ja); jp.lang = "ja"; body.appendChild(jp); }
    if (s.summary) body.appendChild(el("p", "sum", s.summary));
    var foot = el("div", "foot"), links = el("div", "links");
    if (url) { var r = el("a", null, (s.kind === "video" ? "Watch" : "Read") + " on " + (s.source || "site") + " ›"); r.href = url; r.target = "_blank"; r.rel = "noopener"; links.appendChild(r); }
    foot.appendChild(links);
    var vs = el("div", "votes");
    [["up", "More like this", "i-up"], ["down", "Less like this", "i-down"]].forEach(function (b) {
      var btn = el("button", "vote " + b[0]); btn.type = "button"; btn.setAttribute("aria-label", b[1]); btn.title = b[1]; btn.appendChild(svgUse(b[2])); vs.appendChild(btn);
    });
    var sv = el("button", "vote save"); sv.type = "button"; sv.appendChild(svgUse("i-save", "ic-off")); sv.appendChild(svgUse("i-saved", "ic-on"));
    vs.appendChild(sv); foot.appendChild(vs); body.appendChild(foot); card.appendChild(body);
    return card;
  }
  var shownOnPage = null;  // ids rendered on the Saved page; unsaving keeps the card (dimmed) so it can be re-saved
  function renderSaved() {
    var grid = $("savedGrid"), empty = $("savedEmpty"), m = merged();
    if (!shownOnPage) {
      shownOnPage = Object.keys(m).filter(function (k) { return m[k].saved && m[k].story && m[k].story.title; })
        .sort(function (a, b) { return (m[b].at || "").localeCompare(m[a].at || ""); });
    }
    grid.replaceChildren();
    var n = 0;
    shownOnPage.forEach(function (id) {
      var entry = m[id];
      if (!entry || !entry.story || (filter && entry.story.topic !== filter)) return;
      var card = buildCard(entry); grid.appendChild(card); wire(card); n++;
    });
    var total = savedCount();
    $("savedLead").textContent = total ? total + (total === 1 ? " story" : " stories") + " saved from any edition, newest first." : "Stories you save from any edition appear here.";
    empty.hidden = n > 0;
    empty.querySelector("p").textContent = filter ? "Nothing saved in this section yet." : "Nothing saved yet. Tap the bookmark on any story to keep it here.";
  }
  if (PAGE === "saved") {
    document.querySelectorAll("#savedFilters .pill").forEach(function (b) {
      b.addEventListener("click", function () {
        filter = b.getAttribute("data-topic") || "";
        document.querySelectorAll("#savedFilters .pill").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
        renderSaved();
      });
    });
  }
  pull().then(function () {
    paintSavedCount();
    if (PAGE === "saved") renderSaved();
    else document.querySelectorAll(".card").forEach(paint);
  });

  // ── Sending votes and saves to the locked GitHub issue ──
  var dialog = $("likesDialog"), statusEl = $("likesStatus");
  function setStatus(text, kind) { statusEl.textContent = text; statusEl.className = "status" + (kind ? " " + kind : ""); }
  function waiting() { return pending.length + pendingSaves.length; }
  function updateBadge() {
    $("pendingDot").hidden = waiting() === 0;
    var all = Object.keys(votes).map(function (k) { return votes[k].vote; });
    var up = all.filter(function (v) { return v === 1; }).length, down = all.filter(function (v) { return v === -1; }).length;
    var n = savedCount();
    $("likesCount").textContent = (up || down || n)
      ? up + " liked, " + down + " less like this, " + n + " saved" + (waiting() ? ". " + waiting() + " change" + (waiting() === 1 ? "" : "s") + " waiting to be sent." : ". Everything is sent.")
      : "No votes or saves yet.";
  }
  var timer = null, sending = false;
  function scheduleSend() { if (!token) return; clearTimeout(timer); timer = setTimeout(send, 3000); }
  function send(keepalive) {
    if (!token || sending || !waiting()) return Promise.resolve(false);
    sending = true;
    var vb = pending.slice(0, 150), sb = pendingSaves.slice(0, 50);
    var payload = {};
    if (vb.length) payload.votes = vb.map(function (id) { var v = votes[id] || {}; return { id: id, vote: v.vote || 0, topic: v.topic, source: v.source, title: v.title, at: v.at }; });
    if (sb.length) payload.saves = sb.map(function (id) { var s = saves[id] || {}; return { id: id, saved: !!s.saved, at: s.at, story: s.story }; });
    var body = "Pline votes and saves\n\n```json\n" + JSON.stringify(payload) + "\n```";
    return fetch(API, {
      method: "POST",
      keepalive: !!keepalive && body.length < 60000,
      headers: { "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "Content-Type": "application/json" },
      body: JSON.stringify({ body: body })
    }).then(function (r) {
      sending = false;
      if (r.status === 201) {
        pending = pending.filter(function (id) { return vb.indexOf(id) < 0; });
        pendingSaves = pendingSaves.filter(function (id) { return sb.indexOf(id) < 0; });
        sb.forEach(function (id) { if (saves[id]) synced[id] = saves[id]; });
        store("pline.pending", pending); store("pline.pendingSaves", pendingSaves); updateBadge();
        try { Object.keys(sessionStorage).forEach(function (k) { if (k.indexOf("pline.comments.") === 0) sessionStorage.removeItem(k); }); } catch (e) {}
        setStatus("Sent. Saves show up on your other devices right away; likes shape tomorrow's edition.", "ok");
        if (waiting()) scheduleSend();
        return true;
      }
      setStatus(r.status === 401 ? "GitHub refused the token. It may have expired: create a new one and connect again."
        : r.status === 403 || r.status === 404 ? "This token can't write to PLINE issues. Check it has Issues › Read and write on PLINE."
        : "GitHub answered " + r.status + ". Changes are kept and will be retried.", "err");
      return false;
    }).catch(function () { sending = false; setStatus("No connection. Changes are kept and will be sent later.", "err"); return false; });
  }

  $("likesBtn").addEventListener("click", function () {
    $("tokenInput").value = "";
    $("tokenInput").placeholder = token ? "Connected · paste a new token to replace it" : "github_pat_…";
    setStatus(token ? "This device is connected." : "This device isn't connected yet.");
    updateBadge();
    if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", "");
  });
  $("saveToken").addEventListener("click", function () {
    var t = $("tokenInput").value.trim();
    if (!/^(github_pat_|ghp_)[A-Za-z0-9_]{20,}$/.test(t)) { setStatus("That doesn't look like a GitHub token. It starts with github_pat_.", "err"); return; }
    token = t; store("pline.token", token);
    $("tokenInput").value = "";
    setStatus("Connected." + (waiting() ? " Sending your changes…" : ""), "ok");
    send();
  });
  $("sendNow").addEventListener("click", function () {
    if (!token) { setStatus("Connect a token first.", "err"); return; }
    if (!waiting()) { setStatus("Nothing waiting. Everything is sent.", "ok"); return; }
    setStatus("Sending…"); send();
  });
  $("forgetToken").addEventListener("click", function () {
    token = ""; try { localStorage.removeItem("pline.token"); } catch (e) {}
    setStatus("Disconnected. Votes and saves stay on this device.");
  });
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden" && waiting()) send(true); });
  updateBadge();
  if (waiting()) scheduleSend();
})();
