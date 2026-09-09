<template>
  <div class="stage" ref="stageEl">
    <!-- zoom / rotate toolbar (WP2): same tool semantics for images and PDFs —
         左转/右转、适应页面、放大/缩小、1:1、重置; the toolbar and rail always
         stay upright because only page content is transformed -->
    <div class="tools">
      <button title="左转 90°" @click="rotateBy(-90)">⟲</button>
      <button title="右转 90°" @click="rotateBy(90)">⟳</button>
      <button title="适应页面" :class="{ on: fitMode }" @click="fitMode = true">适应</button>
      <button title="缩小" @click="zoomBy(-0.25)">－</button>
      <span class="zoom-val">{{ Math.round(zoom * 100) }}%</span>
      <button title="放大" @click="zoomBy(0.25)">＋</button>
      <button title="实际大小 (100%)" @click="zoomOne()">1:1</button>
      <button title="重置视图（转正并适应页面）" @click="resetView()">重置</button>
      <span v-if="loading" class="hint load">⏳ 正在加载原件…</span>
    </div>

    <!-- load failed: say why and offer the two ways out, instead of a blank pane.
         WP1: a missing original (original_missing) gets its own honest banner —
         retry cannot bring the blob back, so its buttons are not offered. -->
    <div v-if="loadError" class="stage-error">
      <p class="err-title">{{ loadErrorCode === "original_missing" ? "原件不可用" : "原件加载失败" }}</p>
      <p class="err-msg">{{ loadError }}</p>
      <p v-if="loadErrorCode === 'original_missing'" class="err-msg">
        该记录的原件数据缺失，重试或刷新无法恢复。
      </p>
      <div v-if="loadErrorCode !== 'original_missing'" class="err-acts">
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

    <!-- the scroll container (WP2): zoomed/rotated pages overflow INSIDE this
         pane — every edge stays reachable, the outer page never scrolls -->
    <div class="stage-viewport" ref="viewportEl">
      <!-- page-number rail (需求5): forward-facing, sticky in both axes -->
      <nav v-if="isPdf && pdfPages.length > 1" class="page-rail" aria-label="页码">
        <button v-for="p in pdfPages" :key="p.no"
                :class="{ on: p.no === activePage }"
                :title="`第 ${p.no} 页`" @click="gotoPage(p.no)">{{ p.no }}</button>
      </nav>

      <div class="scaler">
      <!-- images render directly; single page, UDR dims from pages[0] -->
      <div v-if="isImage" class="page-wrap" :class="{ annotating: annotate }"
           :data-page="1" :style="pageBoxStyle(1)"
           @pointerdown="down($event, 1)" @pointermove="move" @pointerup="up">
        <div class="page-inner" :style="innerStyle(1)">
          <img v-if="imgUrl" :src="imgUrl" @load="onImgLoad" draggable="false" />
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
      </div>

      <!-- non-renderable formats (e.g. OFD): honest fallback instead of a blank pane -->
      <div v-else-if="!isPdf" class="no-preview dim">
        该格式暂不支持原件预览 ——
        <a href="#" @click.prevent="downloadFile(src, fileName)">下载原件</a>
      </div>

      <!-- PDF: pdf.js canvas per page (replaces the M1 iframe), same overlay.
           Each page sits in an explicit placeholder sized to the rotated,
           zoomed bounding box; the content inside carries the transform, so
           the page never lands in unreachable negative coordinates. -->
      <template v-else>
        <div v-for="p in pdfPages" :key="p.no" class="page-wrap pdf"
             :class="{ annotating: annotate }" :data-page="p.no"
             :style="pageBoxStyle(p.no)"
             @pointerdown="down($event, p.no)" @pointermove="move" @pointerup="up">
          <div class="page-inner" :style="innerStyle(p.no)">
            <canvas :ref="(el) => setCanvas(p.no, el as HTMLCanvasElement)"></canvas>
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
          </div>
          <!-- page chrome lives OUTSIDE the transformed box: it stays upright -->
          <span v-if="pageErrors[p.no]" class="page-err" :title="pageErrors[p.no]">
            ⚠ 此页渲染失败</span>
          <span class="page-no dim">{{ p.no }} / {{ pdfPages.length }}</span>
        </div>
      </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
