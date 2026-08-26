// Dev proxy to the FastAPI backend; build output goes to ../app/webdist so the
// API can serve it statically in private deployments (KBase delivery pattern).
import vue from "@vitejs/plugin-vue";
import { createReadStream, existsSync, statSync } from "node:fs";
import { cp } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, extname, join, normalize, resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";

const require = createRequire(import.meta.url);
const PDFJS_ROOT = dirname(require.resolve("pdfjs-dist/package.json"));

// pdf.js 6 loads JBIG2 / JPEG2000 / colour-management decoders as separate wasm
// modules, and CJK CMaps + the 14 standard fonts as separate data files. None of
// them are reachable unless the app serves them and passes the URLs to
// getDocument() — without the wasm bundle a JBIG2-compressed scan (the usual
// output of an office scanner) decodes to nothing and the page renders blank
// white, which is what "对照原件加载不出来" turned out to be (2026-08-26).
// Copy them beside the bundle rather than through the asset pipeline: pdf.js
// builds the file names itself (`${wasmUrl}jbig2.wasm`), so hashed asset names
// would not resolve.
const PDFJS_ASSET_DIRS = ["wasm", "cmaps", "standard_fonts", "iccs"];
const MIME: Record<string, string> = {
  ".wasm": "application/wasm", ".js": "text/javascript", ".mjs": "text/javascript",
  ".bcmap": "application/octet-stream", ".pfb": "application/octet-stream",
  ".icc": "application/octet-stream",
};

function pdfjsAssets(): Plugin {
  let outDir = "";
  return {
    name: "pdfjs-assets",
    configResolved(cfg) {
      // build.outDir is relative to the vite root, not to cwd
      outDir = resolve(cfg.root, cfg.build.outDir);
    },
    // dev server: the same /pdfjs/* paths the production build will expose
    configureServer(server) {
      server.middlewares.use("/pdfjs", (req, res, next) => {
        const rel = normalize(decodeURIComponent((req.url || "").split("?")[0]));
        const dir = rel.split("/").filter(Boolean)[0];
        if (!PDFJS_ASSET_DIRS.includes(dir)) return next();
        const file = join(PDFJS_ROOT, rel);
        if (!file.startsWith(PDFJS_ROOT) || !existsSync(file) || !statSync(file).isFile())
          return next();
        res.setHeader("Content-Type", MIME[extname(file)] ?? "application/octet-stream");
        createReadStream(file).pipe(res);
      });
    },
    async closeBundle() {
      for (const dir of PDFJS_ASSET_DIRS)
        await cp(join(PDFJS_ROOT, dir), join(outDir, "pdfjs", dir), { recursive: true });
    },
  };
}

export default defineConfig({
  plugins: [vue(), pdfjsAssets()],
  server: {
    port: 5180,
    proxy: { "/api": "http://localhost:8200" },
  },
  build: { outDir: "../app/webdist", emptyOutDir: true },
});
