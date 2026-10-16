<template>
  <div class="field-editor">
    <label>字段名称 <span class="required">*</span>
      <input v-model="draft.name" placeholder="例如：invoice_number" @input="publish" />
    </label>
    <label>字段类型
      <select v-model="draft.type" @change="changeType">
        <option value="string">字符串</option><option value="number">数字</option>
        <option value="date">日期</option><option value="enum">枚举</option>
        <option v-if="!isColumn" value="table">明细表（数组）</option>
      </select>
    </label>
    <label class="wide">抽取说明
      <textarea v-model="draft.instruction" rows="3" placeholder="说明要提取什么、如何清洗，以及输出格式" @input="publish"></textarea>
    </label>
    <label>抽取模式
      <select v-model="draft.mode" @change="publish">
        <option value="verbatim">原文抄录</option><option value="inferred">模型推断</option>
      </select>
    </label>
    <label class="check"><input v-model="draft.required" type="checkbox" @change="publish" /> 必填字段</label>
    <label v-if="draft.type === 'enum'" class="wide">枚举值
      <input v-model="enumText" placeholder="逗号分隔，例如：USD,EUR,JPY" @input="setList('enum_values', enumText)" />
    </label>
    <label v-if="draft.type === 'date'">日期格式
      <select v-model="datePattern" @change="publish">
        <option value="">保持原文写法</option><option value="YYYY-MM-DD">2026-09-17</option>
        <option value="YYYY/MM/DD">2026/09/17</option><option value="YYYYMMDD">20260917</option>
        <option value="YYYY-MM-DD HH:mm:ss">2026-09-17 14:30:00</option>
        <option value="DD/MM/YYYY">17/09/2026</option><option value="MM/DD/YYYY">09/17/2026</option>
      </select>
    </label>
    <label v-if="draft.type === 'number'">小数位数
      <select v-model="decimalPlaces" @change="publish">
        <option :value="null">保持原值</option><option :value="0">0 位（整数）</option>
        <option :value="1">1 位</option><option :value="2">2 位（金额常用）</option>
        <option :value="3">3 位</option><option :value="4">4 位</option>
      </select>
    </label>
    <label :class="{ wide: draft.type !== 'date' && draft.type !== 'number' }">位置提示
      <input v-model="anchorText" placeholder="逗号分隔，例如：右上角、页脚" @input="setList('anchor_hints', anchorText)" />
    </label>
    <details v-if="!isColumn" class="wide rule-details">
      <summary>提取规则 <span class="dim">{{ draft.rule ? '已配置' : '可选 · 规则优先通道使用' }}</span></summary>
      <div class="rule-fields">
        <label>规则类型
          <select :value="draft.rule?.kind ?? ''" @change="setRule(($event.target as HTMLSelectElement).value)">
            <option value="">未配置</option><option v-if="draft.type !== 'table'" value="anchor">键值锚点</option>
            <option v-if="draft.type !== 'table'" value="regex">正则</option><option v-if="draft.type === 'table'" value="table">表头匹配</option>
          </select>
        </label>
        <template v-if="draft.rule">
          <template v-if="draft.rule.kind !== 'table'">
            <label>标签词（逗号分隔）<input v-model="labelText" @input="draft.rule.labels = split(labelText); publish()" /></label>
            <button type="button" class="ghost use-anchor" @click="useAnchors">从位置提示带入</button>
          </template>
          <label v-if="draft.rule.kind === 'regex'">正则（恰好一个捕获组）
            <input v-model="draft.rule.pattern" placeholder="发票号[:：]\s*(\S+)" @input="publish" />
          </label>
          <template v-if="draft.rule.kind === 'table'">
            <label>工作表名（可选）<input v-model="draft.rule.sheet_name" @input="publish" /></label>
            <label v-for="(col, i) in draft.columns" :key="i">{{ col.name || '未命名列' }} 的表头别名
              <input :value="aliasText[col.name] ?? ''" :placeholder="col.name" @input="setAliases(col.name, ($event.target as HTMLInputElement).value)" />
            </label>
            <p v-if="!draft.columns.length" class="dim">添加数组列后，可在这里配置各列的表头别名。</p>
            <label>停止词<input v-model="stopText" @input="draft.rule.stop_words = split(stopText); publish()" /></label>
          </template>
          <label>规则没取到时
            <select v-model="draft.rule.on_miss" @change="publish"><option value="model">交给模型补</option><option value="review">留空进复核</option></select>
          </label>
        </template>
      </div>
    </details>
    <p class="wide editor-note">改动随整个技能一起保存。</p>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import type { ExtractionRule, FieldOutputFormat, FieldSpec } from "../api";

