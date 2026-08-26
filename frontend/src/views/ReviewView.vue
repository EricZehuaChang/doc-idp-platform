<template>
  <main class="review" v-if="detail">
    <!-- left: original document with bbox overlay (Insavlo dual-screen shape) -->
    <section class="doc-pane">
      <div class="doc-head">
        <!-- explicit way back: the review page used to be a one-way door
             (browser back was the only exit) — P10 -->
        <button class="back" title="返回上级列表" @click="goBack">‹ 返回</button>
        <div class="nav-group">
          <button :disabled="queuePos <= 0" title="上一份 (←)" @click="goSibling(-1)">←</button>
          <span class="pos dim" v-if="queueIds.length">{{ queuePos + 1 }} / {{ queueIds.length }}</span>
          <button :disabled="queuePos < 0 || queuePos >= queueIds.length - 1"
                  title="下一份 (→)" @click="goSibling(1)">→</button>
        </div>
        <strong class="fname-head" :title="detail.file_name">{{ detail.file_name }}</strong>
        <span class="chip" :class="`chip-${detail.status}`">{{ statusLabel }}</span>
        <button class="anno" :class="{ primary: annotate }" :disabled="!locked"
                @click="annotate = !annotate" title="拖拽框选字段位置 (B)">
          ▣ 框选{{ annotate ? "中" : "" }}
        </button>
        <button class="anno" :class="{ primary: !!sealRegions }"
                :disabled="detecting || !detectSupported" @click="toggleDetect"
                :title="detectSupported ? '检测印章/签名并在原图上叠框（再点一次清除）'
                                        : '该格式不支持印章检测（仅 PDF 与图片）'">
          ◉ 印章{{ detecting ? "…" : sealRegions ? ` ×${sealRegions.length}` : "" }}
        </button>
      </div>
      <div class="doc-body">
        <DocStage :src="api.downloadUrl(fileId)" :file-name="detail.file_name"
                  :pages="detail.pages" :active-box="activeBox" :active-page="activePage"
                  :annotate="annotate" :regions="sealRegions"
                  :boxes="stageBoxes" :active-key="activeKey"
                  @box="onBoxDrawn" @pick="pickBox" />
      </div>
    </section>

    <!-- right: extracted fields panel -->
    <section class="field-pane">
      <div class="tabs">
        <button :class="{ primary: tab === 'all' }" @click="tab = 'all'">全部字段</button>
        <button :class="{ primary: tab === 'review' }" @click="tab = 'review'">
          待复核 <span v-if="reviewCount" class="badge-r">{{ reviewCount }}</span>
        </button>
        <button v-if="detectMeta" :class="{ primary: tab === 'seal' }" @click="tab = 'seal'">
          印章/签名 <span class="badge-r">{{ detectMeta.regions.length }}</span>
        </button>
        <span class="lock-state dim">{{ lockMsg }}</span>
      </div>

      <!-- seal / signature structured results (P06): detection is not the whole
           answer — the reviewer needs type, page, position and confidence as
           data, and an explicit "text not readable" state instead of a guess -->
      <div v-if="tab === 'seal'" class="fields">
        <p class="dim seal-note">
          检测器：{{ detectMeta?.detector }} · 扫描 {{ detectMeta?.pages_scanned }}/{{
            detectMeta?.page_count }} 页<template v-if="detectMeta?.truncated">（已达页数上限，
          其余页未检测）</template>
        </p>
        <p v-if="!detectMeta?.regions.length" class="dim">未检测到印章或签名。</p>
        <div v-for="(r, i) in detectMeta?.regions ?? []" :key="`seal-${i}`" class="field"
             :class="{ active: activeKey === `r:${i}` }" @click="pickBox(`r:${i}`)">
          <div class="field-head">
            <span class="fname">{{ r.label === "seal" ? "印章" : "签名" }} #{{ i + 1 }}</span>
            <span class="dim">第 {{ r.page }} 页</span>
            <span class="conf dim">置信 {{ (r.score * 100).toFixed(0) }}%</span>
          </div>
          <p class="seal-pos dim">
            位置 x {{ r.x.toFixed(1) }}% · y {{ r.y.toFixed(1) }}% ·
            宽 {{ r.w.toFixed(1) }}% · 高 {{ r.h.toFixed(1) }}%
          </p>
          <p class="seal-text dim">
            {{ r.label === "seal" ? "印章文字" : "签字人" }}：未识别（当前通道只做版面检测，
            不做文字提取；不推测内容以免写入不实信息）</p>
        </div>
        <p v-if="detectMeta?.misses?.length" class="rulefail">
          未启用的检测器：{{ detectMeta.misses.join("、") }}
        </p>
      </div>

      <div v-else class="fields" ref="fieldsEl">
        <div v-for="(f, i) in visibleFields" :key="f.name" class="field"
             :class="{ active: activeKey === `f:${f.name}` }" :data-key="`f:${f.name}`"
             @click="focusField(f)">
          <div class="field-head">
            <span class="fname">{{ f.name }}</span>
            <span v-if="f.cell.inferred" class="badge-inferred">参考结果</span>
            <span v-if="f.cell.$confidence < 2" class="badge-r">R</span>
            <span v-if="pendingBoxes[f.name]" class="badge-box" title="已重画定位框，待保存">▣</span>
            <span class="conf dim">c{{ f.cell.$confidence }}</span>
          </div>
          <input v-model="edits[f.name]" :disabled="!locked"
                 :ref="(el) => setInput(i, el as HTMLInputElement)"
                 @focus="focusField(f)" @keydown.esc.prevent="blurInput" />
          <div v-if="f.cell.$reasoning" class="reasoning dim">{{ f.cell.$reasoning }}</div>
          <div v-if="f.cell.$rule_failures" class="rulefail">{{ f.cell.$rule_failures.join("; ") }}</div>
        </div>

        <!-- line-item tables: editable in place while locked (UX debt) -->
        <div v-for="t in tableNames" :key="t" class="field">
          <div class="field-head">
            <span class="fname">{{ t }}</span>
            <span class="dim">明细表 {{ tableEdits[t]?.length ?? 0 }} 行</span>
            <span v-if="tableDirty(t)" class="badge-box" title="明细已修改，待保存">✎</span>
            <button v-if="locked" class="mini" @click.stop="addRow(t)">＋ 行</button>
          </div>
          <div class="table-scroll" v-if="tableEdits[t]?.length">
            <table>
              <thead>
                <tr>
                  <th v-for="c in tableCols(t)" :key="c">{{ c }}</th>
                  <th v-if="locked"></th>
                </tr>
              </thead>
              <tbody>
                <!-- a table row is a selectable target too: for entity-list
                     skills (PII sweeps) it is the ONLY thing on the page, and
                     without this nothing could ever be selected (P02) -->
                <tr v-for="(row, ri) in tableEdits[t]" :key="ri"
                    :class="{ 'row-active': activeKey.startsWith(`t:${t}:${ri}:`) }"
                    :data-key="`t:${t}:${ri}`">
                  <td v-for="c in tableCols(t)" :key="c"
                      :class="{ 'cell-hit': hasHit(t, ri, c) }"
                      :title="hasHit(t, ri, c) ? '点击定位到原件中的位置' : ''"
                      @click="pickCell(t, ri, c)">
                    <input v-if="locked" v-model="row[c]" class="cell-input"
                           @focus="pickCell(t, ri, c)" />
                    <template v-else>{{ row[c] }}</template>
                  </td>
                  <td v-if="locked" class="row-ops">
                    <button class="mini danger" title="删除行" @click.stop="delRow(t, ri)">✕</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="dim empty-rows">无明细行<template v-if="locked">，点“＋ 行”添加</template></p>
        </div>
      </div>

      <div class="actions">
        <button v-if="!locked" class="primary big" @click="acquire">开始校验（锁定） L</button>
        <template v-else>
          <button class="ghost" @click="saveEdits" :disabled="!dirty">
            保存修正 S<span v-if="dirty" class="dot">●</span></button>
          <!-- non-conclusive exits: a reviewer who is not ready to judge must
               not be forced to press 通过/拒绝 just to get out (P03) -->
          <button class="ghost" @click="exitReview()" :title="dirty
            ? '保存已做的修正，退出但不改变审核状态' : '解除锁定并返回，不改变审核状态'">
            {{ dirty ? "保存并退出" : "退出校验" }}</button>
          <button v-if="dirty" class="ghost" title="丢弃本次修改并退出"
                  @click="exitReview(true)">放弃修改退出</button>
          <span class="spacer" />
          <button class="danger" @click="decide('reject')">拒绝 X</button>
          <button class="confirm big" @click="decide('confirm')">✓ 通过并下一份 C</button>
        </template>
        <span class="keys dim" title="J/K 字段 · Enter 编辑 · Esc 退出编辑 · T 页签 · B 框选 · L 锁定 · S 保存 · Q 退出 · C 通过 · X 拒绝 · ←/→ 上下份">⌨</span>
      </div>
    </section>
  </main>

  <!-- loading skeleton / not-found guidance -->
  <main v-else class="review-fallback">
    <template v-if="isLoading">
      <Skeleton :rows="3" width="40%" />
      <Skeleton :rows="8" />
    </template>
    <div v-else class="empty-state">
      <p>文件不存在或已被处理。</p>
      <router-link to="/tasks"><button class="primary">返回任务列表</button></router-link>
    </div>
  </main>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { onBeforeRouteLeave, useRoute, useRouter } from "vue-router";
