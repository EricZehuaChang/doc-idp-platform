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
      <span v-if="loading" class="hint load">⏳ 正在加载原件…</span>
    </div>

    <!-- load failed: say why and offer the two ways out, instead of a blank pane -->
    <div v-if="loadError" class="stage-error">
      <p class="err-title">原件加载失败</p>
      <p class="err-msg">{{ loadError }}</p>
      <div class="err-acts">
        <button class="primary" @click="retry">重试</button>
        <button class="ghost" @click="downloadFile(src, fileName)">下载原件</button>
      </div>
    </div>

    <!-- rendered, but nothing was painted anywhere: an unsupported image codec
         inside the PDF (JBIG2/JPX) fails this way — pdf.js only warns, so
         without this banner the reviewer just sees white paper -->
    <div v-else-if="blankRender" class="stage-error warn">
      <p class="err-title">原件渲染为空白</p>
      <p class="err-msg">文档已下载但没有可显示的内容，可能是不支持的图像编码或空白页。</p>
      <div class="err-acts">
        <button class="primary" @click="retry">重试</button>
        <button class="ghost" @click="downloadFile(src, fileName)">下载原件</button>
      </div>
    </div>

    <div class="scaler" :style="scalerStyle">
    <!-- images render directly; single page, UDR dims from pages[0] -->
    <div v-if="isImage" class="page-wrap" :class="{ annotating: annotate && !rotation }"
         @pointerdown="down($event, 1)" @pointermove="move" @pointerup="up">
      <img v-if="imgUrl" :src="imgUrl" @load="ready = true" draggable="false" />
      <svg v-if="ready && pageDim(1)" class="overlay"
           :viewBox="`0 0 ${pageDim(1)!.width} ${pageDim(1)!.height}`"
           preserveAspectRatio="none">
        <!-- every locatable field, clickable: canvas -> field selection (P02) -->
        <rect v-for="b in boxesOn(1)" :key="b.key" v-bind="rectOf(b.bbox)"
              class="fieldbox" :class="{ active: b.key === activeKey, pick: !annotate }"
              @click="emit('pick', b.key)">
          <title>{{ b.label }}</title>
        </rect>
        <rect v-if="activeBox && activePage === 1" :x="activeBox[0]" :y="activeBox[1]"
              :width="activeBox[2] - activeBox[0]" :height="activeBox[3] - activeBox[1]"
              class="hl" />
        <template v-for="(rg, ri) in regionsOn(1)" :key="`rg1-${ri}`">
          <polygon v-if="rg.mask?.length" :points="regionPoints(rg, 1)"
                   class="region" :class="rg.label" />
          <rect v-else v-bind="regionRect(rg, 1)" class="region" :class="rg.label" />
        </template>
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
        <span v-if="pageErrors[p.no]" class="page-err" :title="pageErrors[p.no]">
          ⚠ 此页渲染失败</span>
        <svg v-if="pageDim(p.no)" class="overlay"
             :viewBox="`0 0 ${pageDim(p.no)!.width} ${pageDim(p.no)!.height}`"
             preserveAspectRatio="none">
          <rect v-for="b in boxesOn(p.no)" :key="b.key" v-bind="rectOf(b.bbox)"
                class="fieldbox" :class="{ active: b.key === activeKey, pick: !annotate }"
                @click="emit('pick', b.key)">
            <title>{{ b.label }}</title>
          </rect>
          <rect v-if="activeBox && activePage === p.no" :x="activeBox[0]" :y="activeBox[1]"
                :width="activeBox[2] - activeBox[0]" :height="activeBox[3] - activeBox[1]"
                class="hl" />
          <template v-for="(rg, ri) in regionsOn(p.no)" :key="`rg${p.no}-${ri}`">
            <polygon v-if="rg.mask?.length" :points="regionPoints(rg, p.no)"
                     class="region" :class="rg.label" />
            <rect v-else v-bind="regionRect(rg, p.no)" class="region" :class="rg.label" />
          </template>
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
// located field boxes, and — in annotate mode — lets the reviewer drag a new
// box (emitted in UDR page-pixel coordinates, the backend's bbox space).
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { downloadFile, fetchBlob, type RegionOverlay } from "../api";
import { loadPdfjs, PDFJS_ASSETS, type PdfjsModule } from "../pdfjs";

/** One locatable thing on the page: a scalar field or one table cell. */
export interface StageBox {
  key: string;          // selection identity, echoed back by @pick
  page: number;
  bbox: number[];       // UDR page-pixel space [x0, y0, x1, y1]
  label: string;
}

