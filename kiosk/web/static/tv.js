// TV page: renders hub messages. All visitor/agent text goes in with textContent;
// only the server-made QR SVGs are inserted as markup.
(function () {
  "use strict";

  var CONFIRM_HINT = "Press to go, hold to try again";
  var PROMPTS = {
    idle: "Press the big button!",
    choose_mode: "Hold for kid mode, press twice for nerd mode",
    listen: "Hold the button and talk",
    confirm: null, // built from the last caption
    takehome: "Press the button when you're done"
  };
  // The scrolling strip along the bottom: these lines on the idle screen, else the step's own line.
  var TICKER_IDLE = [PROMPTS.idle, "Say what you want to see", "Watch the AI draw it live",
    "Vote for your favourite in the gallery"];
  var TICKER_AGENT = ["The AI is drawing your picture"];
  var TICKER_PX_PER_S = 150;
  // The page is laid out at 1920x1080 and scaled to fill the screen (4K included).
  var DESIGN_W = 1920;
  var DESIGN_H = 1080;
  var STEP_STATES = ["choose_mode", "listen", "confirm"];
  var STUDIO_STATES = ["agent", "tweak"];
  var NEW_VISITOR_STATES = ["idle", "choose_mode"];
  var SLIDE_MS = 7000;
  var LEAVE_MS = 600; // how long a slide takes to fly out
  var TOP3_EVERY = 4; // every 4th slide is the "Top 3 so far" card
  var MAX_TOOLS = 7;
  var NOTICE_MS = 8000;
  var WINNER_MS = 20000;
  // The moving wall of pictures behind the idle screen.
  var MOSAIC_COLS = 7;
  var MOSAIC_PER_COL = 6;
  var MIN_FIT = 0.55; // the side column's text can shrink to this fraction to fit
  var CONFETTI = ["#ff9a2e", "#c08bff", "#4ade80", "#ffd23f", "#ff5ca8", "#2fd4f7"];

  var $ = function (id) { return document.getElementById(id); };
  var panels = ["studio", "slideshow", "winner", "takehome", "step", "offline"];

  var st = {
    state: "", mode: "", captionText: "", captionFinal: false, narration: "", tweaks: [],
    tools: [], prompt: null, slides: [], top3: [], signupQr: "", slideIndex: 0, slideCount: 0,
    slideDrawn: false, winnerUntil: 0, notice: "", noticeTimer: null, slideTimer: null,
    panel: "", stepShown: null, footerKey: null, mosaicKey: null, tweaksKey: null, nerdKey: null,
    fitKey: null
  };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  }

  // Restart a CSS animation on an element that is already showing.
  function replay(e, cls) {
    e.classList.remove(cls);
    void e.offsetWidth;
    e.classList.add(cls);
  }

  function showOnly(id) {
    panels.forEach(function (p) { $(p).hidden = (p !== id); });
    if (id === st.panel) return;
    st.panel = id;
    document.body.setAttribute("data-panel", id);
    if (id === "takehome") confetti($("takehome-confetti"), 70, false);
    if (id === "winner") confetti($("winner-confetti"), 90, true);
  }

  function confirmText() {
    return "I heard: " + st.captionText + "  " + CONFIRM_HINT;
  }

  function stepText() {
    if (st.state === "confirm") return confirmText();
    return PROMPTS[st.state] || "";
  }

  function render() {
    var s = st.state;
    document.body.setAttribute("data-state", s);

    // left panel
    var winnerOn = Date.now() < st.winnerUntil;
    if (STUDIO_STATES.indexOf(s) >= 0) showOnly("studio");
    else if (s === "takehome") showOnly("takehome");
    else if (s === "offline") showOnly("offline");
    else if (STEP_STATES.indexOf(s) >= 0) {
      renderStep();
      showOnly("step");
    } else if (s === "idle" && winnerOn) showOnly("winner");
    else showOnly("slideshow");
    $("idle-signup").hidden = $("slideshow").hidden || !st.signupQr;
    $("attract").hidden = $("slideshow").hidden;
    renderSlide();
    renderFooter();

    // right column
    setBlock("caption-block", "caption", st.captionText, false);
    $("caption").classList.toggle("partial", !st.captionFinal);
    setBlock("narration-block", "narration", st.narration, true);
    renderTweaks();
    renderNerd();

    // With nothing in the right column (idle, choose mode...), the stage takes the full width.
    var sideEmpty = ["caption-block", "narration-block", "tweaks-block", "nerd"].every(function (id) {
      return $(id).hidden;
    });
    document.body.classList.toggle("stage-wide", sideEmpty && $("studio").hidden);
    fitSide();
  }

  // Shrink the side column's text in small steps until nothing is cut off at the bottom.
  // Only re-measured when what it shows changes.
  function fitSide() {
    var side = document.querySelector(".side");
    var k = [st.captionText, st.narration, st.tweaksKey, $("tweaks-block").hidden,
      document.body.classList.contains("stage-wide")].join("|");
    if (k === st.fitKey) return;
    st.fitKey = k;
    var fit = 1;
    side.style.setProperty("--fit", "1");
    while (fit > MIN_FIT && side.scrollHeight > side.clientHeight + 1) {
      fit = Math.round((fit - 0.05) * 100) / 100;
      side.style.setProperty("--fit", String(fit));
    }
  }

  function fitScreen() {
    var scale = Math.min(window.innerWidth / DESIGN_W, window.innerHeight / DESIGN_H);
    var x = (window.innerWidth - DESIGN_W * scale) / 2;
    var y = (window.innerHeight - DESIGN_H * scale) / 2;
    $("tv-grid").style.transform = "translate(" + x + "px, " + y + "px) scale(" + scale + ")";
  }

  function renderStep() {
    var text = stepText();
    if (text === st.stepShown) return;
    st.stepShown = text;
    $("step-prompt").textContent = text;
    replay($("step-prompt"), "pop");
  }

  function setBlock(blockId, textId, text, pop) {
    var box = $(textId);
    $(blockId).hidden = !text;
    if (box.textContent === (text || "")) return;
    box.textContent = text || "";
    if (text && pop) replay(box, "pop");
  }

  function renderTweaks() {
    var show = st.state === "tweak" && st.tweaks.length > 0;
    $("tweaks-block").hidden = !show;
    var k = show ? JSON.stringify(st.tweaks) : "";
    if (k === st.tweaksKey) return;
    st.tweaksKey = k;
    var list = $("tweaks");
    list.textContent = "";
    st.tweaks.forEach(function (opt, i) {
      var li = document.createElement("li");
      li.style.animationDelay = (0.15 + i * 0.18) + "s";
      var num = document.createElement("span");
      num.textContent = String(i + 1);
      li.appendChild(num);
      li.appendChild(document.createTextNode(textOf(opt)));
      list.appendChild(li);
    });
  }

  function renderNerd() {
    var box = $("nerd");
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
    box.hidden = parts.length === 0;
    var k = JSON.stringify(parts);
    if (k === st.nerdKey) return;
    st.nerdKey = k;
    box.textContent = "";
    parts.forEach(function (p, i) {
      var line = document.createElement("div");
      if (i === parts.length - 1) line.className = "new"; // only the newest line slides in
      var tag = document.createElement("span");
      tag.className = "tool";
      tag.textContent = p[0] + ": ";
      line.appendChild(tag);
      line.appendChild(document.createTextNode(p[1]));
      box.appendChild(line);
    });
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

  // ----- footer: a scrolling ticker, or a notice that holds still -----
  function footerLines() {
    if (st.state === "confirm") return [CONFIRM_HINT];
    if (st.state === "idle" || st.state === "") return TICKER_IDLE;
    if (PROMPTS[st.state]) return [PROMPTS[st.state]];
    if (st.state === "agent") return TICKER_AGENT;
    return [];
  }

  // Only rebuilt when the text changes, so the scroll doesn't jump on every message.
  function renderFooter() {
    var footer = $("footer");
    var lines = st.notice ? [] : footerLines();
    var k = st.notice ? "n:" + st.notice : "t:" + lines.join("|");
    if (k === st.footerKey) return;
    st.footerKey = k;
    footer.textContent = "";
    footer.classList.toggle("notice", !!st.notice);
    if (st.notice) {
      footer.appendChild(el("div", "notice-text", st.notice));
      return;
    }
    if (!lines.length) return;
    var track = el("div", "ticker-track");
    var half = el("div", "ticker-half");
    track.appendChild(half);
    footer.appendChild(track);
    // Repeat the lines until one copy is wider than the screen, then add a second copy:
    // sliding left by exactly one copy loops without a seam.
    for (var n = 0; n < 12 && half.offsetWidth < DESIGN_W; n++) {
      lines.forEach(function (line) {
        var item = el("span", "ticker-item");
        item.appendChild(el("b", "", "★"));
        item.appendChild(document.createTextNode(line));
        half.appendChild(item);
      });
    }
    track.appendChild(half.cloneNode(true));
    track.style.animationDuration = (half.offsetWidth / TICKER_PX_PER_S) + "s";
  }

  // ----- confetti -----
  function confetti(box, count, loop) {
    box.textContent = "";
    for (var i = 0; i < count; i++) {
      var bit = el("i");
      bit.style.left = (Math.random() * 100) + "%";
      bit.style.background = CONFETTI[i % CONFETTI.length];
      bit.style.animationDelay = (loop ? Math.random() * 4 : Math.random() * 0.8) + "s";
      bit.style.animationDuration = (2.4 + Math.random() * 1.6) + "s";
      bit.style.setProperty("--drift", (Math.random() * 240 - 120) + "px");
      bit.style.setProperty("--spin", (Math.random() * 1080 - 540) + "deg");
      if (loop) bit.style.animationIterationCount = "infinite";
      box.appendChild(bit);
    }
  }

  // ----- mosaic: the wall of pictures scrolling behind the idle screen -----
  function mosaicTile(urls, n) {
    if (!urls.length) return el("div", "tile blank t" + (n % 6));
    var img = el("img", "tile");
    img.alt = "";
    img.onerror = function () { img.style.visibility = "hidden"; };
    img.src = urls[n % urls.length];
    return img;
  }

  function renderMosaic() {
    var urls = st.slides.map(function (s) { return s.url; });
    var k = urls.join(",");
    if (k === st.mosaicKey) return;
    st.mosaicKey = k;
    var box = $("mosaic");
    box.textContent = "";
    for (var c = 0; c < MOSAIC_COLS; c++) {
      var col = el("div", "mosaic-col " + (c % 2 ? "down" : "up"));
      col.style.animationDuration = (48 + (c * 11) % 30) + "s";
      // Two identical runs of tiles, so sliding by one run loops without a seam.
      for (var copy = 0; copy < 2; copy++) {
        for (var r = 0; r < MOSAIC_PER_COL; r++) col.appendChild(mosaicTile(urls, r * MOSAIC_COLS + c));
      }
      box.appendChild(col);
    }
  }

  // ----- slideshow -----
  // The idle screen: pictures with their prompts, every 4th slide the top 3 card.
  // Pictures that fail to load are dropped from the list.
  function nextSlide() {
    st.slideCount += 1;
    if (st.top3.length && st.slideCount % TOP3_EVERY === 0) return { top3: true };
    if (!st.slides.length) return null;
    var slide = st.slides[st.slideIndex % st.slides.length];
    st.slideIndex = (st.slideIndex + 1) % st.slides.length;
    return slide;
  }

  function drawEmpty(box) {
    var empty = el("div", "slide empty ready");
    empty.appendChild(el("div", "empty-big", "Be the first!"));
    box.appendChild(empty);
  }

  // The prompt appears a word at a time.
  function addWords(box, text) {
    text.split(/\s+/).filter(Boolean).forEach(function (w, i) {
      var span = el("span", "w", w);
      span.style.animationDelay = Math.min(0.5 + i * 0.07, 2.5) + "s";
      box.appendChild(span);
      box.appendChild(document.createTextNode(" "));
    });
  }

  function drawImage(box, slide) {
    var wrap = el("div", "slide");
    var hero = el("div", "hero");
    var img = el("img");
    img.alt = "";
    img.onload = function () { wrap.classList.add("ready"); };
    img.onerror = function () {
      st.slides = st.slides.filter(function (s) { return s !== slide; });
      if (wrap.isConnected && !wrap.classList.contains("leaving")) advanceSlide(); // skip a picture that will not load
    };
    img.src = slide.url;
    hero.appendChild(img);
    wrap.appendChild(hero);
    if (slide.prompt) {
      // The padding sits on the outer box so a clamped third line can't peek out under it.
      var caption = el("div", "caption");
      var text = el("div", "caption-text");
      addWords(text, slide.prompt);
      caption.appendChild(text);
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
    var card = el("div", "slide top3-card ready");
    card.appendChild(el("div", "top3-title", "Top 3 so far"));
    var row = el("div", "top3-row");
    st.top3.forEach(function (t, i) {
      var tile = el("div", "top3-tile rank-" + (i + 1));
      var img = el("img");
      img.alt = "";
      img.onerror = function () { tile.hidden = true; };
      img.src = t.url;
      tile.appendChild(el("div", "medal", String(i + 1)));
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
    // The old slide flies out while the new one swoops in.
    Array.prototype.slice.call(box.children).forEach(function (old) {
      if (old.classList.contains("leaving")) return;
      old.classList.add("leaving");
      setTimeout(function () { if (old.parentNode) old.parentNode.removeChild(old); }, LEAVE_MS);
    });
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
      // One span per character, so the digits can drop in one after another.
      var code = $("takehome-code");
      code.textContent = "";
      String(m.claim || "").split("").forEach(function (ch, i) {
        var d = el("span", "digit", ch);
        d.style.animationDelay = (0.5 + i * 0.15) + "s";
        code.appendChild(d);
      });
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
      $("winner-code").textContent = m.claim_code || "";
      $("winner-claim").hidden = !m.claim_code; // a picture made elsewhere has no code here
      var box = $("winner-top3");
      box.textContent = "";
      (Array.isArray(m.top3) ? m.top3 : []).forEach(function (t, i) {
        var url = typeof t === "string" ? t : (t && (t.image_url || t.url));
        if (!url) return;
        var img = document.createElement("img");
        img.src = url;
        img.alt = "";
        img.style.animationDelay = (0.8 + i * 0.2) + "s";
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
      renderMosaic();
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
  renderMosaic();
  render();
  connect();
})();
