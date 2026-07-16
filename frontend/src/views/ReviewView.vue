<template>
  <main class="review" v-if="detail">
    <!-- left: original document with bbox overlay (Insavlo dual-screen shape) -->
    <section class="doc-pane">
      <div class="doc-head">
        <div class="nav-group">
          <button :disabled="queuePos <= 0" title="上一份 (←)" @click="goSibling(-1)">←</button>
          <span class="pos dim" v-if="queueIds.length">{{ queuePos + 1 }} / {{ queueIds.length }}</span>
          <button :disabled="queuePos < 0 || queuePos >= queueIds.length - 1"
                  title="下一份 (→)" @click="goSibling(1)">→</button>
        </div>
        <strong class="fname-head" :title="detail.file_name">{{ detail.file_name }}</strong>
        <span class="dim">{{ statusLabel }}</span>
        <button class="anno" :class="{ primary: annotate }" :disabled="!locked"
                @click="annotate = !annotate" title="拖拽框选字段位置 (B)">
          ▣ 框选{{ annotate ? "中" : "" }}
        </button>
      </div>
      <div class="doc-body">
        <DocStage :src="api.downloadUrl(fileId)" :file-name="detail.file_name"
                  :pages="detail.pages" :active-box="activeBox" :active-page="activePage"
                  :annotate="annotate" @box="onBoxDrawn" />
      </div>
    </section>

    <!-- right: extracted fields panel -->
    <section class="field-pane">
      <div class="tabs">
        <button :class="{ primary: tab === 'all' }" @click="tab = 'all'">全部字段</button>
        <button :class="{ primary: tab === 'review' }" @click="tab = 'review'">
          待复核 <span v-if="reviewCount" class="badge-r">{{ reviewCount }}</span>
        </button>
        <span class="lock-state dim">{{ lockMsg }}</span>
      </div>

      <div class="fields" ref="fieldsEl">
        <div v-for="(f, i) in visibleFields" :key="f.name" class="field"
             :class="{ active: f.name === activeField }" @click="focusField(f)">
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
                <tr v-for="(row, ri) in tableEdits[t]" :key="ri">
                  <td v-for="c in tableCols(t)" :key="c">
                    <input v-if="locked" v-model="row[c]" class="cell-input" />
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
        <button v-if="!locked" class="primary" @click="acquire">开始校验（锁定）L</button>
        <template v-else>
          <button @click="saveEdits" :disabled="!dirty">保存修正 S</button>
          <button class="confirm" @click="decide('confirm')">✓ 通过 C</button>
          <button class="danger" @click="decide('reject')">拒绝 X</button>
        </template>
        <span class="keys dim" title="J/K 字段 · Enter 编辑 · Esc 退出 · T 页签 · B 框选 · L 锁定 · S 保存 · C 通过 · X 拒绝 · ←/→ 上下份">⌨ 快捷键</span>
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
      <router-link to="/queue"><button class="primary">返回待审队列</button></router-link>
    </div>
  </main>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { api, type FieldCell } from "../api";
import DocStage from "../components/DocStage.vue";
import Skeleton from "../components/Skeleton.vue";
import { toast } from "../toast";

const props = defineProps<{ fileId: string }>();
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
const tab = ref<"all" | "review">(
  (localStorage.getItem("idp_review_tab") as "all" | "review") || "all");
watch(tab, (v) => localStorage.setItem("idp_review_tab", v));
const locked = ref(false);
const edits = ref<Record<string, string>>({});
const original = ref<Record<string, string>>({});
const tableEdits = ref<Record<string, Record<string, string>[]>>({});
const tableOriginal = ref<Record<string, string>>({});     // JSON snapshots
const activeField = ref("");
const activeBox = ref<number[] | null>(null);
const activePage = ref(1);
const annotate = ref(false);
const pendingBoxes = ref<Record<string, { page: number; bbox: number[] }>>({});
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
  for (const r of rows) Object.keys(r).forEach((c) => cols.add(c));
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

const reviewCount = computed(() =>
  scalarFields.value.filter((f) => f.cell.$confidence < 2).length);
