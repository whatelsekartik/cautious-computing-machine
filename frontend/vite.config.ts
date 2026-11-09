import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dashboard is served by FastAPI in production. During development Vite
// proxies /api to the local backend so the browser only ever talks to one
// origin (required for localhost-only API access and for SSE).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
});
