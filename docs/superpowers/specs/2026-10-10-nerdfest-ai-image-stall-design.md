# Nerdfest 2026 AI Image Stall — Design

Date: 10/10/2026. Status: approved 10/10/2026.

## 1. Goal

A self-running stall at Nerdfest 2026 (Friday 30/10/2026, 3pm–6:30pm, The Pā, University of Waikato; setup from 1pm). A visitor presses a big button, says what they want, and watches an AI agent visibly operate an image tool to make it. They get one tweak, then a QR code to take the image home. The screen resets by itself.

**Success means:**
- The organiser does not touch the laptop between visitors.
- One visitor takes 60–90 s.
- No inappropriate image reaches the TV or the gallery.
- If something fails, the show does not get stuck.

**Out of scope:** an extra vision-model check of generated images (the three filters are enough), visitor accounts, collecting names or contact details, multiple stations, languages other than English.

## 2. Decisions

| Area | Decision |
|---|---|
| Agent approach | Option A: our own studio page, driven visibly by Playwright. The LLM only calls high-level tools. |
| LLM | GPT-6.1 Sol (`openai/gpt-6.1-sol`) via OpenRouter. Fallback: self-hosted Qwen3.8-27B (model id `Qwen3.8-27B`, OpenAI-style Responses API, reached over Tailscale). Tested on 10/10/2026: tool calling works, 0.6–1.3 s per call. |
| Speech-to-text | Gemini 3.5 Transcribe Live (`gemini-3.5-transcribe-live`), paid tier |
| Image model | Nano Banana 2 Lite (`gemini-3.1-flash-lite-image`), 1K square. Fallback: nano banana 2.1 (`gemini-nano-banana-2.1`), one config change. Timing test on 10/10/2026: Lite took about 7 s to generate and about 11 s to tweak, against about 20 s and 21 s for 2.1. |
| Narration | On-screen text only |
| Hosting | Cloudflare Worker with static assets, R2 (images) and D1 (votes, image index) |
| Hardware | Windows laptop (organiser screen), external TV (crowd screen), headset, USB arcade button that acts as a keyboard key |
| Mode choice | The visitor chooses on screen: hold the button for kid mode, press twice for nerd mode |
| Copyrighted characters | Allowed as asked (Google's filter may still refuse some) |
| Voting | Phone voting, closes at 6:00pm. The winner claims with a claim code at the stall before 6:30pm. |
| Data retention | Gallery stays up until 30/11/2026, then everything is deleted |

## 3. Architecture

```
┌──────────────── Windows laptop ─────────────────────────────┐
│ kiosk (Python)                                              │
│  show controller (state machine, timeouts)                  │
│  ├─ input: global key hook (arcade button) + typed fallback │
│  ├─ voice: mic → Gemini Transcribe Live → live captions     │
│  ├─ filter: blocklist → LLM check/rewrite                   │
│  ├─ agent: GPT-6.1 Sol tool loop → Playwright actions       │
│  ├─ images: Google GenAI (generate, edit)                   │
│  └─ uploader: → Cloudflare (with retry queue)               │
│  web server (FastAPI + WebSocket)                           │
│  ├─ TV page (Playwright-controlled Chromium, fullscreen)    │
│  │    contains the studio page in an iframe                 │
│  └─ organiser page (laptop screen)                          │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTPS, bearer token
┌──────────────── Cloudflare ──▼──────────────────────────────┐
│ Worker: gallery page, image pages, vote API, upload/delete  │
│ R2: images     D1: images, votes                            │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 Kiosk units (laptop, Python)

Each unit has one job and is testable on its own.

- **`show`: show controller.** A state machine: `idle → choose_mode → listen → confirm → filter → agent → tweak → takehome → idle`. It owns all timeouts. It talks to the other units only through their interfaces and broadcasts state to the TV and organiser pages over WebSocket.
- **`inputs`: button and typed fallback.** A global keyboard hook (works whichever window has focus) turns the configured key into `press`, `hold_start`, `hold_end` and `double_press` events. Typed text from the organiser page arrives as a `typed_request` event.
- **`voice`: speech-to-text.** While the button is held, it captures headset audio (16 kHz mono PCM) and streams it to Gemini Transcribe Live. It emits partial and final transcripts. Audio is never saved.
- **`safety`: content filter.**
  1. A blocklist with normalised matching (lower case, common letter swaps like `0→o`, `3→e`, repeated letters squashed).
  2. An LLM check that returns `{allowed, reason, image_prompt}`. The rewritten `image_prompt` adds a family-friendly style. Real people are always refused. Copyrighted characters are allowed.

  The same check applies to the tweak options.
- **`agent`: the LLM tool loop.** It sends the request to GPT-6.1 Sol with these tools. Every tool takes a `narration` string, which is shown as a caption.
  - `open_studio()`
  - `type_prompt(text)`: Playwright types it visibly, character by character
  - `generate_image()`
  - `offer_tweaks(options[4])`: short, kid-friendly labels
  - `apply_tweak(option)`
  - `finish()`

  It is capped at 10 tool calls per visitor. The system prompt differs for kid mode ("make a creature" style, simple words) and nerd mode (explains each step; the TV also shows the full prompt, model names and each tool call).
- **`studio`: the studio page and the Playwright driver.** A simple image tool page with a prompt box, a Generate button and an image area. The driver exposes `open()`, `type(text)`, `click_generate()` and `show_image(bytes)`. The page itself never calls Google. The kiosk calls the image API and pushes the result into the page.
- **`images`: image generation.** `generate(prompt) → jpeg` and `edit(jpeg, instruction) → jpeg`, using the configured model. Each call has a 30 s timeout and one retry.
- **`uploader`.** `upload(id, jpeg, prompt, mode)` with a persistent local retry queue on disk. Images are kept locally either way.
- **`store`: local record.** A SQLite file holding id, prompt, mode, time, claim code and upload status. Claim codes live only here.

### 3.2 Gallery units (Cloudflare Worker)

| Route | Purpose |
|---|---|
| `GET /` | Gallery: all images, newest first, with the top 3 by votes |
| `GET /i/:id` | Image page: image, download button, vote button. Reads the claim code from the URL fragment (`#c=4821`), saves it in localStorage and shows it. The fragment is never sent to the server. |
| `GET /img/:id.jpg` | Image from R2 |
| `GET /api/images` | JSON list (also used by the TV slideshow) |
| `GET /api/leaderboard` | Top 3 |
| `POST /api/vote` | `{id}`. The voter is identified by a random cookie. One vote per phone; voting again moves the vote. Rejected after the close time (18:00 Pacific/Auckland, configurable). |
| `POST /api/upload` | Bearer token. Image plus metadata. |
| `DELETE /api/images/:id` | Bearer token. Removes the image from R2 and D1. |

**D1 tables:**
- `images(id, prompt, mode, created_at)`
- `votes(voter_id PRIMARY KEY, image_id, voted_at)`

**Image ids** are 8 random lower-case base32 characters, so they can't be guessed.

## 4. Visitor flow and timeouts

| Step | TV | Timeout / limit |
|---|---|---|
| idle | Slideshow from `/api/images`; top 3 between slides; "Press the big button!" | Runs until pressed |
| choose_mode | "Hold for kid mode, press twice for nerd mode" | 10 s, then kid mode |
| listen | Live captions while held | 15 s per hold |
| confirm | "I heard: …  Press to go, hold to try again" | 8 s, then go |
| filter | If blocked: "Let's try something else!" and back to listen | 2 blocked tries, then a random preset safe idea |
| agent | Narration captions; studio typing; generation. Nerd mode adds the prompt, model and tool calls. | About 15 s expected; 45 s hard cap |
| tweak | 4 options. Press 1–4 times to choose. | 12 s, then "keep it as is" |
| takehome | Final image, image QR (with claim code fragment), claim code in large text, club sign-up QR | 15 s |

At 6:00pm, voting closes. The TV shows the winner's image and prompt, plus "Winner: show your claim code at the stall". The organiser page shows the winner's claim code for checking. If it is unclaimed by 6:30pm, the prize goes to 2nd place, then 3rd.

## 5. Failure handling

| Failure | Behaviour |
|---|---|
| Speech-to-text error or empty transcript | "I didn't catch that." One retry, then "Ask the organiser to type it". The organiser page highlights the typed fallback. |
| LLM error | One retry on GPT-6.1 Sol, then switch to the Qwen3.8-27B fallback for the safety check and the agent, and the organiser page shows which LLM is active. If both fail, the safety check can't run, so the visitor's own words are not used. A scripted path runs the same visible steps with a random preset safe idea. |
| Image error or over 30 s | One retry, then "The image machine is tired! Try again", then reset |
| Google safety block | Treated as a blocked prompt |
| Upload error | Kept in the local queue and retried in the background. The QR code still points to the final address. |
| Internet down | Red banner on the organiser page; "Back soon" on the TV; the show pauses until the connection returns |
| Unexpected exception | Logged. The show controller resets to idle. |

The organiser page also has: a pause/resume switch, "remove last image", "remove image by id", a live log, and a connection status for each service.

## 6. Configuration

- **`.env`** (not synced, not committed): `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`, `FALLBACK_LLM_URL`, `FALLBACK_LLM_KEY`, `GALLERY_URL`, `GALLERY_TOKEN`. The keys move out of REQUIREMENTS.md.
- **`config.toml`:** image model, LLM model, push-to-talk key, all timeouts, voting close time, blocklist file path, preset safe ideas, sign-up QR file path (`sign_up_qr.svg`).
- **`--fake` flag:** replaces speech-to-text, the LLM and the image model with instant offline stand-ins, for free rehearsal.

## 7. Testing

- **Unit tests, no network:**
  - the state machine: every transition and timeout, using a fake clock
  - the blocklist, using a list of tricky kid prompts
  - the agent loop, using a scripted fake LLM
  - the upload retry queue
  - claim codes
  - Worker routes: votes, close time, auth on upload and delete
- **Fake mode:** the whole loop runs end to end with no internet.
- **Real end-to-end check:** a script runs one real visitor turn against all services.
- **On-site check (1pm):** a checklist screen tests the mic, button, internet, each API (including the Qwen fallback over Tailscale) and a gallery upload and delete.
- **Windows:** a one-click start script. A full trial run on the Windows laptop at least one week before the event.

## 8. Build order

1. Typed input, filter, agent, studio, images, uploader and QR code, plus the gallery Worker (upload and image page). This is the full loop without voice.
2. Voice with live captions; the arcade button.
3. Kid and nerd modes; idle slideshow.
4. Voting, the winner screen, claim codes, and the club sign-up QR.
