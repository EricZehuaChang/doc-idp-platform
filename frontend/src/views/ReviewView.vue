<template>
  <main class="review" v-if="detail">
    <!-- left: original document with bbox overlay (Insavlo dual-screen shape) -->
    <section class="doc-pane">
      <div class="doc-head">
        <strong>{{ detail.file_name }}</strong>
        <span class="dim">{{ detail.status }}</span>
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
        <button :class="{ primary: tab === 'all' }" @click="tab = 'all'">All Fields</button>
        <button :class="{ primary: tab === 'review' }" @click="tab = 'review'">
          Needs Review <span v-if="reviewCount" class="badge-r">{{ reviewCount }}</span>
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

        <div v-for="t in tableFields" :key="t.name" class="field">
          <div class="field-head"><span class="fname">{{ t.name }}</span>
            <span class="dim">明细表 {{ t.rows.length }} 行</span></div>
          <div class="table-scroll">
            <table v-if="t.rows.length">
              <thead><tr><th v-for="c in Object.keys(t.rows[0])" :key="c">{{ c }}</th></tr></thead>
              <tbody>
                <tr v-for="(row, i) in t.rows" :key="i">
                  <td v-for="c in Object.keys(t.rows[0])" :key="c">{{ row[c] }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="actions">
        <button v-if="!locked" class="primary" @click="acquire">开始校验（锁定）L</button>
        <template v-else>
          <button @click="saveEdits" :disabled="!dirty">保存修正 S</button>
          <button class="confirm" @click="decide('confirm')">✓ Confirm C</button>
          <button class="danger" @click="decide('reject')">Reject X</button>
        </template>
        <span v-if="msg" class="dim">{{ msg }}</span>
        <span class="keys dim" title="J/K 字段导航 · Enter 编辑 · Esc 退出 · T 切页签 · B 框选 · L 锁定 · S 保存 · C 通过 · X 拒绝">⌨ 快捷键</span>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { api, type FieldCell } from "../api";
import DocStage from "../components/DocStage.vue";

const props = defineProps<{ fileId: string }>();
const router = useRouter();
const qc = useQueryClient();

// server state via TanStack Query (§9.0 layer ①): cached per fileId, so
// back/forward between queue and review reuses data instantly
const { data: detail, refetch } = useQuery({
  queryKey: computed(() => ["review", props.fileId]),
  queryFn: () => api.detail(props.fileId),
});

// tab preference survives reloads (caching design §9.0 layer ⑤)
const tab = ref<"all" | "review">(
  (localStorage.getItem("idp_review_tab") as "all" | "review") || "all");
watch(tab, (v) => localStorage.setItem("idp_review_tab", v));
const locked = ref(false);
const msg = ref("");
const edits = ref<Record<string, string>>({});
const original = ref<Record<string, string>>({});
const activeField = ref("");
const activeBox = ref<number[] | null>(null);
const activePage = ref(1);
const annotate = ref(false);
// boxes redrawn by the reviewer, keyed by field — sent with the next save
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
const tableFields = computed(() => {
  if (!detail.value?.result) return [];
  return Object.entries(detail.value.result)
    .filter(([, v]) => Array.isArray(v))
    .map(([name, rows]) => ({ name, rows: rows as Record<string, string>[] }));
});
const reviewCount = computed(() =>
  scalarFields.value.filter((f) => f.cell.$confidence < 2).length);
const visibleFields = computed(() =>
  tab.value === "all" ? scalarFields.value
    : scalarFields.value.filter((f) => f.cell.$confidence < 2));
const dirty = computed(() =>
  Object.keys(edits.value).some((k) => edits.value[k] !== original.value[k])
  || Object.keys(pendingBoxes.value).length > 0);
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
  if (!activeField.value) { msg.value = "先选中一个字段，再框选它的位置"; return; }
  pendingBoxes.value = { ...pendingBoxes.value, [activeField.value]: { page, bbox } };
  activeBox.value = bbox;
  activePage.value = page;
  msg.value = `已为 ${activeField.value} 重画定位框（保存后生效）`;
}

// unsaved-draft persistence (caching design §9.0 layer ④): a reviewer's typed
// corrections survive accidental refresh / crash; cleared on successful save.
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
    msg.value = "已恢复未保存的草稿";
  } catch { sessionStorage.removeItem(draftKey()); }
}

