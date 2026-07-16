<template>
  <div class="stage" ref="stageEl">
    <!-- zoom / rotate toolbar (UX debt: 图片无缩放) -->
    <div class="tools">
      <button title="缩小" @click="zoomBy(-0.25)">－</button>
      <span class="zoom-val">{{ Math.round(zoom * 100) }}%</span>
      <button title="放大" @click="zoomBy(0.25)">＋</button>
      <button title="实际大小" @click="zoom = 1">1:1</button>
      <button v-if="isImage" title="旋转 90°" @click="rotate()">⟳</button>
      <span v-if="rotation && annotate" class="hint">旋转视图下暂不支持框选</span>
    </div>

    <div class="scaler" :style="scalerStyle">
    <!-- images render directly; single page, UDR dims from pages[0] -->
    <div v-if="isImage" class="page-wrap" :class="{ annotating: annotate && !rotation }"
         @pointerdown="down($event, 1)" @pointermove="move" @pointerup="up">
      <img v-if="imgUrl" :src="imgUrl" @load="ready = true" draggable="false" />
      <svg v-if="ready && pageDim(1)" class="overlay"
           :viewBox="`0 0 ${pageDim(1)!.width} ${pageDim(1)!.height}`"
           preserveAspectRatio="none">
        <rect v-if="activeBox && activePage === 1" :x="activeBox[0]" :y="activeBox[1]"
              :width="activeBox[2] - activeBox[0]" :height="activeBox[3] - activeBox[1]"
              class="hl" />
        <rect v-if="drag && drag.page === 1" :x="dragRect[0]" :y="dragRect[1]"
              :width="dragRect[2]" :height="dragRect[3]" class="draw" />
      </svg>
    </div>

    <!-- non-renderable formats (e.g. OFD): honest fallback instead of a blank pane -->
    <div v-else-if="!isPdf" class="no-preview dim">
      该格式暂不支持原件预览 ——
      <a href="#" @click.prevent="downloadFile(src, fileName)">下载原件</a>
    </div>

    <!-- PDF: pdf.js canvas per page (replaces the M1 iframe), same overlay -->
    <template v-else>
      <div v-for="p in pdfPages" :key="p.no" class="page-wrap pdf"
           :class="{ annotating: annotate }" :data-page="p.no"
           @pointerdown="down($event, p.no)" @pointermove="move" @pointerup="up">
        <canvas :ref="(el) => setCanvas(p.no, el as HTMLCanvasElement)"></canvas>
        <svg v-if="pageDim(p.no)" class="overlay"
             :viewBox="`0 0 ${pageDim(p.no)!.width} ${pageDim(p.no)!.height}`"
             preserveAspectRatio="none">
          <rect v-if="activeBox && activePage === p.no" :x="activeBox[0]" :y="activeBox[1]"
                :width="activeBox[2] - activeBox[0]" :height="activeBox[3] - activeBox[1]"
                class="hl" />
          <rect v-if="drag && drag.page === p.no" :x="dragRect[0]" :y="dragRect[1]"
                :width="dragRect[2]" :height="dragRect[3]" class="draw" />
        </svg>
        <span class="page-no dim">{{ p.no }} / {{ pdfPages.length }}</span>
      </div>
    </template>
    </div>
  </div>
</template>

<script setup lang="ts">
// Document stage: renders the original (image or PDF via pdf.js), overlays the
// active field's bbox, and — in annotate mode — lets the reviewer drag a new
// box (emitted in UDR page-pixel coordinates, the backend's bbox space).
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { downloadFile, fetchBlob } from "../api";

// pdf.js must be imported AFTER scrubbing any leaked Node `process` global:
// Electron-embedded webviews (e.g. IDE preview panes) expose one in the page,
// which flips pdf.js into its Node path (Node streams + napi canvas factory)
// and yields silently blank canvases. Real browsers have no such global.
type PdfjsModule = typeof import("pdfjs-dist");
let pdfjsPromise: Promise<PdfjsModule> | null = null;
function loadPdfjs(): Promise<PdfjsModule> {
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

const props = defineProps<{
  src: string;
  fileName: string;
  pages: { page_no: number; width: number; height: number }[];
  activeBox: number[] | null;
  activePage: number;
  annotate: boolean;
}>();
const emit = defineEmits<{ (e: "box", page: number, bbox: number[]): void }>();

const stageEl = ref<HTMLDivElement>();
const ready = ref(false);
const pdfPages = ref<{ no: number; vpW: number; vpH: number }[]>([]);
const canvases = new Map<number, HTMLCanvasElement>();
let loadingTask: ReturnType<PdfjsModule["getDocument"]> | null = null;

const isImage = computed(() => /\.(png|jpe?g|bmp|webp)$/i.test(props.fileName));
const isPdf = computed(() => /\.pdf$/i.test(props.fileName));

// —— zoom / rotate (UX debt) ——
const zoom = ref(1);
const rotation = ref(0);            // image-only; box-select disabled while rotated
function zoomBy(d: number) { zoom.value = Math.min(4, Math.max(0.5, zoom.value + d)); }
function rotate() { rotation.value = (rotation.value + 90) % 360; }
const scalerStyle = computed(() => ({
  transform: `scale(${zoom.value}) rotate(${rotation.value}deg)`,
  transformOrigin: "top left",
}));

// UDR page dims are the bbox coordinate base; fall back to the pdf.js viewport
// when the parser produced no page geometry (e.g. markitdown)
function pageDim(no: number): { width: number; height: number } | null {
  const p = props.pages.find((x) => x.page_no === no);
  if (p && p.width > 0 && p.height > 0) return p;
  const v = pdfPages.value.find((x) => x.no === no);
  return v ? { width: v.vpW, height: v.vpH } : null;
}

// original bytes always come through an authenticated fetch — bare <img src>
// and pdf.js URL loading can't carry the Bearer token (401 under auth-on)
const imgUrl = ref("");

async function loadImage() {
  if (imgUrl.value) URL.revokeObjectURL(imgUrl.value);
  imgUrl.value = URL.createObjectURL(await fetchBlob(props.src));
}

async function renderPdf() {
  loadingTask?.destroy();
  loadingTask = null;
  pdfPages.value = [];
  if (isImage.value) { await loadImage(); return; }
  if (!isPdf.value) return;
  const blob = await fetchBlob(props.src);
  const pdfjs = await loadPdfjs();
  loadingTask = pdfjs.getDocument({ data: await blob.arrayBuffer() });
  const doc = await loadingTask.promise;
  pdfPages.value = await Promise.all(
    Array.from({ length: doc.numPages }, async (_, i) => {
      const page = await doc.getPage(i + 1);
      const vp = page.getViewport({ scale: 1 });
      return { no: i + 1, vpW: vp.width, vpH: vp.height };
    }));
  await nextTick();
  const width = (stageEl.value?.clientWidth || 800) - 24;
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const base = page.getViewport({ scale: 1 });
    const scale = width / base.width;
    const dpr = window.devicePixelRatio || 1;
    const vp = page.getViewport({ scale: scale * dpr });
    const canvas = canvases.get(i);
    if (!canvas) continue;
    canvas.width = vp.width;
    canvas.height = vp.height;
    canvas.style.width = `${vp.width / dpr}px`;
    canvas.style.height = `${vp.height / dpr}px`;
    // intent "print": one-shot static raster. The default display intent
    // schedules paint slices via requestAnimationFrame, which never fires in
    // hidden/background pages (preview panes, prefetch) — render would hang.
    await page.render({ canvas, canvasContext: canvas.getContext("2d")!, viewport: vp,
                        intent: "print" }).promise;
  }
}

