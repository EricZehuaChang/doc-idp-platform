<template>
  <main class="editor" v-if="pkg">
    <!-- toolbar: identity left, actions right, sticky -->
    <div class="toolbar">
      <router-link to="/skills" class="back" title="返回技能中心">←</router-link>
      <div class="ident">
        <input v-model="pkg.name" class="name-input" placeholder="技能名称" />
        <input v-model="pkg.skill_code" class="code-input" placeholder="skill_code（英文）"
               :disabled="!isNew" />
        <span v-if="!isNew && currentVersion" class="ver"
              :class="`vs-${currentVersion.status}`">
          v{{ currentVersion.version }} · {{ verLabel(currentVersion.status) }}</span>
      </div>
      <div class="acts">
        <input v-model="changelog" class="changelog" placeholder="变更说明（可选）" />
        <button class="primary" @click="saveDraft">{{ isNew ? "创建技能" : "存为新草稿" }}</button>
        <button v-if="!isNew && currentVersion?.status === 'draft'" class="confirm"
                @click="publish">发布 v{{ currentVersion.version }}</button>
        <button v-if="!isNew"
                @click="downloadFile(api.skillExportUrl(code), `${code}.yaml`)">导出 YAML</button>
      </div>
    </div>

    <!-- new-skill guidance -->
    <div v-if="isNew && !pkg.fields.length" class="guide">
      <span class="step"><b>1</b> 起草字段：样本预标注 / 自然语言描述 / 表格清单，三选一</span>
      <span class="arrow">→</span>
      <span class="step"><b>2</b> 核对/修改字段与校验规则</span>
      <span class="arrow">→</span>
      <span class="step"><b>3</b> 试跑验证效果，创建并发布</span>
    </div>

    <div class="cols">
      <!-- left 2/3: the contract definition -->
      <div class="def-col">
        <section class="card">
          <header class="card-head">
            <h3>字段定义 <span class="count dim" v-if="pkg.fields.length">{{ pkg.fields.length }}</span></h3>
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
            <button class="mini" @click="addField">＋ 手动</button>
          </header>

          <!-- natural-language draft panel: prefills fields, nothing saved
               until the user reviews and clicks save -->
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
            还没有字段。三种起草方式：「⚡ 样本预标注」上传真实样本让模型起草；
            「📝 描述生成」用一段话描述需求；「📊 表格导入」上传字段清单
            （表头：字段名/类型/说明/必填/枚举值/所属明细表，中英文均可）。
          </p>
          <div v-for="(f, i) in pkg.fields" :key="i" class="fcard">
            <div class="frow">
              <input v-model="f.name" placeholder="字段名（英文）" class="fname-in" />
              <select v-model="f.type">
                <option value="string">文本</option><option value="number">数字</option>
                <option value="date">日期</option><option value="enum">枚举</option>
                <option value="table">明细表</option>
              </select>
              <select v-model="f.mode">
                <option value="verbatim">原文抄录</option>
                <option value="inferred">模型推断</option>
              </select>
              <label class="chk"><input type="checkbox" v-model="f.required" />必填</label>
              <button class="mini danger" title="删除字段" @click="pkg.fields.splice(i, 1)">✕</button>
            </div>
            <input v-model="f.instruction" placeholder="抽取说明，如：发票右上角的发票号码" />
            <input v-if="f.type === 'enum'" :value="f.enum_values.join(',')"
                   placeholder="枚举值，逗号分隔"
                   @input="f.enum_values = splitCsv(($event.target as HTMLInputElement).value)" />
            <div v-if="f.type === 'table'" class="cols-editor">
              <span class="dim">列：</span>
              <input :value="f.columns.map(c => c.name).join(',')"
                     placeholder="列名，逗号分隔，如 name,qty,amount"
                     @input="f.columns = splitCsv(($event.target as HTMLInputElement).value)
                       .map(n => blankField(n))" />
            </div>
          </div>
        </section>

        <section class="card">
          <header class="card-head">
            <h3>校验规则 <span class="count dim" v-if="pkg.validators.length">{{ pkg.validators.length }}</span></h3>
            <button class="mini" @click="addValidator">＋ 规则</button>
          </header>
          <p v-if="!pkg.validators.length" class="dim pad">
            可选。规则失败的字段会标红并强制人审——如必填、正则格式、金额勾稽（合计=明细之和）。
          </p>
          <div v-for="(v, i) in pkg.validators" :key="i" class="vrow">
            <select v-model="v.type">
              <option value="required">必填</option>
              <option value="regex">正则</option>
              <option value="sum_equals">合计勾稽</option>
            </select>
            <template v-if="v.type !== 'sum_equals'">
              <select v-model="v.field">
                <option v-for="f in pkg.fields" :key="f.name" :value="f.name">{{ f.name }}</option>
              </select>
              <input v-if="v.type === 'regex'" v-model="v.pattern" placeholder="正则表达式" />
            </template>
            <template v-else>
              <select v-model="v.target">
                <option v-for="f in pkg.fields" :key="f.name" :value="f.name">{{ f.name }}</option>
              </select>
              <span class="dim">=</span>
              <input :value="v.parts.join(',')" placeholder="相加字段，逗号分隔"
                     @input="v.parts = splitCsv(($event.target as HTMLInputElement).value)" />
            </template>
            <button class="mini danger" @click="pkg.validators.splice(i, 1)">✕</button>
          </div>
        </section>

        <section class="card">
          <header class="card-head"><h3>策略与模型</h3></header>
          <div class="grid3 pad">
            <label>审核策略
              <select v-model="pkg.review_policy.mode">
                <option value="auto">自动（按置信）</option>
                <option value="always">全部人审</option>
                <option value="never">全部直通</option>
              </select>
            </label>
            <label>置信阈值（低于进人审）
              <select v-model.number="pkg.review_policy.confidence_threshold">
                <option :value="1">1</option><option :value="2">2</option><option :value="3">3</option>
              </select>
            </label>
            <label>解析器（空=自动路由）
              <select :value="pkg.parser ?? ''"
                      @change="pkg.parser = ($event.target as HTMLSelectElement).value || null">
                <option value="">自动</option>
                <option v-for="p in PARSERS" :key="p" :value="p">{{ p }}</option>
              </select>
            </label>
            <label>抽取模型（空=平台默认）
              <input v-model="pkg.model_binding.extractor" placeholder="如 qwen" /></label>
            <label>备用模型（fallback）
              <input :value="pkg.model_binding.fallback ?? ''" placeholder="可空"
                     @input="pkg.model_binding.fallback = ($event.target as HTMLInputElement).value || null" /></label>
            <label>挑战者模型（不一致标人审）
              <input :value="pkg.model_binding.challenger ?? ''" placeholder="可空"
                     @input="pkg.model_binding.challenger = ($event.target as HTMLInputElement).value || null" /></label>
          </div>
          <label class="block pad">补充规则（自由文本，进提示词）
            <textarea v-model="pkg.additional_rules" rows="2"
                      placeholder="如：金额一律保留两位小数；日期统一 YYYY-MM-DD"></textarea>
          </label>
        </section>
      </div>

      <!-- right 1/3: workbench -->
      <div class="side-col">
        <section class="card">
          <header class="card-head"><h3>试运行（dry-run）</h3></header>
          <div class="pad col-gap">
            <p class="dim">当前定义在一份样本上试跑，可多模型并排对比，不产生任务。</p>
            <input v-model="dryProviders" placeholder="模型列表，逗号分隔；空=默认" />
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
          </div>
        </section>

        <template v-if="!isNew">
          <section class="card">
            <header class="card-head"><h3>金样本回归（发布门禁）</h3></header>
            <div class="pad col-gap">
              <p class="dim">挂固定样本＋期望值，发布前跑回归防退化。</p>
              <label class="file-btn"><input type="file" hidden @change="pickGolden" />
                <span class="btn-like">{{ goldenFile ? goldenFile.name : "选择样本文件" }}</span></label>
              <textarea v-model="goldenExpected" rows="3"
                        placeholder='期望值 JSON，如 {"invoice_no": "INV-1"}'></textarea>
              <button :disabled="!goldenFile" @click="addGolden">挂载金样本</button>
              <button v-if="currentVersion" @click="goldenCheck" :disabled="checking">
                {{ checking ? "⏳ 回归中…" : `对 v${currentVersion.version} 跑回归` }}</button>
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
            </div>
          </section>

          <section class="card">
            <header class="card-head"><h3>历史版本</h3></header>
            <table class="vers">
              <tbody>
                <tr v-for="v in versions" :key="v.version">
                  <td>v{{ v.version }}</td>
                  <td><span :class="`vs-${v.status}`">{{ verLabel(v.status) }}</span></td>
                  <td class="dim log-cell" :title="v.changelog">{{ v.changelog }}</td>
                  <td><button v-if="v.status !== 'published'" class="mini"
                              @click="publishVersion(v.version)">发布</button></td>
                </tr>
              </tbody>
            </table>
          </section>
        </template>
      </div>
    </div>
  </main>
  <main v-else class="editor"><Skeleton :rows="8" /></main>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { api, downloadFile, type DryRunEntry, type FieldCell, type FieldSpec,
         type SkillPackage } from "../api";
