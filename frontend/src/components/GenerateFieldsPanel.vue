<template>
  <section class="generate-panel" :aria-label="$t('generateFieldsPanel.generate_suggested_fields')">
    <header><div><strong>{{ suggested.length ? $t('generateFieldsPanel.confirm_suggested_fields') : $t('generateFieldsPanel.generate_fields_from_sample') }}</strong>
      <p class="dim">{{ sampleId ? $t('generateFieldsPanel.reference_sample', { name: sampleName || $t('generateFieldsPanel.current_sample') }) : $t('generateFieldsPanel.upload_a_sample_on_the_left_or_directly_describe') }}</p></div>
      <button v-if="existingCount" class="ghost" :aria-label="$t('generateFieldsPanel.collapse_suggestions')" @click="emit('close')">×</button></header>
    <label class="description">{{ $t('generateFieldsPanel.extraction_requirements') }} <span class="dim">{{ $t('generateFieldsPanel.optional') }}</span>
      <textarea v-model="description" rows="3" :placeholder="$t('generateFieldsPanel.e_g_extract_invoice_number_invoice_date_amount_including')" :disabled="generating"></textarea>
    </label>
    <div class="generate-actions">
      <span class="dim">{{ $t('generateFieldsPanel.suggestions_will_not_automatically_overwrite_existing_fields') }}</span>
      <button class="primary" :disabled="generating || (!sampleId && !description.trim())" @click="generate">
        {{ generating ? $t('generateFieldsPanel.generating_suggestions') : suggested.length ? $t('generateFieldsPanel.regenerate') : $t('generateFieldsPanel.generate_suggested_fields') }}</button>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="suggested.length">
      <div class="suggest-head">
        <label><input type="checkbox" :checked="selected.length === suggested.length" @change="toggleAll" />
          {{ $t('generateFieldsPanel.selected') }} {{ selected.length }} / {{ suggested.length }} {{ $t('generateFieldsPanel.items') }}</label>
        <label v-if="existingCount" class="dim"><input type="checkbox" v-model="replaceAll" /> {{ $t('generateFieldsPanel.replace_existing_fields') }}</label>
      </div>
      <ul class="suggest-list">
        <li v-for="(field, i) in suggested" :key="i">
          <label><input type="checkbox" :value="i" v-model="selected" />
            <span class="suggest-name"><strong>{{ field.name }}</strong><small class="dim">{{ field.instruction }}</small></span>
            <span class="type">{{ translateMessage(typeLabel(field.type)) }}</span>
            <span class="example" :title="example(field.name)">{{ example(field.name) || '—' }}</span></label>
        </li>
      </ul>
      <div class="generate-actions">
        <span class="dim">{{ existingCount && !replaceAll ? $t('generateFieldsPanel.append_to_current_list_fields_with_the_same_name') : $t('generateFieldsPanel.you_can_continue_editing_after_adding_or_undo') }}</span>
        <button class="primary" :disabled="!selected.length || generating" @click="apply">{{ replaceAll && existingCount ? $t('generateFieldsPanel.replace_with') : $t('generateFieldsPanel.add') }} {{ selected.length }} {{ $t('generateFieldsPanel.fields') }}</button>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
const { t } = useI18n();

import { onBeforeUnmount, ref, watch } from "vue";
import { api, type FieldSpec } from "../api";
import { translateMessage } from "../i18n";
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
const TYPE_KEYS: Record<string, string> = { string: "generateFieldsPanel.type_text", number: "fieldCard.type_number", date: "fieldCard.type_date", enum: "fieldCard.type_enum", table: "fieldCard.type_table" };
const typeLabel = (type: string) => TYPE_KEYS[type] ? { key: TYPE_KEYS[type] } : type;
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
    if (!result.fields.length) error.value = t('generateFieldsPanel.no_fields_generated_please_add_extraction_requirements_and_try');
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
