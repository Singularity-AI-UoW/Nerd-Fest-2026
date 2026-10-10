import { env, SELF } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";

const TOKEN = "test-upload-token";
const ID = "abcdefg2";
const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0, 16, 0x4a, 0x46, 0x49, 0x46, 1, 2, 3]);

function form(over: Record<string, string | Blob | null> = {}): FormData {
  const f = new FormData();
  const fields: Record<string, string | Blob | null> = {
    id: ID,
    prompt: "a purple cow on the moon",
    mode: "kid",
    created_at: "2026-10-10T10:00:00+13:00",
    image: new Blob([JPEG], { type: "image/jpeg" }),
    ...over,
  };
  for (const [k, v] of Object.entries(fields)) {
    if (v === null) continue;
    if (typeof v === "string") f.set(k, v);
    else f.set(k, v, "x.jpg");
  }
  return f;
}

function upload(body: FormData, token: string | null = TOKEN) {
  return SELF.fetch("https://gallery.test/api/upload", {
    method: "POST",
    headers: token === null ? {} : { authorization: `Bearer ${token}` },
    body,
  });
}

async function count(table: string, where = "1=1", ...args: unknown[]): Promise<number> {
  const r = await env.DB.prepare(`SELECT COUNT(*) AS n FROM ${table} WHERE ${where}`)
    .bind(...args)
    .first<{ n: number }>();
  return r!.n;
}

beforeEach(async () => {
  await env.DB.batch([env.DB.prepare("DELETE FROM images"), env.DB.prepare("DELETE FROM votes")]);
  const listed = await env.IMAGES.list();
  await Promise.all(listed.objects.map((o) => env.IMAGES.delete(o.key)));
});

describe("POST /api/upload", () => {
  it("rejects upload without token (401)", async () => {
    const res = await upload(form(), null);
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: expect.any(String) });
    expect(await count("images")).toBe(0);
  });

  it("rejects a wrong token (401) without echoing it", async () => {
    const res = await upload(form(), "wrong-token-value");
    expect(res.status).toBe(401);
    expect(await res.text()).not.toContain("wrong-token-value");
  });

  it("rejects bad id (400)", async () => {
    for (const id of ["ABCDEFG2", "abcdefg", "abcdefg21", "abcdef01", "../etc/x"]) {
      const res = await upload(form({ id }));
      expect(res.status, id).toBe(400);
    }
    expect(await count("images")).toBe(0);
  });

  it("rejects non-jpeg (400)", async () => {
    const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    const wrongType = await upload(form({ image: new Blob([png], { type: "image/png" }) }));
    expect(wrongType.status).toBe(400);
    // Claims jpeg but bytes are not JPEG.
    const wrongBytes = await upload(form({ image: new Blob([png], { type: "image/jpeg" }) }));
    expect(wrongBytes.status).toBe(400);
    expect(await count("images")).toBe(0);
    expect(await env.IMAGES.head(`${ID}.jpg`)).toBeNull();
  });

  it("rejects bad prompt, mode, created_at and missing image (400)", async () => {
    expect((await upload(form({ prompt: "x".repeat(501) }))).status).toBe(400);
    expect((await upload(form({ prompt: "" }))).status).toBe(400);
    expect((await upload(form({ mode: "adult" }))).status).toBe(400);
    expect((await upload(form({ created_at: "not a date" }))).status).toBe(400);
    expect((await upload(form({ created_at: "2026-02-30T10:00:00Z" }))).status).toBe(400);
    expect((await upload(form({ created_at: "2026-10-10" }))).status).toBe(400);
    expect((await upload(form({ image: null }))).status).toBe(400);
    expect((await upload(form({ image: "just text" }))).status).toBe(400);
    expect(await count("images")).toBe(0);
  });

  it("normalises created_at to an ISO string", async () => {
    await upload(form({ created_at: "2026-10-10T10:00:00" }));
    const row = await env.DB.prepare("SELECT created_at FROM images WHERE id = ?").bind(ID).first();
    expect(row).toEqual({ created_at: "2026-10-10T10:00:00.000Z" });
  });

  it("accepts a 500 character prompt", async () => {
    expect((await upload(form({ prompt: "x".repeat(500) }))).status).toBe(200);
  });

  it("rejects an image over 5 MB (400)", async () => {
    const big = new Uint8Array(5 * 1024 * 1024 + 1);
    big.set([0xff, 0xd8, 0xff]);
    const res = await upload(form({ image: new Blob([big], { type: "image/jpeg" }) }));
    expect(res.status).toBe(400);
    expect(await env.IMAGES.head(`${ID}.jpg`)).toBeNull();
  });

  it("rejects a non-multipart body (400)", async () => {
    const res = await SELF.fetch("https://gallery.test/api/upload", {
      method: "POST",
      headers: { authorization: `Bearer ${TOKEN}`, "content-type": "application/json" },
      body: "{}",
    });
    expect(res.status).toBe(400);
  });

  it("stores and serves image", async () => {
    const res = await upload(form());
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, id: ID });

    const row = await env.DB.prepare("SELECT * FROM images WHERE id = ?").bind(ID).first();
    expect(row).toEqual({
      id: ID,
      prompt: "a purple cow on the moon",
      mode: "kid",
      created_at: new Date("2026-10-10T10:00:00+13:00").toISOString(),
    });

    const img = await SELF.fetch(`https://gallery.test/img/${ID}.jpg`);
    expect(img.status).toBe(200);
    expect(img.headers.get("content-type")).toBe("image/jpeg");
    expect(img.headers.get("cache-control")).toBe("public, max-age=86400");
    expect(new Uint8Array(await img.arrayBuffer())).toEqual(JPEG);
  });

  it("upload is idempotent for the same id", async () => {
    const a = await upload(form());
    const b = await upload(form({ prompt: "updated prompt", mode: "nerd" }));
    expect(a.status).toBe(200);
    expect(b.status).toBe(200);
    expect(await count("images", "id = ?", ID)).toBe(1);
    const row = await env.DB.prepare("SELECT prompt, mode FROM images WHERE id = ?").bind(ID).first();
    expect(row).toEqual({ prompt: "updated prompt", mode: "nerd" });
  });
});

