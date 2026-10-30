<template>
  <!-- card-style field editor (Insavlo interaction): all edits go through this
       modal; the field cards themselves are read-only presentation -->
  <div class="mask" @click.self="$emit('cancel')">
    <div class="modal">
      <header>
        <strong>{{ isNew ? $t('fieldEditModal.add_field') : $t('fieldEditModal.edit_field') }}</strong>
        <button class="ghost x" @click="$emit('cancel')">✕</button>
      </header>

      <label>{{ $t('fieldEditModal.field_name') }}
        <input v-model="draft.name" :placeholder="$t('fieldEditModal.e_g_invoice_number')" autofocus />
        <span class="hint dim">{{ $t('fieldEditModal.lowercase_letters_numbers_and_underscores_recommended_used_as_the') }}</span>
      </label>

      <label>{{ $t('fieldEditModal.field_type') }}
        <select v-model="draft.type">
          <option value="string">{{ $t('fieldEditModal.string') }}</option>
          <option value="number">{{ $t('fieldEditModal.number') }}</option>
          <option value="date">{{ $t('fieldEditModal.date') }}</option>
          <option value="enum">{{ $t('fieldEditModal.enum') }}</option>
          <option v-if="!isColumn" value="table">{{ $t('fieldEditModal.detail_table_array') }}</option>
        </select>
      </label>

      <label>{{ $t('fieldEditModal.extraction_mode') }}
        <select v-model="draft.mode">
          <option value="verbatim">{{ $t('fieldEditModal.verbatim_transcription_value_must_be_locatable_in_the_source') }}</option>
          <option value="inferred">{{ $t('fieldEditModal.model_inference_judgment_classification_derivation_with_rationale') }}</option>
        </select>
      </label>

      <label>{{ $t('fieldEditModal.description') }}
        <textarea v-model="draft.instruction" rows="5"
                  :placeholder="$t('fieldEditModal.extraction_instructions_find_keywords_cleaning_rules_output_format')"></textarea>
        <span class="hint dim">{{ $t('fieldEditModal.include_extraction_rules_and_any_special_notes_save_and') }}</span>
      </label>

      <label v-if="draft.type === 'enum'">{{ $t('fieldEditModal.enum_values') }}
        <input :value="draft.enum_values.join(',')" :placeholder="$t('fieldEditModal.comma_separated_e_g_usd_eur_jpy')"
               @input="draft.enum_values = split(($event.target as HTMLInputElement).value)" />
      </label>

      <!-- 9.15 R11: finite display format for dates / numbers. The value is
           formatted after confidence scoring; the raw reading stays in $raw. -->
      <label v-if="draft.type === 'date'">{{ $t('fieldEditModal.output_format') }}
        <select v-model="datePattern">
          <option value="">{{ $t('fieldEditModal.keep_original_wording') }}</option>
          <option value="YYYY-MM-DD">2026-09-17</option>
          <option value="YYYY/MM/DD">2026/09/17</option>
          <option value="YYYYMMDD">20260917</option>
          <option value="YYYY-MM-DD HH:mm:ss">2026-09-17 14:30:00</option>
          <option value="DD/MM/YYYY">17/09/2026</option>
          <option value="MM/DD/YYYY">09/17/2026</option>
        </select>
        <span class="hint dim">{{ $t('fieldEditModal.if_parsing_fails_keep_the_original_text_mark_it') }}</span>
      </label>
      <label v-if="draft.type === 'number'">{{ $t('fieldEditModal.decimal_places') }}
        <select v-model.number="decimalPlaces">
          <option :value="null">{{ $t('fieldEditModal.keep_original_value') }}</option>
          <option :value="0">{{ $t('fieldEditModal.0_places_integer') }}</option>
          <option :value="1">{{ $t('fieldEditModal.1_place') }}</option>
          <option :value="2">{{ $t('fieldEditModal.2_places_common_for_amounts') }}</option>
          <option :value="3">{{ $t('fieldEditModal.3_places') }}</option>
          <option :value="4">{{ $t('fieldEditModal.4_places') }}</option>
        </select>
        <span class="hint dim">{{ $t('fieldEditModal.quantize_with_rounding_round_half_up_currency_symbols_and') }}</span>
      </label>

      <label>{{ $t('fieldEditModal.location_hint') }}
        <input :value="draft.anchor_hints.join(',')" :placeholder="$t('fieldEditModal.e_g_top_right_footer')"
               @input="draft.anchor_hints = split(($event.target as HTMLInputElement).value)" />
        <span class="hint dim">{{ $t('fieldEditModal.optional_hint_about_the_field_s_typical_location_in') }}</span>
      </label>

      <!-- R1 (2026-09-22): deterministic rule for the rules_first channel -->
      <fieldset v-if="!isColumn" class="rule-box"><legend>{{ $t('fieldEditModal.extraction_rule_used_by_the_rule_priority_channel') }}</legend>
        <select :value="draft.rule?.kind ?? ''" @change="setRule(($event.target as HTMLSelectElement).value)" :aria-label="$t('fieldEditModal.extraction_rule_type')">
          <option value="">{{ $t('fieldEditModal.not_configured') }}</option><option v-if="draft.type !== 'table'" value="anchor">{{ $t('fieldEditModal.key_value_anchor') }}</option><option v-if="draft.type !== 'table'" value="regex">{{ $t('fieldEditModal.regex') }}</option><option v-if="draft.type === 'table'" value="table">{{ $t('fieldEditModal.header_match') }}</option>
        </select>
        <template v-if="draft.rule">
          <label v-if="draft.rule.kind !== 'table'">{{ $t('fieldEditModal.label_words_comma_separated') }}<input :value="draft.rule.labels.join(',')" @input="draft.rule.labels = split(($event.target as HTMLInputElement).value)" /></label>
          <button v-if="draft.rule.kind !== 'table'" class="ghost" @click="draft.rule.labels = [...draft.anchor_hints]">{{ $t('fieldEditModal.populated_from_location_hint') }}</button>
          <label v-if="draft.rule.kind === 'regex'">{{ $t('fieldEditModal.regex_exactly_one_capture_group') }}<input v-model="draft.rule.pattern" :placeholder="$t('fieldEditModal.invoice_no_s_s')" /></label>
          <template v-if="draft.rule.kind === 'table'">
            <label>{{ $t('fieldEditModal.sheet_name_optional') }}<input v-model="draft.rule.sheet_name" /></label>
            <label v-for="col in draft.columns" :key="col.name">{{ col.name }} {{ $t('fieldEditModal.header_aliases_for') }}<input :value="(draft.rule.column_aliases[col.name] ?? []).join(',')" :placeholder="col.name" @input="draft.rule.column_aliases[col.name] = split(($event.target as HTMLInputElement).value)" /></label>
            <p v-if="!draft.columns.length" class="dim">{{ $t('fieldEditModal.after_saving_the_table_and_adding_columns_you_can') }}</p>
            <label>{{ $t('fieldEditModal.stop_words') }}<input :value="draft.rule.stop_words.join(',')" @input="draft.rule.stop_words = split(($event.target as HTMLInputElement).value)" /></label>
          </template>
          <label>{{ $t('fieldEditModal.if_the_rule_fails_to_extract') }}<select v-model="draft.rule.on_miss"><option value="model">{{ $t('fieldEditModal.let_the_model_fill_it_in') }}</option><option value="review">{{ $t('fieldEditModal.leave_blank_for_review') }}</option></select></label>
        </template>
      </fieldset>

      <label class="chk">
        <input type="checkbox" v-model="draft.required" /> {{ $t('fieldEditModal.required') }}
      </label>

      <footer>
        <button @click="$emit('cancel')">{{ $t('fieldEditModal.cancel') }}</button>
        <button class="primary" :disabled="!draft.name.trim()" @click="save">{{ $t('fieldEditModal.save') }}</button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import sourceMessages from "../i18n/locales/zh.json";
