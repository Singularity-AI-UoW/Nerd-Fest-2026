declare namespace Cloudflare {
  interface Env {
    DB: D1Database;
    IMAGES: R2Bucket;
    VOTE_CLOSE_AT: string;
    UPLOAD_TOKEN?: string;
  }
}
interface Env extends Cloudflare.Env {}