describe("GET /img/:id.jpg", () => {
  it("img 404 for unknown id", async () => {
    const res = await SELF.fetch("https://gallery.test/img/zzzzzzz2.jpg");
    expect(res.status).toBe(404);
  });

  it("404 for a malformed id", async () => {
    const res = await SELF.fetch("https://gallery.test/img/..%2Fsecret.jpg");
    expect(res.status).toBe(404);
  });
});

describe("DELETE /api/images/:id", () => {
  it("delete removes image row votes and object", async () => {
    await upload(form());
    await upload(form({ id: "bbbbbbb2" }));
    await env.DB.batch([
      env.DB.prepare("INSERT INTO votes VALUES (?, ?, ?)").bind("v1", ID, "2026-10-10T11:00:00Z"),
      env.DB.prepare("INSERT INTO votes VALUES (?, ?, ?)").bind("v2", "bbbbbbb2", "2026-10-10T11:00:00Z"),
    ]);

    const res = await SELF.fetch(`https://gallery.test/api/images/${ID}`, {
      method: "DELETE",
      headers: { authorization: `Bearer ${TOKEN}` },
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, id: ID });
    expect(await count("images", "id = ?", ID)).toBe(0);
    expect(await count("votes", "image_id = ?", ID)).toBe(0);
    expect(await env.IMAGES.head(`${ID}.jpg`)).toBeNull();
    // Other image untouched.
    expect(await count("images", "id = ?", "bbbbbbb2")).toBe(1);
    expect(await count("votes", "image_id = ?", "bbbbbbb2")).toBe(1);
    expect(await env.IMAGES.head("bbbbbbb2.jpg")).not.toBeNull();
  });

  it("returns 200 even if the image does not exist", async () => {
    const res = await SELF.fetch("https://gallery.test/api/images/zzzzzzz2", {
      method: "DELETE",
      headers: { authorization: `Bearer ${TOKEN}` },
    });
    expect(res.status).toBe(200);
  });

  it("requires auth (401)", async () => {
    await upload(form());
    const res = await SELF.fetch(`https://gallery.test/api/images/${ID}`, { method: "DELETE" });
    expect(res.status).toBe(401);
    expect(await count("images", "id = ?", ID)).toBe(1);
  });

  it("400 for a malformed id", async () => {
    const res = await SELF.fetch("https://gallery.test/api/images/NOPE", {
      method: "DELETE",
      headers: { authorization: `Bearer ${TOKEN}` },
    });
    expect(res.status).toBe(400);
  });
});

describe("auth hardening", () => {
  it("rejects when UPLOAD_TOKEN is unset or empty, even with an empty bearer", async () => {
    const { default: worker } = await import("../src/index");
    for (const token of [undefined, ""]) {
      const req = new Request("https://gallery.test/api/upload", {
        method: "POST",
        headers: { authorization: "Bearer " },
        body: form(),
      });
      const res = await worker.fetch(req, { ...env, UPLOAD_TOKEN: token } as Env);
      expect(res.status).toBe(401);
    }
  });
});

describe("routing", () => {
  it("404 JSON for unknown path, 405 for wrong method", async () => {
    const nf = await SELF.fetch("https://gallery.test/nope");
    expect(nf.status).toBe(404);
    expect(await nf.json()).toEqual({ error: expect.any(String) });
    const mm = await SELF.fetch("https://gallery.test/api/upload", { method: "GET" });
    expect(mm.status).toBe(405);
  });
});
