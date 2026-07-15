// Dev proxy to the FastAPI backend; build output goes to ../app/webdist so the
// API can serve it statically in private deployments (KBase delivery pattern).
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5180,
    proxy: { "/api": "http://localhost:8200" },
  },
  build: { outDir: "../app/webdist", emptyOutDir: true },
});
