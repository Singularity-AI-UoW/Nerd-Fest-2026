// Prize contacts. A visitor can leave an email or NZ mobile for 5 minutes after their
// picture is uploaded, from the take-home QR page (which has their claim code). The gallery
// cannot check the code (claim codes stay on the stall laptop); it stores what was sent and
// the stall compares it.
import { getLeaderboard } from "./votes";

export const CONTACT_WINDOW_MS = 5 * 60_000;
const CODE_RE = /^\d{4}$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MAX_EMAIL_CHARS = 100;
// 02 or +642, then 7-9 more digits, once spaces, dashes and brackets are removed.
const MOBILE_RE = /^(?:02|\+642)(\d{7,9})$/;

export type ClaimResult = { ok: true } | { ok: false; status: 400 | 403 | 404; error: string };

/** The deadline for leaving a contact, or null if created_at is unreadable. */
export function contactDeadline(createdAt: string): Date | null {
  const ms = Date.parse(createdAt);
  return Number.isNaN(ms) ? null : new Date(ms + CONTACT_WINDOW_MS);
}

/** An email (lower-cased) or a mobile as 02..., or null if it is neither. */
export function normaliseContact(raw: string): string | null {
  const text = raw.trim();
  if (text.includes("@")) {
    return text.length <= MAX_EMAIL_CHARS && EMAIL_RE.test(text) ? text.toLowerCase() : null;
  }
  const m = MOBILE_RE.exec(text.replace(/[\s\-()]/g, ""));
  return m ? `02${m[1]}` : null;
}

export async function saveClaim(env: Env, id: string, code: unknown, contact: unknown): Promise<ClaimResult> {
  if (typeof code !== "string" || !CODE_RE.test(code)) return { ok: false, status: 400, error: "Invalid code" };
  const normalised = typeof contact === "string" ? normaliseContact(contact) : null;
  if (normalised === null) return { ok: false, status: 400, error: "Enter an email or NZ mobile number" };

  const image = await env.DB.prepare("SELECT created_at FROM images WHERE id = ?")
    .bind(id)
    .first<{ created_at: string }>();
  if (!image) return { ok: false, status: 404, error: "Not found" };
  const deadline = contactDeadline(image.created_at);
  if (deadline === null || Date.now() >= deadline.getTime()) {
    return { ok: false, status: 403, error: "Too late to leave a contact" };
  }

  await env.DB.prepare(
    `INSERT INTO claims (image_id, contact, code, saved_at) VALUES (?1, ?2, ?3, ?4)
     ON CONFLICT(image_id) DO UPDATE SET contact = excluded.contact, code = excluded.code, saved_at = excluded.saved_at`,
  )
    .bind(id, normalised, code, new Date().toISOString())
    .run();
  return { ok: true };
}

export interface ClaimRow {
  image_id: string;
  contact: string;
  code: string;
  saved_at: string;
}

/** Contacts for the current top 3 only. */
export async function topClaims(env: Env): Promise<ClaimRow[]> {
  const ids = (await getLeaderboard(env)).top3.map((t) => t.id);
  if (ids.length === 0) return [];
  const { results } = await env.DB.prepare(
    `SELECT image_id, contact, code, saved_at FROM claims WHERE image_id IN (${ids.map(() => "?").join(",")})`,
  )
    .bind(...ids)
    .all<ClaimRow>();
  return results;
}