import { api, fetchBlob, type DetectResult, type FieldCell, type RegionOverlay,
         type StageBox } from "../api";
import DocStage from "../components/DocStage.vue";
import Skeleton from "../components/Skeleton.vue";
import { toast } from "../toast";

const props = defineProps<{ fileId: string }>();
const route = useRoute();
const router = useRouter();
const qc = useQueryClient();

// server state via TanStack Query (§9.0 layer ①)
const { data: detail, isLoading, refetch } = useQuery({
  queryKey: computed(() => ["review", props.fileId]),
  queryFn: () => api.detail(props.fileId),
  retry: false,
});
// queue order drives Previous/Next (UX debt: 无 Previous/Next)
const { data: queueData } = useQuery({ queryKey: ["queue"], queryFn: api.queue });
const queueIds = computed(() => (queueData.value ?? []).map((q) => q.file_id));
const queuePos = computed(() => queueIds.value.indexOf(props.fileId));

const STATUS_LABELS: Record<string, string> = {
  pending_verification: "待校验", completed: "已完成", passed: "已通过",
  rejected: "已拒绝", error: "处理失败", processing: "处理中",
};
const statusLabel = computed(() =>
  STATUS_LABELS[detail.value?.status ?? ""] ?? detail.value?.status ?? "");

// tab preference survives reloads (caching design §9.0 layer ⑤)
const tab = ref<"all" | "review" | "seal">(
  (localStorage.getItem("idp_review_tab") as "all" | "review") || "all");
