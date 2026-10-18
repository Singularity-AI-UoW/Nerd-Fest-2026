// Server-rendered public pages. Every visitor-supplied string goes through escapeHtml.
import type { Leaderboard } from "./votes";
import { contactDeadline } from "./claims";

export interface ImageRow {
  id: string;
  prompt: string;
  mode: string;
  created_at: string;
}

const HTML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export const escapeHtml = (s: string): string => s.replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);

const CSP =
  "default-src 'self'; img-src 'self' data:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'";

export function htmlResponse(body: string, status = 200): Response {
  return new Response(body, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "content-security-policy": CSP,
    },
  });
}

const CSS = `
:root{--bg:#120a1f;--panel:#1e1233;--panel-2:#2a1a47;--text:#fff;--muted:#cdbfe6;--orange:#ff9a2e;--orange-deep:#ff7a00;--purple:#c08bff;--pink:#ff5ca8;--yellow:#ffd23f;--green:#4ade80;--bounce:cubic-bezier(.3,1.6,.5,1)}
*{box-sizing:border-box}
html{background:var(--bg)}
body{position:relative;margin:0;padding:0 16px 32px;color:var(--text);font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;line-height:1.4;overflow-x:hidden}
body::before{content:"";position:fixed;inset:-25%;z-index:-1;pointer-events:none;background:radial-gradient(circle at 15% 15%,rgba(123,47,255,.35),transparent 30%),radial-gradient(circle at 85% 60%,rgba(255,122,0,.22),transparent 30%),radial-gradient(circle at 30% 90%,rgba(255,92,168,.18),transparent 28%);animation:drift 22s ease-in-out infinite alternate}
@keyframes drift{to{transform:translate(8%,-6%) rotate(8deg)}}
main{max-width:640px;margin:0 auto}
.ticker{margin:0 -16px 18px;overflow:hidden;white-space:nowrap;background:var(--panel-2);border-bottom:3px solid var(--orange);font-weight:800;font-size:.95rem;padding:8px 0}
.ticker div{display:inline-flex;animation:ticker 26s linear infinite}
.ticker span{padding-right:28px}
.ticker b{color:var(--orange);margin-right:12px}
@keyframes ticker{to{transform:translateX(-50%)}}
h1{font-size:1.6rem;font-weight:900;margin:0 0 12px;overflow-wrap:anywhere;line-height:1.15}
main>h1{background:linear-gradient(90deg,var(--orange),var(--pink),var(--purple),var(--orange));background-size:300% 100%;-webkit-background-clip:text;background-clip:text;color:transparent;animation:shimmer 8s linear infinite,rise .6s ease-out both}
@keyframes shimmer{to{background-position:300% 0}}
h2{font-size:1.15rem;margin:24px 0 10px;color:var(--purple)}
a{color:var(--purple)}
img{display:block;width:100%;height:auto;border-radius:14px;background:var(--panel-2)}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;padding:0;margin:0;list-style:none}
.card{background:rgba(30,18,51,.85);border:1px solid rgba(192,139,255,.2);border-radius:16px;overflow:hidden;display:flex;flex-direction:column;transition:transform .25s ease,border-color .25s,box-shadow .25s}
.card:hover{transform:translateY(-4px) rotate(-.5deg);border-color:rgba(255,154,46,.6);box-shadow:0 14px 34px rgba(0,0,0,.4),0 0 24px rgba(255,154,46,.18)}
.card img{border-radius:0}
.card a.pic{color:inherit;text-decoration:none}
.card p{margin:8px 10px;font-size:.85rem;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.card .vote{margin:auto 10px 10px}
.js .reveal{opacity:0;transform:translateY(40px) scale(.94)}
.js .reveal.in{opacity:1;transform:none;transition:opacity .5s ease-out,transform .6s var(--bounce)}
.actions{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:16px 0}
.btn,button.vote{position:relative;display:inline-block;min-height:44px;padding:10px 20px;border:0;border-radius:999px;background:linear-gradient(135deg,var(--orange),var(--orange-deep));color:#1a0f00;font:inherit;font-weight:800;text-decoration:none;cursor:pointer;box-shadow:0 4px 0 #b24d00;transition:transform .12s,box-shadow .12s,filter .2s}
.btn:hover,button.vote:hover{filter:brightness(1.1)}
.btn:active,button.vote:active{transform:translateY(3px);box-shadow:0 1px 0 #b24d00}
.btn.plain{background:var(--panel-2);color:var(--text);box-shadow:0 4px 0 #120a1f;border:2px solid var(--purple)}
button.vote.voted{animation:voted .5s var(--bounce)}
@keyframes voted{40%{transform:scale(1.18) rotate(-4deg)}}
.burst{position:absolute;left:50%;top:50%;pointer-events:none;font-style:normal;font-size:1.1rem;animation:burst .9s ease-out forwards}
@keyframes burst{from{opacity:1;transform:translate(-50%,-50%) scale(.6)}to{opacity:0;transform:translate(calc(-50% + var(--x)),calc(-50% + var(--y))) scale(1.3) rotate(var(--r))}}
.claim{position:relative;margin:16px 0;padding:18px 16px;border-radius:16px;background:var(--panel);text-align:center;overflow:hidden;isolation:isolate;animation:pop .6s var(--bounce) both}
.claim::before{content:"";position:absolute;inset:-60%;z-index:-2;background:conic-gradient(var(--orange),var(--pink),var(--purple),var(--green),var(--orange));animation:spin 4s linear infinite}
.claim::after{content:"";position:absolute;inset:3px;z-index:-1;border-radius:13px;background:var(--panel)}
.claim .code{display:block;font-size:3rem;font-weight:900;letter-spacing:.2em;margin:4px 0;color:var(--orange);text-shadow:0 0 24px rgba(255,154,46,.6)}
.claim p{margin:4px 0}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pop{from{opacity:0;transform:scale(.7)}to{opacity:1;transform:none}}
@keyframes rise{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:none}}
main>img{box-shadow:0 0 0 4px var(--orange),0 0 50px rgba(255,154,46,.35);animation:reveal 1s cubic-bezier(.2,.8,.2,1) both}
@keyframes reveal{from{opacity:0;filter:blur(18px) saturate(2);transform:scale(.9) rotate(-2deg)}to{opacity:1;filter:none;transform:none}}
.contact{margin:16px 0;padding:16px;border-radius:16px;background:rgba(30,18,51,.9);border:2px solid var(--purple);animation:rise .5s ease-out both}
.contact h2{margin:0 0 6px}
.contact p{margin:6px 0}
.contact input{width:100%;min-height:44px;margin:8px 0;padding:10px 12px;border-radius:12px;border:2px solid var(--panel-2);background:var(--bg);color:var(--text);font:inherit;transition:border-color .2s,box-shadow .2s}
.contact input:focus{outline:none;border-color:var(--orange);box-shadow:0 0 0 4px rgba(255,154,46,.25)}
.contact .note{font-size:.85rem;color:var(--muted)}
.contact-msg{min-height:1.2em;font-weight:700;color:var(--green)}
.empty{color:var(--muted);font-size:1.1rem;animation:rise .6s ease-out both}
#top3:empty{display:none}
#top3 ol{padding:0;margin:0;list-style:none;display:grid;grid-template-columns:repeat(3,1fr);gap:10px;align-items:end;counter-reset:rank}
#top3 li{position:relative;counter-increment:rank;background:rgba(30,18,51,.9);border:1px solid rgba(192,139,255,.25);border-radius:14px;font-size:.85rem;font-weight:800;text-align:center;animation:podium .7s var(--bounce) both}
#top3 li:nth-child(1){order:2;animation-delay:.5s;border:2px solid var(--yellow);box-shadow:0 0 24px rgba(255,210,63,.35)}
#top3 li:nth-child(2){order:1;animation-delay:.15s;margin-top:18px}
#top3 li:nth-child(3){order:3;animation-delay:.3s;margin-top:30px}
#top3 li::before{content:counter(rank);position:absolute;z-index:1;top:-12px;left:50%;width:30px;height:30px;margin-left:-15px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:900;color:#1a0f00;background:linear-gradient(135deg,#f1b27a,#a3602c);box-shadow:0 3px 0 rgba(0,0,0,.35)}
#top3 li:nth-child(1)::before{background:linear-gradient(135deg,#fff1a6,#ffb800)}
#top3 li:nth-child(2)::before{background:linear-gradient(135deg,#f4f4f8,#9fa3b5)}
#top3 li img{border-radius:13px 13px 0 0}
@keyframes podium{from{opacity:0;transform:translateY(60px)}to{opacity:1;transform:none}}
#top3 li a{color:inherit;text-decoration:none;display:block}
#top3 li span{display:block;padding:6px;color:var(--green)}
#top3 .closed{font-weight:800;margin:0 0 8px;color:var(--orange)}
.vote-msg{display:block;margin:6px 10px;min-height:1.2em;font-size:.9rem;font-weight:700;animation:rise .35s ease-out both}
.actions .vote-msg{margin:0}
@media (min-width:560px){.grid{grid-template-columns:repeat(3,1fr)}main{max-width:900px}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}.js .reveal{opacity:1;transform:none}}
`;

