import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: ".",
  base: "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // L5 Report fetches per-run artifacts via ``/runs/<run_id>/<file>``.
      // In dev mode the Vite SPA owns port 5173, so the artifact tree is
      // served by ``mock-bridge.py`` on 5174 (see web/dev/README.md);
      // production runs both off the same port through StaticHTTPServer.
      "/runs": { target: "http://localhost:5174", changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  resolve: {
    dedupe: ["react", "react-dom"],
  },
});