const visibleFields = computed(() =>
  tab.value === "all" ? scalarFields.value
    : scalarFields.value.filter((f) => f.cell.$confidence < 2));
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
function focusField(f: ScalarField) {
  activeField.value = f.name;
  const pending = pendingBoxes.value[f.name];
  if (pending) {
    activeBox.value = pending.bbox;
    activePage.value = pending.page;
  } else {
    activeBox.value = f.cell.$bbox?.length === 4 ? f.cell.$bbox : null;
    activePage.value = fieldPage(f.cell);
  }
}
function onBoxDrawn(page: number, bbox: number[]) {
  if (!activeField.value) { toast.error("先选中一个字段，再框选它的位置"); return; }
  pendingBoxes.value = { ...pendingBoxes.value, [activeField.value]: { page, bbox } };
  activeBox.value = bbox;
  activePage.value = page;
  toast.ok(`已为 ${activeField.value} 重画定位框（保存后生效）`);
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
async function saveEdits() {
  const changed: { field: string; value: string; bbox?: number[]; page?: number;
                   rows?: Record<string, string>[] }[] =
    Object.keys(edits.value)
      .filter((k) => edits.value[k] !== original.value[k] || pendingBoxes.value[k])
      .map((k) => ({ field: k, value: edits.value[k],
                     bbox: pendingBoxes.value[k]?.bbox,
                     page: pendingBoxes.value[k]?.page }));
  for (const t of tableNames.value)
    if (tableDirty(t)) changed.push({ field: t, value: "", rows: tableEdits.value[t] });
  if (!changed.length) return;
  try {
    await api.patchFields(props.fileId, changed);
    sessionStorage.removeItem(draftKey());
    pendingBoxes.value = {};
    toast.ok(`已保存 ${changed.length} 处修正`);
    await refetch();
  } catch (e) { toast.error(e); }
}
async function decide(kind: "confirm" | "reject") {
  try {
    if (dirty.value) await saveEdits();
    await (kind === "confirm" ? api.confirm(props.fileId) : api.reject(props.fileId));
    toast.ok(kind === "confirm" ? "已通过" : "已拒绝");
    qc.invalidateQueries({ queryKey: ["queue"] });
    // continuous review: jump straight to the next pending file
    const next = (await api.queue()).find(
      (q) => q.file_id !== props.fileId && (!q.locked_by || q.locked_by === api.currentUser));
    router.push(next ? `/review/${next.file_id}` : "/queue");
  } catch (e) { toast.error(e); }
}
function goSibling(delta: number) {
  const target = queueIds.value[queuePos.value + delta];
  if (target) router.push(`/review/${target}`);
}

// —— reviewer keyboard flow ——
function blurInput() { (document.activeElement as HTMLElement)?.blur(); }
function moveField(delta: number) {
  const list = visibleFields.value;
  if (!list.length) return;
  const cur = list.findIndex((f) => f.name === activeField.value);
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
      const i = visibleFields.value.findIndex((f) => f.name === activeField.value);
      if (i >= 0) inputEls.get(i)?.focus();
      break;
    }
    case "t": tab.value = tab.value === "all" ? "review" : "all"; break;
    case "b": if (locked.value) annotate.value = !annotate.value; break;
    case "l": if (!locked.value) acquire(); break;
    case "s": if (locked.value) saveEdits(); break;
    case "c": if (locked.value) decide("confirm"); break;
    case "x": if (locked.value) decide("reject"); break;
  }
}

// same component instance is reused across /review/:fileId — reset on switch
watch(() => props.fileId, () => {
  locked.value = false;
  activeField.value = "";
  activeBox.value = null;
  activePage.value = 1;
  annotate.value = false;
  pendingBoxes.value = {};
  inputEls.clear();
});
onMounted(() => window.addEventListener("keydown", onKey));
onUnmounted(() => window.removeEventListener("keydown", onKey));
</script>

<style scoped>
.review { display: grid; grid-template-columns: minmax(0, 1fr) minmax(380px, 460px);
  height: calc(100vh - 46px); }
.doc-pane { border-right: 1px solid var(--border); display: flex; flex-direction: column;
  min-width: 0; }
.doc-head { padding: 8px 14px; background: var(--bg-panel); display: flex; gap: 12px;
  align-items: center; }
.nav-group { display: flex; align-items: center; gap: 6px; }
.nav-group button { padding: 2px 10px; }
.pos { font-size: 12px; }
.fname-head { overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  max-width: 40%; }
.anno { margin-left: auto; flex-shrink: 0; }
.doc-body { flex: 1; overflow: auto; padding: 12px; }
.field-pane { display: flex; flex-direction: column; background: var(--bg-panel);
  min-width: 0; }
.tabs { display: flex; gap: 8px; align-items: center; padding: 12px;
  border-bottom: 1px solid var(--border); }
.lock-state { margin-left: auto; font-size: 12px; }
.fields { flex: 1; overflow: auto; padding: 12px; display: flex; flex-direction: column;
  gap: 10px; }
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
.cell-input { border: none; background: transparent; padding: 3px 4px; min-width: 70px;
  font-size: 12px; }
.cell-input:focus { background: var(--bg); }
.row-ops { text-align: center; }
.empty-rows { font-size: 12px; margin: 4px 0 0; }
.actions { display: flex; gap: 10px; align-items: center; padding: 12px;
  border-top: 1px solid var(--border); }
.keys { margin-left: auto; font-size: 12px; cursor: help; }
.dim { color: var(--text-dim); }
.review-fallback { padding: 24px; }
.empty-state { display: flex; flex-direction: column; gap: 14px; align-items: flex-start;
  color: var(--text-dim); }

@media (max-width: 1000px) {
  .review { grid-template-columns: 1fr; grid-template-rows: 45vh 1fr; height: auto; }
  .doc-pane { border-right: none; border-bottom: 1px solid var(--border); }
}
</style>
