import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The Python backend serves the built UI from the same origin, straight out of the
// Python package (`src/utterlane_live/web`), so `pip install` ships it. During
// `vite dev` we proxy REST and the WebSocket to it. Point LIVE_BACKEND at the mock
// server (http://127.0.0.1:5175) to develop without the Python backend.
const backend = process.env.LIVE_BACKEND ?? "http://127.0.0.1:8765";

export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    proxy: {
      "/api": { target: backend, changeOrigin: false },
      "/ws": { target: backend, ws: true, changeOrigin: false },
    },
  },
  build: {
    outDir: "../src/utterlane_live/web",
    emptyOutDir: true,
    target: "es2022",
    sourcemap: false,
  },
});
