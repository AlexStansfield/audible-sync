import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { serviceWorker } from "./src/pwa/serviceWorker.ts";

// The service writes <base href="{base path}/"> into index.html when it serves it, so
// the build uses relative URLs and one build works at the root or under any path
export default defineConfig({
  base: "./",
  plugins: [react(), serviceWorker()],
  server: {
    // Same origin in development too, so the session cookie works as in production
    proxy: {
      "/api": process.env.AUDIBLE_SYNC_API ?? "http://localhost:8080",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // Times are shown in the browser's zone: fixed, so the formatting tests are too
    env: { TZ: "UTC" },
    // Longer than a test's waits (asyncUtilTimeout in setup.ts), so one that fails says
    // what it was looking for rather than only that it timed out
    testTimeout: 20000,
  },
});