// The scrolling strip at the top of every page, like the one on the stall's TV.
const TICKER_LINES = ["Made live at the Nerd Fest AI stall", "Vote for your favourite", "Winners at 6pm"];
const TICKER = (() => {
  const run = TICKER_LINES.map((l) => `<span><b>★</b>${l}</span>`).join("");
  return `<div class="ticker" aria-hidden="true"><div>${run}${run}${run}${run}</div></div>`;
})();

// Runs in the visitor's browser: cards rise into view as they scroll on screen.
// Without JavaScript (or IntersectionObserver) every card simply shows.
const MOTION_SCRIPT = `(function () {
  if (!("IntersectionObserver" in window)) return;
  document.documentElement.classList.add("js");
  var seen = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      e.target.classList.add("in");
      seen.unobserve(e.target);
    });
  }, { rootMargin: "0px 0px -40px 0px" });
  document.querySelectorAll(".card").forEach(function (card, i) {
    card.classList.add("reveal");
    card.style.transitionDelay = (i % 3) * 0.08 + "s";
    // Once it has risen in, hand the card back to its normal hover effects.
    card.addEventListener("transitionend", function done(e) {
      if (e.target !== card || e.propertyName !== "transform") return;
      card.removeEventListener("transitionend", done);
      card.classList.remove("reveal", "in");
      card.style.transitionDelay = "";
    });
    seen.observe(card);
  });
})();`;

