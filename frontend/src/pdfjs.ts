// Shared pdf.js loader. Two surfaces need it: the review stage (renders pages)
// and the upload page (reads the real page count for the cost estimate), and
// both must go through the same guarded import.
//
// pdf.js must be imported AFTER scrubbing any leaked Node `process` global:
// Electron-embedded webviews (e.g. IDE preview panes) expose one in the page,
// which flips pdf.js into its Node path (Node streams + napi canvas factory)
// and yields silently blank canvases. Real browsers have no such global.
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

export type PdfjsModule = typeof import("pdfjs-dist");

let pdfjsPromise: Promise<PdfjsModule> | null = null;

// Side-car assets that pdf.js 6 fetches at runtime, served by the
// pdfjs-assets plugin in vite.config.ts. `wasmUrl` is the important one:
// JBIG2 / JPEG2000 / colour decoders live in wasm now, and without it a
// JBIG2-compressed scan renders as a blank white page with only a console
// warning (2026-08-26 production bug "对照原件加载不出来"). CMaps and the
// standard fonts follow the same shape — a missing CMap silently drops CJK
// glyphs. Every value must end with a slash (pdf.js validates this).
const base = new URL("pdfjs/", document.baseURI).href;
export const PDFJS_ASSETS = {
  wasmUrl: `${base}wasm/`,
  cMapUrl: `${base}cmaps/`,
  cMapPacked: true,
  standardFontDataUrl: `${base}standard_fonts/`,
  iccUrl: `${base}iccs/`,
} as const;

export function loadPdfjs(): Promise<PdfjsModule> {
  if (!pdfjsPromise) {
    const g = globalThis as Record<string, unknown>;
    if (String(g.process) === "[object process]") {
      try { delete g.process; } catch { /* frozen: pdf.js will warn but run */ }
    }
    pdfjsPromise = import("pdfjs-dist").then((m) => {
      m.GlobalWorkerOptions.workerSrc = workerUrl;
      return m;
    });
  }
  return pdfjsPromise;
}

/** Real page count of a PDF blob; 0 when the file is not a readable PDF. */
export async function pdfPageCount(file: Blob): Promise<number> {
  const pdfjs = await loadPdfjs();
  const task = pdfjs.getDocument({ data: await file.arrayBuffer(), ...PDFJS_ASSETS });
  try {
    const doc = await task.promise;
    return doc.numPages;
  } catch {
    return 0;                       // encrypted/corrupt: fall back to 1 page
  } finally {
    task.destroy();
  }
}
