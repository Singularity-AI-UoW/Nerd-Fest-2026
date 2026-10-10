const ID_RE = /^[a-z2-7]{8}$/;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_PROMPT_CHARS = 500;
// Advisory fast-path only: Content-Length can be omitted or faked. The real limit is the
// per-file 5 MB check below, and only authorised callers ever reach body parsing.
const MAX_BODY_BYTES = MAX_IMAGE_BYTES + 64 * 1024;
const MODES = new Set(["kid", "nerd"]);
// ISO 8601 date-time, e.g. 2026-10-10T10:00:00+13:00 or ...Z. A missing zone is read as UTC.
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$/;
const HAS_ZONE_RE = /(Z|[+-]\d{2}:\d{2})$/;

/** True when the YYYY-MM-DD prefix is a real calendar date (Date.parse rolls 30 Feb over to March). */
function isRealCalendarDate(iso: string): boolean {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const probe = new Date(Date.UTC(y, m - 1, d));
  return probe.getUTCFullYear() === y && probe.getUTCMonth() === m - 1 && probe.getUTCDate() === d;
}

class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

const errorResponse = (status: number, error: string, headers?: Record<string, string>) =>
  json({ error }, status, headers);

const sha256 = async (s: string) =>
  crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));

/** Bearer auth. Constant-time compare of digests; an unset or empty token never authorises. */
async function isAuthorised(request: Request, env: Env): Promise<boolean> {
  const expected = env.UPLOAD_TOKEN;
  if (typeof expected !== "string" || expected === "") return false;
  const match = /^Bearer (.+)$/.exec(request.headers.get("authorization") ?? "");
  if (!match) return false;
  const [a, b] = await Promise.all([sha256(match[1]), sha256(expected)]);
  return crypto.subtle.timingSafeEqual(a, b);
}

function requireText(form: FormData, name: string): string {
  const value = form.get(name);
  if (typeof value !== "string") throw new HttpError(400, `Missing field: ${name}`);
  return value;
}

async function handleUpload(request: Request, env: Env): Promise<Response> {
  // Auth has already passed in route(), so only the kiosk can make us parse a body.
  // Advisory fast-path; the per-file size check further down is what enforces the limit.
  const length = Number(request.headers.get("content-length"));
  if (Number.isFinite(length) && length > MAX_BODY_BYTES) throw new HttpError(400, "Upload too large");
  if (!(request.headers.get("content-type") ?? "").toLowerCase().startsWith("multipart/form-data")) {
    throw new HttpError(400, "Expected multipart/form-data");
  }

  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    throw new HttpError(400, "Could not read form data");
  }

  const id = requireText(form, "id");
  if (!ID_RE.test(id)) throw new HttpError(400, "Invalid id");

  const prompt = requireText(form, "prompt");
  if (prompt.trim() === "" || Array.from(prompt).length > MAX_PROMPT_CHARS) {
    throw new HttpError(400, `prompt must be 1 to ${MAX_PROMPT_CHARS} characters`);
  }

  const mode = requireText(form, "mode");
  if (!MODES.has(mode)) throw new HttpError(400, "mode must be kid or nerd");

  const createdRaw = requireText(form, "created_at");
  const createdMs = ISO_RE.test(createdRaw) && isRealCalendarDate(createdRaw) ? Date.parse(HAS_ZONE_RE.test(createdRaw) ? createdRaw : `${createdRaw}Z`) : NaN;
  if (Number.isNaN(createdMs)) throw new HttpError(400, "created_at must be an ISO date-time");
  const createdAt = new Date(createdMs).toISOString();

  // workers-types declares FormData.get() as string | null, but file parts arrive as File.
  const image = form.get("image") as string | File | null;
  if (image === null || typeof image === "string") throw new HttpError(400, "Missing field: image");
  if (image.type !== "image/jpeg") throw new HttpError(400, "image must be image/jpeg");
  if (image.size > MAX_IMAGE_BYTES) throw new HttpError(400, "image must be 5 MB or less");
  const bytes = await image.arrayBuffer();
  const head = new Uint8Array(bytes, 0, Math.min(3, bytes.byteLength));
  if (head.length < 3 || head[0] !== 0xff || head[1] !== 0xd8 || head[2] !== 0xff) {
    throw new HttpError(400, "image is not a JPEG");
  }

  await env.IMAGES.put(`${id}.jpg`, bytes, { httpMetadata: { contentType: "image/jpeg" } });
  await env.DB.prepare(
    `INSERT INTO images (id, prompt, mode, created_at) VALUES (?1, ?2, ?3, ?4)
     ON CONFLICT(id) DO UPDATE SET prompt = excluded.prompt, mode = excluded.mode, created_at = excluded.created_at`,
  )
    .bind(id, prompt, mode, createdAt)
    .run();

  return json({ ok: true, id });
}

async function handleImage(id: string, env: Env): Promise<Response> {
  const object = await env.IMAGES.get(`${id}.jpg`);
  if (!object) return errorResponse(404, "Not found");
  return new Response(object.body, {
    headers: { "content-type": "image/jpeg", "cache-control": "public, max-age=86400" },
  });
}

async function handleDelete(id: string, env: Env): Promise<Response> {
  if (!ID_RE.test(id)) throw new HttpError(400, "Invalid id");
  await env.IMAGES.delete(`${id}.jpg`);
  await env.DB.batch([
    env.DB.prepare("DELETE FROM votes WHERE image_id = ?").bind(id),
    env.DB.prepare("DELETE FROM images WHERE id = ?").bind(id),
  ]);
  return json({ ok: true, id });
}

async function route(request: Request, env: Env): Promise<Response> {
  const { pathname } = new URL(request.url);
  const method = request.method;

  if (pathname === "/api/upload") {
    if (method !== "POST") return errorResponse(405, "Method not allowed", { allow: "POST" });
    if (!(await isAuthorised(request, env))) return errorResponse(401, "Unauthorised");
    return await handleUpload(request, env);
  }

  const img = /^\/img\/([^/]+)\.jpg$/.exec(pathname);
  if (img) {
    if (method !== "GET") return errorResponse(405, "Method not allowed", { allow: "GET" });
    // Malformed ids can never exist, so skip the R2 lookup.
    return ID_RE.test(img[1]) ? await handleImage(img[1], env) : errorResponse(404, "Not found");
  }

  const del = /^\/api\/images\/([^/]+)$/.exec(pathname);
  if (del) {
    if (method !== "DELETE") return errorResponse(405, "Method not allowed", { allow: "DELETE" });
    if (!(await isAuthorised(request, env))) return errorResponse(401, "Unauthorised");
    return await handleDelete(del[1], env);
  }

  return errorResponse(404, "Not found");
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      return await route(request, env);
    } catch (err) {
      if (err instanceof HttpError) return errorResponse(err.status, err.message);
      console.error("Unhandled error", err instanceof Error ? err.message : "unknown");
      return errorResponse(500, "Internal error");
    }
  },
} satisfies ExportedHandler<Env>;
