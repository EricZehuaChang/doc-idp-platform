<template>
  <main class="review" v-if="detail">
    <!-- left: original document with bbox overlay (Insavlo dual-screen shape) -->
    <section class="doc-pane">
      <div class="doc-head">
        <strong>{{ detail.file_name }}</strong>
        <span class="dim">{{ detail.status }}</span>
      </div>
      <div class="doc-body">
        <div v-if="isImage" class="img-stage">
          <img ref="imgEl" :src="api.downloadUrl(fileId)" @load="onImgLoad" />
          <svg v-if="page && imgSize" class="overlay"
               :viewBox="`0 0 ${page.width} ${page.height}`" preserveAspectRatio="none">
            <rect v-if="activeBox" :x="activeBox[0]" :y="activeBox[1]"
                  :width="activeBox[2] - activeBox[0]" :height="activeBox[3] - activeBox[1]"
                  class="hl" />
          </svg>
        </div>
        <!-- PDF: native viewer for M1; pdf.js overlay lands in M2 -->
        <iframe v-else :src="api.downloadUrl(fileId)" class="pdf-frame"></iframe>
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

      <div class="fields">
        <div v-for="f in visibleFields" :key="f.name" class="field"
             :class="{ active: f.name === activeField }" @click="focusField(f)">
          <div class="field-head">
            <span class="fname">{{ f.name }}</span>
            <span v-if="f.cell.inferred" class="badge-inferred">参考结果</span>
            <span v-if="f.cell.$confidence < 2" class="badge-r">R</span>
            <span class="conf dim">c{{ f.cell.$confidence }}</span>
          </div>
          <input v-model="edits[f.name]" :disabled="!locked" />
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
        <button v-if="!locked" class="primary" @click="acquire">开始校验（锁定）</button>
        <template v-else>
          <button @click="saveEdits" :disabled="!dirty">保存修正</button>
          <button class="confirm" @click="decide('confirm')">✓ Confirm</button>
          <button class="danger" @click="decide('reject')">Reject</button>
        </template>
        <span v-if="msg" class="dim">{{ msg }}</span>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { api, type FieldCell, type ReviewDetail } from "../api";

const props = defineProps<{ fileId: string }>();
const router = useRouter();

const detail = ref<ReviewDetail | null>(null);
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
const imgSize = ref<{ w: number; h: number } | null>(null);
const imgEl = ref<HTMLImageElement>();

const isImage = computed(() =>
  /\.(png|jpe?g|bmp|webp)$/i.test(detail.value?.file_name || ""));
const page = computed(() => detail.value?.pages?.[0] ?? null);

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
  Object.keys(edits.value).some((k) => edits.value[k] !== original.value[k]));
const lockMsg = computed(() => {
  if (locked.value) return "已锁定（15 分钟）";
  const by = detail.value?.locked_by;
  return by && by !== api.currentUser ? `被 ${by} 锁定` : "未锁定";
});

function focusField(f: ScalarField) {
  activeField.value = f.name;
  activeBox.value = f.cell.$bbox?.length === 4 ? f.cell.$bbox : null;
}
function onImgLoad() {
  if (imgEl.value) imgSize.value = { w: imgEl.value.naturalWidth, h: imgEl.value.naturalHeight };
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

async function load() {
  detail.value = await api.detail(props.fileId);
  const vals: Record<string, string> = {};
  for (const f of scalarFields.value) vals[f.name] = f.cell.$value ?? "";
  edits.value = { ...vals };
  original.value = { ...vals };
  restoreDraft();
}
watch(edits, saveDraft, { deep: true });
async function acquire() {
  try { await api.lock(props.fileId); locked.value = true; msg.value = ""; }
  catch (e) { msg.value = String(e); }
}
async function saveEdits() {
  const changed = Object.keys(edits.value)
    .filter((k) => edits.value[k] !== original.value[k])
    .map((k) => ({ field: k, value: edits.value[k] }));
  if (!changed.length) return;
  try {
    await api.patchFields(props.fileId, changed);
    sessionStorage.removeItem(draftKey());   // draft fulfilled its purpose
    msg.value = `已保存 ${changed.length} 处修正`;
    await load();
  } catch (e) { msg.value = String(e); }
}
async function decide(kind: "confirm" | "reject") {
  try {
    if (dirty.value) await saveEdits();
    await (kind === "confirm" ? api.confirm(props.fileId) : api.reject(props.fileId));
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

// same component instance is reused across /review/:fileId — reset state on switch
watch(() => props.fileId, () => {
  locked.value = false;
  msg.value = "";
  activeField.value = "";
  activeBox.value = null;
  load();
});
onMounted(load);
</script>

<style scoped>
.review { display: grid; grid-template-columns: 1fr 460px; height: calc(100vh - 45px); }
.doc-pane { border-right: 1px solid var(--border); display: flex; flex-direction: column; }
.doc-head { padding: 10px 16px; background: var(--bg-panel); display: flex; gap: 12px; }
.doc-body { flex: 1; overflow: auto; padding: 12px; }
.img-stage { position: relative; display: inline-block; }
.img-stage img { max-width: 100%; display: block; background: #fff; }
.overlay { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.hl { fill: rgba(240, 180, 41, 0.25); stroke: var(--accent); stroke-width: 4; }
.pdf-frame { width: 100%; height: 100%; border: 0; background: #fff; }
.field-pane { display: flex; flex-direction: column; background: var(--bg-panel); }
.tabs { display: flex; gap: 8px; align-items: center; padding: 12px; border-bottom: 1px solid var(--border); }
.lock-state { margin-left: auto; font-size: 12px; }
.fields { flex: 1; overflow: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; }
.field { background: var(--bg-raised); border: 1px solid var(--border); border-radius: 8px; padding: 10px; cursor: pointer; }
.field.active { border-color: var(--accent); }
.field-head { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.fname { color: var(--blue); font-weight: 600; }
.conf { margin-left: auto; font-size: 12px; }
.reasoning { font-size: 12px; margin-top: 6px; }
.rulefail { color: var(--red); font-size: 12px; margin-top: 4px; }
.table-scroll { overflow-x: auto; }
.table-scroll table { border-collapse: collapse; font-size: 12px; width: 100%; }
.table-scroll th, .table-scroll td { border: 1px solid var(--border); padding: 4px 8px; white-space: nowrap; }
.actions { display: flex; gap: 10px; align-items: center; padding: 12px; border-top: 1px solid var(--border); }
.dim { color: var(--text-dim); }
</style>
