<template>
  <main class="page">
    <PageHeader :title="$t('upload.upload_document')"
                :desc="$t('upload.select_a_skill_and_drop_files_the_platform_parses')">
      <router-link to="/tasks"><button class="ghost">{{ $t('upload.go_to_task_list') }}</button></router-link>
    </PageHeader>

    <!-- no publishable skill: uploading would 400 on every file, so send the
         user to the place that fixes it instead of letting them try -->
    <section v-if="skillsLoaded && usableSkills.length === 0" class="card-panel block">
      <EmptyState :title="t('upload.no-skills')" glyph="🧩">
        <I18nT keypath="upload.published-skill-required" tag="span" scope="global"><template #published><strong>{{ t('upload.published') }}</strong></template></I18nT>
        <template #action>
          <router-link to="/skills"><button class="primary">{{ t('upload.go-to-skills') }}</button></router-link>
        </template>
      </EmptyState>
    </section>

    <template v-else>
      <!-- 1. skill -->
      <section class="card-panel block">
        <h3><span class="step-n">1</span>{{ $t('upload.select_skill') }}</h3>
        <div class="skill-row">
          <select v-model="skillCode" :disabled="uploading">
            <option value="" disabled>{{ $t('upload.please_select_a_skill') }}</option>
            <option v-for="s in usableSkills" :key="s.skill_code" :value="s.skill_code">
              {{ s.name }}（{{ s.skill_code }} · v{{ s.published_version }}）
            </option>
          </select>
          <div v-if="selectedSkill" class="skill-meta">
            <span class="tag">{{ selectedSkill.kind }}</span>
            <span class="tag ok">v{{ selectedSkill.published_version }} {{ $t('upload.published') }}</span>
          </div>
        </div>
      </section>

      <!-- 2. intake -->
      <section class="card-panel block">
        <h3><span class="step-n">2</span>{{ $t('upload.upload_files') }}</h3>
        <DropZone :accept="accept" :disabled="uploading" @add="addFiles">
          <template #limits>
            {{ $t('upload.single_file') }} {{ limits.max_size_mb }}{{ $t('upload.mb_over') }} {{ limits.max_files }} {{ $t('upload.files_or') }} {{ limits.max_batch_mb }}{{ $t('upload.mb_will_be_submitted_in_batches_automatically') }}
          </template>
        </DropZone>
      </section>

      <!-- 3. pending list -->
      <section v-if="items.length" class="card-panel list-block">
        <h3 class="pad">
          <span class="step-n">3</span>{{ $t('upload.pending_upload') }}{{ items.length }} {{ $t('upload.files_total') }} {{ human(totalBytes) }}）
        </h3>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr><th>{{ $t('upload.file_name') }}</th><th>{{ $t('upload.type') }}</th><th>{{ $t('upload.size') }}</th><th>{{ $t('upload.pages') }}</th><th>{{ $t('upload.validation') }}</th><th class="th-act"></th></tr>
            </thead>
            <tbody>
              <tr v-for="it in items" :key="it.id" :class="{ rowbad: !!it.problem }">
                <td class="fname" :class="{ bad: !!it.problem }" :title="it.file.name">
                  {{ it.file.name }}</td>
                <td><span class="ftype">{{ it.ext.replace(".", "").toUpperCase() }}</span></td>
                <td class="dim">{{ human(it.file.size) }}</td>
                <td>{{ it.problem ? "—" : (it.pages ?? "…") }}</td>
                <td>
                  <span v-if="it.problem" class="reason">{{ translateMessage(it.problem) }}</span>
                  <span v-else class="dim">{{ $t('upload.ready_to_submit') }}</span>
                </td>
                <td class="row-act">
                  <button class="x" :disabled="uploading" :title="$t('upload.remove')"
                          @click="remove(it.id)">✕</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- submit bar -->
        <div class="submit-bar">
          <div class="est">
            <I18nT keypath="upload.estimated-pages" tag="span" scope="global"><template #count><b>{{ estPages }}</b></template></I18nT>
            <I18nT v-if="batchCountPreview > 1" keypath="upload.batch-count" tag="span" scope="global"><template #count><b>{{ batchCountPreview }}</b></template></I18nT>
            <span class="sub">
              {{ t('upload.ready-count', { count: okItems.length }) }}
              <template v-if="badItems.length">{{ t('upload.skip-count', { count: badItems.length }) }}</template>
              <template v-if="credits !== null">{{ t('upload.remaining-credits', { count: credits }) }}</template>
            </span>
          </div>
          <div class="right">
            <button class="ghost" :disabled="uploading" @click="clearAll">{{ $t('upload.clear') }}</button>
            <button class="primary" :disabled="!canSubmit" @click="submit">
              {{ uploading ? $t('upload.submitting') : $t('upload.submit_for_processing') }}</button>
          </div>
        </div>
      </section>

      <!-- upload progress -->
      <section v-if="uploading" class="card-panel block">
        <div class="prog-head">
          <span>{{ $t('upload.uploading') }}<template v-if="batchTotal > 1">{{ $t('upload.batch') }} {{ batchIndex }}/{{ batchTotal }} {{ $t('upload.message') }}</template>…</span>
          <span class="dim">{{ human(sentBytes) }} / {{ human(uploadBytes) }}</span>
          <span class="pct">{{ pct }}%</span>
        </div>
        <div class="bar"><i :style="{ width: pct + '%' }" /></div>
      </section>

      <!-- results -->
      <section v-if="rows.length || txnIds.length" class="card-panel list-block">
        <div class="pad">
          <I18nT class="notice" keypath="upload.accepted-notice" tag="div" scope="global"><template #count><b>{{ rows.length }}</b></template></I18nT>
          <div class="txn">
            <span class="dim">{{ $t('upload.transaction_no') }}</span>
            <code v-for="t in txnIds" :key="t">{{ t }}</code>
            <span class="dim">{{ $t('upload.skills') }}</span><span>{{ skillCode }}</span>
            <span v-if="polling" class="dim">{{ $t('upload.refreshes_every_2_seconds') }}</span>
          </div>
        </div>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr><th>{{ $t('upload.file_name') }}</th><th>{{ $t('upload.pages') }}</th><th>{{ $t('upload.status') }}</th><th>{{ $t('upload.description') }}</th><th class="th-act"></th></tr>
            </thead>
            <tbody>
              <tr v-for="r in rows" :key="r.file_id">
                <td class="fname" :title="r.file_name">
                  {{ r.file_name }}
                  <span v-if="r.child_count" class="split-badge">{{ $t('upload.split_into') }} {{ r.child_count }} {{ $t('upload.documents') }}</span>
                </td>
                <td>{{ r.page_count || "—" }}</td>
                <td><span class="chip" :class="`chip-${r.status}`">{{ stLabel(r.status) }}</span></td>
                <td>
                  <span v-if="r.msg" class="err-msg" :title="r.msg">⚠ {{ r.msg }}</span>
                  <details v-else-if="r.children.length" class="child-info">
                    <summary>{{ $t('upload.processed_in_the_background_as') }} {{ r.children.length }} {{ $t('upload.documents_f8daf7') }}</summary>
                    <div v-for="(c, i) in r.children" :key="c.file_id" class="child-line">
                      {{ $t('upload.document') }} {{ i + 1 }} · {{ c.page_count }} {{ $t('upload.pages_c53d75') }} {{ stLabel(c.status) }}
                    </div>
                  </details>
                  <span v-else class="dim">—</span>
                </td>
                <td class="row-act">
                  <router-link v-if="r.status === 'pending_verification'"
                               :to="`/review/${r.file_id}`">
                    <button class="primary slim">{{ $t('upload.go_to_validation') }}</button></router-link>
                  <router-link v-else-if="TERMINAL.has(r.status) && r.status !== 'split'"
                               :to="`/review/${r.file_id}`">
                    <button class="ghost slim">{{ $t('upload.view') }}</button></router-link>
                  <button v-else class="ghost slim" disabled>—</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="submit-bar">
          <span class="dim note">{{ $t('upload.failed_files_are_not_charged') }}</span>
          <div class="right">
            <router-link to="/tasks"><button class="ghost">{{ $t('upload.go_to_task_list_939259') }}</button></router-link>
            <button class="confirm" @click="resetForNextBatch">{{ $t('upload.upload_another_batch') }}</button>
          </div>
        </div>
      </section>
    </template>
  </main>
