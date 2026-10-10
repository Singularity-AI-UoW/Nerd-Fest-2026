import { env, SELF } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";

const BASE = "https://gallery.test";

async function seed(id: string, prompt: string, createdAt: string, mode = "kid") {
  await env.DB.prepare("INSERT INTO images (id, prompt, mode, created_at) VALUES (?1, ?2, ?3, ?4)")
    .bind(id, prompt, mode, createdAt)
    .run();
}

beforeEach(async () => {
  await env.DB.batch([env.DB.prepare("DELETE FROM images"), env.DB.prepare("DELETE FROM votes")]);
});

describe("GET /api/images", () => {
  it("api images newest first", async () => {
    await seed("aaaaaaa2", "oldest", "2026-10-10T09:00:00.000Z");
    await seed("bbbbbbb2", "newest", "2026-10-10T11:00:00.000Z");
    await seed("ccccccc2", "middle", "2026-10-10T10:00:00.000Z", "nerd");
    const res = await SELF.fetch(`${BASE}/api/images`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("application/json");
    const body = (await res.json()) as { images: Record<string, string>[] };
    expect(body.images.map((i) => i.id)).toEqual(["bbbbbbb2", "ccccccc2", "aaaaaaa2"]);
    expect(body.images[1]).toEqual({
      id: "ccccccc2",
      prompt: "middle",
      mode: "nerd",
      created_at: "2026-10-10T10:00:00.000Z",
      url: "/img/ccccccc2.jpg",
    });
  });

  it("api images is empty when nothing is uploaded", async () => {
    const res = await SELF.fetch(`${BASE}/api/images`);
    expect(await res.json()).toEqual({ images: [] });
  });

  it("api images returns at most 500", async () => {
    const stmt = env.DB.prepare("INSERT INTO images (id, prompt, mode, created_at) VALUES (?1, 'p', 'kid', ?2)");
    const alphabet = "abcdefghijklmnopqrstuvwxyz234567";
    const rows = Array.from({ length: 502 }, (_, n) =>
      stmt.bind(`${alphabet[n >> 5]}${alphabet[n & 31]}aaaaaa`, new Date(Date.UTC(2026, 9, 10, 0, 0, n)).toISOString()),
    );
    await env.DB.batch(rows);
    const body = (await (await SELF.fetch(`${BASE}/api/images`)).json()) as { images: unknown[] };
    expect(body.images).toHaveLength(500);
  });

  it("rejects non-GET with 405", async () => {
    const res = await SELF.fetch(`${BASE}/api/images`, { method: "POST" });
    expect(res.status).toBe(405);
  });
});

describe("GET /i/:id", () => {
  it("image page escapes prompt", async () => {
    await seed("abcdefg2", `<script>alert("x")</script> & 'q'`, "2026-10-10T10:00:00.000Z");
    const res = await SELF.fetch(`${BASE}/i/abcdefg2`);
    expect(res.status).toBe(200);
    const html = await res.text();
    expect(html).toContain("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &#39;q&#39;");
    expect(html).not.toContain(`<script>alert("x")</script>`);
    expect(html).toContain(`alt="&lt;script&gt;`);
  });

  it("image page 404 for unknown id", async () => {
    const res = await SELF.fetch(`${BASE}/i/abcdefg2`);
    expect(res.status).toBe(404);
    expect(res.headers.get("content-type")).toContain("text/html");
    expect(await res.text()).toContain("See the gallery");
  });

  it("image page 404 for malformed id", async () => {
    const res = await SELF.fetch(`${BASE}/i/not-an-id`);
    expect(res.status).toBe(404);
    expect(res.headers.get("content-type")).toContain("text/html");
  });

  it("image page includes claim script and download link", async () => {
    await seed("abcdefg2", "a purple cow", "2026-10-10T10:00:00.000Z");
    const res = await SELF.fetch(`${BASE}/i/abcdefg2`);
    const html = await res.text();
    expect(html).toMatch(/<a [^>]*href="\/img\/abcdefg2\.jpg" download>Download<\/a>/);
    expect(html).toContain(`src="/img/abcdefg2.jpg"`);
    expect(html).toContain('loading="lazy"');
    expect(html).toContain("localStorage");
    expect(html).toContain('"claim:"');
    expect(html).toContain("history.replaceState");
    expect(html).toContain("\\d{4}");
    expect(html).toContain("Your claim code:");
    expect(html).toContain("Keep this! If your picture wins, show it at the stall before 6:30pm.");
    expect(html).toContain('<button class="vote" data-id="abcdefg2"');
    expect(html).toContain('href="/"');
    expect(html).toContain("See the gallery");
    expect(html).toContain("<title>Nerdfest AI Gallery</title>");
    expect(html).toContain("<h1>a purple cow</h1>");
  });

  it("html pages carry noindex, no-store and CSP", async () => {
    await seed("abcdefg2", "a purple cow", "2026-10-10T10:00:00.000Z");
    for (const path of ["/", "/i/abcdefg2", "/i/zzzzzzz2"]) {
      const res = await SELF.fetch(`${BASE}${path}`);
      expect(res.headers.get("content-type")).toContain("text/html");
      expect(res.headers.get("cache-control")).toBe("no-store");
      expect(res.headers.get("content-security-policy")).toBe(
        "default-src 'self'; img-src 'self' data:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'",
      );
      expect(await res.text()).toContain('<meta name="robots" content="noindex">');
    }
  });

  it("claim script never sends the code anywhere", async () => {
    await seed("abcdefg2", "a purple cow", "2026-10-10T10:00:00.000Z");
    const html = await (await SELF.fetch(`${BASE}/i/abcdefg2`)).text();
    const script = /<script>([\s\S]*?)<\/script>/.exec(html)![1];
    expect(script).not.toMatch(/fetch|XMLHttpRequest|sendBeacon|\.src\s*=/);
  });
});

describe("GET /", () => {
  it("gallery lists images", async () => {
    await seed("aaaaaaa2", "first <b>one</b>", "2026-10-10T09:00:00.000Z");
    await seed("bbbbbbb2", "second one", "2026-10-10T11:00:00.000Z");
    const res = await SELF.fetch(`${BASE}/`);
    expect(res.status).toBe(200);
    const html = await res.text();
    expect(html).toContain("<title>Nerdfest AI Gallery</title>");
    expect(html).toContain('<section id="top3"');
    expect(html).toContain('href="/i/aaaaaaa2"');
    expect(html).toContain('src="/img/bbbbbbb2.jpg"');
    expect(html).toContain('loading="lazy"');
    expect(html).toContain('<button class="vote" data-id="bbbbbbb2"');
    expect(html).toContain("first &lt;b&gt;one&lt;/b&gt;");
    expect(html).not.toContain("<b>one</b>");
    expect(html.indexOf("bbbbbbb2")).toBeLessThan(html.indexOf("aaaaaaa2"));
  });

  it("gallery shows a friendly message when empty", async () => {
    const html = await (await SELF.fetch(`${BASE}/`)).text();
    expect(html).toContain("No pictures yet");
    expect(html).toContain('<section id="top3"');
  });

  it("rejects non-GET with 405", async () => {
    const res = await SELF.fetch(`${BASE}/`, { method: "POST" });
    expect(res.status).toBe(405);
  });
});
