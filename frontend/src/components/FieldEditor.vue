<template>
  <div class="field-editor">
    <label>{{ $t('fieldEditor.field_name') }} <span class="required">*</span>
      <input v-model="draft.name" :placeholder="$t('fieldEditor.e_g_invoice_number')" @input="publish" />
    </label>
    <label>{{ $t('fieldEditor.field_type') }}
      <select v-model="draft.type" @change="changeType">
        <option value="string">{{ $t('fieldEditor.string') }}</option><option value="number">{{ $t('fieldEditor.number') }}</option>
        <option value="date">{{ $t('fieldEditor.date') }}</option><option value="enum">{{ $t('fieldEditor.enum') }}</option>
        <option v-if="!isColumn" value="table">{{ $t('fieldEditor.detail_table_array') }}</option>
      </select>
    </label>
    <label class="wide">{{ $t('fieldEditor.extraction_instructions') }}
      <textarea v-model="draft.instruction" rows="3" :placeholder="$t('fieldEditor.describe_what_to_extract_how_to_clean_it_and')" @input="publish"></textarea>
    </label>
    <label>{{ $t('fieldEditor.extraction_mode') }}
      <select v-model="draft.mode" @change="publish">
        <option value="verbatim">{{ $t('fieldEditor.verbatim_transcription') }}</option><option value="inferred">{{ $t('fieldEditor.model_inference') }}</option>
      </select>
    </label>
    <label class="check"><input v-model="draft.required" type="checkbox" @change="publish" /> {{ $t('fieldEditor.required_field') }}</label>
    <label v-if="draft.type === 'enum'" class="wide">{{ $t('fieldEditor.enum_values') }}
      <input v-model="enumText" :placeholder="$t('fieldEditor.comma_separated_e_g_usd_eur_jpy')" @input="setList('enum_values', enumText)" />
    </label>
    <label v-if="draft.type === 'date'">{{ $t('fieldEditor.date_format') }}
      <select v-model="datePattern" @change="publish">
        <option value="">{{ $t('fieldEditor.keep_original_wording') }}</option><option value="YYYY-MM-DD">2026-09-17</option>
        <option value="YYYY/MM/DD">2026/09/17</option><option value="YYYYMMDD">20260917</option>
        <option value="YYYY-MM-DD HH:mm:ss">2026-09-17 14:30:00</option>
        <option value="DD/MM/YYYY">17/09/2026</option><option value="MM/DD/YYYY">09/17/2026</option>
      </select>
    </label>
    <label v-if="draft.type === 'number'">{{ $t('fieldEditor.decimal_places') }}
      <select v-model="decimalPlaces" @change="publish">
        <option :value="null">{{ $t('fieldEditor.keep_original_value') }}</option><option :value="0">{{ $t('fieldEditor.0_places_integer') }}</option>
        <option :value="1">{{ $t('fieldEditor.1_place') }}</option><option :value="2">{{ $t('fieldEditor.2_places_common_for_amounts') }}</option>
        <option :value="3">{{ $t('fieldEditor.3_places') }}</option><option :value="4">{{ $t('fieldEditor.4_places') }}</option>
      </select>
    </label>
    <label :class="{ wide: draft.type !== 'date' && draft.type !== 'number' }">{{ $t('fieldEditor.location_hint') }}
      <input v-model="anchorText" :placeholder="$t('fieldEditor.comma_separated_e_g_top_right_footer')" @input="setList('anchor_hints', anchorText)" />
    </label>
    <details v-if="!isColumn" class="wide rule-details">
      <summary>{{ $t('fieldEditor.extraction_rules') }} <span class="dim">{{ draft.rule ? $t('fieldEditor.configured') : $t('fieldEditor.optional_used_by_rule_priority_channel') }}</span></summary>
      <div class="rule-fields">
        <label>{{ $t('fieldEditor.rule_type') }}
          <select :value="draft.rule?.kind ?? ''" @change="setRule(($event.target as HTMLSelectElement).value)">
            <option value="">{{ $t('fieldEditor.not_configured') }}</option><option v-if="draft.type !== 'table'" value="anchor">{{ $t('fieldEditor.key_value_anchor') }}</option>
            <option v-if="draft.type !== 'table'" value="regex">{{ $t('fieldEditor.regex') }}</option><option v-if="draft.type === 'table'" value="table">{{ $t('fieldEditor.header_match') }}</option>
          </select>
        </label>
        <template v-if="draft.rule">
          <template v-if="draft.rule.kind !== 'table'">
            <label>{{ $t('fieldEditor.label_words_comma_separated') }}<input v-model="labelText" @input="draft.rule.labels = split(labelText); publish()" /></label>
            <button type="button" class="ghost use-anchor" @click="useAnchors">{{ $t('fieldEditor.populated_from_location_hint') }}</button>
          </template>
          <label v-if="draft.rule.kind === 'regex'">{{ $t('fieldEditor.regex_exactly_one_capture_group') }}
            <input v-model="draft.rule.pattern" :placeholder="$t('fieldEditor.invoice_no_s_s')" @input="publish" />
          </label>
          <template v-if="draft.rule.kind === 'table'">
            <label>{{ $t('fieldEditor.sheet_name_optional') }}<input v-model="draft.rule.sheet_name" @input="publish" /></label>
            <label v-for="(col, i) in draft.columns" :key="i">{{ col.name || $t('fieldEditor.unnamed_column') }} {{ $t('fieldEditor.header_aliases_for') }}
              <input :value="aliasText[col.name] ?? ''" :placeholder="col.name" @input="setAliases(col.name, ($event.target as HTMLInputElement).value)" />
            </label>
            <p v-if="!draft.columns.length" class="dim">{{ $t('fieldEditor.after_adding_array_columns_you_can_configure_header_aliases') }}</p>
            <label>{{ $t('fieldEditor.stop_words') }}<input v-model="stopText" @input="draft.rule.stop_words = split(stopText); publish()" /></label>
          </template>
          <label>{{ $t('fieldEditor.if_the_rule_fails_to_extract') }}
            <select v-model="draft.rule.on_miss" @change="publish"><option value="model">{{ $t('fieldEditor.let_the_model_fill_it_in') }}</option><option value="review">{{ $t('fieldEditor.leave_blank_for_review') }}</option></select>
          </label>
        </template>
      </div>
    </details>
    <p class="wide editor-note">{{ $t('fieldEditor.changes_are_saved_together_with_the_entire_skill') }}</p>
  </div>
</template>

<script setup lang="ts">
import sourceMessages from "../i18n/locales/zh.json";
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
  draft.rule = kind ? { kind: kind as ExtractionRule["kind"], labels: [], pattern: "", column_aliases: {}, sheet_name: null, stop_words: [sourceMessages.fieldEditor.stop_word_total, sourceMessages.fieldEditor.stop_word_grand_total, "Total"], on_miss: "model" } : null;
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