</template>

<script setup lang="ts">
import { I18nT, useI18n } from "vue-i18n";
const { t } = useI18n();
import { translateMessage, type MessageDescriptor } from "../i18n";

// Upload page (2026-08-18): the browser-side client of POST /api/v1/process.
// Product rule from the design review — a bad file never blocks the batch:
// unsupported/oversized items are marked and skipped, everything else goes
// through, and a selection past the per-request ceiling is split into several
// transactions instead of being refused.
import { useQuery } from "@tanstack/vue-query";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api, type SkillInfo, type UploadLimits } from "../api";
import DropZone from "../components/DropZone.vue";
import EmptyState from "../components/EmptyState.vue";
import PageHeader from "../components/PageHeader.vue";
import { STATUS_LABELS } from "../labels";
import { pdfPageCount } from "../pdfjs";
import { toast } from "../toast";

interface Pending {
  id: number; file: File; ext: string;
  pages: number | null;          // null while the PDF page count is being read
  problem: MessageDescriptor | null;        // non-null => skipped at submit time
}
interface ResultRow {
  file_id: string; file_name: string; status: string;
  page_count: number; msg: string; child_count: number;
  children: { file_id: string; file_name: string; status: string; page_count: number }[];
}

// statuses that will never change again -> polling can stop
const TERMINAL = new Set(["completed", "passed", "rejected", "error", "split",
                          "pending_verification"]);
