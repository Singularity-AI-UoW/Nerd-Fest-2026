# Prize claim: remote contact and in-person check

Date: 11/10/2026. Status: approved design, awaiting spec review.

## Problem

Most visitors leave long before voting closes at 6:00pm. Today a winner only finds out
if they are at the stall then, and there is no way to send the prize (a Prezzy card) to
someone who has gone home.

## Prize rules (unchanged)

- One prize. Voting closes at 6:00pm.
- The winner claims by 6:30pm, in person or through a contact they left earlier.
- If 1st place has not claimed in person and left no checked contact by 6:30pm, the
  organiser goes down the top 3 in rank order and sends the card to the first one with a
  checked contact (or who is at the stall).
- The organiser sends the card details by hand. Nothing is sent automatically.

## Design

### 1. Leave a contact at the end of the turn (gallery picture page)

- The take-home QR opens `/i/<id>#c=<code>`. When the page has a claim code (from the
  fragment, or saved in localStorage by an earlier visit) **and** the picture was uploaded
  less than 5 minutes ago, it shows an optional form:
  - Heading: "Won't be here at 6pm?"
  - Text: "Leave an email or mobile number and we'll send the prize to you if you win."
  - One field: email or NZ mobile number.
  - Note: "Under 16? Use a parent's or caregiver's details. We only use this to send the
    prize, and delete it after the event."
  - Button: "Save". After saving: "Saved. If you win, we'll be in touch after 6:30pm."
- The slideshow QR (`/i/<id>`, no code) never shows the form, so a stranger at the TV
  cannot register on someone else's picture.
- Once the 5 minutes are over, the page shows nothing about contacts (no form, no saved value).

### 2. Gallery API and storage

- New table, `claims`:

  | column | type | notes |
  |---|---|---|
  | image_id | TEXT PRIMARY KEY | one contact per picture |
  | contact | TEXT NOT NULL | normalised email or mobile |
  | code | TEXT NOT NULL | the claim code the visitor's page sent |
  | saved_at | TEXT NOT NULL | ISO time |

- `POST /api/claim` (public), JSON `{id, code, contact}`:
  - `id` must match the existing image id format and exist, otherwise 404.
  - `code` must be exactly 4 digits, otherwise 400.
  - `contact` must be an email (one `@`, a dot in the domain, up to 100 characters) or an
    NZ mobile (`02` or `+642`, then 7–9 more digits; spaces, dashes and brackets
    ignored; stored as `02…`), otherwise 400 with "Enter an email or NZ mobile number".
  - Refused with 403 "Too late to leave a contact" when now is 5 minutes or more after the
    image's `created_at` (server clock only).
  - Saving again within the window replaces the earlier contact (upsert).
  - The gallery cannot check the code (claim codes never leave the stall laptop); it
    stores what was sent. The stall checks it (section 3).
- `GET /api/claims` (Bearer token, the existing `UPLOAD_TOKEN`): returns
  `{claims: [{image_id, contact, code, saved_at}]}` for the current top 3 only.
- `DELETE /api/images/<id>` also deletes that picture's claim row, so `wipe_gallery.py`
  and "Remove an image" remove contacts too.
- Contacts never appear on public pages, `/api/images` or `/api/leaderboard`.

### 3. Organiser page: Winners table

- The stall fetches `/api/claims` with the winners list (`kiosk/winner.py` `winners`).
- Two new columns:
  - **Contact**: the contact, or "In person only" when there is none.
  - **Code**: ✓ when the submitted code matches the local claim code for that picture,
    ✗ when it does not (do not send the prize to a ✗ contact), blank with no contact.
- If the gallery cannot be reached, the columns show "Can't reach the gallery", and the
  rest of the table still works.
- The contact is shown only on the organiser page (local laptop), never on the TV.

### 4. TV winner screen (6:00pm) and in-person check

- The winner screen shows the winning picture, its prompt and its claim code in large
  text: "Is this your code? Come to the stall by 6:30pm with your phone."
  This replaces the rule that the winner message never carries a claim code.
- The organiser hands over the card only when the person's phone shows the picture page
  with the same claim code (saved when they scanned the take-home QR). Someone who only
  photographed the TV will not have that page. The README's "Winners" section says this.

### 5. Text changes on the stall

- Take-home card, under the claim code: "Winners at 6pm. Scan your QR within 5 minutes to
  leave a contact if you won't be here."

## Out of scope

- Sending the prize automatically (email or SMS).
- Moving the 6:30pm deadline or handling 2nd/3rd deadlines in code.
- Showing contacts anywhere other than the organiser page.

## Privacy

- Only visitors who choose to leave a contact are stored, and only for 5 minutes after
  their picture is made.
- The form asks under-16s to use a parent's or caregiver's details and says what the
  contact is for.
- Contacts are deleted with the picture (organiser "Remove", or the gallery wipe on or
  after 30/11/2026).
- The club should confirm this meets its own privacy rules before the event.

## Testing

- Gallery (vitest): form shown only with a code and within 5 minutes; `POST /api/claim`
  accepts email and mobile formats, rejects bad id/code/contact, refuses after 5 minutes,
  upserts; `GET /api/claims` needs the token and returns top 3 only; delete removes the
  claim; contacts absent from public responses.
- Stall (pytest): winners rows get contact and ✓/✗ from the local code; gallery failure
  shows the "Can't reach the gallery" state; the winner message carries the claim code.
- Fake-mode run of the take-home and winner screens at 4K.
