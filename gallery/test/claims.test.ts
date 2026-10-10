import { env, SELF } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";

const BASE = "https://gallery.test";
const TOKEN = "test-upload-token";
const ID = "abcdefg2";

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

async function seed(id: string, createdAt: string) {
  await env.DB.prepare("INSERT INTO images (id, prompt, mode, created_at) VALUES (?1, 'a dragon', 'kid', ?2)")
    .bind(id, createdAt)
    .run();
}

async function vote(id: string, n: number) {
  for (let i = 0; i < n; i++) {
    await env.DB.prepare("INSERT INTO votes (voter_id, image_id, voted_at) VALUES (?1, ?2, ?3)")
      .bind(crypto.randomUUID(), id, new Date().toISOString())
      .run();
  }
}

function claim(body: unknown) {
  return SELF.fetch(`${BASE}/api/claim`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

function claims(token: string | null = TOKEN) {
  return SELF.fetch(`${BASE}/api/claims`, {
    headers: token === null ? {} : { authorization: `Bearer ${token}` },
  });
}

async function storedClaims() {
  const { results } = await env.DB.prepare("SELECT image_id, contact, code FROM claims").all();
  return results;
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM images"),
    env.DB.prepare("DELETE FROM votes"),
    env.DB.prepare("DELETE FROM claims"),
  ]);
});

describe("POST /api/claim", () => {
  it("saves an email within 5 minutes", async () => {
    await seed(ID, minutesAgo(1));
    const res = await claim({ id: ID, code: "0123", contact: " Kia.Ora@Example.co.nz " });
    expect(res.status).toBe(200);
    expect(await storedClaims()).toEqual([{ image_id: ID, contact: "kia.ora@example.co.nz", code: "0123" }]);
  });

  it.each([
    ["021 123 4567", "0211234567"],
    ["+64 21-123-456", "021123456"],
    ["(022) 1234 5678", "02212345678"],
  ])("normalises the mobile %s", async (raw, stored) => {
    await seed(ID, minutesAgo(1));
    expect((await claim({ id: ID, code: "0123", contact: raw })).status).toBe(200);
    expect((await storedClaims())[0]).toMatchObject({ contact: stored });
  });

  it("replaces an earlier contact", async () => {
    await seed(ID, minutesAgo(1));
    await claim({ id: ID, code: "0123", contact: "a@example.com" });
    await claim({ id: ID, code: "0123", contact: "b@example.com" });
    expect(await storedClaims()).toEqual([{ image_id: ID, contact: "b@example.com", code: "0123" }]);
  });

  it("refuses after 5 minutes", async () => {
    await seed(ID, minutesAgo(5.1));
    const res = await claim({ id: ID, code: "0123", contact: "a@example.com" });
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "Too late to leave a contact" });
    expect(await storedClaims()).toEqual([]);
  });

  it("404 for an unknown picture", async () => {
    expect((await claim({ id: ID, code: "0123", contact: "a@example.com" })).status).toBe(404);
  });

  it.each([
    [{ id: "bad", code: "0123", contact: "a@example.com" }],
    [{ id: ID, code: "123", contact: "a@example.com" }],
    [{ id: ID, code: 1234, contact: "a@example.com" }],
    [{ id: ID, code: "0123", contact: "" }],
    [{ id: ID, code: "0123", contact: "not an email" }],
    [{ id: ID, code: "0123", contact: "a@b" }],
    [{ id: ID, code: "0123", contact: "09 123 4567" }],
    [{ id: ID, code: "0123", contact: `${"x".repeat(95)}@example.com` }],
    [{ id: ID, code: "0123" }],
  ])("rejects bad input %j", async (body) => {
    await seed(ID, minutesAgo(1));
    const res = await claim(body);
    expect([400, 404]).toContain(res.status);
    expect(await storedClaims()).toEqual([]);
  });

  it("says what to enter for a bad contact", async () => {
    await seed(ID, minutesAgo(1));
    const res = await claim({ id: ID, code: "0123", contact: "nope" });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "Enter an email or NZ mobile number" });
  });

  it("rejects a body that is not JSON", async () => {
    const res = await SELF.fetch(`${BASE}/api/claim`, { method: "POST", body: "x" });
    expect(res.status).toBe(400);
  });

  it("only accepts POST", async () => {
    expect((await SELF.fetch(`${BASE}/api/claim`)).status).toBe(405);
  });
});

