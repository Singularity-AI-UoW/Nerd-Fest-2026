// TV page: renders hub messages. All visitor/agent text goes in with textContent;
// only the server-made QR SVGs are inserted as markup.
(function () {
  "use strict";

  var PROMPTS = {
    idle: "Press the big button!",
    choose_mode: "Hold for kid mode, press twice for nerd mode",
    listen: "Hold the button and talk",
    confirm: null // built from the last caption
  };
  var STEP_STATES = ["choose_mode", "listen", "confirm"];
  var STUDIO_STATES = ["agent", "tweak"];
  var NEW_VISITOR_STATES = ["idle", "choose_mode"];
  var SLIDE_MS = 6000;
  var NOTICE_MS = 8000;
  var WINNER_MS = 20000;

  var $ = function (id) { return document.getElementById(id); };
  var panels = ["studio", "slideshow", "winner", "takehome", "step", "offline"];

  var st = {
    state: "", mode: "", captionText: "", captionFinal: false, narration: "", tweaks: [],
    tool: null, prompt: null, slides: [], slideIndex: 0,
    winnerUntil: 0, notice: "", noticeTimer: null, slideTimer: null
  };

  function showOnly(id) {
    panels.forEach(function (p) { $(p).hidden = (p !== id); });
  }

  function confirmText() {
    return "I heard: " + st.captionText + "  Press to go, hold to try again";
  }

  function stepText() {
    if (st.state === "confirm") return confirmText();
    return PROMPTS[st.state] || "";
  }

  function render() {
    var s = st.state;

    // left panel
    var winnerOn = Date.now() < st.winnerUntil;
    if (STUDIO_STATES.indexOf(s) >= 0) showOnly("studio");
    else if (s === "takehome") showOnly("takehome");
    else if (s === "offline") showOnly("offline");
    else if (STEP_STATES.indexOf(s) >= 0) {
      $("step-prompt").textContent = stepText();
      showOnly("step");
    } else if (s === "idle" && winnerOn) showOnly("winner");
    else showOnly("slideshow");
    renderSlide();

    // footer
    var footer = $("footer");
    footer.textContent = st.notice || stepText();
    footer.classList.toggle("notice", !!st.notice);

    // right column
    setBlock("caption-block", "caption", st.captionText);
    $("caption").classList.toggle("partial", !st.captionFinal);
    setBlock("narration-block", "narration", st.narration);
    renderTweaks();
    renderNerd();
  }

  function setBlock(blockId, textId, text) {
    $(blockId).hidden = !text;
    $(textId).textContent = text || "";
  }

  function renderTweaks() {
    var list = $("tweaks");
    list.textContent = "";
    var show = st.state === "tweak" && st.tweaks.length > 0;
    st.tweaks.forEach(function (opt, i) {
      var li = document.createElement("li");
      var num = document.createElement("span");
      num.textContent = String(i + 1);
      li.appendChild(num);
      li.appendChild(document.createTextNode(textOf(opt)));
      list.appendChild(li);
    });
    $("tweaks-block").hidden = !show;
  }

  function renderNerd() {
    var box = $("nerd");
    box.textContent = "";
    var parts = [];
    if (st.mode === "nerd") {
      if (st.prompt) parts.push(["Prompt", st.prompt.text + (st.prompt.model ? " (" + st.prompt.model + ")" : "")]);
      if (st.tool) parts.push(["Tool", st.tool.name + " " + shortJson(st.tool.args)]);
    }
    parts.forEach(function (p) {
      var line = document.createElement("div");
      var tag = document.createElement("span");
      tag.className = "tool";
      tag.textContent = p[0] + ": ";
      line.appendChild(tag);
      line.appendChild(document.createTextNode(p[1]));
      box.appendChild(line);
    });
    box.hidden = parts.length === 0;
  }

  function textOf(v) {
    if (v == null) return "";
    if (typeof v === "string") return v;
    return v.text || v.label || v.name || JSON.stringify(v);
  }

  function shortJson(v) {
    if (v == null) return "";
    var s;
    try { s = typeof v === "string" ? v : JSON.stringify(v); } catch (e) { s = ""; }
    return s.length > 120 ? s.slice(0, 117) + "..." : s;
  }

  // ----- slideshow -----
  function renderSlide() {
    var box = $("slideshow");
    if (box.hidden) { stopSlides(); return; }
    box.textContent = "";
    if (!st.slides.length) {
      var empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = PROMPTS.idle;
      box.appendChild(empty);
      stopSlides();
      return;
    }
    var img = document.createElement("img");
    img.alt = "A picture made at the stall";
    img.src = st.slides[st.slideIndex % st.slides.length];
    box.appendChild(img);
    if (!st.slideTimer && st.slides.length > 1) {
      st.slideTimer = setInterval(function () {
        st.slideIndex = (st.slideIndex + 1) % st.slides.length;
        renderSlide();
      }, SLIDE_MS);
    }
  }

  function stopSlides() {
    if (st.slideTimer) { clearInterval(st.slideTimer); st.slideTimer = null; }
  }

  // ----- message handlers -----
  var handlers = {
    state: function (m) {
      var changed = m.state !== st.state;
      st.state = m.state || "";
      st.mode = m.mode || "";
      if (changed && st.state !== "idle") st.winnerUntil = 0;
      if (NEW_VISITOR_STATES.indexOf(st.state) >= 0) {
        st.captionText = ""; st.captionFinal = false; st.narration = "";
        st.tweaks = []; st.tool = null; st.prompt = null;
      }
      if (st.state !== "tweak") st.tweaks = [];
    },
    caption: function (m) { st.captionText = m.text || ""; st.captionFinal = !!m.final; },
    narration: function (m) { st.narration = m.text || ""; },
    tool: function (m) { st.tool = { name: m.name || "", args: m.args }; },
    prompt: function (m) { st.prompt = { text: m.text || "", model: m.model || "" }; },
    image: function (m) {
      // Warm the cache so the take-home card shows the picture straight away.
      if (m.url) { var pre = new Image(); pre.src = m.url; }
    },
    tweaks: function (m) { st.tweaks = Array.isArray(m.options) ? m.options : []; },
    takehome: function (m) {
      $("takehome-image").src = m.image_url || "";
      $("takehome-code").textContent = m.claim || "";
      $("takehome-qr").innerHTML = m.qr_svg || "";
      $("signup-qr").innerHTML = m.signup_qr_svg || "";
      $("signup-item").hidden = !m.signup_qr_svg;
    },
    message: function (m) {
      st.notice = m.text || "";
      if (st.noticeTimer) clearTimeout(st.noticeTimer);
      if (st.notice) {
        st.noticeTimer = setTimeout(function () { st.notice = ""; render(); }, NOTICE_MS);
      }
    },
    status: function () { /* organiser only */ },
    log: function () { /* organiser only */ },
    winner: function (m) {
      $("winner-image").src = m.image_url || "";
      $("winner-prompt").textContent = m.prompt || "";
      var box = $("winner-top3");
      box.textContent = "";
      (Array.isArray(m.top3) ? m.top3 : []).forEach(function (t) {
        var url = typeof t === "string" ? t : (t && (t.image_url || t.url));
        if (!url) return;
        var img = document.createElement("img");
        img.src = url;
        img.alt = "";
        box.appendChild(img);
      });
      st.winnerUntil = Date.now() + WINNER_MS;
      setTimeout(render, WINNER_MS + 50);
    },
    slideshow: function (m) {
      st.slides = Array.isArray(m.images) ? m.images.filter(Boolean) : [];
      st.slideIndex = 0;
      stopSlides();
    }
  };

  function onMessage(m) {
    var h = m && handlers[m.type];
    if (!h) return;
    try { h(m); } catch (e) { console.error("tv message failed", m.type, e); }
    render();
  }

  // ----- websocket -----
  function connect() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var ws = new WebSocket(proto + "//" + location.host + "/ws");
    ws.onmessage = function (ev) {
      try { onMessage(JSON.parse(ev.data)); } catch (e) { console.error("bad message", e); }
    };
    ws.onclose = function () { setTimeout(connect, 1000); };
    ws.onerror = function () { ws.close(); };
  }

  render();
  connect();
})();