function renderPdfSafe() {
  renderPdf().catch((e) => console.error("[DocStage] pdf render failed:", e));
}

function setCanvas(no: number, el: HTMLCanvasElement | null) {
  if (el) canvases.set(no, el);
}

// —— box-select (annotate mode): pointer px -> UDR page-pixel space ——
const drag = ref<{ page: number; x0: number; y0: number; x1: number; y1: number } | null>(null);

function toUdr(ev: PointerEvent, page: number): [number, number] | null {
  const wrap = (ev.currentTarget as HTMLElement);
  const dim = pageDim(page);
  if (!dim) return null;
  const r = wrap.getBoundingClientRect();
  return [((ev.clientX - r.left) / r.width) * dim.width,
          ((ev.clientY - r.top) / r.height) * dim.height];
}
function down(ev: PointerEvent, page: number) {
  if (!props.annotate || rotation.value !== 0) return;
  const pt = toUdr(ev, page);
  if (!pt) return;
  (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
  drag.value = { page, x0: pt[0], y0: pt[1], x1: pt[0], y1: pt[1] };
}
function move(ev: PointerEvent) {
  if (!drag.value) return;
  const pt = toUdr(ev, drag.value.page);
  if (pt) { drag.value.x1 = pt[0]; drag.value.y1 = pt[1]; }
}
function up() {
  if (!drag.value) return;
  const { page, x0, y0, x1, y1 } = drag.value;
  drag.value = null;
  const box = [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
  if (box[2] - box[0] > 2 && box[3] - box[1] > 2) emit("box", page, box);
}
const dragRect = computed<number[]>(() => {
  if (!drag.value) return [0, 0, 0, 0];
  const { x0, y0, x1, y1 } = drag.value;
  return [Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0)];
});

// bring the highlighted page into view when the reviewer focuses a field
watch(() => [props.activeBox, props.activePage] as const, async () => {
  if (!props.activeBox || isImage.value) return;
  await nextTick();
  stageEl.value?.querySelector(`[data-page="${props.activePage}"]`)
    ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
});

watch(() => props.src, () => {
  ready.value = false;
  zoom.value = 1;
  rotation.value = 0;
  renderPdfSafe();
}, { immediate: true });
onBeforeUnmount(() => {
  loadingTask?.destroy();
  if (imgUrl.value) URL.revokeObjectURL(imgUrl.value);
});
</script>

<style scoped>
.stage { display: flex; flex-direction: column; gap: 12px; }
.tools { display: flex; align-items: center; gap: 6px; position: sticky; top: 0;
  z-index: 5; background: var(--bg); padding: 2px 0 6px; }
.tools button { padding: 2px 10px; font-size: 13px; }
.zoom-val { font-size: 12px; color: var(--text-dim); min-width: 42px; text-align: center; }
.hint { font-size: 12px; color: var(--accent); }
.scaler { align-self: flex-start; display: flex; flex-direction: column; gap: 12px; }
.page-wrap { position: relative; display: inline-block; align-self: flex-start; }
.page-wrap.annotating { cursor: crosshair; }
.page-wrap img, .page-wrap canvas { max-width: 100%; display: block; background: #fff;
  user-select: none; -webkit-user-drag: none; }
.overlay { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.hl { fill: rgba(240, 180, 41, 0.25); stroke: var(--accent); stroke-width: 4; }
.draw { fill: rgba(90, 170, 255, 0.15); stroke: var(--blue); stroke-width: 3;
  stroke-dasharray: 8 5; }
.page-no { position: absolute; right: 6px; bottom: 6px; font-size: 11px;
  background: rgba(0, 0, 0, 0.55); color: #ddd; padding: 1px 7px; border-radius: 4px; }
.no-preview { padding: 40px 20px; text-align: center; }
.no-preview a { color: var(--blue); }
.dim { color: var(--text-dim); }
</style>