watch(tab, (v) => { if (v !== "seal") localStorage.setItem("idp_review_tab", v); });
const locked = ref(false);
const edits = ref<Record<string, string>>({});
const original = ref<Record<string, string>>({});
const tableEdits = ref<Record<string, Record<string, string>[]>>({});
const tableOriginal = ref<Record<string, string>>({});     // JSON snapshots
const activeKey = ref("");                                 // selection identity
const activeBox = ref<number[] | null>(null);
const activePage = ref(1);
const annotate = ref(false);
const pendingBoxes = ref<Record<string, { page: number; bbox: number[] }>>({});

// —— seal/signature detection overlay (/detect, design 2026-08-07 §3) ——
const detecting = ref(false);
const sealRegions = ref<RegionOverlay[] | null>(null);
const detectMeta = ref<DetectResult | null>(null);
// suffix gate mirrors the backend's (detect.py): other formats would only
// round-trip to a guaranteed 422, so the button greys out with a tooltip
const detectSupported = computed(() =>
  !!detail.value && /\.(pdf|png|jpe?g|bmp|webp)$/i.test(detail.value.file_name));
async function toggleDetect() {
  if (sealRegions.value) {                     // second click clears
    sealRegions.value = null;
    detectMeta.value = null;
    if (tab.value === "seal") tab.value = "all";
    return;
  }
  if (!detail.value) return;
  // capture identity at call start: detection takes seconds and the reviewer
  // can ←/→ away mid-flight — a settling stale response must never overlay
  // another document's boxes (the fileId watch reset alone can't stop that)
  const fid = props.fileId;
  const fname = detail.value.file_name;
  detecting.value = true;
  try {
    // re-fetch the original through the authenticated blob channel and run
    // the pure-compute /detect on it (no Transaction, no charge)
    const blob = await fetchBlob(api.downloadUrl(fid));
    const res = await api.detect(blob, fname);
    if (props.fileId !== fid) return;      // switched away: discard stale result
    detectMeta.value = res;
    sealRegions.value = res.regions.map((r) => {
      const p = res.pages.find((x) => x.page === r.page);
      return { ...r, pageWidth: p?.width ?? 0, pageHeight: p?.height ?? 0 };
    });
    const seals = res.regions.filter((r) => r.label === "seal").length;
    const sigs = res.regions.filter((r) => r.label === "signature").length;
    const cut = res.truncated ? `；文档共 ${res.page_count} 页，仅检测前 ${res.pages_scanned} 页` : "";
    if (res.regions.length) tab.value = "seal";
    toast.ok(res.regions.length
      ? `检测到 ${seals} 处印章、${sigs} 处签名（${res.detector}）${cut}`
      : `未检测到印章/签名${cut}`);
  } catch (e) {
    if (props.fileId === fid) toast.error(e);
  } finally {
    detecting.value = false;
  }
}
const fieldsEl = ref<HTMLDivElement>();
const inputEls = new Map<number, HTMLInputElement>();
function setInput(i: number, el: HTMLInputElement | null) {
  if (el) inputEls.set(i, el);
}

