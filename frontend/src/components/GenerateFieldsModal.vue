<template>
  <AppModal @close="$emit('close')">
    <div class="gen-modal">
      <h3>{{ $t('generateFieldsModal.auto_generate_fields') }}</h3>
      <p class="dim">{{ $t('generateFieldsModal.sample_pre_labeling_description_combined_field_scope_follows_the') }}</p>

      <textarea v-model="description" rows="3"
                :placeholder="$t('generateFieldsModal.describe_what_to_extract_e_g_extract_invoice_number')"></textarea>
      <label v-if="samples.length" class="row">
        <span>{{ $t('generateFieldsModal.sample') }}</span>
        <select v-model="sampleId">
          <option value="">{{ $t('generateFieldsModal.no_sample') }}</option>
          <option v-for="s in samples" :key="s.id" :value="s.id">{{ s.file_name }}</option>
        </select>
      </label>

      <div class="gen-acts">
        <button class="primary" :disabled="generating
                || (!description.trim() && !sampleId)" @click="generate">
          {{ generating ? $t('generateFieldsModal.generating') : (suggested.length ? $t('generateFieldsModal.regenerate') : $t('generateFieldsModal.generate_suggested_fields')) }}</button>
      </div>

      <template v-if="suggested.length">
        <div class="suggest-head">
          <strong>{{ $t('generateFieldsModal.suggested_fields') }}{{ suggested.length }}）</strong>
          <label class="repl"><input type="checkbox" v-model="replaceAll" />
            {{ $t('generateFieldsModal.replace_existing_fields_unchecked_appends_skipping_same_names') }}</label>
        </div>
        <ul class="suggest-list">
          <li v-for="f in suggested" :key="f.name">
            <code>{{ f.name }}</code>
            <span class="dim">{{ f.type }}{{ f.required ? $t('generateFieldsModal.required') : "" }}</span>
            <span class="ex" v-if="examples[f.name]">{{ $t('generateFieldsModal.example') }}{{ examples[f.name] }}</span>
          </li>
        </ul>
        <div class="gen-acts">
          <button @click="$emit('close')">{{ $t('generateFieldsModal.cancel') }}</button>
          <button class="primary" @click="apply">{{ $t('generateFieldsModal.apply_fields') }}</button>
        </div>
      </template>
      <div class="gen-acts">
        <button @click="$emit('close')">{{ $t('generateFieldsModal.close') }}</button>
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
  toast.ok({ key: replaceAll.value ? "generateFieldsModal.replaced_fields" : "generateFieldsModal.appended_fields", params: { count: suggested.value.length } });
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
.err { color: var(--danger); }
</style>