// Document stage: renders the original (image or PDF via pdf.js), overlays the
// located field boxes, and — in annotate mode — lets the reviewer drag a new
// box (emitted in UDR page-pixel coordinates, the backend's bbox space).
// WP2: every page renders into an explicit placeholder sized by
// documentGeometry.ts (rotated bounding box × zoom); zoom/rotate transform the
// page content, never the toolbar/rail, and fit-to-page is the default.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { downloadFile, fetchBlob, FetchBlobError, type RegionOverlay } from "../api";
import { loadPdfjs, PDFJS_ASSETS, type PdfjsModule } from "../pdfjs";
import { fitScale, normRotation, rotatedSize, rotatedTranslate, viewToUdr,
         type Rotation } from "../documentGeometry";

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
  /** server-converted PDF for Office originals (需求3): when set, the stage
   *  renders this instead of `src`, which stays the download target */
  previewSrc?: string;
}>();
const emit = defineEmits<{
  (e: "box", page: number, bbox: number[]): void;
  (e: "pick", key: string): void;
}>();

const stageEl = ref<HTMLDivElement>();
const viewportEl = ref<HTMLDivElement>();
const ready = ref(false);
const pdfPages = ref<{ no: number; w: number; h: number }[]>([]);
const canvases = new Map<number, HTMLCanvasElement>();
let loadingTask: ReturnType<PdfjsModule["getDocument"]> | null = null;

// —— load state (P01): a silent failure used to leave an unexplained blank pane ——
const loading = ref(false);
const loadError = ref("");
const loadErrorCode = ref("");
const blankRender = ref(false);
const pageErrors = ref<Record<number, string>>({});

// what to draw: Office originals render their converted PDF (previewSrc);
// without it, the decision follows the file extension
const renderSrc = computed(() => props.previewSrc || props.src);
const isImage = computed(() =>
  !props.previewSrc && /\.(png|jpe?g|bmp|webp)$/i.test(props.fileName));
const isPdf = computed(() => !!props.previewSrc || /\.pdf$/i.test(props.fileName));

// —— zoom / rotate (WP2: fit-to-page default, per-page placeholders) ——
const zoom = ref(1);
const rotation = ref<Rotation>(0);
const fitMode = ref(true);
const imgNatural = ref<{ w: number; h: number } | null>(null);

function rotateBy(deg: number) { rotation.value = normRotation(rotation.value + deg); }
function zoomBy(d: number) {
  fitMode.value = false;
  zoom.value = Math.min(4, Math.max(0.5, zoom.value + d));   // manual clamp only
}
function zoomOne() { fitMode.value = false; zoom.value = 1; }
function resetView() { rotation.value = 0; fitMode.value = true; }
watch(rotation, () => { if (fitMode.value) refit(); });
watch(fitMode, (v) => { if (v) refit(); });

/** Layout base of a page in CSS px at zoom 1 (canvas point size / image
 *  natural size). The overlay's UDR space stretches onto this box. */
function contentDim(no: number): { w: number; h: number } | null {
  if (isImage.value) {
    if (imgNatural.value) return imgNatural.value;
    const p = props.pages.find((x) => x.page_no === 1);
    return p && p.width > 0 ? { w: p.width, h: p.height } : null;
  }
  const v = pdfPages.value.find((x) => x.no === no);
  return v ? { w: v.w, h: v.h } : null;
}

/** Placeholder = the page's rotated bounding box × zoom. */
function pageBoxStyle(no: number): Record<string, string> {
  const dim = contentDim(no);
  if (!dim) return {};
  const { w, h } = rotatedSize(dim.w, dim.h, rotation.value);
  return { width: `${w * zoom.value}px`, height: `${h * zoom.value}px` };
}

/** Content transform: translate into the positive quadrant, then rotate and
 *  scale — origin 0 0, same matrix the pointer mapping inverts. */
function innerStyle(no: number): Record<string, string> {
  const dim = contentDim(no);
  if (!dim) return {};
  const z = zoom.value;
  const [tx, ty] = rotatedTranslate(dim.w, dim.h, rotation.value, z);
  return {
    width: `${dim.w}px`, height: `${dim.h}px`,
    transform: `translate(${tx}px, ${ty}px) rotate(${rotation.value}deg) scale(${z})`,
  };
}

function currentPageNo(): number {
  return isImage.value ? 1 : (props.activePage || 1);
}

/** Fit the current page into the real available viewport (minus rail and
 *  padding). Unbounded — a big page may fit below 50% by design. */
function refit() {
  const vp = viewportEl.value;
  const dim = contentDim(currentPageNo());
  if (!vp || !dim) return;
  const rail = vp.querySelector<HTMLElement>(".page-rail");
  const availW = vp.clientWidth - (rail ? rail.offsetWidth + 10 : 0) - 28;
  const availH = vp.clientHeight - 28;
  const f = fitScale(availW, availH, dim.w, dim.h, rotation.value);
  if (f != null && Number.isFinite(f) && f > 0) zoom.value = f;
}

