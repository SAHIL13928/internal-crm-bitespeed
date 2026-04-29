import { defineConfig } from "vite";

// Output goes to `dist/`. FastAPI serves it via the StaticFiles mount
// at /app/. The base path matches that mount so asset URLs resolve.
export default defineConfig({
  base: "/app/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    target: "es2020",
  },
  server: {
    port: 5173,
    proxy: {
      // During `npm run dev`, proxy API calls to a locally-running uvicorn.
      "/api":      { target: "http://127.0.0.1:8765", changeOrigin: true },
      "/admin":    { target: "http://127.0.0.1:8765", changeOrigin: true },
      "/webhooks": { target: "http://127.0.0.1:8765", changeOrigin: true },
    },
  },
});
