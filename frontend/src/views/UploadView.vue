<template>
  <main class="page">
    <PageHeader title="上传文档"
                desc="选择技能、投放文件，平台自动解析并送入校验队列。">
      <router-link to="/tasks"><button class="ghost">去任务列表 →</button></router-link>
    </PageHeader>

    <!-- no publishable skill: uploading would 400 on every file, so send the
         user to the place that fixes it instead of letting them try -->
    <section v-if="skillsLoaded && usableSkills.length === 0" class="card-panel block">
      <EmptyState title="还没有可用的技能" glyph="🧩">
        上传前需要先有一个<strong>已发布</strong>的技能——技能决定从文档里抽取哪些字段。
        <template #action>
          <router-link to="/skills"><button class="primary">去技能中心</button></router-link>
        </template>
      </EmptyState>
    </section>

    <template v-else>
      <!-- 1. skill -->
      <section class="card-panel block">
        <h3><span class="step-n">1</span>选择技能</h3>
        <div class="skill-row">
          <select v-model="skillCode" :disabled="uploading">
            <option value="" disabled>请选择一个技能…</option>
            <option v-for="s in usableSkills" :key="s.skill_code" :value="s.skill_code">
              {{ s.name }}（{{ s.skill_code }} · v{{ s.published_version }}）
            </option>
          </select>
          <div v-if="selectedSkill" class="skill-meta">
            <span class="tag">{{ selectedSkill.kind }}</span>
            <span class="tag ok">v{{ selectedSkill.published_version }} 已发布</span>
          </div>
        </div>
      </section>

      <!-- 2. intake -->
      <section class="card-panel block">
        <h3><span class="step-n">2</span>投放文件</h3>
        <DropZone :accept="accept" :disabled="uploading" @add="addFiles">
          <template #limits>
            单文件 ≤ {{ limits.max_size_mb }}MB ｜
            超过 {{ limits.max_files }} 个或 {{ limits.max_batch_mb }}MB 会自动分批提交
          </template>
        </DropZone>
      </section>

      <!-- 3. pending list -->
      <section v-if="items.length" class="card-panel list-block">
        <h3 class="pad">
          <span class="step-n">3</span>待上传（{{ items.length }} 个 · 合计 {{ human(totalBytes) }}）
        </h3>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr><th>文件名</th><th>类型</th><th>大小</th><th>页数</th><th>校验</th><th class="th-act"></th></tr>
            </thead>
            <tbody>
              <tr v-for="it in items" :key="it.id" :class="{ rowbad: !!it.problem }">
                <td class="fname" :class="{ bad: !!it.problem }" :title="it.file.name">
                  {{ it.file.name }}</td>
                <td><span class="ftype">{{ it.ext.replace(".", "").toUpperCase() }}</span></td>
                <td class="dim">{{ human(it.file.size) }}</td>
                <td>{{ it.problem ? "—" : (it.pages ?? "…") }}</td>
                <td>
                  <span v-if="it.problem" class="reason">{{ it.problem }}</span>
                  <span v-else class="dim">✓ 可提交</span>
                </td>
                <td class="row-act">
                  <button class="x" :disabled="uploading" title="移除"
                          @click="remove(it.id)">✕</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- submit bar -->
        <div class="submit-bar">
          <div class="est">
            预估 <b>{{ estPages }} 页</b>
            <span v-if="batchCountPreview > 1"> · 自动分 <b>{{ batchCountPreview }}</b> 批提交</span>
            <span class="sub">
              可提交 {{ okItems.length }} 个<template v-if="badItems.length">，
                {{ badItems.length }} 个不受支持将被跳过</template>
              <template v-if="credits !== null"> ｜ 剩余 {{ credits }} credits</template>
            </span>
          </div>
          <div class="right">
            <button class="ghost" :disabled="uploading" @click="clearAll">清空</button>
            <button class="primary" :disabled="!canSubmit" @click="submit">
              {{ uploading ? "提交中…" : "提交处理 →" }}</button>
          </div>
        </div>
      </section>

      <!-- upload progress -->
      <section v-if="uploading" class="card-panel block">
        <div class="prog-head">
          <span>正在上传<template v-if="batchTotal > 1">（第 {{ batchIndex }}/{{ batchTotal }} 批）</template>…</span>
          <span class="dim">{{ human(sentBytes) }} / {{ human(uploadBytes) }}</span>
          <span class="pct">{{ pct }}%</span>
        </div>
        <div class="bar"><i :style="{ width: pct + '%' }" /></div>
      </section>

      <!-- results -->
      <section v-if="rows.length || txnIds.length" class="card-panel list-block">
        <div class="pad">
          <div class="notice">
            ✅ 已受理 <b>{{ rows.length }}</b> 个文件，正在后台处理——可以留在本页看进度，也可以去任务列表。
          </div>
          <div class="txn">
            <span class="dim">事务号</span>
            <code v-for="t in txnIds" :key="t">{{ t }}</code>
            <span class="dim">技能</span><span>{{ skillCode }}</span>
            <span v-if="polling" class="dim">· 每 2 秒刷新</span>
          </div>
        </div>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr><th>文件名</th><th>页数</th><th>状态</th><th>说明</th><th class="th-act"></th></tr>
            </thead>
            <tbody>
              <tr v-for="r in rows" :key="r.file_id">
                <td class="fname" :class="{ child: r.child }" :title="r.file_name">
                  {{ r.file_name }}</td>
                <td>{{ r.page_count || "—" }}</td>
                <td><span class="chip" :class="`chip-${r.status}`">{{ stLabel(r.status) }}</span></td>
                <td>
                  <span v-if="r.msg" class="err-msg" :title="r.msg">⚠ {{ r.msg }}</span>
                  <span v-else class="dim">—</span>
                </td>
                <td class="row-act">
                  <router-link v-if="r.status === 'pending_verification'"
                               :to="`/review/${r.file_id}`">
                    <button class="primary slim">去校验 →</button></router-link>
                  <router-link v-else-if="TERMINAL.has(r.status) && r.status !== 'split'"
                               :to="`/review/${r.file_id}`">
                    <button class="ghost slim">查看</button></router-link>
                  <button v-else class="ghost slim" disabled>—</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="submit-bar">
          <span class="dim note">失败的文件不计费。</span>
          <div class="right">
            <router-link to="/tasks"><button class="ghost">去任务列表</button></router-link>
            <button class="confirm" @click="resetForNextBatch">再传一批</button>
          </div>
        </div>
      </section>
    </template>
  </main>