import Skeleton from "../components/Skeleton.vue";
import { toast } from "../toast";

const props = defineProps<{ code: string }>();
const router = useRouter();
const isNew = computed(() => props.code === "new");

const PARSERS = ["pdfplumber", "markitdown", "glm-ocr-cloud", "rapidocr", "monkeyocr", "ofd"];

const pkg = ref<SkillPackage | null>(null);
const versions = ref<{ version: number; status: string; changelog: string }[]>([]);
const changelog = ref("");
const currentVersion = computed(() => versions.value[versions.value.length - 1] ?? null);

function blankPkg(): SkillPackage {
  return { skill_code: "", name: "", kind: "extract", doc_type_hint: "",
           system_prompt: "", fields: [], few_shot: [], validators: [],
           review_policy: { mode: "auto", confidence_threshold: 2 },
           model_binding: { extractor: "", fallback: null, challenger: null },
           parser: null, additional_rules: "" };
}
function blankField(name = ""): FieldSpec {
  return { name, type: "string", instruction: "", mode: "verbatim", required: false,
           anchor_hints: [], enum_values: [], columns: [] };
}
const splitCsv = (v: string) => v.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
const verLabel = (s: string) =>
  ({ draft: "草稿", published: "已发布", archived: "已归档" }[s] ?? s);

