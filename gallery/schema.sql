CREATE TABLE IF NOT EXISTS images (
  id TEXT PRIMARY KEY,
  prompt TEXT NOT NULL,
  mode TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS votes (
  voter_id TEXT PRIMARY KEY,
  image_id TEXT NOT NULL,
  voted_at TEXT NOT NULL
);
