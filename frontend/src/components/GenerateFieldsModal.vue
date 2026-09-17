<template>
  <AppModal @close="$emit('close')">
    <div class="gen-modal">
      <h3>✨ 自动生成字段</h3>
      <p class="dim">样本预标注 + 描述 二合一：字段范围以描述为准，字段名、类型和示例值参考样本。</p>

      <textarea v-model="description" rows="3"
                placeholder="描述要抽什么。例：抽取发票号（去空格）、开票日期（YYYY-MM-DD）、含税总金额（两位小数）"></textarea>
      <label v-if="samples.length" class="row">
        <span>样本</span>
        <select v-model="sampleId">
          <option value="">不使用样本</option>
          <option v-for="s in samples" :key="s.id" :value="s.id">{{ s.file_name }}</option>
        </select>
      </label>

      <div class="gen-acts">
        <button class="primary" :disabled="generating
                || (!description.trim() && !sampleId)" @click="generate">
          {{ generating ? "⏳ 生成中…" : (suggested.length ? "🔄 重新生成" : "生成建议字段") }}</button>
      </div>

      <template v-if="suggested.length">
        <div class="suggest-head">
          <strong>建议字段（{{ suggested.length }}）</strong>
          <label class="repl"><input type="checkbox" v-model="replaceAll" />
            替换现有字段（不勾选则追加，同名跳过）</label>
        </div>
        <ul class="suggest-list">
          <li v-for="f in suggested" :key="f.name">
            <code>{{ f.name }}</code>
            <span class="dim">{{ f.type }}{{ f.required ? " · 必填" : "" }}</span>
            <span class="ex" v-if="examples[f.name]">示例：{{ examples[f.name] }}</span>
          </li>
        </ul>
        <div class="gen-acts">
          <button @click="$emit('close')">取消</button>
          <button class="primary" @click="apply">应用字段</button>
        </div>
      </template>
      <div class="gen-acts">
        <button @click="$emit('close')">关闭</button>
      </div>
      <p v-if="error" class="err">{{ error }}</p>
    </div>
  </AppModal>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, type FieldSpec } from "../api";
import AppModal from "./AppModal.vue";
import { toast } from "../toast";

const props = defineProps<{ skillCode?: string }>();
const emit = defineEmits<{
  (e: "close"): void;
  (e: "apply", fields: FieldSpec[], replaceAll: boolean): void;
}>();

const samples = ref<{ id: string; file_name: string }[]>([]);
const sampleId = ref("");
const description = ref("");
const replaceAll = ref(false);
const generating = ref(false);
const suggested = ref<FieldSpec[]>([]);
const examples = ref<Record<string, string>>({});
const error = ref("");

onMounted(async () => {
  try {
    samples.value = (await api.studioSamples()).samples;
    if (samples.value.length) sampleId.value = samples.value[0].id;
  } catch { /* listing is optional; the description path still works */ }
});

async function generate() {
  generating.value = true;
  error.value = "";
  try {
    const r = await api.generateFields({
      sample_id: sampleId.value || undefined,
      description: description.value || undefined,
    });
    suggested.value = r.fields;
    examples.value = r.examples;
  } catch (e) { error.value = String(e instanceof Error ? e.message : e); }
  finally { generating.value = false; }
}

function apply() {
  if (!suggested.value.length) return;
  emit("apply", suggested.value, replaceAll.value);
  toast.ok(`已${replaceAll.value ? "替换为" : "追加"} ${suggested.value.length} 个建议字段`);
  emit("close");
}
</script>

<style scoped>
textarea { width: 100%; margin: 8px 0; }
.row { display: flex; gap: 8px; align-items: center; margin: 6px 0; }
.row select { flex: 1; }
.gen-acts { display: flex; gap: 8px; justify-content: flex-end; margin: 10px 0; }
.suggest-head { display: flex; justify-content: space-between; align-items: center;
  margin-top: 6px; }
.repl { display: flex; gap: 6px; align-items: center; font-size: 13px; }
.suggest-list { list-style: none; margin: 8px 0; padding: 0;
  border: 1px solid var(--border); border-radius: 8px; max-height: 240px; overflow: auto; }
.suggest-list li { display: flex; gap: 10px; padding: 7px 10px;
  border-bottom: 1px solid var(--border); font-size: 13px; align-items: baseline; }
.suggest-list li:last-child { border-bottom: 0; }
.ex { margin-left: auto; color: var(--text-dim, #888);
  max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.err { color: var(--danger, #c0392b); }
</style>
