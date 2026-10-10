import { env } from "cloudflare:test";
import schema from "../schema.sql?raw";

// Apply schema.sql to the local D1 before each test file runs.
const statements = schema
  .split(";")
  .map((s) => s.trim())
  .filter(Boolean)
  .map((s) => env.DB.prepare(s));
await env.DB.batch(statements);
