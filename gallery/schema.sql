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

-- Contacts left by visitors who want the prize sent to them. Private: only the stall
-- reads them (GET /api/claims, with the token). Deleted with the picture.
CREATE TABLE IF NOT EXISTS claims (
  image_id TEXT PRIMARY KEY,
  contact TEXT NOT NULL,
  code TEXT NOT NULL,
  saved_at TEXT NOT NULL
);
