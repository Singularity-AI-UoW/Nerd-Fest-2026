// Voting and the leaderboard. One vote per browser (the `voter` cookie); voting again moves it.

export interface TopImage {
  id: string;
  prompt: string;
  url: string;
  votes: number;
}

export interface Leaderboard {
  closed: boolean;
  top3: TopImage[];
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const COOKIE_MAX_AGE = 86400;

/**
 * True once now >= VOTE_CLOSE_AT. An unset or unparsable value counts as closed, so a
 * misconfiguration can never leave voting open past the deadline.
 * Tests move the deadline by changing VOTE_CLOSE_AT; there is no separate clock override.
 */
export function isVotingClosed(env: Env): boolean {
  const closeAt = Date.parse(env.VOTE_CLOSE_AT);
  return Number.isNaN(closeAt) || Date.now() >= closeAt;
}

export async function getLeaderboard(env: Env): Promise<Leaderboard> {
  // Joining images means votes left behind by a deleted image never appear.
  const { results } = await env.DB.prepare(
    `SELECT i.id AS id, i.prompt AS prompt, COUNT(*) AS votes
     FROM votes v JOIN images i ON i.id = v.image_id
     GROUP BY i.id
     ORDER BY votes DESC, i.created_at ASC, i.id ASC
     LIMIT 3`,
  ).all<{ id: string; prompt: string; votes: number }>();
  return {
    closed: isVotingClosed(env),
    top3: results.map((r) => ({ id: r.id, prompt: r.prompt, url: `/img/${r.id}.jpg`, votes: r.votes })),
  };
}

function existingVoterId(request: Request): string | null {
  const match = /(?:^|;\s*)voter=([^;]*)/.exec(request.headers.get("cookie") ?? "");
  return match && UUID_RE.test(match[1]) ? match[1] : null;
}

export type VoteResult =
  | { ok: true; setCookie: string | null }
  | { ok: false; status: 403 | 404; error: string };

/** Records (or moves) the caller's vote. `imageId` must already be a well-formed id. */
export async function castVote(request: Request, env: Env, imageId: string): Promise<VoteResult> {
  if (isVotingClosed(env)) return { ok: false, status: 403, error: "voting closed" };

  const image = await env.DB.prepare("SELECT 1 AS found FROM images WHERE id = ?").bind(imageId).first();
  if (!image) return { ok: false, status: 404, error: "Not found" };

  const existing = existingVoterId(request);
  const voterId = existing ?? crypto.randomUUID();
  await env.DB.prepare(
    `INSERT INTO votes (voter_id, image_id, voted_at) VALUES (?1, ?2, ?3)
     ON CONFLICT(voter_id) DO UPDATE SET image_id = excluded.image_id, voted_at = excluded.voted_at`,
  )
    .bind(voterId, imageId, new Date().toISOString())
    .run();

  return {
    ok: true,
    setCookie: existing ? null : `voter=${voterId}; HttpOnly; Secure; SameSite=Lax; Max-Age=${COOKIE_MAX_AGE}; Path=/`,
  };
}