const POLL_MS = 2000;
const POLL_LIMIT = 600;            // 20 min ceiling: never poll a dead page forever

const route = useRoute();

// —— server-declared capability + limits (never hardcoded in the UI) ——
const FALLBACK: UploadLimits = {
  extensions: [".pdf", ".docx", ".xlsx", ".pptx", ".ofd", ".png", ".jpg", ".jpeg"],
  max_files: 10, max_size_mb: 50, max_batch_mb: 80,
};
const { data: limitsData } = useQuery({
  queryKey: ["formats"], queryFn: api.formats, staleTime: 5 * 60_000 });
const limits = computed<UploadLimits>(() => limitsData.value ?? FALLBACK);
const accept = computed(() => limits.value.extensions.join(","));

const { data: skills, isFetched: skillsLoaded } = useQuery({
  queryKey: ["skills"], queryFn: api.skills });
const usableSkills = computed<SkillInfo[]>(
  // WP4: only ACTIVE skills with a published version can take submissions —
  // disabled/deleted ones must not even appear in the picker
  () => (skills.value ?? [])
    .filter((s) => s.state === "active" && s.published_version != null));
const skillCode = ref(String(route.query.skill || ""));
const selectedSkill = computed(
  () => usableSkills.value.find((s) => s.skill_code === skillCode.value) ?? null);
// single skill (or a deep-linked one) needs no choosing
watch(usableSkills, (list) => {
  if (!skillCode.value && list.length === 1) skillCode.value = list[0].skill_code;
  if (skillCode.value && !list.some((s) => s.skill_code === skillCode.value)) {
    skillCode.value = "";
  }
});

// credits are shown for context only; the real gate is the server's 402
const { data: home } = useQuery({ queryKey: ["home-stats"], queryFn: () => api.homeStats() });
const credits = computed(() => home.value?.remaining_credits ?? null);

// —— selection ——
const items = ref<Pending[]>([]);
let seq = 0;

function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i < 0 ? "" : name.slice(i).toLowerCase();
}
function problemOf(file: File, ext: string): MessageDescriptor | null {
  if (!limits.value.extensions.includes(ext)) {
    return ext === ".doc" || ext === ".xls" || ext === ".ppt"
      ? { key: 'upload.legacy-office' }
      : { key: 'upload.unsupported-format' };
  }
  if (file.size === 0) return { key: 'upload.empty-file' };
  if (file.size > limits.value.max_size_mb * 1024 * 1024) {
    return { key: 'upload.file-too-large', params: { limit: limits.value.max_size_mb } };
  }
  return null;
}