// refit on pane resizes only — the viewport box never changes with content,
// so observing it cannot feed back into itself
let ro: ResizeObserver | null = null;
onMounted(() => {
  ro = new ResizeObserver(() => { if (fitMode.value) refit(); });
  if (viewportEl.value) ro.observe(viewportEl.value);
});

// UDR page dims are the bbox coordinate base; fall back to the pdf.js viewport
// when the parser produced no page geometry (e.g. markitdown)
function pageDim(no: number): { width: number; height: number } | null {
  const p = props.pages.find((x) => x.page_no === no);
  if (p && p.width > 0 && p.height > 0) return p;
  const v = pdfPages.value.find((x) => x.no === no);
  if (v) return { width: v.w, height: v.h };
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

function onImgLoad(ev: Event) {
  const img = ev.target as HTMLImageElement;
  imgNatural.value = { w: img.naturalWidth, h: img.naturalHeight };
  ready.value = true;
  if (fitMode.value) refit();
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

// Generation token: every load bumps it, so a superseded load (navigation
// away mid-fetch, part switch, retry) cannot paint its stale error/loading
// state over the newer one — its aborted pdf.js promise must die silently.
let renderSeq = 0;
// WP3: each load owns an AbortController — a superseded fetch dies outright
// instead of resolving late and fighting the newer document for shared state
let loadAbort: AbortController | null = null;

async function renderPdf() {
  const seq = ++renderSeq;
  loadAbort?.abort();
  const abort = new AbortController();
  loadAbort = abort;
  loadingTask?.destroy();
  loadingTask = null;
  pdfPages.value = [];
  imgNatural.value = null;
  pageErrors.value = {};
  loadError.value = "";
  loadErrorCode.value = "";
  blankRender.value = false;
  if (!isImage.value && !isPdf.value) return;
  loading.value = true;
  try {
    if (isImage.value) {
      const blob = await fetchBlob(renderSrc.value, { signal: abort.signal });
      if (seq !== renderSeq) return;          // superseded mid-fetch
      const url = URL.createObjectURL(blob);
      if (imgUrl.value) URL.revokeObjectURL(imgUrl.value);   // revoke only after a good fetch
      imgUrl.value = url;
      return;
    }
    const blob = await fetchBlob(renderSrc.value, { signal: abort.signal });
    if (seq !== renderSeq) return;
    const pdfjs = await loadPdfjs();
    if (seq !== renderSeq) return;
    const task = pdfjs.getDocument({ data: await blob.arrayBuffer(), ...PDFJS_ASSETS });
    if (seq !== renderSeq) { task.destroy(); return; }
    loadingTask = task;
    const doc = await task.promise;
    if (seq !== renderSeq) { task.destroy(); return; }
    pdfPages.value = await Promise.all(
      Array.from({ length: doc.numPages }, async (_, i) => {
        const page = await doc.getPage(i + 1);
        const vp = page.getViewport({ scale: 1 });
        return { no: i + 1, w: vp.width, h: vp.height };  // zoom 1 == true 1:1 (points)
      }));
    if (seq !== renderSeq) return;
    await nextTick();
    if (fitMode.value) refit();            // first paint already fits
    consumePendingPage();
    let inked = false;
    for (let i = 1; i <= doc.numPages; i++) {
      if (seq !== renderSeq) return;       // stop painting after a newer load won
      const canvas = canvases.get(i);
      if (!canvas) continue;
      // one bad page must not abort the rest of the document
      try {
        const page = await doc.getPage(i);
        const base = page.getViewport({ scale: 1 });
        const dpr = window.devicePixelRatio || 1;
        const vp = page.getViewport({ scale: dpr });
        canvas.width = vp.width;
        canvas.height = vp.height;
        canvas.style.width = `${base.width}px`;    // CSS size = points; zoom scales it
        canvas.style.height = `${base.height}px`;
        // intent "print": one-shot static raster. The default display intent
        // schedules paint slices via requestAnimationFrame, which never fires in
        // hidden/background pages (preview panes, prefetch) — render would hang.
        await page.render({ canvas, canvasContext: canvas.getContext("2d")!, viewport: vp,
                            intent: "print" }).promise;
        inked = inked || hasInk(canvas);
      } catch (e) {
        if (seq !== renderSeq) return;      // superseded: stop touching shared state
        pageErrors.value = { ...pageErrors.value, [i]: String((e as Error)?.message ?? e) };
      }
    }
    if (seq === renderSeq) blankRender.value = doc.numPages > 0 && !inked;
  } finally {
    if (seq === renderSeq) loading.value = false;
  }
}

function renderPdfSafe() {
  const seq = renderSeq + 1;
  renderPdf().catch((e) => {
    if (seq !== renderSeq) return;                 // superseded by a newer load
    if (/abort/i.test(String((e as Error)?.message ?? e))) return;  // benign nav abort
    console.error("[DocStage] original failed to load:", e);
    loading.value = false;
    loadErrorCode.value = e instanceof FetchBlobError ? e.code : "";
    loadError.value = String((e as Error)?.message ?? e).slice(0, 300);
  });
}
function retry() { renderPdfSafe(); }

function setCanvas(no: number, el: HTMLCanvasElement | null) {
  if (el) canvases.set(no, el);
  else canvases.delete(no);
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
// Works under rotation too (WP2): the placeholder rect shows the rotated
// content, so unzoom, invert the rotation, then rescale into UDR space.
const drag = ref<{ page: number; x0: number; y0: number; x1: number; y1: number } | null>(null);

function toUdr(ev: PointerEvent, page: number): [number, number] | null {
  const wrap = (ev.currentTarget as HTMLElement);
  const dim = pageDim(page);        // UDR space (overlay coordinate base)
  const box = contentDim(page);     // CSS px at zoom 1 (layout base)
  if (!dim || !box) return null;
  const r = wrap.getBoundingClientRect();
  const vx = (ev.clientX - r.left) / zoom.value;
  const vy = (ev.clientY - r.top) / zoom.value;
  const [ix, iy] = viewToUdr(vx, vy, box.w, box.h, rotation.value);
  // the overlay stretches UDR space onto the content box (preserveAspectRatio
  // "none") — rescale the inverse-rotated point the same way
  return [ix * (dim.width / box.w), iy * (dim.height / box.h)];
}
function down(ev: PointerEvent, page: number) {
  if (!props.annotate) return;      // rotation no longer blocks drawing (WP2)
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

// bring a page into view INSIDE the stage viewport only — scrollIntoView would
// drag the whole outer page along (WP2)
function scrollPageIntoView(no: number, block: "start" | "nearest") {
  const vp = viewportEl.value;
  const el = vp?.querySelector<HTMLElement>(`[data-page="${no}"]`);
  if (!vp || !el) return;
  const vr = vp.getBoundingClientRect();
  const er = el.getBoundingClientRect();
  const pad = 8;
  if (block === "start") {
    if (er.top < vr.top + pad || er.top > vr.bottom - pad) vp.scrollTop += er.top - vr.top - pad;
  } else if (er.top < vr.top) {
    vp.scrollTop += er.top - vr.top - pad;
  } else if (er.bottom > vr.bottom) {
    vp.scrollTop += er.bottom - vr.bottom + pad;
  }
}

// gotoPage before the document finishes loading: stash the target, consume it
// once pages exist (nextTick used to beat the pdf.js fetch and drop the jump)
const pendingPage = ref<number | null>(null);
function consumePendingPage() {
  if (pendingPage.value == null) return;
  const no = pendingPage.value;
  pendingPage.value = null;
  scrollPageIntoView(no, "start");
}

/** Jump the document to a page (page-number rail + part switch, 需求5). */
function gotoPage(no: number) {
  const el = viewportEl.value?.querySelector(`[data-page="${no}"]`);
  if (el) scrollPageIntoView(no, "start");
  else pendingPage.value = no;
}
defineExpose({ gotoPage });

// highlight the page of a selected field without scrolling the outer page
watch(() => [props.activeBox, props.activePage] as const, async () => {
  if (!props.activeBox || isImage.value) return;
  await nextTick();
  scrollPageIntoView(props.activePage, "nearest");
});

watch(() => [props.src, props.previewSrc] as const, () => {
  ready.value = false;
  rotation.value = 0;
  fitMode.value = true;
  zoom.value = 1;
  pendingPage.value = null;
  imgNatural.value = null;
  renderPdfSafe();
}, { immediate: true });

onBeforeUnmount(() => {
  loadAbort?.abort();                    // an in-flight fetch must not settle late
  loadingTask?.destroy();
  ro?.disconnect();
  if (imgUrl.value) URL.revokeObjectURL(imgUrl.value);
});
</script>

<style scoped>
.stage { display: flex; flex-direction: column; gap: 8px; height: 100%; min-height: 0; }
.tools { display: flex; align-items: center; gap: 6px; flex-shrink: 0; padding: 2px 0; }
.tools button { padding: 2px 10px; font-size: 13px; }
.tools button.on { border-color: var(--accent); color: var(--accent); font-weight: 700; }
.zoom-val { font-size: 12px; color: var(--text-dim); min-width: 42px; text-align: center; }
.hint { font-size: 12px; color: var(--accent); }
.hint.load { margin-left: auto; }
/* the pane's own scroll container: zoomed content overflows here, so manual
   zoom reaches every edge without ever scrolling the outer page (WP2) */
.stage-viewport { position: relative; flex: 1 1 auto; min-height: 0; overflow: auto;
  display: flex; align-items: flex-start; gap: 10px; overscroll-behavior: contain; }
/* sticky in both axes: the rail survives vertical AND horizontal scrolling */
.page-rail { position: sticky; top: 0; left: 0; z-index: 4; display: flex;
  flex-direction: column; gap: 3px; max-height: 100%; overflow-y: auto;
  padding: 4px 2px 8px 4px; flex-shrink: 0; background: var(--bg); }
.page-rail button { min-width: 30px; padding: 3px 8px; font-size: 12px;
  font-variant-numeric: tabular-nums; text-align: center; }
.page-rail button.on { background: var(--accent); color: var(--accent-text);
  border-color: var(--accent); font-weight: 700; }
.stage-error { border: 1px solid var(--red); border-radius: 8px; padding: 14px 16px;
  background: var(--bg-raised); display: flex; flex-direction: column; gap: 6px; }
.stage-error.warn { border-color: var(--accent); }
.err-title { margin: 0; font-weight: 700; }
.err-msg { margin: 0; font-size: 12px; color: var(--text-dim); word-break: break-word; }
.err-acts { display: flex; gap: 8px; margin-top: 6px; }
.page-err { position: absolute; left: 6px; top: 6px; font-size: 11px; color: #fff;
  background: var(--red); padding: 1px 7px; border-radius: 4px; z-index: 2; }
/* margin auto centers the flow in the viewport; when it overflows, the margins
   resolve to 0 and the overflow stays scrollable to every edge */
.scaler { margin: auto; flex-shrink: 0; display: flex; flex-direction: column;
  align-items: center; gap: 12px; padding: 14px; }
.page-wrap { position: relative; }
.page-wrap.annotating { cursor: crosshair; }
/* the transformed content box: sized to the page at zoom 1, mapped into the
   placeholder by translate→rotate→scale (documentGeometry.ts) */
.page-inner { position: absolute; top: 0; left: 0; transform-origin: 0 0; }
.page-inner img, .page-inner canvas { display: block; background: #fff;
  user-select: none; -webkit-user-drag: none; }
.overlay { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
/* selected-field highlight: translucent amber, thin — must never hide the text
   under it (需求6) */
.hl { fill: rgba(240, 180, 41, 0.10); stroke: var(--accent); stroke-width: 2; }
/* located fields: faint translucent boxes that never fight the document;
   each table cell draws its own box(es), not one table-wide block */
.fieldbox { fill: rgba(90, 170, 255, 0.04); stroke: rgba(90, 170, 255, 0.30);
  stroke-width: 1.2; }
.fieldbox.pick { pointer-events: auto; cursor: pointer; }
.fieldbox.pick:hover { fill: rgba(90, 170, 255, 0.12); stroke: var(--blue); stroke-width: 2; }
.fieldbox.active { fill: rgba(240, 180, 41, 0.12); stroke: var(--accent); stroke-width: 2; }
.region { stroke-width: 3; pointer-events: none; }
.region.seal { fill: rgba(229, 72, 77, 0.14); stroke: #e5484d; }
.region.signature { fill: rgba(90, 170, 255, 0.12); stroke: var(--blue); }
.draw { fill: rgba(90, 170, 255, 0.15); stroke: var(--blue); stroke-width: 2;
  stroke-dasharray: 8 5; }
.page-no { position: absolute; right: 6px; bottom: 6px; font-size: 11px;
  background: rgba(0, 0, 0, 0.55); color: #ddd; padding: 1px 7px; border-radius: 4px;
  z-index: 2; }
.no-preview { padding: 40px 20px; text-align: center; }
.no-preview a { color: var(--blue); }
.dim { color: var(--text-dim); }
</style>
