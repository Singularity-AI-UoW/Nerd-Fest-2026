// Organiser page: status, fallback controls, winners and a live log.
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var MAX_LOG_LINES = 300;
  var WINNERS_REFRESH_MS = 10000;
  var paused = false;
  var holdingOffline = false; // the show is in its offline state (TV shows "Back soon")
  var bothDown = false;       // the latest status has the gallery and Google both down
  var feedbackTimer = null;

  function feedback(text, isError) {
    var el = $("feedback");
    el.textContent = text;
    el.className = "feedback" + (isError ? " err" : "");
    if (feedbackTimer) clearTimeout(feedbackTimer);
    feedbackTimer = setTimeout(function () { el.textContent = ""; }, 6000);
  }

  function post(url, body, okText) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (!r.ok) throw new Error(r.status === 422 ? "That wasn't accepted - check what you typed" : "Server said " + r.status);
      return r.json();
    }).then(function (data) {
      if (okText) feedback(typeof okText === "function" ? okText(data) : okText, false);
      return data;
    }).catch(function (e) {
      feedback(e.message || "Could not reach the kiosk", true);
    });
  }

  // ----- controls -----
  $("send").addEventListener("click", function () {
    var box = $("typed");
    var text = box.value.trim();
    if (!text) { feedback("Type something first", true); return; }
    post("/api/typed", { text: text }, "Sent").then(function (d) { if (d) box.value = ""; });
  });
  $("typed").addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") $("send").click();
  });

  document.querySelectorAll("#tweak-buttons button").forEach(function (b) {
    b.addEventListener("click", function () {
      var n = Number(b.dataset.n);
      post("/api/tweak", { n: n }, n === 0 ? "Keeping the picture" : "Tweak " + n + " chosen");
    });
  });

  var STEP_TEXT = { kid: "Kid mode", nerd: "Nerd mode", go: "Going ahead", retry: "Trying again", next: "Next visitor" };
  document.querySelectorAll("button[data-step]").forEach(function (b) {
    b.addEventListener("click", function () {
      post("/api/step", { action: b.dataset.step }, STEP_TEXT[b.dataset.step]);
    });
  });

  // Only the current step's buttons are enabled (the show ignores the others anyway).
  function renderSteps(state) {
    document.querySelectorAll(".step-group").forEach(function (g) {
      var on = g.dataset.state === state;
      g.classList.toggle("current", on);
      g.querySelectorAll("button").forEach(function (b) { b.disabled = !on; });
    });
  }

  $("pause").addEventListener("click", function () {
    var want = !paused;
    post("/api/pause", { paused: want }, want ? "Paused: the current visitor finishes first" : "Resumed")
      .then(function (d) { if (d) setPaused(want); });
  });

  function removeText(d) { return d.removed ? "Image removed" : "Nothing was removed"; }
  $("remove-last").addEventListener("click", function () {
    post("/api/remove", { id: "last" }, removeText);
  });
  $("remove-btn").addEventListener("click", function () {
    var id = $("remove-id").value.trim();
    if (!/^[a-z2-7]{8}$/.test(id)) { feedback("Image ids are 8 characters, a-z and 2-7", true); return; }
    post("/api/remove", { id: id }, removeText).then(function (d) { if (d && d.removed) $("remove-id").value = ""; });
  });

  // ----- status -----
  function setPaused(p) {
    paused = !!p;
    var b = $("pause");
    b.textContent = paused ? "Resume" : "Pause";
    b.classList.toggle("on", paused);
  }

  function isUp(v) {
    if (typeof v === "string") return ["ok", "up", "true", "connected", "ready"].indexOf(v.toLowerCase()) >= 0;
    if (v && typeof v === "object" && "ok" in v) return !!v.ok;
    return !!v;
  }

  function renderBanner() {
    var b = $("offline-banner");
    var on = holdingOffline || bothDown;
    b.hidden = !on;
    b.textContent = on
      ? "OFFLINE: the gallery and Google can't be reached. The TV shows \"Back soon\" between visitors and resumes by itself."
      : "";
  }

  function renderStatus(m) {
    var lights = $("lights");
    lights.textContent = "";
    var services = m.services && typeof m.services === "object" ? m.services : {};
    Object.keys(services).forEach(function (name) {
      var el = document.createElement("span");
      var up = isUp(services[name]);
      el.className = "light" + (up ? " ok" : "");
      el.appendChild(document.createElement("i"));
      el.appendChild(document.createTextNode(name + (up ? "" : " (down)")));
      lights.appendChild(el);
    });
    bothDown = "gallery" in services && "google" in services &&
      !isUp(services.gallery) && !isUp(services.google);
    renderBanner();
    $("llm").textContent = m.llm == null ? "-" : String(m.llm);
    $("queue").textContent = m.queue == null ? "-" : String(m.queue);
    setPaused(m.paused);
  }

  // ----- log -----
  function addLog(text) {
    var box = $("log");
    var stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
    var stamp = new Date().toLocaleTimeString("en-NZ", { hour12: false, timeZone: "Pacific/Auckland" });
    box.appendChild(document.createTextNode(stamp + "  " + text + "\n"));
    while (box.childNodes.length > MAX_LOG_LINES) box.removeChild(box.firstChild);
    if (stick) box.scrollTop = box.scrollHeight;
  }

  // ----- winners -----
  // "Claimed" ticks live in this page's localStorage only (nothing is sent to the server).
  var CLAIMED_KEY = "nerdfest.claimed";
  var claimedMemory = {}; // used when localStorage is blocked

  function readClaimed() {
    try {
      var saved = JSON.parse(localStorage.getItem(CLAIMED_KEY) || "{}");
      return saved && typeof saved === "object" ? saved : {};
    } catch (e) { return claimedMemory; }
  }

  function setClaimed(id, on) {
    var all = readClaimed();
    if (on) all[id] = true; else delete all[id];
    claimedMemory = all;
    try { localStorage.setItem(CLAIMED_KEY, JSON.stringify(all)); } catch (e) { /* kept in memory */ }
  }

  function cell(tr, text) {
    var td = document.createElement("td");
    td.textContent = text == null ? "" : String(text);
    tr.appendChild(td);
  }

  // Contact column: the contact itself for "ok"/"mismatch", else this text.
  var CONTACT_TEXT = { none: "In person only", unreachable: "Can't reach the gallery" };
  var CODE_MARK = { ok: "✓", mismatch: "✗ don't send" };

  function loadWinners() {
    fetch("/api/winners").then(function (r) { return r.json(); }).then(function (rows) {
      var body = $("winners");
      var claimed = readClaimed();
      body.textContent = "";
      if (!rows.length) {
        var tr = document.createElement("tr");
        var td = document.createElement("td");
        td.colSpan = 7;
        td.textContent = "No winners yet";
        tr.appendChild(td);
        body.appendChild(tr);
        return;
      }
      rows.forEach(function (w, i) {
        var tr = document.createElement("tr");
        cell(tr, w.rank != null ? w.rank : i + 1);
        cell(tr, w.id);
        cell(tr, w.votes);
        cell(tr, w.claim_code != null ? w.claim_code : "not found");
        var contact = CONTACT_TEXT[w.contact_status];
        cell(tr, contact !== undefined ? contact : w.contact);
        cell(tr, CODE_MARK[w.contact_status] || "");
        var td = document.createElement("td");
        var tick = document.createElement("input");
        tick.type = "checkbox";
        tick.checked = !!claimed[w.id];
        tick.setAttribute("aria-label", "Claimed: " + w.id);
        tick.addEventListener("change", function () { setClaimed(w.id, tick.checked); });
        td.appendChild(tick);
        tr.appendChild(td);
        body.appendChild(tr);
      });
    }).catch(function () { /* try again on the next refresh */ });
  }

  // ----- websocket -----
  var handlers = {
    state: function (m) {
      $("state").textContent = m.state || "-";
      $("mode").textContent = m.mode || "-";
      holdingOffline = m.state === "offline";
      renderBanner();
      renderSteps(m.state);
    },
    status: renderStatus,
    log: function (m) { addLog(m.text || ""); },
    message: function (m) { addLog("Message: " + (m.text || "")); }
  };

  function connect() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var ws = new WebSocket(proto + "//" + location.host + "/ws");
    ws.onopen = function () { $("conn").textContent = "connected"; };
    ws.onmessage = function (ev) {
      var m;
      try { m = JSON.parse(ev.data); } catch (e) { return; }
      var h = m && handlers[m.type];
      if (h) h(m);
    };
    ws.onclose = function () { $("conn").textContent = "reconnecting..."; setTimeout(connect, 1000); };
    ws.onerror = function () { ws.close(); };
  }

  setPaused(false);
  renderSteps("");
  loadWinners();
  setInterval(loadWinners, WINNERS_REFRESH_MS);
  connect();
})();