import { computed, reactive } from "vue";
import type { FieldSpec, ExtractionRule } from "../api";

const props = defineProps<{ field: FieldSpec; isNew: boolean; isColumn: boolean }>();
const emit = defineEmits<{ (e: "save", field: FieldSpec): void; (e: "cancel"): void }>();

// edit a working copy: cancel discards, save hands the copy back
const draft = reactive<FieldSpec>(JSON.parse(JSON.stringify(props.field)));

const split = (v: string) =>
  v.replace(/，/g, ",").split(",").map((s) => s.trim()).filter(Boolean);

const datePattern = computed<string>({
  get: () => draft.output_format?.date_pattern ?? "",
  set: (v) => {
    if (!v) draft.output_format = null;
    else draft.output_format = { date_pattern: v as never, decimal_places: null };
  },
});
const decimalPlaces = computed<number | null>({
  get: () => draft.output_format?.decimal_places ?? null,
  set: (v) => {
    if (v === null || v === undefined) draft.output_format = null;
    else draft.output_format = { date_pattern: null, decimal_places: v };
  },
});

function setRule(kind: string) {
  draft.rule = kind ? { kind: kind as ExtractionRule['kind'], labels: [], pattern: '', column_aliases: {}, sheet_name: null, stop_words: [sourceMessages.fieldEditor.stop_word_total, sourceMessages.fieldEditor.stop_word_grand_total, "Total"], on_miss: 'model' } : null;
}
function save() {
  if (draft.type !== "table") draft.columns = [];
  if (draft.type !== "enum") draft.enum_values = [];
  if (draft.type !== "date" && draft.type !== "number") draft.output_format = null;
  emit("save", JSON.parse(JSON.stringify(draft)));
}
</script>

<style scoped>
.mask { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.55); z-index: 120;
  display: flex; align-items: center; justify-content: center; padding: 20px; }
.modal { width: 480px; max-width: 94vw; max-height: 90vh; overflow-y: auto;
  background: var(--bg-panel); border: 1px solid var(--border); border-radius: 12px;
  padding: 20px; display: flex; flex-direction: column; gap: 14px; }
header { display: flex; justify-content: space-between; align-items: center; }
header strong { font-size: 16px; }
.x { padding: 2px 8px; }
label { display: flex; flex-direction: column; gap: 5px; font-size: 13px;
  font-weight: 600; }
label input, label select, label textarea { font-weight: 400; }
.hint { font-weight: 400; font-size: 12px; }
.chk { flex-direction: row; align-items: center; gap: 8px; }
.chk input { width: auto; }
.rule-box { border: 1px solid var(--border); border-radius: 8px; padding: 12px;
  display: flex; flex-direction: column; gap: 10px; margin: 0; }
.rule-box legend { font-size: 13px; font-weight: 600; padding: 0 6px; }
.rule-box select { width: 100%; }
.rule-box > button { align-self: flex-start; }
footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 4px; }
</style>