function addFiles(files: File[]) {
  for (const file of files) {
    // same name+size twice in one batch is nearly always a mis-drop
    if (items.value.some((i) => i.file.name === file.name && i.file.size === file.size)) {
      toast.error(t('upload.duplicate-file', { name: file.name }));
      continue;
    }
    const ext = extOf(file.name);
    const it: Pending = {
      id: ++seq, file, ext, pages: 1, problem: problemOf(file, ext),
    };
    const pending = ext === ".pdf" && !it.problem;
    if (pending) it.pages = null;
    items.value.push(it);
    if (pending) {
      // replace the row instead of mutating it: an in-place write updates the
      // table cell but leaves the derived totals (page estimate, batch count)
      // on their cached value
      pdfPageCount(file).then((n) => {
        items.value = items.value.map(
          (x) => (x.id === it.id ? { ...x, pages: n || 1 } : x));
      });
    }
  }
}
function remove(id: number) { items.value = items.value.filter((i) => i.id !== id); }
function clearAll() { items.value = []; }

const okItems = computed(() => items.value.filter((i) => !i.problem));
const badItems = computed(() => items.value.filter((i) => !!i.problem));
const totalBytes = computed(() => items.value.reduce((n, i) => n + i.file.size, 0));
const estPages = computed(() => okItems.value.reduce((n, i) => n + (i.pages ?? 1), 0));

/** Split the selection into requests the API will accept: at most max_files
 *  per request and under the proxy body ceiling. Oversized single files are
 *  already filtered out, so every batch is submittable. */
function buildBatches(list: Pending[]): File[][] {
  const maxBytes = limits.value.max_batch_mb * 1024 * 1024;
  const batches: File[][] = [];
  let cur: File[] = [];
  let curBytes = 0;
  for (const it of list) {
    if (cur.length >= limits.value.max_files
        || (cur.length && curBytes + it.file.size > maxBytes)) {
      batches.push(cur);
      cur = [];
      curBytes = 0;
    }
    cur.push(it.file);
    curBytes += it.file.size;
  }
  if (cur.length) batches.push(cur);
  return batches;
}
const batchCountPreview = computed(() => buildBatches(okItems.value).length);

// —— submission ——
const uploading = ref(false);
const batchIndex = ref(0);
const batchTotal = ref(0);
const sentBytes = ref(0);
const uploadBytes = ref(0);
const pct = computed(() => uploadBytes.value
  ? Math.min(100, Math.round((sentBytes.value / uploadBytes.value) * 100)) : 0);
const canSubmit = computed(
  () => !uploading.value && !!skillCode.value && okItems.value.length > 0);

const txnIds = ref<string[]>([]);
const rows = ref<ResultRow[]>([]);

async function submit() {
  if (!skillCode.value) { toast.error(t('upload.please_select_a_skill_first_the_skill_determines_which')); return; }
  const batches = buildBatches(okItems.value);
  if (!batches.length) return;
  uploading.value = true;
  txnIds.value = [];
  rows.value = [];
  stopPolling();
  uploadBytes.value = okItems.value.reduce((n, i) => n + i.file.size, 0);
  sentBytes.value = 0;
  batchTotal.value = batches.length;
  let base = 0;
  let accepted = 0;
  for (let i = 0; i < batches.length; i++) {
    batchIndex.value = i + 1;
    const bytes = batches[i].reduce((n, f) => n + f.size, 0);
    try {
      const res = await api.submitProcess(
        batches[i], skillCode.value, (sent) => { sentBytes.value = base + sent; });
      txnIds.value.push(res.transaction_id);
      accepted += res.files.length;
    } catch (e) {
      // one rejected batch (402 balance, proxy limit, network) must not sink
      // the batches that already went through
      toast.error(e);
    }
    base += bytes;
    sentBytes.value = base;
  }
  uploading.value = false;
  if (accepted > 0) {
    toast.ok(t('upload.files-accepted', { count: accepted }));
    const submitted = new Set(batches.flat().map((f) => `${f.name}|${f.size}`));
    items.value = items.value.filter((i) => !submitted.has(`${i.file.name}|${i.file.size}`));
    startPolling();
  }
}

// —— progress polling (stops at terminal states) ——
let timer: number | null = null;
let ticks = 0;
const polling = ref(false);

