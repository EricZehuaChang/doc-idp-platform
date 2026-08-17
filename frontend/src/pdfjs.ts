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
  const task = pdfjs.getDocument({ data: await file.arrayBuffer() });
  try {
    const doc = await task.promise;
    return doc.numPages;
  } catch {
    return 0;                       // encrypted/corrupt: fall back to 1 page
  } finally {
    task.destroy();
  }
}