function syncFromDetail() {
  const vals: Record<string, string> = {};
  for (const f of scalarFields.value) vals[f.name] = f.cell.$value ?? "";
  edits.value = { ...vals };
  original.value = { ...vals };
  restoreDraft();
}
watch(detail, (v) => { if (v) syncFromDetail(); }, { immediate: true });
watch(edits, saveDraft, { deep: true });

async function acquire() {
  try { await api.lock(props.fileId); locked.value = true; msg.value = ""; }
  catch (e) { msg.value = String(e); }
}
async function saveEdits() {
  const changed = Object.keys(edits.value)
    .filter((k) => edits.value[k] !== original.value[k] || pendingBoxes.value[k])
    .map((k) => ({ field: k, value: edits.value[k],
                   bbox: pendingBoxes.value[k]?.bbox,
                   page: pendingBoxes.value[k]?.page }));
  if (!changed.length) return;
  try {
    await api.patchFields(props.fileId, changed);
    sessionStorage.removeItem(draftKey());   // draft fulfilled its purpose
    pendingBoxes.value = {};
    msg.value = `已保存 ${changed.length} 处修正`;
    await refetch();
  } catch (e) { msg.value = String(e); }
}
async function decide(kind: "confirm" | "reject") {
  try {
    if (dirty.value) await saveEdits();
    await (kind === "confirm" ? api.confirm(props.fileId) : api.reject(props.fileId));
    qc.invalidateQueries({ queryKey: ["queue"] });
    // continuous review: jump straight to the next pending file (reviewer
    // rhythm — no round-trip through the queue page between documents)
    const next = (await api.queue()).find(
      (q) => q.file_id !== props.fileId && (!q.locked_by || q.locked_by === api.currentUser));
    if (next) {
      router.push(`/review/${next.file_id}`);   // fileId watcher re-initializes
    } else {
      router.push("/queue");
    }
  } catch (e) { msg.value = String(e); }
}

// —— reviewer keyboard flow (M2: 8h/day hands-on-keys efficiency) ——
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
    return;                                  // Esc handled on the input itself
  }
  switch (ev.key.toLowerCase()) {
    case "j": case "arrowdown": ev.preventDefault(); moveField(1); break;
    case "k": case "arrowup": ev.preventDefault(); moveField(-1); break;
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

// same component instance is reused across /review/:fileId — reset state on switch
watch(() => props.fileId, () => {
  locked.value = false;
  msg.value = "";
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
.review { display: grid; grid-template-columns: 1fr 460px; height: calc(100vh - 45px); }
.doc-pane { border-right: 1px solid var(--border); display: flex; flex-direction: column; }
.doc-head { padding: 10px 16px; background: var(--bg-panel); display: flex; gap: 12px; align-items: center; }
.anno { margin-left: auto; }
.doc-body { flex: 1; overflow: auto; padding: 12px; }
.field-pane { display: flex; flex-direction: column; background: var(--bg-panel); }
.tabs { display: flex; gap: 8px; align-items: center; padding: 12px; border-bottom: 1px solid var(--border); }
.lock-state { margin-left: auto; font-size: 12px; }
.fields { flex: 1; overflow: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; }
.field { background: var(--bg-raised); border: 1px solid var(--border); border-radius: 8px; padding: 10px; cursor: pointer; }
.field.active { border-color: var(--accent); }
.field-head { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.fname { color: var(--blue); font-weight: 600; }
.conf { margin-left: auto; font-size: 12px; }
.badge-box { color: var(--blue); font-size: 12px; }
.reasoning { font-size: 12px; margin-top: 6px; }
.rulefail { color: var(--red); font-size: 12px; margin-top: 4px; }
.table-scroll { overflow-x: auto; }
.table-scroll table { border-collapse: collapse; font-size: 12px; width: 100%; }
.table-scroll th, .table-scroll td { border: 1px solid var(--border); padding: 4px 8px; white-space: nowrap; }
.actions { display: flex; gap: 10px; align-items: center; padding: 12px; border-top: 1px solid var(--border); }
.keys { margin-left: auto; font-size: 12px; cursor: help; }
.dim { color: var(--text-dim); }
</style>
