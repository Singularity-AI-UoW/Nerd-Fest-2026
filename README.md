# Nerd Fest 2026 AI image stall

A self-running stall for families. A child presses the big arcade button, says what picture they want, and an AI agent draws it live on the TV. Every picture goes to the gallery website, where people can vote, and the winners claim a prize at the stall.

It runs on a Windows laptop at the venue. Setup is from 1pm on 30/10/2026. Voting closes at 6:00pm and prizes can be claimed until 6:30pm.

## First-time setup (Windows laptop)

1. **Install uv** (it fetches Python and everything else). In PowerShell:

   ```
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   Then close the window and open a new one.
2. **Copy the keys file.** Copy `~/.nerdfest/.env` from the dev machine to `%USERPROFILE%\.nerdfest\.env` on the laptop (make the `.nerdfest` folder if it is not there). It holds the three API keys and the gallery address. Never put it in the project folder.
3. **Tailscale.** Install Tailscale on the laptop and sign in. The backup AI model is reached through it.
4. **The arcade button.** It must send the **F9** key. If it sends a different key, change `key` under `[button]` in `config.toml`.
5. **Sign-up QR.** Save `sign_up_qr.svg` in the project folder.

## Running it

- Double-click **`start.bat`** for the real stall.
- Double-click **`start-fake.bat`** to practise with fake AI and no internet. Nothing is sent anywhere.

The first run installs things and can take a few minutes. After that the TV window opens full screen on the second display, and the organiser page opens in your browser at `http://127.0.0.1:8765/organiser`. Keep the black command window open; closing it stops the stall.

## The 1pm checklist

Open **`http://127.0.0.1:8765/check`** (there is a link at the top of the organiser page).

1. Click **Run all**. It tests the internet, the Google image model, speech-to-text, the main AI model, the backup AI model and the gallery. Every line should go green. A red line says what to check.
2. Click **Run** on **Microphone level** and say something. It should report a level, not "very quiet".
3. Click **Run** on **Arcade button press**, then press the button within 10 seconds. The show is paused while it waits, so a visitor is not started.

Then do one trial picture yourself and look at it on the TV and on the gallery website.

## During the event

Use the organiser page (`/organiser`).

- **Status lights.** Green is working. If the gallery and Google are both down, a red banner appears and the TV shows "Back soon". It carries on by itself when they come back.
- **Pause / Resume.** Pauses between visitors. Use it for breaks.
- **Type it for the visitor.** If the TV says "Ask the organiser to type it", or a child cannot speak the idea, type it in the box and click Send (up to 200 characters).
- **Visitor.** The stall waits at four steps until the visitor presses the button. If they are unsure, use these buttons instead. Only the current step's buttons work:
  - **Mode:** Kid or Nerd.
  - **I heard…:** Go, or Try again to record again.
  - **Tweak:** 1 to 4, or Keep.
  - **Take-home:** Next visitor, once they have scanned their QR code.

  If nobody does anything for 3 minutes, the stall moves on by itself. Pictures also show their QR code on the idle slideshow, so visitors can come back and scan it later. That QR has no claim code in it: only the take-home card shows the claim code.
- **Remove an image.** **Remove last image** takes down the most recent picture. To remove another, type its 8-character id and click Remove. It is deleted from the gallery as well.
- **Winners.** After voting closes, the top three appear here with their claim codes. Tick **Claimed** when a prize has been handed over.

## After the event

1. **Replace all three API keys** (Google, OpenRouter and the backup model) and delete the old ones.
2. **On or after 30/11/2026**, wipe the gallery:

   ```
   uv run python scripts/wipe_gallery.py --yes
   ```

   It shows which gallery it is wiping, then deletes every picture. It does nothing without `--yes`.

## For developers

- Tests: `uv run pytest`
- `uv run python scripts/e2e_real.py` runs one real visitor turn against the live services and the gallery (paid APIs, so not part of the tests).
