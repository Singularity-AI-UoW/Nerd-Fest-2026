# Event
- Nerdfest 2026, Friday 30/10/2026, 3pm–6:30pm, The Pā, University of Waikato, Hamilton. Free, all ages (kids, families, tech enthusiasts). Halloween costumes welcome.
- Setup and testing from 1pm.
- Voting is a single-day competition; the winner is announced near the end (time to confirm).
- Venue Wi-Fi and power for exhibitors aren't listed — confirm with the organisers, and bring a phone hotspot as a backup.

# The visitor loop (about 60 - 90 seconds each)
1. The screen idles on a slideshow of images people made ealier. This draws a crowd and shows what it does.
2. A visitor presses a big button and says what they want, e.g., "a dragon riding a bike".
3. Live captions on the screen shows what it heard, so the organiser and the crowd can see if it misheard.
4. The agent narrates as it works ("open the image tool...typeing the prompt...") while the screen shows it doing so. THe narration covers the generation wait, which is the dead time.
5. The image appears and the agent offers one tweak ("want it spookiers?") out of 4 options. Capping it at one refinement keeps the queue moving.
6. The visitor gets a take-home. Their image goes up on the gallery and a QR code lets them grab it.
7. The screen resets to idle automatically. The organiser shouldn't have to touch anything between visitors.

# How real the "agent operates the computer" part is
- Hybrid: a real LLM decides what to do and calls tools. A browser automation script (Playwright) then visibly types into a real image generator page.

# Hooks for engagement
- Daily competition: visitors vote on the best image generated so far and the winner gets a prize.
- Kid mode vs nerd mode: kids get "make a creature", while nerds get "explain how this works" with the screen showing the promopt and the model name.
  - Mode is chosen by the visitor on screen at the start: hold the button briefly for kid mode, press twice for nerd mode (the typed fallback can pick either too).
- Club sign-up: a QR code on the sign and at the end of the loop.

# Equipment
- A windows laptop
- A headset
- A big USB arcade push-to-talk button (to buy). Pick one that acts as a keyboard key (HID keyboard, no driver); the key is a config setting.
- A big external screen or TV for the crowd (captions, the agent-driven browser, the image). The laptop screen is the organiser's view (status, remove-image button, typed fallback).

# Consideration
- Use push-to-talk button with a headset or directional mic. The hall will be loud and an open mic will trigger on background chatter.
- Add a typed fallback on a keyboard so a mic failure doesn't stop the stall.
- A content filter on the prompts is a must, because the kids will try to break it.

# Decision
- Image model: Nano Banana 2 Lite (`gemini-3.1-flash-lite-image`) hosted on google AI studio. Fallback: nano banana 2.1 (`gemini-nano-banana-2.1`) — keep the model as one config setting.
    - Timing test 10/10/2026 (5 prompts, 1K square): Lite averaged 7.1 s first image + 10.7 s tweak (~18 s per visitor) vs 2.1 at 19.6 s + 20.6 s (~40 s). Quality was close; Lite handles the tweak (edit) step.
    - Tweak options need kid-friendly wording — "make it spookier" on Lite went quite dark (zombies/ghouls).
- Voice: Gemini 3.5 Transcribe Live (`gemini-3.5-transcribe-live`) via Google AI Studio (Live API, streaming for live captions). Use the paid tier, as the free tier may use voice data to improve Google products.
- LLM: GPT-6.1 Sol (`openai/gpt-6.1-sol`) from OpenRouter. It calls our own high-level tools (open studio, type prompt, generate, offer tweaks), so no computer-use/vision model is needed.
- Narration: on-screen text only (large captions beside the browser). No text-to-speech — the hall is too loud and it keeps the build simpler.
- Agent approach: Option A — our own local "studio" web page; Playwright visibly drives that page, and its backend calls the image API. (Driving the real AI Studio site was ruled out as too fragile for a live stall.)
- Take-home: QR to a simple gallery page where the users can find and download their generated images.
    - Hosting: Cloudflare (Pages for the gallery site, R2 for image storage, D1 database for phone voting). Chosen over GitHub Pages, which can't do phone voting (no database) and is slow to show new images.
    - Gallery is hosted online (laptop uploads each image; visitors' phones use their own mobile data, and images stay available after the event). The laptop needs internet anyway for voice, LLM and image generation.
