<template>
  <main class="editor" :class="{ 'no-rail': isNew }" v-if="pkg">
    <!-- left rail: version history (Insavlo editor layout) -->
    <aside v-if="!isNew" class="ver-rail">
      <div class="rail-head">版本历史</div>
      <button class="new-ver" @click="newVersion">＋ 新建版本</button>
      <div class="ver-list">
        <div v-for="v in [...versions].reverse()" :key="v.version" class="ver-card"
             :class="{ current: v.version === selectedVersion }"
             @click="selectVersion(v.version)">
          <div class="ver-row">
            <strong>v{{ v.version }}</strong>
            <span class="chip" :class="`chip-${v.status}`">{{ verLabel(v.status) }}</span>
          </div>
          <span class="dim ver-date">{{ shortDate(v.created_at) }}</span>
          <!-- each version carries its own note; showing it here is what makes
               "介绍不见了" diagnosable instead of mysterious (P05) -->
          <p v-if="v.changelog" class="ver-note-line" :title="v.changelog">{{ v.changelog }}</p>
        </div>
      </div>
    </aside>

    <!-- main column -->
    <div class="main-col">
      <div class="toolbar">
        <div class="ident">
          <h1>{{ pkg.name || pkg.skill_code || "新技能" }}</h1>
          <span class="dim code-line">技能代码：{{ pkg.skill_code || "（保存时确定）" }}</span>
        </div>
        <div class="acts">
          <button class="primary" :disabled="saving" @click="save">
            💾 {{ isNew ? "创建技能" : saveLabel }}</button>
          <button v-if="!isNew && selectedStatus === 'draft'" class="confirm"
                  @click="publishSelected">🚀 发布</button>
          <button v-if="!isNew" @click="apiModal = true">🔌 API 接入</button>
          <button v-if="!isNew" @click="downloadFile(api.skillExportUrl(code), `${code}.yaml`)">
            导出 YAML</button>
          <button v-if="!isNew" class="danger" @click="removeSkill">删除</button>
          <router-link to="/skills"><button class="ghost">‹ 返回技能列表</button></router-link>
        </div>
      </div>
      <p v-if="!isNew && selectedStatus === 'draft'" class="ver-note dim">
        当前编辑 v{{ selectedVersion }}（草稿）——点「保存」原地更新本草稿，不会新增版本；
        要留存快照请点左侧「＋ 新建版本」。
      </p>
      <p v-else-if="!isNew && selectedStatus" class="ver-note dim">
        当前查看 v{{ selectedVersion }}（{{ verLabel(selectedStatus) }}）——已发布版本不可改，
        点「另存为新草稿」将以此为底新建一个草稿版本。
      </p>

      <!-- guidance for a blank new skill -->
      <div v-if="isNew && !pkg.fields.length" class="guide card-panel">
        <span class="step"><b>1</b> 起草字段：样本预标注 / 自然语言描述 / 表格清单</span>
        <span class="arrow">→</span>
        <span class="step"><b>2</b> 逐字段核对修改（点 ✎ 编辑）</span>
        <span class="arrow">→</span>
        <span class="step"><b>3</b> 试跑验证，创建并发布</span>
      </div>

      <!-- basic info -->
      <section class="card-panel block">
        <h3 class="block-title">基本信息</h3>
        <div class="basic-grid">
          <label>技能名称
            <input v-model="pkg.name" placeholder="例如：XCMG 海外发票" /></label>
          <label>skill_code（英文，创建后不可改）
            <input v-model="pkg.skill_code" class="mono" :disabled="!isNew" /></label>
          <label class="span2">描述
            <textarea v-model="pkg.description" rows="2"
                      placeholder="这个技能处理什么文档、服务什么业务（给同事看的说明）"></textarea>
          </label>
          <!-- version note lives on the SkillVersion row, not in the package:
               it answers "这一版改了什么", per version, and never leaks across
               versions (P05) -->
          <label v-if="!isNew" class="span2">版本介绍（v{{ selectedVersion }} 的说明）
            <input v-model="changelog"
                   placeholder="这一版改了什么，例如：新增税额字段、放宽发票号正则" />
          </label>
          <label>审核模式（Needs Review Mode）
            <select v-model="pkg.review_policy.mode">
              <option value="auto">低置信进人审（Review on Low Confidence）</option>
              <option value="always">全部人审</option>
              <option value="never">全部直通</option>
            </select></label>
          <label>置信阈值（低于则人审）
            <select v-model.number="pkg.review_policy.confidence_threshold">
              <option :value="1">1</option><option :value="2">2</option><option :value="3">3</option>
            </select></label>
        </div>
      </section>

      <!-- field configuration: hierarchical cards, modal editing -->
      <section class="card-panel block">
        <div class="block-head">
          <h3 class="block-title">字段配置</h3>
          <label class="file-btn slim">
            <input type="file" hidden @change="probe" :disabled="probing" />
            <span class="btn-like">{{ probing ? "⏳ 分析中…" : "⚡ 样本预标注" }}</span>
          </label>
          <button class="mini" :class="{ primary: textPanel }"
                  @click="textPanel = !textPanel">📝 描述生成</button>
          <label class="file-btn slim">
            <input type="file" accept=".xlsx,.csv,.tsv" hidden @change="tableImport" />
            <span class="btn-like">📊 表格导入</span>
          </label>
          <button v-if="pkg.fields.length" class="mini" :disabled="enriching"
                  title="让大模型把字段说明扩写为 关键词→清洗规则→输出格式 的完整抽取指令"
                  @click="enrich">{{ enriching ? "⏳ 补全中…" : "✨ AI 补全说明" }}</button>
        </div>

        <div v-if="textPanel" class="text-panel">
          <textarea v-model="draftText" rows="4"
                    placeholder="用一段话描述要抽取什么。例：从海外发票抽取发票号（去掉空格和连字符）、开票日期（统一 YYYY-MM-DD）、币种（ISO 三位码）、总金额（保留两位小数）、明细行（品名/数量/单价/金额），并判断是否为红字发票。"></textarea>
          <div class="text-panel-act">
            <span class="dim">生成的字段会预填到下方，可修改后再保存（消耗少量 token）</span>
            <button class="primary" :disabled="drafting || !draftText.trim()"
                    @click="draftFromText">{{ drafting ? "⏳ 起草中…" : "生成字段草稿" }}</button>
          </div>
        </div>

        <p v-if="!pkg.fields.length" class="dim pad">
          还没有字段。用上方三种方式起草，或点下方「＋ 添加字段」手动创建。
        </p>
        <p v-if="pkg.fields.length > 1" class="dim drag-tip">
          拖动字段左侧 ⠿ 可调整顺序；顺序即抽取结果与导出的字段顺序，保存后生效。
        </p>
        <div class="field-tree" ref="treeEl">
          <FieldCard v-for="(f, i) in pkg.fields" :key="f.name || i" :field="f" :index="i"
                     :drag-from="reorder.from.value ?? -1" :drag-over="reorder.over.value ?? -1"
                     @edit="openEdit(pkg!.fields, i)"
                     @remove="pkg!.fields.splice(i, 1)"
                     @edit-column="(ci) => openEdit(f.columns, ci, true)"
                     @add-column="openAdd(f.columns, true)"
                     @grip-down="startFieldDrag" />
          <button class="add-field" @click="openAdd(pkg!.fields)">＋ 添加字段</button>
        </div>
      </section>

      <!-- model binding / parser / extra rules -->
      <section class="card-panel block">
        <h3 class="block-title">模型与解析</h3>
        <!-- Model and parser fields are comboboxes: pick a configured channel
             from the list, or type any name by hand — private deployments run
             channels this console has never heard of (self-hosted vLLM, a
             customer's internal gateway), so a closed <select> would lock them
             out. The lists come from the server's own config, never hardcoded. -->
        <datalist id="dl-providers">
          <option v-for="p in providerOptions" :key="p.name" :value="p.name">
            {{ p.model }}{{ p.active ? "（平台默认）" : "" }}</option>
        </datalist>
        <datalist id="dl-parsers">
          <option v-for="p in parserOptions" :key="p" :value="p" />
        </datalist>
        <div class="basic-grid">
          <label>抽取模型（空=平台默认{{ activeProvider ? `：${activeProvider}` : "" }}）
            <input v-model="pkg.model_binding.extractor" list="dl-providers"
                   placeholder="下拉选择或直接输入，如 qwen" /></label>
          <label>备用模型（fallback）
            <input :value="pkg.model_binding.fallback ?? ''" list="dl-providers"
                   placeholder="可空；下拉选择或直接输入"
                   @input="pkg.model_binding.fallback = ($event.target as HTMLInputElement).value || null" /></label>
          <label>挑战者模型（不一致标人审）
            <input :value="pkg.model_binding.challenger ?? ''" list="dl-providers"
                   placeholder="可空；下拉选择或直接输入"
                   @input="pkg.model_binding.challenger = ($event.target as HTMLInputElement).value || null" /></label>
          <label>解析器（空=自动路由）
            <input :value="pkg.parser ?? ''" list="dl-parsers"
                   placeholder="自动；下拉选择或直接输入"
                   @input="pkg.parser = ($event.target as HTMLInputElement).value || null" /></label>
          <p class="span2 dim combo-note">
            模型与解析器均支持下拉选择或手动输入；手动输入的名称需在服务端
            <code>configs/providers.yaml</code> / <code>configs/parsers.yaml</code> 中存在才会生效。
          </p>
          <label class="span2">附加规则（自由文本，进提示词）
            <textarea v-model="pkg.additional_rules" rows="2"
                      placeholder="如：金额一律保留两位小数；日期统一 YYYY-MM-DD"></textarea></label>
        </div>
      </section>
    </div>

    <!-- right rail: studio tools -->
    <aside class="studio-rail">
      <section class="card-panel s-block">
        <h3 class="block-title">试运行（dry-run）</h3>
        <p class="dim">当前定义在一份样本上试跑，可多模型并排对比。</p>
        <input v-model="dryProviders" list="dl-providers"
               placeholder="模型列表，逗号分隔；空=默认" />
        <label class="file-btn"><input type="file" hidden @change="dryRun" :disabled="running" />
          <span class="btn-like">{{ running ? "⏳ 试跑中…" : "上传样本试跑" }}</span></label>
        <div v-if="dryRuns.length" class="runs">
          <div v-for="r in dryRuns" :key="r.provider" class="run">
            <div class="run-head">
              <strong>{{ r.provider }}</strong>
              <span v-if="r.ok" class="dim">
                {{ r.usage?.prompt_tokens }}+{{ r.usage?.completion_tokens }} tokens</span>
              <span v-else class="rulefail">{{ r.error }}</span>
            </div>
            <table v-if="r.ok && r.result">
              <tbody>
                <tr v-for="(cell, name) in scalarCells(r.result)" :key="name">
                  <td class="dim">{{ name }}</td>
                  <td>{{ cell.$value || "—" }}</td>
                  <td><span class="conf">c{{ cell.$confidence }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section v-if="!isNew" class="card-panel s-block">
        <h3 class="block-title">金样本回归（发布门禁）</h3>
        <p class="dim">挂固定样本＋期望值，发布前跑回归防退化。</p>
        <label class="file-btn"><input type="file" hidden @change="pickGolden" />
          <span class="btn-like">{{ goldenFile ? goldenFile.name : "选择样本文件" }}</span></label>
        <textarea v-model="goldenExpected" rows="3"
                  placeholder='期望值 JSON，如 {"invoice_no": "INV-1"}'></textarea>
        <button :disabled="!goldenFile" @click="addGolden">挂载金样本</button>
        <button v-if="selectedVersion" @click="goldenCheck" :disabled="checking">
          {{ checking ? "⏳ 回归中…" : `对 v${selectedVersion} 跑回归` }}</button>
        <div v-if="goldenReport" class="golden-report">
          <template v-if="goldenReport.samples === 0">
            <p class="dim">{{ goldenReport.note }}</p>
          </template>
          <template v-else>
            <p><strong>{{ goldenReport.samples }}</strong> 个样本，平均匹配率
              <strong :class="{ warn: (goldenReport.avg_match_rate ?? 0) < 1 }">
                {{ Math.round((goldenReport.avg_match_rate ?? 0) * 100) }}%</strong></p>
            <div v-for="(rep, i) in goldenReport.reports" :key="i" class="rep">
              <template v-if="rep.ok">
                <span class="dim">样本 {{ i + 1 }}：匹配 {{ Math.round((rep.match_rate ?? 0) * 100) }}%</span>
                <div v-for="(d, fname) in rep.diffs" :key="fname" class="rulefail">
                  {{ fname }}: 期望「{{ d.expected }}」→ 实得「{{ d.got }}」</div>
              </template>
              <span v-else class="rulefail">样本 {{ i + 1 }}：{{ rep.error }}</span>
            </div>
          </template>
        </div>
      </section>
    </aside>

    <FieldEditModal v-if="editing" :field="editing.spec" :is-new="editing.isNew"
                    :is-column="editing.isColumn"
                    @save="commitEdit" @cancel="editing = null" />
    <SkillApiModal v-if="apiModal" :skill-code="code" @close="apiModal = false" />
  </main>
  <main v-else class="editor"><Skeleton :rows="8" /></main>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { api, downloadFile, type DryRunEntry, type FieldCell, type FieldSpec,
         type SkillPackage } from "../api";
import FieldCard from "../components/FieldCard.vue";
import FieldEditModal from "../components/FieldEditModal.vue";
import SkillApiModal from "../components/SkillApiModal.vue";
import Skeleton from "../components/Skeleton.vue";
import { VERSION_LABELS } from "../labels";
import { moveItem, useListReorder } from "../reorder";
import { toast } from "../toast";

const props = defineProps<{ code: string }>();
const router = useRouter();
const isNew = computed(() => props.code === "new");

// provider/parser catalogue from the server — the editor no longer keeps its
// own copy (the hardcoded list had drifted from configs/parsers.yaml)
const providerOptions = ref<{ name: string; model: string; active: boolean }[]>([]);
const parserOptions = ref<string[]>([]);
const activeProvider = computed(() => providerOptions.value.find((p) => p.active)?.name ?? "");
api.skillOptions()
  .then((o) => { providerOptions.value = o.providers; parserOptions.value = o.parsers; })
  .catch(() => { /* pickers degrade to free text, which still works */ });

const pkg = ref<SkillPackage | null>(null);
const versions = ref<{ version: number; status: string; changelog: string;
                       created_at?: string }[]>([]);
const selectedVersion = ref<number | null>(null);
const changelog = ref("");
const apiModal = ref(false);
const selectedStatus = computed(() =>
  versions.value.find((v) => v.version === selectedVersion.value)?.status ?? null);

function blankPkg(): SkillPackage {
  return { skill_code: "", name: "", description: "", kind: "extract", doc_type_hint: "",
           system_prompt: "", fields: [], few_shot: [], validators: [],
           review_policy: { mode: "auto", confidence_threshold: 2 },
           model_binding: { extractor: "", fallback: null, challenger: null },
           parser: null, additional_rules: "" };
}
function blankField(name = ""): FieldSpec {
  return { name, type: "string", instruction: "", mode: "verbatim", required: false,
           anchor_hints: [], enum_values: [], columns: [] };
}
const verLabel = (s: string) => VERSION_LABELS[s] ?? s;
const shortDate = (v?: string) =>
  v ? new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
const splitCsv = (v: string) => v.split(/[,，]/).map((s) => s.trim()).filter(Boolean);

async function load(version?: number) {
  if (isNew.value) {
    pkg.value = blankPkg();
    versions.value = [];
    selectedVersion.value = null;
    return;
  }
  try {
    const d = await api.skillDetail(props.code, version);
    pkg.value = { ...blankPkg(), ...(d.latest_package ?? {}),
                  skill_code: d.skill_code, name: d.latest_package?.name || d.name };
    versions.value = d.versions;
    selectedVersion.value = d.selected_version ?? null;
    // the note box always shows the note of the version on screen
    changelog.value = d.versions.find((v) => v.version === d.selected_version)?.changelog ?? "";
  } catch (e) { toast.error(e); }
}
watch(() => props.code, () => load(), { immediate: true });
function selectVersion(v: number) { load(v); }

// —— field order (P07): order is part of the contract (extraction output and
// CSV/JSON export follow it), so a reorder marks the draft dirty like any edit
const treeEl = ref<HTMLElement>();
const reorder = useListReorder();
function startFieldDrag(i: number, ev: PointerEvent) {
  reorder.start(i, ev, treeEl.value,
                (f, t) => { if (pkg.value) moveItem(pkg.value.fields, f, t); });
}

// —— field modal editing: all card edits round-trip through the modal ——
const editing = ref<{ list: FieldSpec[]; index: number; spec: FieldSpec;
                      isNew: boolean; isColumn: boolean } | null>(null);
function openEdit(list: FieldSpec[], index: number, isColumn = false) {
  editing.value = { list, index, spec: list[index], isNew: false, isColumn };
}
function openAdd(list: FieldSpec[], isColumn = false) {
  editing.value = { list, index: -1, spec: blankField(), isNew: true, isColumn };
}
function commitEdit(spec: FieldSpec) {
  if (!editing.value) return;
  const { list, index, isNew: adding } = editing.value;
  if (adding) list.push(spec);
  else list.splice(index, 1, spec);
  editing.value = null;
}

/** Save = update the draft on screen. Only 「新建版本」 and publishing move the
 *  version pointer; a published version has no in-place save, so its button
 *  says what it actually does (P04). */
const saveLabel = computed(() =>
  selectedStatus.value && selectedStatus.value !== "draft" ? "另存为新草稿" : "保存");
const saving = ref(false);

function validPkg(): boolean {
  if (!pkg.value) return false;
  if (!pkg.value.skill_code) { toast.error("请填写 skill_code"); return false; }
  if (!pkg.value.fields.length) { toast.error("至少定义一个字段"); return false; }
  return true;
}

async function save() {
  if (!validPkg() || !pkg.value) return;
  saving.value = true;
  try {
    if (isNew.value) {
      await api.skillCreate(pkg.value, changelog.value);
      toast.ok("技能已创建（v1 草稿）");
      router.push(`/skills/${pkg.value.skill_code}`);
    } else if (selectedStatus.value === "draft" && selectedVersion.value) {
      await api.skillSaveDraft(props.code, selectedVersion.value, pkg.value, changelog.value);
      toast.ok(`v${selectedVersion.value} 草稿已保存（未新增版本）`);
      await load(selectedVersion.value);
    } else {
      // published/archived versions are immutable: branch instead of failing
      const r = await api.skillNewDraft(props.code, pkg.value, changelog.value);
      toast.ok(`已以 v${selectedVersion.value} 为底新建 v${r.version} 草稿`);
      await load(r.version);
    }
  } catch (e) { toast.error(e); }
  finally { saving.value = false; }
}

/** Explicit branch: the only button besides publish that adds a version. */
async function newVersion() {
  if (!validPkg() || !pkg.value) return;
  try {
    const r = await api.skillNewDraft(props.code, pkg.value, changelog.value);
    toast.ok(`已新建 v${r.version} 草稿`);
    await load(r.version);
  } catch (e) { toast.error(e); }
}
async function publishSelected() {
  if (!selectedVersion.value) return;
  try {
    await api.skillPublish(props.code, selectedVersion.value);
    toast.ok(`v${selectedVersion.value} 已发布（旧版本自动归档）`);
    await load(selectedVersion.value);
  } catch (e) { toast.error(e); }
}
async function removeSkill() {
  if (!confirm(`确定删除技能 ${props.code}？运行中任务不受影响，技能将从列表消失。`)) return;
  try {
    await api.skillDelete(props.code);
    toast.ok("技能已删除");
    router.push("/skills");
  } catch (e) { toast.error(e); }
}

// —— draft channels ——
const probing = ref(false);
function mergeDraft(fields: FieldSpec[], docType?: string): number {
  if (!pkg.value) return 0;
  const existing = new Set(pkg.value.fields.map((f) => f.name));
  const fresh = fields.filter((f) => !existing.has(f.name));
  pkg.value.fields.push(...fresh);
  if (docType && !pkg.value.doc_type_hint) pkg.value.doc_type_hint = docType;
  return fresh.length;
}
async function probe(ev: Event) {
  const file = (ev.target as HTMLInputElement).files?.[0];
  if (!file || !pkg.value) return;
  probing.value = true;
  try {
    const r = await api.skillProbe(file);
    toast.ok(`预标注完成：新增 ${mergeDraft(r.fields, r.doc_type)} 个字段草稿（${r.provider_used}），请核对后保存`);
  } catch (e) { toast.error(e); }
  finally { probing.value = false; (ev.target as HTMLInputElement).value = ""; }
}
const textPanel = ref(false);
const draftText = ref("");
const drafting = ref(false);
async function draftFromText() {
  if (!draftText.value.trim()) return;
  drafting.value = true;
  try {
    const r = await api.skillDraftFromText(draftText.value);
    toast.ok(`已从描述起草 ${mergeDraft(r.fields, r.doc_type)} 个字段（${r.provider_used}），请核对后保存`);
    textPanel.value = false;
    draftText.value = "";
  } catch (e) { toast.error(e); }
  finally { drafting.value = false; }
}
async function tableImport(ev: Event) {
  const file = (ev.target as HTMLInputElement).files?.[0];
  if (!file) return;
  try {
    const r = await api.skillDraftFromTable(file);
    toast.ok(`表格导入完成：新增 ${mergeDraft(r.fields)} 个字段。可点「✨ AI 补全说明」扩写规则`);
  } catch (e) { toast.error(e); }
  finally { (ev.target as HTMLInputElement).value = ""; }
}
const enriching = ref(false);
async function enrich() {
  if (!pkg.value?.fields.length) return;
  enriching.value = true;
  try {
    const r = await api.skillEnrich(pkg.value.fields, pkg.value.doc_type_hint);
    let n = 0;
    const apply = (specs: FieldSpec[], patches: { name: string; instruction: string;
                                                  columns?: { name: string; instruction: string }[] }[]) => {
      for (const p of patches) {
        const f = specs.find((x) => x.name === p.name);
        if (!f) continue;
        if (p.instruction && p.instruction !== f.instruction) { f.instruction = p.instruction; n++; }
        if (p.columns?.length && f.columns.length)
          apply(f.columns, p.columns.map((c) => ({ ...c, columns: undefined })));
      }
    };
    apply(pkg.value.fields, r.fields);
    toast.ok(`已补全 ${n} 处字段说明（${r.provider_used}），请核对后保存`);
  } catch (e) { toast.error(e); }
  finally { enriching.value = false; }
}

// —— studio: dry-run + golden ——
const running = ref(false);
const dryProviders = ref("");
const dryRuns = ref<DryRunEntry[]>([]);
async function dryRun(ev: Event) {
  const file = (ev.target as HTMLInputElement).files?.[0];
  if (!file || !pkg.value) return;
  running.value = true;
  try {
    const r = await api.skillDryRun(file, pkg.value, splitCsv(dryProviders.value));
    dryRuns.value = r.runs;
  } catch (e) { toast.error(e); }
  finally { running.value = false; (ev.target as HTMLInputElement).value = ""; }
}
function scalarCells(result: Record<string, FieldCell>): Record<string, FieldCell> {
  const out: Record<string, FieldCell> = {};
  for (const [k, v] of Object.entries(result))
    if (!Array.isArray(v)) out[k] = v;
  return out;
}
const goldenFile = ref<File | null>(null);
const goldenExpected = ref("");
const checking = ref(false);
const goldenReport = ref<Awaited<ReturnType<typeof api.goldenCheck>> | null>(null);
function pickGolden(ev: Event) {
  goldenFile.value = (ev.target as HTMLInputElement).files?.[0] ?? null;
}
async function addGolden() {
  if (!goldenFile.value) return;
  let expected: Record<string, string> = {};
  try { expected = goldenExpected.value ? JSON.parse(goldenExpected.value) : {}; }
  catch { toast.error("期望值不是合法 JSON"); return; }
  try {
    await api.goldenAdd(props.code, goldenFile.value, expected);
    toast.ok("金样本已挂载");
    goldenFile.value = null;
    goldenExpected.value = "";
  } catch (e) { toast.error(e); }
}
async function goldenCheck() {
  if (!selectedVersion.value) return;
  checking.value = true;
  goldenReport.value = null;
  try { goldenReport.value = await api.goldenCheck(props.code, selectedVersion.value); }
  catch (e) { toast.error(e); }
  finally { checking.value = false; }
}
</script>

<style scoped>
.editor { display: grid; grid-template-columns: 210px minmax(0, 1fr) minmax(280px, 340px);
  gap: 0; align-items: start; min-height: calc(100vh - 52px); }
/* new-skill page has no version rail — drop its column or main lands in 210px */
.editor.no-rail { grid-template-columns: minmax(0, 1fr) minmax(280px, 340px); }

/* version rail */
.ver-rail { position: sticky; top: 52px; height: calc(100vh - 52px); overflow-y: auto;
  border-right: 1px solid var(--border); background: var(--bg-panel);
  padding: 14px 12px; display: flex; flex-direction: column; gap: 10px; }
.rail-head { font-size: 13px; font-weight: 700; color: var(--text-dim); }
.new-ver { border-style: dashed; }
.ver-list { display: flex; flex-direction: column; gap: 8px; }
.ver-card { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px;
  cursor: pointer; background: var(--bg); }
.ver-card:hover { border-color: var(--accent); }
.ver-card.current { border-color: var(--accent); background: var(--bg-raised); }
.ver-row { display: flex; justify-content: space-between; align-items: center; }
.ver-date { font-size: 11px; }
.ver-note-line { margin: 4px 0 0; font-size: 11px; color: var(--text-dim);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* main */
.main-col { padding: 16px 20px 32px; display: flex; flex-direction: column;
  gap: 14px; min-width: 0; }
.toolbar { display: flex; gap: 14px; align-items: flex-start; flex-wrap: wrap; }
.ident { flex: 1; min-width: 240px; }
.ident h1 { margin: 0; font-size: 20px; }
.code-line { font-family: Consolas, monospace; font-size: 12px; }
.acts { display: flex; gap: 8px; flex-wrap: wrap; }
.ver-note { margin: -6px 0 0; font-size: 12px; }
.guide { display: flex; gap: 14px; align-items: center; justify-content: center;
  padding: 14px; border-style: dashed; flex-wrap: wrap; font-size: 13px;
  color: var(--text-dim); }
.guide b { display: inline-flex; width: 20px; height: 20px; border-radius: 50%;
  background: var(--accent); color: var(--accent-text); align-items: center;
  justify-content: center; margin-right: 6px; }
.arrow { color: var(--accent); }
.block { padding: 14px 16px; }
.block-title { margin: 0 0 10px; font-size: 14px; color: var(--accent); }
.block-head { display: flex; gap: 8px; align-items: center; margin-bottom: 10px;
  flex-wrap: wrap; }
.block-head .block-title { margin: 0; flex: 1; }
.basic-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.basic-grid label { display: flex; flex-direction: column; gap: 4px; font-size: 13px;
  color: var(--text-dim); }
.span2 { grid-column: span 2; }
.mono { font-family: Consolas, monospace; }
.file-btn .btn-like { border: 1px solid var(--border); background: var(--bg-raised);
  border-radius: 6px; padding: 6px 14px; cursor: pointer; display: inline-block;
  font-size: 13px; }
.file-btn.slim .btn-like { padding: 3px 10px; font-size: 12px; }
.file-btn .btn-like:hover { border-color: var(--accent); }
.mini { padding: 2px 10px; font-size: 12px; }
.text-panel { border: 1px solid var(--border); border-radius: 8px; padding: 12px;
  display: flex; flex-direction: column; gap: 8px; background: var(--bg-raised);
  margin-bottom: 10px; }
.text-panel-act { display: flex; gap: 12px; align-items: center;
  justify-content: space-between; font-size: 12px; }
.pad { padding: 4px 0; margin: 0; }
.drag-tip { margin: 0 0 8px; font-size: 12px; }
.combo-note { margin: -2px 0 0; font-size: 11.5px; line-height: 1.6; }
.combo-note code { font-family: Consolas, monospace; }
.field-tree { display: flex; flex-direction: column; gap: 10px; }
.add-field { border-style: dashed; padding: 8px; }

/* studio rail */
.studio-rail { position: sticky; top: 52px; max-height: calc(100vh - 52px);
  overflow-y: auto; padding: 16px 16px 32px 0; display: flex;
  flex-direction: column; gap: 14px; }
.s-block { padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
.s-block .dim { font-size: 12px; }
.runs { display: flex; flex-direction: column; gap: 10px; }
.run { border: 1px solid var(--border); border-radius: 8px; padding: 8px; }
.run-head { display: flex; gap: 10px; align-items: baseline; margin-bottom: 6px; }
.run table { font-size: 12px; border-collapse: collapse; width: 100%; }
.run td { padding: 3px 6px; border-bottom: 1px solid var(--border); }
.conf { font-size: 11px; color: var(--text-dim); }
.rulefail { color: var(--red); font-size: 12px; }
.golden-report { border: 1px solid var(--border); border-radius: 8px; padding: 8px;
  font-size: 13px; }
.warn { color: var(--red); }
.rep { margin-top: 6px; }
.dim { color: var(--text-dim); }

@media (max-width: 1200px) {
  .editor { grid-template-columns: 180px minmax(0, 1fr); }
  .studio-rail { grid-column: 1 / -1; position: static; max-height: none;
    padding: 0 20px 24px; }
}
</style>