interface ScalarField { name: string; cell: FieldCell }
const scalarFields = computed<ScalarField[]>(() => {
  if (!detail.value?.result) return [];
  return Object.entries(detail.value.result)
    .filter(([, v]) => !Array.isArray(v))
    .map(([name, cell]) => ({ name, cell: cell as FieldCell }));
});
const tableNames = computed(() => {
  if (!detail.value?.result) return [];
  return Object.entries(detail.value.result)
    .filter(([, v]) => Array.isArray(v)).map(([name]) => name);
});
function tableCols(t: string): string[] {
  const rows = tableEdits.value[t] ?? [];
  const cols = new Set<string>();
  // $-prefixed keys are engine metadata ($cells locations), not columns
  for (const r of rows) Object.keys(r).filter((c) => !c.startsWith("$")).forEach((c) => cols.add(c));
  return [...cols];
}
function tableDirty(t: string): boolean {
  return JSON.stringify(tableEdits.value[t] ?? []) !== (tableOriginal.value[t] ?? "[]");
}
function addRow(t: string) {
  const cols = tableCols(t);
  const blank: Record<string, string> = {};
  for (const c of cols) blank[c] = "";
  (tableEdits.value[t] ??= []).push(blank);
}
function delRow(t: string, i: number) {
  tableEdits.value[t]?.splice(i, 1);
}

// —— cell locations: rows carry $cells[col].$hits [{page,bbox,...}] ——
interface CellHit { page?: number; bbox?: number[] }
function cellHit(t: string, ri: number, col: string): CellHit | null {
  const row = tableEdits.value[t]?.[ri] as unknown as
    Record<string, { $hits?: CellHit[] }> | undefined;
  const cells = (row as unknown as { $cells?: Record<string, { $hits?: CellHit[] }> })?.$cells;
  const hit = cells?.[col]?.$hits?.[0];
  return hit?.bbox?.length === 4 ? hit : null;
}
const hasHit = (t: string, ri: number, col: string) => !!cellHit(t, ri, col);

const reviewCount = computed(() =>
  scalarFields.value.filter((f) => f.cell.$confidence < 2).length);
const visibleFields = computed(() =>
  tab.value === "review" ? scalarFields.value.filter((f) => f.cell.$confidence < 2)
    : scalarFields.value);
const dirty = computed(() =>
  Object.keys(edits.value).some((k) => edits.value[k] !== original.value[k])
  || Object.keys(pendingBoxes.value).length > 0
  || tableNames.value.some((t) => tableDirty(t)));
const lockMsg = computed(() => {
  if (locked.value) return "已锁定（15 分钟）";
  const by = detail.value?.locked_by;
  return by && by !== api.currentUser ? `被 ${by} 锁定` : "未锁定";
});