const props = defineProps<{
  src: string;
  fileName: string;
  pages: { page_no: number; width: number; height: number }[];
  activeBox: number[] | null;
  activePage: number;
  annotate: boolean;
  boxes?: StageBox[];                 // all locatable fields (click to select)
  activeKey?: string;
  regions?: RegionOverlay[] | null;   // seal/signature overlay (/detect)
}>();
const emit = defineEmits<{
  (e: "box", page: number, bbox: number[]): void;
  (e: "pick", key: string): void;
}>();

const stageEl = ref<HTMLDivElement>();
const ready = ref(false);
const pdfPages = ref<{ no: number; vpW: number; vpH: number }[]>([]);
const canvases = new Map<number, HTMLCanvasElement>();
let loadingTask: ReturnType<PdfjsModule["getDocument"]> | null = null;

// —— load state (P01): a silent failure used to leave an unexplained blank pane ——
const loading = ref(false);
const loadError = ref("");
const blankRender = ref(false);
const pageErrors = ref<Record<number, string>>({});

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
  if (v) return { width: v.vpW, height: v.vpH };
  // last resort: seed the viewBox from the /detect raster dims — geometry-less
  // parses (OCR chain without page info) leave UDR pages at 0x0, which would
  // otherwise suppress the overlay SVG entirely while the toast reports hits;
  // regionScale then resolves to 1 and detect boxes draw in their own space
  const r = (props.regions ?? []).find((x) => x.page === no && x.pageWidth > 0);
  return r ? { width: r.pageWidth, height: r.pageHeight } : null;
}

// —— field boxes (P02: canvas <-> field selection sync) ——
function boxesOn(no: number): StageBox[] {
  return (props.boxes ?? []).filter((b) => b.page === no && b.bbox?.length === 4);
}
function rectOf(bbox: number[]) {
  return { x: bbox[0], y: bbox[1], width: bbox[2] - bbox[0], height: bbox[3] - bbox[1] };
}

// original bytes always come through an authenticated fetch — bare <img src>
// and pdf.js URL loading can't carry the Bearer token (401 under auth-on)
const imgUrl = ref("");

async function loadImage() {
  if (imgUrl.value) URL.revokeObjectURL(imgUrl.value);
  imgUrl.value = URL.createObjectURL(await fetchBlob(props.src));
}

/** Did anything at all get painted? A document that renders 100% white is the
 *  signature of a decoder pdf.js could not load — it only logs a warning. */
function hasInk(canvas: HTMLCanvasElement): boolean {
  try {
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx || !canvas.width || !canvas.height) return false;
    const d = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    for (let i = 0; i < d.length; i += 4 * 32)       // sample: 1 px in 32
      if (d[i] < 235 || d[i + 1] < 235 || d[i + 2] < 235) return true;
    return false;
  } catch { return true; }          // tainted canvas: don't cry wolf
}

async function renderPdf() {
  loadingTask?.destroy();
  loadingTask = null;
  pdfPages.value = [];
  pageErrors.value = {};
  loadError.value = "";
  blankRender.value = false;
  if (!isImage.value && !isPdf.value) return;
  loading.value = true;
  try {
    if (isImage.value) { await loadImage(); return; }
    const blob = await fetchBlob(props.src);
    const pdfjs = await loadPdfjs();
    loadingTask = pdfjs.getDocument({ data: await blob.arrayBuffer(), ...PDFJS_ASSETS });
    const doc = await loadingTask.promise;
    pdfPages.value = await Promise.all(
      Array.from({ length: doc.numPages }, async (_, i) => {
        const page = await doc.getPage(i + 1);
        const vp = page.getViewport({ scale: 1 });
        return { no: i + 1, vpW: vp.width, vpH: vp.height };
      }));
    await nextTick();
    const width = (stageEl.value?.clientWidth || 800) - 24;
    let inked = false;
    for (let i = 1; i <= doc.numPages; i++) {
      const canvas = canvases.get(i);
      if (!canvas) continue;
      // one bad page must not abort the rest of the document
      try {
        const page = await doc.getPage(i);
        const base = page.getViewport({ scale: 1 });
        const scale = width / base.width;
        const dpr = window.devicePixelRatio || 1;
        const vp = page.getViewport({ scale: scale * dpr });
        canvas.width = vp.width;
        canvas.height = vp.height;
        canvas.style.width = `${vp.width / dpr}px`;
        canvas.style.height = `${vp.height / dpr}px`;
        // intent "print": one-shot static raster. The default display intent
        // schedules paint slices via requestAnimationFrame, which never fires in
        // hidden/background pages (preview panes, prefetch) — render would hang.
        await page.render({ canvas, canvasContext: canvas.getContext("2d")!, viewport: vp,
                            intent: "print" }).promise;
        inked = inked || hasInk(canvas);
      } catch (e) {
        pageErrors.value = { ...pageErrors.value, [i]: String((e as Error)?.message ?? e) };
      }
    }
    blankRender.value = doc.numPages > 0 && !inked;
  } finally {
    loading.value = false;
  }
}

