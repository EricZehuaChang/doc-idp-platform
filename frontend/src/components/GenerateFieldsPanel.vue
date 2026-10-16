<template>
  <section class="generate-panel" aria-label="生成建议字段">
    <header><div><strong>{{ suggested.length ? '确认建议字段' : '从样本生成字段' }}</strong>
      <p class="dim">{{ sampleId ? `参考样本：${sampleName || '当前样本'}` : '上传左侧样本，或直接描述要提取的字段' }}</p></div>
      <button v-if="existingCount" class="ghost" aria-label="收起生成建议" @click="emit('close')">×</button></header>
    <label class="description">提取要求 <span class="dim">（可选）</span>
      <textarea v-model="description" rows="3" placeholder="例如：提取发票号、开票日期、含税金额和商品明细" :disabled="generating"></textarea>
    </label>
    <div class="generate-actions">
      <span class="dim">建议不会自动覆盖现有字段</span>
      <button class="primary" :disabled="generating || (!sampleId && !description.trim())" @click="generate">
        {{ generating ? '正在生成建议…' : suggested.length ? '重新生成' : '生成建议字段' }}</button>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="suggested.length">
      <div class="suggest-head">
        <label><input type="checkbox" :checked="selected.length === suggested.length" @change="toggleAll" />
          已选 {{ selected.length }} / {{ suggested.length }} 项</label>
        <label v-if="existingCount" class="dim"><input type="checkbox" v-model="replaceAll" /> 替换现有字段</label>
      </div>
      <ul class="suggest-list">
        <li v-for="(field, i) in suggested" :key="i">
          <label><input type="checkbox" :value="i" v-model="selected" />
            <span class="suggest-name"><strong>{{ field.name }}</strong><small class="dim">{{ field.instruction }}</small></span>
            <span class="type">{{ typeLabel(field.type) }}</span>
            <span class="example" :title="example(field.name)">{{ example(field.name) || '—' }}</span></label>
        </li>
      </ul>
      <div class="generate-actions">
        <span class="dim">{{ existingCount && !replaceAll ? '追加到当前清单，同名字段会跳过' : '添加后可继续编辑，也可撤销' }}</span>
        <button class="primary" :disabled="!selected.length || generating" @click="apply">{{ replaceAll && existingCount ? '替换为' : '添加' }} {{ selected.length }} 个字段</button>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { api, type FieldSpec } from "../api";
const props = defineProps<{ sampleId?: string; sampleName?: string; existingCount: number }>();
const emit = defineEmits<{
  (e: "apply", fields: FieldSpec[], replaceAll: boolean, examples: Record<string, unknown>): void;
  (e: "close"): void;
}>();
const description = ref("");
const generating = ref(false);
const suggested = ref<FieldSpec[]>([]);
const selected = ref<number[]>([]);
const examples = ref<Record<string, unknown>>({});
const replaceAll = ref(false);
const error = ref("");
let generation = 0;
function reset() { ++generation; generating.value = false; suggested.value = []; selected.value = []; examples.value = {}; error.value = ""; replaceAll.value = false; }
watch(() => props.sampleId, reset);
onBeforeUnmount(() => { ++generation; });
const typeLabel = (type: string) => ({ string: "文本", number: "数字", date: "日期", enum: "枚举", table: "数组" }[type] || type);
function example(name: string) {
  const value = examples.value[name];
  return value == null ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
}
function toggleAll() { selected.value = selected.value.length === suggested.value.length ? [] : suggested.value.map((_, i) => i); }
async function generate() {
  const request = ++generation;
  generating.value = true; error.value = "";
  try {
    const result = await api.generateFields({ sample_id: props.sampleId || undefined, description: description.value.trim() || undefined });
    if (request !== generation) return;
    suggested.value = result.fields;
    examples.value = result.examples;
    selected.value = result.fields.map((_, i) => i);
    if (!result.fields.length) error.value = "未生成字段，请补充提取要求后重试。";
  } catch (e) { if (request === generation) error.value = e instanceof Error ? e.message : String(e); }
  finally { if (request === generation) generating.value = false; }
}
function apply() {
  const fields = suggested.value.filter((_, i) => selected.value.includes(i));
  if (!fields.length) return;
  const pickedExamples = Object.fromEntries(fields.map(f => [f.name, examples.value[f.name]]));
  emit("apply", fields, !!props.existingCount && replaceAll.value, pickedExamples);
  reset();
}
</script>

<style scoped>
.generate-panel { border: 1px solid var(--border); border-radius: 10px; padding: 18px;
  background: var(--bg); min-width: 0; }
header { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
header p { font-size: 12px; margin: 7px 0 16px; overflow-wrap: anywhere; }
.description { display: block; font-size: 12px; }
textarea { display: block; width: 100%; margin: 8px 0 12px; resize: vertical; }
.generate-actions { display: flex; gap: 12px; justify-content: space-between; align-items: center; font-size: 12px; }
.generate-actions button { flex-shrink: 0; }
.suggest-head { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap;
  margin-top: 20px; font-size: 12px; }
.suggest-head label { display: flex; align-items: center; gap: 6px; }
.suggest-list { padding: 0; margin: 12px 0; list-style: none; border-top: 1px solid var(--border); }
.suggest-list li { border-bottom: 1px solid var(--border); }
.suggest-list li > label { display: grid; grid-template-columns: auto minmax(0,1fr) 40px minmax(60px,.6fr);
  gap: 10px; align-items: center; padding: 10px 0; font-size: 12px; cursor: pointer; }
.suggest-name { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.suggest-name strong { overflow-wrap: anywhere; }
.suggest-name small, .example { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.type, .dim { color: var(--text-dim); }
.example { color: var(--accent); }
.error { color: var(--red); font-size: 13px; }
@media(max-width: 600px) { .generate-panel { padding: 14px; } .generate-actions { flex-wrap: wrap; }
 .generate-actions button { margin-left: auto; } .suggest-list li > label { grid-template-columns: auto minmax(0,1fr) 34px; }
 .example { grid-column: 2 / 4; } }
</style>
