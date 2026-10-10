// TV page: renders hub messages. All visitor/agent text goes in with textContent;
// only the server-made QR SVGs are inserted as markup.
(function () {
  "use strict";

  var PROMPTS = {
    idle: "Press the big button!",
    choose_mode: "Hold for kid mode, press twice for nerd mode",
    listen: "Hold the button and talk",
    confirm: null, // built from the last caption
    takehome: "Press the button when you're done"
  };
  // The page is laid out at 1920x1080 and scaled to fill the screen (4K included).
  var DESIGN_W = 1920;
  var DESIGN_H = 1080;
  var STEP_STATES = ["choose_mode", "listen", "confirm"];
  var STUDIO_STATES = ["agent", "tweak"];
  var NEW_VISITOR_STATES = ["idle", "choose_mode"];
  var SLIDE_MS = 5000;
  var TOP3_EVERY = 4; // every 4th slide is the "Top 3 so far" card
  var MAX_TOOLS = 7;
  var NOTICE_MS = 8000;
  var WINNER_MS = 20000;

  var $ = function (id) { return document.getElementById(id); };
  var panels = ["studio", "slideshow", "winner", "takehome", "step", "offline"];

  var st = {
    state: "", mode: "", captionText: "", captionFinal: false, narration: "", tweaks: [],
    tools: [], prompt: null, slides: [], top3: [], signupQr: "", slideIndex: 0, slideCount: 0,
    slideDrawn: false, winnerUntil: 0, notice: "", noticeTimer: null, slideTimer: null
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
    $("idle-signup").hidden = $("slideshow").hidden || !st.signupQr;
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

    // With nothing in the right column (idle, choose mode...), the stage takes the full width.
    var sideEmpty = ["caption-block", "narration-block", "tweaks-block", "nerd"].every(function (id) {
      return $(id).hidden;
    });
    document.body.classList.toggle("stage-wide", sideEmpty && $("studio").hidden);
  }

  function fitScreen() {
    var scale = Math.min(window.innerWidth / DESIGN_W, window.innerHeight / DESIGN_H);
    var x = (window.innerWidth - DESIGN_W * scale) / 2;
    var y = (window.innerHeight - DESIGN_H * scale) / 2;
    $("tv-grid").style.transform = "translate(" + x + "px, " + y + "px) scale(" + scale + ")";
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
      if (st.prompt) {
        parts.push(["Prompt", st.prompt.text]);
        if (st.prompt.llm) parts.push(["LLM", st.prompt.llm]);
        if (st.prompt.model) parts.push(["Image model", st.prompt.model]);
      }
      st.tools.slice(-MAX_TOOLS).forEach(function (t) {
        parts.push([t.name, shortJson(t.args)]);
      });
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
    if (v && typeof v === "object" && !Array.isArray(v)) {
      v = Object.assign({}, v);
      delete v.narration; // already shown in the Now box
    }
    try { s = typeof v === "string" ? v : JSON.stringify(v); } catch (e) { s = ""; }
    return s.length > 90 ? s.slice(0, 87) + "..." : s;
  }

  // ----- slideshow -----
  // The idle screen: pictures with their prompts, every 4th slide the top 3 card.
  // Pictures that fail to load are dropped from the list.
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  }

  function nextSlide() {
    st.slideCount += 1;
    if (st.top3.length && st.slideCount % TOP3_EVERY === 0) return { top3: true };
    if (!st.slides.length) return null;
    var slide = st.slides[st.slideIndex % st.slides.length];
    st.slideIndex = (st.slideIndex + 1) % st.slides.length;
    return slide;
  }

  function drawEmpty(box) {
    var empty = el("div", "empty");
    empty.appendChild(el("div", "empty-big", "Be the first!"));
    empty.appendChild(el("div", "empty-small", PROMPTS.idle));
    box.appendChild(empty);
  }

  function drawImage(box, slide) {
    var wrap = el("div", "slide");
    var img = el("img");
    img.alt = "";
    img.onerror = function () {
      st.slides = st.slides.filter(function (s) { return s !== slide; });
      if (wrap.isConnected) advanceSlide(); // skip a picture that will not load
    };
    img.src = slide.url;
    wrap.appendChild(img);
    if (slide.prompt) {
      // The padding sits on the outer box so a clamped third line can't peek out under it.
      var caption = el("div", "caption");
      caption.appendChild(el("div", "caption-text", slide.prompt));
      wrap.appendChild(caption);
    }
    if (slide.qr_svg) {
      var qrBox = el("div", "slide-qr");
      var qr = el("div", "qr");
      qr.innerHTML = slide.qr_svg; // server-made SVG
      qrBox.appendChild(qr);
      qrBox.appendChild(el("div", "qr-label", "Scan to get this picture"));
      wrap.appendChild(qrBox);
    }
    box.appendChild(wrap);
  }

  function drawTop3(box) {
    var card = el("div", "top3-card");
    card.appendChild(el("div", "top3-title", "Top 3 so far"));
    var row = el("div", "top3-row");
    st.top3.forEach(function (t) {
      var tile = el("div", "top3-tile");
      var img = el("img");
      img.alt = "";
      img.onerror = function () { tile.hidden = true; };
      img.src = t.url;
      tile.appendChild(img);
      tile.appendChild(el("div", "top3-votes", t.votes + (t.votes === 1 ? " vote" : " votes")));
      if (t.prompt) tile.appendChild(el("div", "top3-prompt", t.prompt));
      row.appendChild(tile);
    });
    card.appendChild(row);
    box.appendChild(card);
  }

  function drawSlide(slide) {
    var box = $("slideshow");
    box.textContent = "";
    st.slideDrawn = true;
    document.body.classList.toggle("top3-on", !!(slide && slide.top3));
    if (!slide) drawEmpty(box);
    else if (slide.top3) drawTop3(box);
    else drawImage(box, slide);
  }

  function advanceSlide() {
    if ($("slideshow").hidden) return;
    drawSlide(nextSlide());
  }

  // Called on every render: keep the timer running while the slideshow shows,
  // and draw once when it first appears (not on every message).
  function renderSlide() {
    if ($("slideshow").hidden) {
      stopSlides();
      st.slideDrawn = false;
      return;
    }
    if (!st.slideDrawn) drawSlide(nextSlide());
    if (!st.slideTimer) st.slideTimer = setInterval(advanceSlide, SLIDE_MS);
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
        st.tweaks = []; st.tools = []; st.prompt = null;
      }
      if (st.state !== "tweak") st.tweaks = [];
    },
    caption: function (m) { st.captionText = m.text || ""; st.captionFinal = !!m.final; },
    narration: function (m) { st.narration = m.text || ""; },
    tool: function (m) { st.tools.push({ name: m.name || "", args: m.args }); },
    prompt: function (m) { st.prompt = { text: m.text || "", model: m.model || "", llm: m.llm || "" }; },
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
      var slides = (Array.isArray(m.images) ? m.images : []).filter(function (i) {
        return i && typeof i.url === "string" && i.url;
      }).map(function (i) {
        return { url: i.url, prompt: i.prompt, qr_svg: typeof i.qr_svg === "string" ? i.qr_svg : "" };
      });
      var top3 = (Array.isArray(m.top3) ? m.top3 : []).filter(function (t) {
        return t && typeof t.url === "string" && t.url;
      });
      var same = key(slides) === key(st.slides) && key(top3) === key(st.top3);
      st.slides = slides;
      st.top3 = top3;
      if (typeof m.signup_qr_svg === "string") {
        st.signupQr = m.signup_qr_svg;
        $("idle-signup-qr").innerHTML = st.signupQr; // server-made SVG
      }
      if (!same) {
        st.slideIndex = 0; // a new list starts from the newest picture
        st.slideCount = 0;
        st.slideDrawn = false;
      }
    }
  };

  function key(list) {
    return list.map(function (i) { return i.url + "|" + (i.votes || 0); }).join(",");
  }

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

  window.addEventListener("resize", fitScreen);
  fitScreen();
  render();
  connect();
})();