function fieldPage(cell: FieldCell): number {
  const p = cell.$pages;
  if (typeof p === "number") return p;
  const n = parseInt(String(p), 10);
  return Number.isFinite(n) && n > 0 ? n : 1;
}

// —— every locatable thing, drawn on the document and clickable (P02) ——
const stageBoxes = computed<StageBox[]>(() => {
  const out: StageBox[] = [];
  for (const f of scalarFields.value) {
    const pending = pendingBoxes.value[f.name];
    const bbox = pending?.bbox ?? (f.cell.$bbox?.length === 4 ? f.cell.$bbox : null);
    if (bbox) out.push({ key: `f:${f.name}`, page: pending?.page ?? fieldPage(f.cell),
                         bbox, label: `${f.name}: ${f.cell.$value}` });
  }
  for (const t of tableNames.value) {
    const rows = tableEdits.value[t] ?? [];
    rows.forEach((row, ri) => {
      for (const col of Object.keys(row).filter((c) => !c.startsWith("$"))) {
        const hit = cellHit(t, ri, col);
        if (hit?.bbox && hit.page)
          out.push({ key: `t:${t}:${ri}:${col}`, page: hit.page, bbox: hit.bbox,
                     label: `${col}: ${row[col]}` });
      }
    });
  }
  return out;
});

