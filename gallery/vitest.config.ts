import { cloudflareTest } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: { configPath: "./wrangler.toml" },
      miniflare: {
        // Test-only token. The real UPLOAD_TOKEN is a Wrangler secret.
        bindings: { UPLOAD_TOKEN: "test-upload-token" },
      },
    }),
  ],
  test: {
    setupFiles: ["./test/setup.ts"],
  },
});