function layout(body: string, ...scripts: string[]): string {
  return `<!doctype html>
<html lang="en-NZ">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Nerdfest AI Gallery</title>
<style>${CSS}</style>
</head>
<body>
${TICKER}
<main>
${body}
</main>${[...scripts, MOTION_SCRIPT].map((s) => `\n<script>${s}</script>`).join("")}
</body>
</html>
`;
}

const voteButton = (id: string) => `<button class="vote" data-id="${escapeHtml(id)}" type="button">Vote</button>`;

// Runs in the visitor's browser. One handler for every vote button on the page.
const VOTE_SCRIPT = `(function () {
  var MESSAGES = { 200: "Thanks for voting!", 403: "Voting has closed" };
  function show(btn, text) {
    var msg = btn.nextElementSibling;
    if (!msg || !msg.classList.contains("vote-msg")) {
      msg = document.createElement("span");
      msg.className = "vote-msg";
      msg.setAttribute("role", "status");
      btn.insertAdjacentElement("afterend", msg);
    }
    msg.textContent = text;
  }
  // A little burst of stars and hearts from the button.
  function burst(btn) {
    btn.classList.remove("voted");
    void btn.offsetWidth;
    btn.classList.add("voted");
    ["★", "♥", "★", "♥", "★", "♥"].forEach(function (ch, i) {
      var bit = document.createElement("i");
      var angle = (i / 6) * 2 * Math.PI - Math.PI / 2;
      bit.className = "burst";
      bit.setAttribute("aria-hidden", "true");
      bit.textContent = ch;
      bit.style.color = i % 2 ? "#ff5ca8" : "#ffd23f";
      bit.style.setProperty("--x", Math.round(Math.cos(angle) * 46) + "px");
      bit.style.setProperty("--y", Math.round(Math.sin(angle) * 34 - 10) + "px");
      bit.style.setProperty("--r", (i * 60 - 150) + "deg");
      btn.appendChild(bit);
      setTimeout(function () { bit.remove(); }, 1000);
    });
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("button.vote");
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    fetch("/api/vote", {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ id: btn.getAttribute("data-id") })
    }).then(function (res) {
      show(btn, MESSAGES[res.status] || "Couldn't vote \u2014 try again");
      if (res.status === 200) burst(btn);
    }, function () {
      show(btn, "Couldn't vote \u2014 try again");
    }).then(function () {
      btn.disabled = false;
    });
  });
})();`;

function renderTop3(board: Leaderboard): string {
  const items = board.top3
    .map((t) => {
      const id = escapeHtml(t.id);
      const prompt = escapeHtml(t.prompt);
      return `<li><a href="/i/${id}"><img src="/img/${id}.jpg" alt="${prompt}" loading="lazy" width="256" height="256"><span>${t.votes} ${t.votes === 1 ? "vote" : "votes"}</span></a></li>`;
    })
    .join("\n");
  const closed = board.closed ? `<p class="closed">Voting has closed</p>` : "";
  if (board.top3.length === 0) return closed;
  return `${closed}<h2>Top 3</h2>\n<ol>\n${items}\n</ol>`;
}