async function load() {
  if (isNew.value) {
    pkg.value = blankPkg();
    versions.value = [];
    return;
  }
  try {
    const d = await api.skillDetail(props.code);
    pkg.value = d.latest_package ?? { ...blankPkg(), skill_code: d.skill_code, name: d.name };
    versions.value = d.versions;
  } catch (e) { toast.error(e); }
}
watch(() => props.code, load, { immediate: true });

function addField() { pkg.value?.fields.push(blankField()); }
function addValidator() {
  pkg.value?.validators.push({ type: "required", field: pkg.value.fields[0]?.name ?? "",
                               pattern: null, target: null, parts: [] });
}

async function saveDraft() {
  if (!pkg.value) return;
  if (!pkg.value.skill_code) { toast.error("请填写 skill_code"); return; }
  if (!pkg.value.fields.length) { toast.error("至少定义一个字段"); return; }
  try {
    if (isNew.value) {
      await api.skillCreate(pkg.value, changelog.value);
      toast.ok("技能已创建（v1 草稿）");
      router.push(`/skills/${pkg.value.skill_code}`);
    } else {
      const r = await api.skillNewDraft(props.code, pkg.value, changelog.value);
      toast.ok(`已存为 v${r.version} 草稿`);
      changelog.value = "";
      await load();
    }
  } catch (e) { toast.error(e); }
}
async function publish() {
  if (currentVersion.value) await publishVersion(currentVersion.value.version);
}
async function publishVersion(v: number) {
  try {
    await api.skillPublish(props.code, v);
    toast.ok(`v${v} 已发布（旧版本自动归档）`);
    await load();
  } catch (e) { toast.error(e); }
}

// —— studio tools ——
const probing = ref(false);

/** merge drafted fields into the editor (dedup by name); nothing is persisted
 * until the user reviews and saves — the "prefill, human confirms" contract */
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
    const n = mergeDraft(r.fields, r.doc_type);
    toast.ok(`预标注完成：新增 ${n} 个字段草稿（${r.provider_used}），请核对后保存`);
  } catch (e) { toast.error(e); }
  finally { probing.value = false; (ev.target as HTMLInputElement).value = ""; }
}

// natural-language rule import
const textPanel = ref(false);
const draftText = ref("");
const drafting = ref(false);
async function draftFromText() {
  if (!draftText.value.trim()) return;
  drafting.value = true;
  try {
    const r = await api.skillDraftFromText(draftText.value);
    const n = mergeDraft(r.fields, r.doc_type);
    toast.ok(`已从描述起草 ${n} 个字段（${r.provider_used}），请核对后保存`);
    textPanel.value = false;
    draftText.value = "";
  } catch (e) { toast.error(e); }
  finally { drafting.value = false; }
}

// spreadsheet rule import (pure parsing, no tokens)
async function tableImport(ev: Event) {
  const file = (ev.target as HTMLInputElement).files?.[0];
  if (!file) return;
  try {
    const r = await api.skillDraftFromTable(file);
    const n = mergeDraft(r.fields);
    toast.ok(`表格导入完成：新增 ${n} 个字段。可点「✨ AI 补全说明」扩写规则，核对后保存`);
  } catch (e) { toast.error(e); }
  finally { (ev.target as HTMLInputElement).value = ""; }
}