function stopPolling() {
  if (timer !== null) { clearInterval(timer); timer = null; }
  polling.value = false;
  ticks = 0;
}
async function pollOnce() {
  const results = await Promise.all(
    txnIds.value.map((id) => api.txnStatus(id).catch(() => null)));
  const next: ResultRow[] = [];
  for (const t of results) {
    if (!t) continue;
    for (const f of t.files) {
      next.push({
        file_id: f.file_id, file_name: f.file_name, status: f.status,
        page_count: f.page_count, msg: f.msg,
        child_count: f.child_count ?? 0,
        children: f.children ?? [],
      });
    }
  }
  rows.value = next;
  if (next.length && next.every((r) => TERMINAL.has(r.status))) stopPolling();
  if (++ticks >= POLL_LIMIT) stopPolling();
}
function startPolling() {
  stopPolling();
  polling.value = true;
  void pollOnce();
  timer = window.setInterval(() => { void pollOnce(); }, POLL_MS);
}
function resetForNextBatch() {
  stopPolling();
  txnIds.value = [];
  rows.value = [];
}

// an in-flight upload dies with the page: warn before that happens
function guard(e: BeforeUnloadEvent) {
  if (!uploading.value) return;
  e.preventDefault();
  e.returnValue = "";
}
onMounted(() => window.addEventListener("beforeunload", guard));
onBeforeUnmount(() => {
  window.removeEventListener("beforeunload", guard);
  stopPolling();
});

const stLabel = (s: string) => STATUS_LABELS()[s] ?? s;
function human(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(2)} MB`;
}
</script>

<style scoped>
.block { padding: 14px 16px; margin-bottom: 12px; }
.list-block { margin-bottom: 12px; }
.pad { padding: 14px 16px 0; }
h3 { margin: 0 0 10px; font-size: 13px; color: var(--text-dim); font-weight: 600; }
.step-n { display: inline-flex; width: 17px; height: 17px; border-radius: 4px;
  background: var(--bg-raised); color: var(--accent); align-items: center;
  justify-content: center; font-size: 11px; font-weight: 700; margin-right: 7px; }
.skill-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.skill-row select { min-width: 320px; width: auto; }
.skill-meta { display: flex; gap: 8px; align-items: center; }
.tag { border: 1px solid var(--border); border-radius: 5px; padding: 1px 7px;
  font-size: 11px; color: var(--text-dim); }
.tag.ok { color: var(--green); border-color: currentColor; }

.table-scroll { overflow-x: auto; }
.fname { max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.split-badge { display: inline-block; margin-left: 6px; padding: 1px 7px;
  border-radius: 999px; background: var(--bg-raised); color: var(--accent);
  font-size: 11px; white-space: nowrap; }
.child-info { font-size: 12px; color: var(--text-dim); }
.child-info summary { cursor: pointer; color: var(--blue); }
.child-line { padding: 2px 0 0 12px; white-space: nowrap; }
.ftype { font-size: 11px; color: var(--red); font-weight: 700; }
.bad { color: var(--red); }
.rowbad td { background: rgba(229, 83, 75, .07); }
.reason { font-size: 12px; color: var(--red); }
.err-msg { color: var(--red); font-size: 12px; cursor: help; }
.row-act, .th-act { text-align: right; }
.slim { padding: 2px 11px; font-size: 12px; }
.x { background: transparent; border: none; color: var(--text-dim); font-size: 14px;
  padding: 0 4px; width: auto; }

.submit-bar { display: flex; align-items: center; gap: 14px; padding: 12px 16px;
  border-top: 1px solid var(--border); flex-wrap: wrap; }
.est { font-size: 13px; }
.est b { color: var(--accent); }
.est .sub { display: block; font-size: 11.5px; color: var(--text-dim); }
.submit-bar .right { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.note { font-size: 12.5px; }

.prog-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px;
  font-size: 13px; }
.prog-head .pct { margin-left: auto; color: var(--accent); font-weight: 700; }
.bar { height: 7px; border-radius: 999px; background: var(--bg-raised); overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--accent); border-radius: 999px;
  transition: width .2s; }

.notice { border-left: 3px solid var(--accent); padding: 9px 13px;
  background: var(--bg-raised); border-radius: 0 8px 8px 0; font-size: 13px;
  margin-bottom: 10px; }
.txn { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  font-size: 13px; margin-bottom: 10px; }
.txn code { background: var(--bg-raised); border-radius: 5px; padding: 2px 8px;
  font-size: 12px; }

@media (max-width: 860px) {
  .skill-row select { min-width: 100%; }
  .submit-bar .right { margin-left: 0; width: 100%; }
}
</style>