export function renderGallery(images: ImageRow[], board: Leaderboard = { closed: false, top3: [] }): string {
  const cards = images
    .map((img) => {
      const prompt = escapeHtml(img.prompt);
      const id = escapeHtml(img.id);
      return `<li class="card">
<a class="pic" href="/i/${id}"><img src="/img/${id}.jpg" alt="${prompt}" loading="lazy" width="512" height="512"><p>${prompt}</p></a>
${voteButton(img.id)}
</li>`;
    })
    .join("\n");
  const list =
    images.length === 0
      ? `<p class="empty">No pictures yet. Come and make one at the stall!</p>`
      : `<ul class="grid">\n${cards}\n</ul>`;
  return layout(`<h1>Nerdfest AI Gallery</h1>
<section id="top3" aria-label="Top 3">${renderTop3(board)}</section>
<section id="all">
<h2>All pictures</h2>
${list}
</section>`, VOTE_SCRIPT);
}

// Runs in the visitor's browser. The claim code stays in the browser: it is read from the URL
// fragment (which browsers never send to a server), kept in localStorage and shown on screen.
const CLAIM_SCRIPT = `(function () {
  var id = document.body.getAttribute("data-id");
  var key = "claim:" + id;
  var code = null;
  var m = /^#c=(\\d{4})$/.exec(location.hash);
  if (m) {
    code = m[1];
    try { localStorage.setItem(key, code); } catch (e) {}
    try { history.replaceState(null, "", location.pathname + location.search); } catch (e) {}
  } else {
    try { code = localStorage.getItem(key); } catch (e) {}
  }
  if (code && /^\\d{4}$/.test(code)) {
    document.getElementById("claim-code").textContent = code;
    document.getElementById("claim").hidden = false;
  }
})();`;

// Runs in the visitor's browser, only while the contact form can be used. The claim code
// leaves the browser only here, and only when the visitor presses Save. It reads the code
// the claim script put on the page, and shows the form only with a code and until the
// deadline (the server checks the deadline too).
const CONTACT_SCRIPT = `(function () {
  var id = document.body.getAttribute("data-id");
  var code = document.getElementById("claim").hidden ? "" : document.getElementById("claim-code").textContent;
  var form = document.getElementById("contact-form");
  if (!form || !/^\\d{4}$/.test(code)) return;
  var left = Date.parse(form.getAttribute("data-contact-until")) - Date.now();
  if (!(left > 0)) return;
  form.hidden = false;
  setTimeout(function () { form.hidden = true; }, left);
  var msg = document.getElementById("contact-msg");
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var btn = form.querySelector("button");
    btn.disabled = true;
    fetch("/api/claim", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ id: id, code: code, contact: document.getElementById("contact").value })
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (body) {
        msg.textContent = res.ok ? "Saved. If you win, we'll be in touch after 6:30pm."
          : (body.error || "Couldn't save \\u2014 try again");
      });
    }, function () {
      msg.textContent = "Couldn't save \\u2014 try again";
    }).then(function () { btn.disabled = false; });
  });
})();`;

function contactForm(img: ImageRow): string {
  const until = contactDeadline(img.created_at);
  if (until === null || Date.now() >= until.getTime()) return "";
  return `<form class="contact" id="contact-form" data-contact-until="${until.toISOString()}" hidden>
<h2>Won&#39;t be here at 6pm?</h2>
<p>Leave an email or mobile number and we&#39;ll send the prize to you if you win.</p>
<input id="contact" name="contact" type="text" inputmode="email" autocomplete="email" maxlength="100" required aria-label="Email or mobile number" placeholder="Email or mobile number">
<button class="btn" type="submit">Save</button>
<p class="note">Under 16? Use a parent's or caregiver's details. We only use this to send the prize, and delete it after the event.</p>
<p class="contact-msg" id="contact-msg" role="status"></p>
</form>`;
}

export function renderImagePage(img: ImageRow): string {
  const id = escapeHtml(img.id);
  const prompt = escapeHtml(img.prompt);
  const form = contactForm(img);
  return layout(
    `<h1>${prompt}</h1>
<img src="/img/${id}.jpg" alt="${prompt}" loading="lazy" width="1024" height="1024">
<div class="claim" id="claim" hidden>
<p>Your claim code:</p>
<strong class="code" id="claim-code"></strong>
<p>Keep this! If your picture wins, show it at the stall before 6:30pm.</p>
</div>
${form}
<div class="actions">
<a class="btn" href="/img/${id}.jpg" download>Download</a>
${voteButton(img.id)}
<a class="btn plain" href="/">See the gallery</a>
</div>`,
    CLAIM_SCRIPT,
    ...(form ? [CONTACT_SCRIPT] : []),
    VOTE_SCRIPT,
  ).replace("<body>", `<body data-id="${id}">`);
}

export function renderNotFound(): string {
  return layout(`<h1>Picture not found</h1>
<p>We couldn't find that picture. It may have been removed.</p>
<div class="actions"><a class="btn plain" href="/">See the gallery</a></div>`);
}