describe("GET /api/claims", () => {
  it("needs the token", async () => {
    expect((await claims(null)).status).toBe(401);
    expect((await claims("wrong-token-value")).status).toBe(401);
  });

  it("returns claims for the top 3 only", async () => {
    for (const [id, votes] of [["aaaaaaa2", 4], ["bbbbbbb2", 3], ["ccccccc2", 2], ["ddddddd2", 1]] as const) {
      await seed(id, minutesAgo(1));
      await vote(id, votes);
      await claim({ id, code: "0123", contact: `${id}@example.com` });
    }
    const res = await claims();
    expect(res.status).toBe(200);
    const body = (await res.json()) as { claims: { image_id: string }[] };
    expect(body.claims.map((c) => c.image_id).sort()).toEqual(["aaaaaaa2", "bbbbbbb2", "ccccccc2"]);
    expect(body.claims[0]).toHaveProperty("saved_at");
  });
});

describe("contacts stay private", () => {
  it("never appear on public pages or APIs", async () => {
    await seed(ID, minutesAgo(1));
    await vote(ID, 1);
    await claim({ id: ID, code: "0123", contact: "secret@example.com" });
    for (const path of ["/", `/i/${ID}`, "/api/images", "/api/leaderboard"]) {
      expect(await (await SELF.fetch(`${BASE}${path}`)).text()).not.toContain("secret@example.com");
    }
  });

  it("are deleted with the picture", async () => {
    await seed(ID, minutesAgo(1));
    await claim({ id: ID, code: "0123", contact: "a@example.com" });
    const res = await SELF.fetch(`${BASE}/api/images/${ID}`, {
      method: "DELETE",
      headers: { authorization: `Bearer ${TOKEN}` },
    });
    expect(res.status).toBe(200);
    expect(await storedClaims()).toEqual([]);
  });
});

describe("contact form on the picture page", () => {
  it("is on the page with the upload time while the 5 minutes run", async () => {
    const created = minutesAgo(1);
    await seed(ID, created);
    const html = await (await SELF.fetch(`${BASE}/i/${ID}`)).text();
    expect(html).toContain('id="contact-form"');
    expect(html).toContain("Won&#39;t be here at 6pm?");
    expect(html).toContain("parent");
    // The browser shows the form only with a claim code and before this deadline.
    expect(html).toContain(`data-contact-until="${new Date(Date.parse(created) + 5 * 60_000).toISOString()}"`);
  });

  it("is left out once the 5 minutes are over", async () => {
    await seed(ID, minutesAgo(6));
    const html = await (await SELF.fetch(`${BASE}/i/${ID}`)).text();
    expect(html).not.toContain('id="contact-form"');
    expect(html).not.toContain("/api/claim");
  });

  it("sends the code only from the form's submit handler, to /api/claim", async () => {
    await seed(ID, minutesAgo(1));
    const html = await (await SELF.fetch(`${BASE}/i/${ID}`)).text();
    const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
    const senders = scripts.filter((s) => /fetch\(/.test(s) && /code/.test(s) && !/api\/vote/.test(s));
    expect(senders).toHaveLength(1);
    expect(senders[0]).toMatch(/addEventListener\("submit"[\s\S]*fetch\("\/api\/claim"/);
    expect(senders[0]).not.toMatch(/XMLHttpRequest|sendBeacon|\.src\s*=/);
  });
});
