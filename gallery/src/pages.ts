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
*{box-sizing:border-box}
body{margin:0;padding:16px;background:#101418;color:#f2f4f7;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;line-height:1.4}
main{max-width:640px;margin:0 auto}
h1{font-size:1.4rem;margin:0 0 12px;overflow-wrap:anywhere}
h2{font-size:1.15rem;margin:24px 0 8px}
a{color:#7cc4ff}
img{display:block;width:100%;height:auto;border-radius:12px;background:#1b222a}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;padding:0;margin:0;list-style:none}
.card{background:#1b222a;border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.card a.pic{color:inherit;text-decoration:none}
.card p{margin:8px 10px;font-size:.85rem;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.card .vote{margin:0 10px 10px}
.actions{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:16px 0}
.btn,button.vote{display:inline-block;min-height:44px;padding:10px 18px;border:0;border-radius:10px;background:#2f81f7;color:#fff;font:inherit;font-weight:600;text-decoration:none;cursor:pointer}
.btn.plain{background:#2a323c}
.claim{margin:16px 0;padding:16px;border-radius:12px;background:#1f2a1f;border:2px solid #4caf50;text-align:center}
.claim .code{display:block;font-size:3rem;font-weight:700;letter-spacing:.2em;margin:4px 0}
.claim p{margin:4px 0}
.contact{margin:16px 0;padding:16px;border-radius:12px;background:#1b222a;border:2px solid #2f81f7}
.contact h2{margin:0 0 6px}
.contact p{margin:6px 0}
.contact input{width:100%;min-height:44px;margin:8px 0;padding:10px 12px;border-radius:10px;border:1px solid #3a4552;background:#101418;color:#f2f4f7;font:inherit}
.contact .note{font-size:.85rem;color:#aab4bf}
.contact-msg{min-height:1.2em;font-weight:600}
.empty{color:#aab4bf}
#top3:empty{display:none}
#top3 ol{padding:0;margin:0;list-style:none;display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
#top3 li{background:#1b222a;border-radius:12px;overflow:hidden;font-size:.85rem;text-align:center}
#top3 li a{color:inherit;text-decoration:none;display:block}
#top3 li span{display:block;padding:6px}
#top3 .closed{font-weight:600;margin:0 0 8px}
.vote-msg{display:block;margin:6px 10px;min-height:1.2em;font-size:.9rem}
.actions .vote-msg{margin:0}
`;

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
<main>
${body}
</main>${scripts.map((s) => `\n<script>${s}</script>`).join("")}
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
