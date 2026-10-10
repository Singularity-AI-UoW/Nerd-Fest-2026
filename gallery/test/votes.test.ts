import { env, SELF } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

const BASE = "https://gallery.test";
const A = "aaaaaaa2";
const B = "bbbbbbb2";
const C = "ccccccc2";
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const ORIGINAL_CLOSE_AT = env.VOTE_CLOSE_AT;

async function seed(id: string, createdAt = "2026-10-10T10:00:00.000Z") {
  await env.DB.prepare("INSERT INTO images (id, prompt, mode, created_at) VALUES (?1, ?2, 'kid', ?3)")
    .bind(id, `prompt ${id}`, createdAt)
    .run();
}

function vote(id: unknown, cookie?: string, body?: string) {
  return SELF.fetch(`${BASE}/api/vote`, {
    method: "POST",
    headers: { "content-type": "application/json", ...(cookie ? { cookie: `voter=${cookie}` } : {}) },
    body: body ?? JSON.stringify({ id }),
  });
}

const voterFrom = (res: Response) => /(?:^|[ ,])voter=([^;]+)/.exec(res.headers.get("set-cookie") ?? "")?.[1];

async function voteRows() {
  const { results } = await env.DB.prepare("SELECT voter_id, image_id FROM votes ORDER BY voter_id").all<{
    voter_id: string;
    image_id: string;
  }>();
  return results;
}

async function leaderboard() {
  const res = await SELF.fetch(`${BASE}/api/leaderboard`);
  expect(res.status).toBe(200);
  return (await res.json()) as { closed: boolean; top3: { id: string; prompt: string; url: string; votes: number }[] };
}

beforeEach(async () => {
  env.VOTE_CLOSE_AT = ORIGINAL_CLOSE_AT;
  await env.DB.batch([env.DB.prepare("DELETE FROM images"), env.DB.prepare("DELETE FROM votes")]);
});

afterEach(() => {
  env.VOTE_CLOSE_AT = ORIGINAL_CLOSE_AT;
});

describe("POST /api/vote", () => {
  it("first vote sets cookie and counts", async () => {
    await seed(A);
    const res = await vote(A);
    expect(res.status).toBe(200);
    const setCookie = res.headers.get("set-cookie") ?? "";
    expect(voterFrom(res)).toMatch(UUID_RE);
    for (const attr of ["HttpOnly", "Secure", "SameSite=Lax", "Max-Age=86400", "Path=/"]) {
      expect(setCookie).toContain(attr);
    }
    const rows = await voteRows();
    expect(rows).toEqual([{ voter_id: voterFrom(res), image_id: A }]);
    expect((await leaderboard()).top3).toEqual([{ id: A, prompt: `prompt ${A}`, url: `/img/${A}.jpg`, votes: 1 }]);
  });

  it("second vote from same cookie moves vote", async () => {
    await seed(A);
    await seed(B);
    const first = await vote(A);
    const voter = voterFrom(first)!;
    const second = await vote(B, voter);
    expect(second.status).toBe(200);
    expect(second.headers.get("set-cookie")).toBeNull();
    expect(await voteRows()).toEqual([{ voter_id: voter, image_id: B }]);
    expect((await leaderboard()).top3.map((t) => [t.id, t.votes])).toEqual([[B, 1]]);
  });

  it("malformed voter cookie is replaced with a new uuid", async () => {
    await seed(A);
    const res = await vote(A, "not-a-uuid");
    expect(res.status).toBe(200);
    const issued = voterFrom(res);
    expect(issued).toMatch(UUID_RE);
    expect((await voteRows())[0].voter_id).toBe(issued);
  });

  it("vote after close is 403", async () => {
    await seed(A);
    env.VOTE_CLOSE_AT = "2026-10-10T00:00:00+13:00";
    const res = await vote(A);
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "voting closed" });
    expect(await voteRows()).toEqual([]);
    expect((await leaderboard()).closed).toBe(true);
  });

  it("vote at exactly the close time is 403", async () => {
    await seed(A);
    env.VOTE_CLOSE_AT = new Date(Date.now() - 1).toISOString();
    expect((await vote(A)).status).toBe(403);
  });

  it("vote for unknown image is 404", async () => {
    const res = await vote(A);
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ error: expect.any(String) });
    expect(await voteRows()).toEqual([]);
  });

  it("rejects a bad body with 400", async () => {
    await seed(A);
    expect((await vote(null, undefined, "not json")).status).toBe(400);
    expect((await vote(123)).status).toBe(400);
    expect((await vote("BAD")).status).toBe(400);
    expect(await voteRows()).toEqual([]);
  });

  it("rejects non-POST with 405", async () => {
    const res = await SELF.fetch(`${BASE}/api/vote`);
    expect(res.status).toBe(405);
    expect(res.headers.get("allow")).toBe("POST");
  });
});