</template>

<script setup lang="ts">
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
  problem: string | null;        // non-null => skipped at submit time
}
interface ResultRow {
  file_id: string; file_name: string; status: string;
  page_count: number; msg: string; child: boolean;
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
  () => (skills.value ?? []).filter((s) => s.published_version != null));
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
const { data: home } = useQuery({ queryKey: ["home-stats"], queryFn: api.homeStats });
const credits = computed(() => home.value?.remaining_credits ?? null);

// —— selection ——
const items = ref<Pending[]>([]);
let seq = 0;

function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i < 0 ? "" : name.slice(i).toLowerCase();
}
function problemOf(file: File, ext: string): string | null {
  if (!limits.value.extensions.includes(ext)) {
    return ext === ".doc" || ext === ".xls" || ext === ".ppt"
      ? "暂不支持旧版 Office 格式，请另存为 .docx/.xlsx/.pptx"
      : "暂不支持该格式";
  }
  if (file.size === 0) return "空文件";
  if (file.size > limits.value.max_size_mb * 1024 * 1024) {
    return `超过单文件 ${limits.value.max_size_mb}MB 上限`;
  }
  return null;
}

function addFiles(files: File[]) {
  for (const file of files) {
    // same name+size twice in one batch is nearly always a mis-drop
    if (items.value.some((i) => i.file.name === file.name && i.file.size === file.size)) {
      toast.error(`${file.name}：已在待上传列表中`);
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
  if (!skillCode.value) { toast.error("请先选择一个技能——技能决定抽取哪些字段"); return; }
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
    toast.ok(`已受理 ${accepted} 个文件`);
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
        // split children are named "<stem>#docN<suffix>" by the runner
        child: /#doc\d+\./.test(f.file_name),
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

const stLabel = (s: string) => STATUS_LABELS[s] ?? s;
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
.fname.child { padding-left: 26px; color: var(--text-dim); }
.fname.child::before { content: "└ "; }
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
