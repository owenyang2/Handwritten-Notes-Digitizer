import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Vite + Vitest configuration for the Handwriting Notes Digitizer frontend.
// Tests run with `vitest --run` (see package.json "test" script) so the
// suite executes once and exits, which is required for CI / task runners.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": new URL("./src", import.meta.url).pathname } },
  // Dev server proxy: forward /api/* to the FastAPI backend on :8000 and strip
  // the /api prefix so backend routes stay unprefixed (mirrors nginx.conf in
  // production). Lets `npm run dev` talk to a locally-running backend.
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // Applies the shared fast-check config (>=100 iterations) to every test.
    setupFiles: ["./vitest.setup.ts"],
    // Property-based tests (fast-check) can run many iterations; give them room.
    testTimeout: 30_000,
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
    },
  },
});