// LLM instruction enrichment: expands terse notes into full extraction rules
// (keyword hunt -> cleaning -> output format); prefill-only, user confirms
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

// —— golden samples ——
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
  if (!currentVersion.value) return;
  checking.value = true;
  goldenReport.value = null;
  try { goldenReport.value = await api.goldenCheck(props.code, currentVersion.value.version); }
  catch (e) { toast.error(e); }
  finally { checking.value = false; }
}
</script>

<style scoped>
.editor { padding: 0 0 24px; }
.toolbar { display: flex; gap: 14px; align-items: center; padding: 10px 20px;
  background: var(--bg-panel); border-bottom: 1px solid var(--border);
  position: sticky; top: 46px; z-index: 15; flex-wrap: wrap; }
.back { font-size: 18px; color: var(--text-dim); padding: 0 4px; }
.back:hover { color: var(--accent); }
.ident { display: flex; gap: 8px; align-items: center; flex: 1; min-width: 280px; }
.name-input { font-size: 15px; font-weight: 600; max-width: 200px; }
.code-input { font-family: Consolas, monospace; max-width: 200px; font-size: 13px; }
.ver { font-size: 12px; white-space: nowrap; border-radius: 4px; padding: 2px 8px;
  border: 1px solid currentColor; }
.acts { display: flex; gap: 8px; align-items: center; }
.changelog { max-width: 180px; }
.guide { display: flex; gap: 14px; align-items: center; justify-content: center;
  padding: 14px 20px; margin: 16px 20px 0; background: var(--bg-panel);
  border: 1px dashed var(--border); border-radius: 10px; flex-wrap: wrap;
  font-size: 13px; color: var(--text-dim); }
.guide b { display: inline-flex; width: 20px; height: 20px; border-radius: 50%;
  background: var(--accent); color: var(--accent-text); align-items: center;
  justify-content: center; margin-right: 6px; }
.arrow { color: var(--accent); }
.cols { display: grid; grid-template-columns: minmax(0, 2fr) minmax(320px, 1fr);
  gap: 16px; padding: 16px 20px 0; align-items: start; }
.def-col, .side-col { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.card { background: var(--bg-panel); border: 1px solid var(--border);
  border-radius: 10px; overflow: hidden; }
.card-head { display: flex; gap: 10px; align-items: center; padding: 10px 14px;
  border-bottom: 1px solid var(--border); }
.card-head h3 { font-size: 14px; color: var(--accent); margin: 0; flex: 1; }
.count { font-weight: 400; margin-left: 6px; }
.pad { padding: 12px 14px; margin: 0; }
.col-gap { display: flex; flex-direction: column; gap: 10px; }
.text-panel { border-bottom: 1px solid var(--border); padding: 12px 14px;
  display: flex; flex-direction: column; gap: 8px; background: var(--bg-raised); }
.text-panel-act { display: flex; gap: 12px; align-items: center;
  justify-content: space-between; font-size: 12px; }
.fcard { border-bottom: 1px solid var(--border); padding: 10px 14px;
  display: flex; flex-direction: column; gap: 8px; }
.fcard:last-child { border-bottom: none; }
.frow { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.fname-in { max-width: 180px; font-family: Consolas, monospace; }
select { background: var(--bg-raised); color: var(--text); border: 1px solid var(--border);
  border-radius: 6px; padding: 5px 8px; }
.chk { display: flex; gap: 4px; align-items: center; font-size: 13px;
  color: var(--text-dim); white-space: nowrap; }
.chk input { width: auto; }
.mini { padding: 2px 10px; font-size: 12px; }
.vrow { display: flex; gap: 8px; align-items: center; padding: 8px 14px;
  border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.vrow:last-child { border-bottom: none; }
.grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.grid3 label, .block { display: flex; flex-direction: column; gap: 4px; font-size: 13px;
  color: var(--text-dim); }
.file-btn .btn-like { border: 1px solid var(--border); background: var(--bg-raised);
  border-radius: 6px; padding: 6px 14px; cursor: pointer; display: inline-block;
  font-size: 13px; }
.file-btn.slim .btn-like { padding: 3px 10px; font-size: 12px; }
.file-btn .btn-like:hover { border-color: var(--accent); }
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
.vers { font-size: 13px; border-collapse: collapse; width: 100%; }
.vers td { padding: 6px 14px; border-bottom: 1px solid var(--border); }
.vers tr:last-child td { border-bottom: none; }
.log-cell { max-width: 160px; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.vs-published { color: var(--green); }
.vs-draft { color: var(--accent); }
.vs-archived { color: var(--text-dim); }
.dim { color: var(--text-dim); }
@media (max-width: 1100px) { .cols { grid-template-columns: 1fr; } }
</style>