const props = withDefaults(defineProps<{ field: FieldSpec; isColumn?: boolean }>(), { isColumn: false });
const emit = defineEmits<{ (e: "update", field: FieldSpec): void }>();
const copy = (field: FieldSpec): FieldSpec => JSON.parse(JSON.stringify(field));
const draft = reactive(copy(props.field));
const enumText = ref("");
const anchorText = ref("");
const labelText = ref("");
const stopText = ref("");
const aliasText = ref<Record<string, string>>({});
let lastPublished = "";

function syncText() {
  enumText.value = draft.enum_values.join(",");
  anchorText.value = draft.anchor_hints.join(",");
  labelText.value = draft.rule?.labels.join(",") ?? "";
  stopText.value = draft.rule?.stop_words.join(",") ?? "";
  aliasText.value = Object.fromEntries(Object.entries(draft.rule?.column_aliases ?? {}).map(([key, values]) => [key, values.join(",")]));
}
syncText();
// Ignore our own parent echo so a trailing comma and the input caret survive.
watch(() => props.field, (field) => {
  if (JSON.stringify(field) === lastPublished) return;
  Object.assign(draft, { rule: null, output_format: null }, copy(field));
  syncText();
}, { deep: true });

const split = (text: string) => text.replace(/，/g, ",").split(",").map((s) => s.trim()).filter(Boolean);
function publish() {
  const field = copy(draft);
  lastPublished = JSON.stringify(field);
  emit("update", field);
}
function setList(key: "enum_values" | "anchor_hints", value: string) {
  draft[key] = split(value);
  publish();
}
function changeType() {
  if (draft.type !== "table") draft.columns = [];
  if (draft.type !== "enum") { draft.enum_values = []; enumText.value = ""; }
  if (draft.type !== "date" && draft.type !== "number") draft.output_format = null;
  if (draft.type === "date" && draft.output_format?.decimal_places != null) draft.output_format = null;
  if (draft.type === "number" && draft.output_format?.date_pattern) draft.output_format = null;
  if (draft.rule && ((draft.type === "table") !== (draft.rule.kind === "table"))) draft.rule = null;
  publish();
}
const datePattern = computed({
  get: () => draft.output_format?.date_pattern ?? "",
  set: (value: string) => { draft.output_format = value ? { date_pattern: value as FieldOutputFormat["date_pattern"], decimal_places: null } : null; },
});
const decimalPlaces = computed({
  get: () => draft.output_format?.decimal_places ?? null,
  set: (value: number | null) => { draft.output_format = value === null ? null : { date_pattern: null, decimal_places: value }; },
});
function setRule(kind: string) {
  draft.rule = kind ? { kind: kind as ExtractionRule["kind"], labels: [], pattern: "", column_aliases: {}, sheet_name: null, stop_words: ["合计", "总计", "Total"], on_miss: "model" } : null;
  syncText();
  publish();
}
function useAnchors() {
  if (!draft.rule) return;
  draft.rule.labels = [...draft.anchor_hints];
  labelText.value = draft.rule.labels.join(",");
  publish();
}
function setAliases(name: string, value: string) {
  if (!draft.rule) return;
  aliasText.value[name] = value;
  draft.rule.column_aliases[name] = split(value);
  publish();
}
</script>

<style scoped>
.field-editor { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px 16px; }
label { display: flex; flex-wrap: wrap; gap: 6px; font-size: 12px; color: var(--text-dim); align-content: start; }
label input, label select, label textarea { width: 100%; min-width: 0; font-size: 13px; font-weight: 400; color: var(--text); }
label textarea { resize: vertical; }
.wide { grid-column: 1 / -1; }
.required { color: var(--red); }
.check { flex-direction: row; align-items: center; align-content: center; color: var(--text); padding-top: 20px; }
.check input { width: auto; }
.rule-details { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
.rule-details summary { cursor: pointer; font-size: 12px; }
.rule-details summary span { margin-left: 8px; font-size: 11px; }
.rule-fields { display: grid; gap: 12px; padding-top: 14px; }
.use-anchor { justify-self: start; font-size: 12px; }
.rule-fields p, .editor-note { margin: 0; font-size: 11px; line-height: 1.6; color: var(--text-dim); }
.dim { color: var(--text-dim); }
@media (max-width: 600px) { .field-editor { grid-template-columns: minmax(0, 1fr); } .check { padding-top: 0; } }
</style>