function focusField(f: ScalarField) {
  activeKey.value = `f:${f.name}`;
  const pending = pendingBoxes.value[f.name];
  if (pending) {
    activeBox.value = pending.bbox;
    activePage.value = pending.page;
  } else {
    activeBox.value = f.cell.$bbox?.length === 4 ? f.cell.$bbox : null;
    activePage.value = fieldPage(f.cell);
  }
}
function pickCell(t: string, ri: number, col: string) {
  activeKey.value = `t:${t}:${ri}:${col}`;
  const hit = cellHit(t, ri, col);
  activeBox.value = hit?.bbox ?? null;
  if (hit?.page) activePage.value = hit.page;
}
/** Canvas -> field: the other half of the selection loop the UI never had. */
function pickBox(key: string) {
  activeKey.value = key;
  if (key.startsWith("f:")) {
    const f = scalarFields.value.find((x) => `f:${x.name}` === key);
    if (f) {
      if (tab.value === "seal") tab.value = "all";
      focusField(f);
    }
  } else if (key.startsWith("t:")) {
    const [, t, ri, col] = key.split(":");
    if (tab.value === "seal") tab.value = "all";
    pickCell(t, Number(ri), col);
  } else if (key.startsWith("r:")) {
    const r = detectMeta.value?.regions[Number(key.slice(2))];
    if (r) { activePage.value = r.page; activeBox.value = null; }
    tab.value = "seal";
  }
  scrollToSelection();
}
async function scrollToSelection() {
  await new Promise((r) => setTimeout(r, 0));
  const key = activeKey.value;
  const sel = key.startsWith("t:")
    ? `[data-key="${key.split(":").slice(0, 3).join(":")}"]` : `[data-key="${key}"]`;
  document.querySelector(sel)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function onBoxDrawn(page: number, bbox: number[]) {
  const key = activeKey.value;
  if (key.startsWith("f:")) {
    const name = key.slice(2);
    pendingBoxes.value = { ...pendingBoxes.value, [name]: { page, bbox } };
    activeBox.value = bbox;
    activePage.value = page;
    toast.ok(`已为 ${name} 重画定位框（保存后生效）`);
    return;
  }
  if (key.startsWith("t:")) {
    const [, t, ri, col] = key.split(":");
    if (!setCellHit(t, Number(ri), col, page, bbox)) {
      toast.error("该单元格没有可写入的定位结构，无法保存框选");
      return;
    }
    activeBox.value = bbox;
    activePage.value = page;
    toast.ok(`已为第 ${Number(ri) + 1} 行「${col}」重画定位框（保存后生效）`);
    return;
  }
  toast.error("先在右侧选中一个字段或明细单元格，再框选它的位置");
}

/** Write a redrawn box back into the row's $cells metadata. Rows are PATCHed
 *  whole, so this rides along with the normal table save — no new API. The
 *  percent form is what the masking consumers read, so keep both in step. */
function setCellHit(t: string, ri: number, col: string, page: number, bbox: number[]): boolean {
  const rows = tableEdits.value[t];
  const row = rows?.[ri] as unknown as
    { $cells?: Record<string, { $confidence?: number; $hits?: unknown[] }> } | undefined;
  if (!row) return false;
  const dim = detail.value?.pages.find((p) => p.page_no === page);
  const hit: Record<string, number | number[]> = { page, bbox };
  if (dim?.width && dim?.height) {
    hit.x = +(bbox[0] / dim.width * 100).toFixed(2);
    hit.y = +(bbox[1] / dim.height * 100).toFixed(2);
    hit.w = +((bbox[2] - bbox[0]) / dim.width * 100).toFixed(2);
    hit.h = +((bbox[3] - bbox[1]) / dim.height * 100).toFixed(2);
  }
  const cells = (row.$cells ??= {});
  const cell = (cells[col] ??= { $confidence: 3, $hits: [] });
  cell.$hits = [hit];
  cell.$confidence = 3;                       // human-placed box is ground truth
  tableEdits.value = { ...tableEdits.value, [t]: [...rows!] };
  return true;
}

// unsaved-draft persistence (caching design §9.0 layer ④)
const draftKey = () => `idp_draft_${props.fileId}`;
function saveDraft() {
  if (dirty.value) sessionStorage.setItem(draftKey(), JSON.stringify(edits.value));
}
function restoreDraft() {
  const raw = sessionStorage.getItem(draftKey());
  if (!raw) return;
  try {
    const draft = JSON.parse(raw) as Record<string, string>;
    for (const k of Object.keys(edits.value))
      if (k in draft && draft[k] !== edits.value[k]) edits.value[k] = draft[k];
    toast.ok("已恢复未保存的草稿");
  } catch { sessionStorage.removeItem(draftKey()); }
}

function syncFromDetail() {
  const vals: Record<string, string> = {};
  for (const f of scalarFields.value) vals[f.name] = f.cell.$value ?? "";
  edits.value = { ...vals };
  original.value = { ...vals };
  const tEdits: Record<string, Record<string, string>[]> = {};
  const tOrig: Record<string, string> = {};
  for (const [name, v] of Object.entries(detail.value?.result ?? {})) {
    if (Array.isArray(v)) {
      tEdits[name] = JSON.parse(JSON.stringify(v));
      tOrig[name] = JSON.stringify(v);
    }
  }
  tableEdits.value = tEdits;
  tableOriginal.value = tOrig;
  restoreDraft();
}
watch(detail, (v) => { if (v) syncFromDetail(); }, { immediate: true });
watch(edits, saveDraft, { deep: true });

async function acquire() {
  try { await api.lock(props.fileId); locked.value = true; }
  catch (e) { toast.error(e); }
}
async function saveEdits(): Promise<boolean> {
  const changed: { field: string; value: string; bbox?: number[]; page?: number;
                   rows?: Record<string, string>[] }[] =
    Object.keys(edits.value)
      .filter((k) => edits.value[k] !== original.value[k] || pendingBoxes.value[k])
      .map((k) => ({ field: k, value: edits.value[k],
                     bbox: pendingBoxes.value[k]?.bbox,
                     page: pendingBoxes.value[k]?.page }));
  for (const t of tableNames.value)
    if (tableDirty(t)) changed.push({ field: t, value: "", rows: tableEdits.value[t] });
  if (!changed.length) return true;
  try {
    await api.patchFields(props.fileId, changed);
    sessionStorage.removeItem(draftKey());
    pendingBoxes.value = {};
    toast.ok(`已保存 ${changed.length} 处修正`);
    await refetch();
    return true;
  } catch (e) { toast.error(e); return false; }
}

// —— exits (P03) ——
/** Where 返回 goes: the list we came from, else the task workbench. */
const backTarget = computed(() => String(route.query.back || "/tasks"));
let leavingCleanly = false;         // suppresses the unsaved-changes guard

async function releaseLock() {
  if (!locked.value) return;
  try { await api.unlock(props.fileId); } catch { /* TTL will reclaim it anyway */ }
  locked.value = false;
}

/** Leave without judging the file. `discard` drops local edits; otherwise they
 *  are persisted first. Neither path touches the review status. */
async function exitReview(discard = false) {
  if (discard) {
    if (!confirm("放弃本次未保存的修改并退出？")) return;
    sessionStorage.removeItem(draftKey());
    pendingBoxes.value = {};
    syncFromDetail();
  } else if (dirty.value && !(await saveEdits())) {
    return;                                   // save failed: stay put
  }
  await releaseLock();
  qc.invalidateQueries({ queryKey: ["queue"] });
  leavingCleanly = true;
  router.push(backTarget.value);
}
function goBack() {
  if (locked.value) { exitReview(); return; }
  router.push(backTarget.value);
}

async function decide(kind: "confirm" | "reject") {
  try {
    if (dirty.value) await saveEdits();
    await (kind === "confirm" ? api.confirm(props.fileId) : api.reject(props.fileId));
    toast.ok(kind === "confirm" ? "已通过" : "已拒绝");
    locked.value = false;
    qc.invalidateQueries({ queryKey: ["queue"] });
    // continuous review: jump straight to the next pending file
    const next = (await api.queue()).find(
      (q) => q.file_id !== props.fileId && (!q.locked_by || q.locked_by === api.currentUser));
    leavingCleanly = true;
    router.push(next ? `/review/${next.file_id}` : backTarget.value);
  } catch (e) { toast.error(e); }
}
function goSibling(delta: number) {
  const target = queueIds.value[queuePos.value + delta];
  if (target) router.push(`/review/${target}`);
}

// leaving with unsaved work (browser back, nav click) must not silently drop it
onBeforeRouteLeave(() => {
  if (leavingCleanly || !dirty.value) return true;
  return confirm("有未保存的修改，确定离开？（点“取消”可返回并保存）");
});
// closing the tab / reloading with unsaved work
function beforeUnload(ev: BeforeUnloadEvent) {
  if (dirty.value) { ev.preventDefault(); ev.returnValue = ""; }
}

// —— reviewer keyboard flow ——
function blurInput() { (document.activeElement as HTMLElement)?.blur(); }
function moveField(delta: number) {
  const list = visibleFields.value;
  if (!list.length) return;
  const cur = list.findIndex((f) => `f:${f.name}` === activeKey.value);
  const next = Math.min(Math.max(cur + delta, 0), list.length - 1);
  focusField(list[next]);
  fieldsEl.value?.querySelectorAll(".field")[next]
    ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
function onKey(ev: KeyboardEvent) {
  const typing = (ev.target as HTMLElement)?.tagName === "INPUT";
  if (typing) {
    if (ev.key === "s" && ev.ctrlKey) { ev.preventDefault(); saveEdits(); }
    return;
  }
  switch (ev.key.toLowerCase()) {
    case "j": case "arrowdown": ev.preventDefault(); moveField(1); break;
    case "k": case "arrowup": ev.preventDefault(); moveField(-1); break;
    case "arrowleft": ev.preventDefault(); goSibling(-1); break;
    case "arrowright": ev.preventDefault(); goSibling(1); break;
    case "enter": {
      ev.preventDefault();
      const i = visibleFields.value.findIndex((f) => `f:${f.name}` === activeKey.value);
      if (i >= 0) inputEls.get(i)?.focus();
      break;
    }
    case "t": tab.value = tab.value === "all" ? "review" : "all"; break;
    case "b": if (locked.value) annotate.value = !annotate.value; break;
    case "l": if (!locked.value) acquire(); break;
    case "s": if (locked.value) saveEdits(); break;
    case "q": goBack(); break;
    case "c": if (locked.value) decide("confirm"); break;
    case "x": if (locked.value) decide("reject"); break;
  }
}

// same component instance is reused across /review/:fileId — reset on switch
watch(() => props.fileId, () => {
  locked.value = false;
  activeKey.value = "";
  activeBox.value = null;
  activePage.value = 1;
  annotate.value = false;
  sealRegions.value = null;      // detection overlay belongs to one file
  detectMeta.value = null;
  if (tab.value === "seal") tab.value = "all";
  pendingBoxes.value = {};
  inputEls.clear();
});
onMounted(() => {
  window.addEventListener("keydown", onKey);
  window.addEventListener("beforeunload", beforeUnload);
});
onUnmounted(() => {
  window.removeEventListener("keydown", onKey);
  window.removeEventListener("beforeunload", beforeUnload);
});
</script>

<style scoped>
/* Fixed work area, one scroll container per column. Two things are load-bearing
   here and both were wrong before (P08): the height must subtract the REAL
   navbar height (52px — it was 46px, so the shell grew a second scrollbar), and
   every flex/grid ancestor of a scroller needs min-height:0, otherwise
   min-height:auto lets the column grow to its content and the whole page
   scrolls instead of the pane under the cursor. */
.review { display: grid; grid-template-columns: minmax(0, 1fr) minmax(380px, 460px);
  height: calc(100vh - 52px); min-height: 0; overflow: hidden; }
.doc-pane { border-right: 1px solid var(--border); display: flex; flex-direction: column;
  min-width: 0; min-height: 0; }
.doc-head { padding: 8px 14px; background: var(--bg-panel); display: flex; gap: 12px;
  align-items: center; flex-shrink: 0; }
.back { padding: 2px 10px; }
.nav-group { display: flex; align-items: center; gap: 6px; }
.nav-group button { padding: 2px 10px; }
.pos { font-size: 12px; }
.fname-head { overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  max-width: 34%; }
.anno { margin-left: auto; flex-shrink: 0; }
.anno + .anno { margin-left: 0; }
.doc-body { flex: 1 1 auto; min-height: 0; overflow: auto; padding: 12px;
  overscroll-behavior: contain; }
.field-pane { display: flex; flex-direction: column; background: var(--bg-panel);
  min-width: 0; min-height: 0; }
.tabs { display: flex; gap: 8px; align-items: center; padding: 12px;
  border-bottom: 1px solid var(--border); flex-shrink: 0; }
.lock-state { margin-left: auto; font-size: 12px; }
.fields { flex: 1 1 auto; min-height: 0; overflow: auto; padding: 12px; display: flex;
  flex-direction: column; gap: 10px; overscroll-behavior: contain; }
.field { background: var(--bg-raised); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px; cursor: pointer; }
.field.active { border-color: var(--accent); }
.field-head { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.fname { color: var(--blue); font-weight: 600; }
.conf { margin-left: auto; font-size: 12px; }
.badge-box { color: var(--blue); font-size: 12px; }
.reasoning { font-size: 12px; margin-top: 6px; }
.rulefail { color: var(--red); font-size: 12px; margin-top: 4px; }
.mini { padding: 1px 8px; font-size: 12px; margin-left: auto; }
.mini.danger { margin-left: 0; }
.table-scroll { overflow-x: auto; }
.table-scroll table { border-collapse: collapse; font-size: 12px; width: 100%; }
.table-scroll th, .table-scroll td { border: 1px solid var(--border); padding: 2px 6px;
  white-space: nowrap; }
.table-scroll tr.row-active td { background: rgba(240, 180, 41, 0.12); }
.cell-hit { cursor: pointer; }
.cell-hit:hover { background: rgba(90, 170, 255, 0.14); }
.cell-input { border: none; background: transparent; padding: 3px 4px; min-width: 70px;
  font-size: 12px; }
.cell-input:focus { background: var(--bg); }
.row-ops { text-align: center; }
.empty-rows { font-size: 12px; margin: 4px 0 0; }
.seal-note { font-size: 12px; margin: 0 0 4px; }
.seal-pos, .seal-text { margin: 2px 0 0; font-size: 12px; }
.actions { display: flex; gap: 10px; align-items: center; padding: 12px;
  border-top: 1px solid var(--border); flex-wrap: wrap; flex-shrink: 0; }
.actions .big { padding: 8px 18px; font-size: 14px; }
.spacer { flex: 1; }
.dot { color: var(--accent); margin-left: 4px; font-size: 10px; }
.keys { font-size: 14px; cursor: help; }
.dim { color: var(--text-dim); }
.review-fallback { padding: 24px; }
.empty-state { display: flex; flex-direction: column; gap: 14px; align-items: flex-start;
  color: var(--text-dim); }

@media (max-width: 1000px) {
  .review { grid-template-columns: 1fr; grid-template-rows: 45vh 1fr; height: auto;
    overflow: visible; }
  .doc-pane { border-right: none; border-bottom: 1px solid var(--border); }
}
</style>