function renderPdfSafe() {
  renderPdf().catch((e) => {
    console.error("[DocStage] original failed to load:", e);
    loading.value = false;
    loadError.value = String((e as Error)?.message ?? e).slice(0, 300);
  });
}
function retry() { renderPdfSafe(); }

function setCanvas(no: number, el: HTMLCanvasElement | null) {
  if (el) canvases.set(no, el);
}

// —— seal/signature overlay: detect raster px -> UDR viewBox px ——
// /detect boxes live in the 200-DPI raster space (RegionOverlay.pageWidth/
// Height); the SVG viewBox is UDR page space — rescale by the per-page ratio.
function regionsOn(no: number): RegionOverlay[] {
  return (props.regions ?? []).filter((r) => r.page === no);
}
function regionScale(rg: RegionOverlay, no: number): [number, number] {
  const dim = pageDim(no);
  if (!dim || !rg.pageWidth || !rg.pageHeight) return [1, 1];
  return [dim.width / rg.pageWidth, dim.height / rg.pageHeight];
}
function regionRect(rg: RegionOverlay, no: number) {
  const [sx, sy] = regionScale(rg, no);
  const [x0, y0, x1, y1] = rg.bbox_px;
  return { x: x0 * sx, y: y0 * sy, width: (x1 - x0) * sx, height: (y1 - y0) * sy };
}
function regionPoints(rg: RegionOverlay, no: number): string {
  const [sx, sy] = regionScale(rg, no);
  return (rg.mask ?? []).map(([x, y]) => `${x * sx},${y * sy}`).join(" ");
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
.hint.load { margin-left: auto; }
.stage-error { border: 1px solid var(--red); border-radius: 8px; padding: 14px 16px;
  background: var(--bg-raised); display: flex; flex-direction: column; gap: 6px; }
.stage-error.warn { border-color: var(--accent); }
.err-title { margin: 0; font-weight: 700; }
.err-msg { margin: 0; font-size: 12px; color: var(--text-dim); word-break: break-word; }
.err-acts { display: flex; gap: 8px; margin-top: 6px; }
.page-err { position: absolute; left: 6px; top: 6px; font-size: 11px; color: #fff;
  background: var(--red); padding: 1px 7px; border-radius: 4px; }
.scaler { align-self: flex-start; display: flex; flex-direction: column; gap: 12px; }
.page-wrap { position: relative; display: inline-block; align-self: flex-start; }
.page-wrap.annotating { cursor: crosshair; }
.page-wrap img, .page-wrap canvas { max-width: 100%; display: block; background: #fff;
  user-select: none; -webkit-user-drag: none; }
.overlay { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.hl { fill: rgba(240, 180, 41, 0.25); stroke: var(--accent); stroke-width: 4; }
/* located fields: faint until hovered so they never fight the document */
.fieldbox { fill: rgba(90, 170, 255, 0.05); stroke: rgba(90, 170, 255, 0.45);
  stroke-width: 2; }
.fieldbox.pick { pointer-events: auto; cursor: pointer; }
.fieldbox.pick:hover { fill: rgba(90, 170, 255, 0.2); stroke: var(--blue); stroke-width: 3; }
.fieldbox.active { fill: rgba(240, 180, 41, 0.18); stroke: var(--accent); stroke-width: 3; }
.region { stroke-width: 4; pointer-events: none; }
.region.seal { fill: rgba(229, 72, 77, 0.18); stroke: #e5484d; }
.region.signature { fill: rgba(90, 170, 255, 0.16); stroke: var(--blue); }
.draw { fill: rgba(90, 170, 255, 0.15); stroke: var(--blue); stroke-width: 3;
  stroke-dasharray: 8 5; }
.page-no { position: absolute; right: 6px; bottom: 6px; font-size: 11px;
  background: rgba(0, 0, 0, 0.55); color: #ddd; padding: 1px 7px; border-radius: 4px; }
.no-preview { padding: 40px 20px; text-align: center; }
.no-preview a { color: var(--blue); }
.dim { color: var(--text-dim); }
</style>
