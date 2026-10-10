// Organiser page: status, fallback controls, winners and a live log.
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var MAX_LOG_LINES = 300;
  var WINNERS_REFRESH_MS = 10000;
  var paused = false;
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

  $("pause").addEventListener("click", function () {
    post("/api/pause", { paused: !paused }).then(function (d) { if (d) setPaused(!paused); });
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

  function renderStatus(m) {
    var lights = $("lights");
    lights.textContent = "";
    var services = m.services && typeof m.services === "object" ? m.services : {};
    Object.keys(services).forEach(function (name) {
      var el = document.createElement("span");
      el.className = "light" + (isUp(services[name]) ? " ok" : "");
      el.appendChild(document.createElement("i"));
      el.appendChild(document.createTextNode(name));
      lights.appendChild(el);
    });
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
  function cell(tr, text) {
    var td = document.createElement("td");
    td.textContent = text == null ? "" : String(text);
    tr.appendChild(td);
  }

  function loadWinners() {
    fetch("/api/winners").then(function (r) { return r.json(); }).then(function (rows) {
      var body = $("winners");
      body.textContent = "";
      if (!rows.length) {
        var tr = document.createElement("tr");
        var td = document.createElement("td");
        td.colSpan = 4;
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
        cell(tr, w.claim != null ? w.claim : w.claim_code);
        body.appendChild(tr);
      });
    }).catch(function () { /* try again on the next refresh */ });
  }

  // ----- websocket -----
  var handlers = {
    state: function (m) { $("state").textContent = m.state || "-"; $("mode").textContent = m.mode || "-"; },
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
  loadWinners();
  setInterval(loadWinners, WINNERS_REFRESH_MS);
  connect();
})();