describe("GET /api/leaderboard", () => {
  it("is open and empty with no votes", async () => {
    await seed(A);
    expect(await leaderboard()).toEqual({ closed: false, top3: [] });
  });

  it("deleted image votes removed from leaderboard", async () => {
    await seed(A);
    await seed(B);
    // Orphaned vote: image row gone but vote row left behind.
    await env.DB.batch([
      env.DB.prepare("INSERT INTO votes VALUES ('v1', ?, 't')").bind(A),
      env.DB.prepare("INSERT INTO votes VALUES ('v2', ?, 't')").bind(A),
      env.DB.prepare("INSERT INTO votes VALUES ('v3', ?, 't')").bind(B),
      env.DB.prepare("DELETE FROM images WHERE id = ?").bind(A),
    ]);
    expect((await leaderboard()).top3.map((t) => t.id)).toEqual([B]);
  });

  it("leaderboard tiebreak earliest first", async () => {
    await seed(A, "2026-10-10T12:00:00.000Z");
    await seed(B, "2026-10-10T09:00:00.000Z");
    await seed(C, "2026-10-10T10:00:00.000Z");
    await env.DB.batch(
      [
        ["v1", A],
        ["v2", B],
        ["v3", C],
      ].map(([v, i]) => env.DB.prepare("INSERT INTO votes VALUES (?, ?, 't')").bind(v, i)),
    );
    expect((await leaderboard()).top3.map((t) => t.id)).toEqual([B, C, A]);
  });

  it("orders by votes first and returns at most 3", async () => {
    const ids = [A, B, C, "ddddddd2"];
    for (const [n, id] of ids.entries()) await seed(id, `2026-10-10T10:00:0${n}.000Z`);
    const votes: [string, string][] = [
      ["v1", "ddddddd2"],
      ["v2", "ddddddd2"],
      ["v3", C],
      ["v4", B],
      ["v5", A],
    ];
    await env.DB.batch(votes.map(([v, i]) => env.DB.prepare("INSERT INTO votes VALUES (?, ?, 't')").bind(v, i)));
    const top = (await leaderboard()).top3;
    expect(top.map((t) => [t.id, t.votes])).toEqual([
      ["ddddddd2", 2],
      [A, 1],
      [B, 1],
    ]);
  });
});

describe("pages", () => {
  it("gallery renders top 3 server-side from votes", async () => {
    await seed(A);
    await env.DB.prepare("INSERT INTO votes VALUES ('v1', ?, 't')").bind(A).run();
    const html = await (await SELF.fetch(`${BASE}/`)).text();
    expect(html).toMatch(/<section id="top3"[^>]*>[\s\S]*Top 3[\s\S]*href="\/i\/aaaaaaa2"/);
    expect(/<section id="top3"[\s\S]*?<\/section>/.exec(html)![0]).not.toContain("Voting has closed");
    expect(html).toContain("/api/vote");
  });

  it("gallery shows closed state once voting has closed", async () => {
    await seed(A);
    env.VOTE_CLOSE_AT = "2026-10-10T00:00:00+13:00";
    const html = await (await SELF.fetch(`${BASE}/`)).text();
    expect(html).toMatch(/<section id="top3"[^>]*>[\s\S]*Voting has closed/);
  });

  it("image page carries the vote script", async () => {
    await seed(A);
    const html = await (await SELF.fetch(`${BASE}/i/${A}`)).text();
    expect(html).toContain("/api/vote");
    expect(html).toContain("Thanks for voting!");
  });
});
