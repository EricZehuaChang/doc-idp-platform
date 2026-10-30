<template>
  <div class="fc" :class="{ nested: depth > 0, dragging: isDragging, over: isOver, 'fc-inline': inline, active, expanded }">
    <div v-if="inline" class="fc-inline-row" role="button" tabindex="0" :aria-expanded="expanded"
         :aria-label="$t(expanded ? 'fieldCard.collapse_editor' : 'fieldCard.expand_editor', { name: field.name || $t('fieldCard.unnamed_field') })"
         @click="$emit('select')" @keydown.enter.self.prevent="$emit('select')" @keydown.space.self.prevent="$emit('select')">
      <span class="grip" :title="$t('fieldCard.press_and_drag_to_reorder_fields')" @pointerdown.stop="$emit('grip-down', index, $event)" @click.stop>⠿</span>
      <span class="fc-summary">
        <span class="fc-name">{{ field.name || $t('fieldCard.unnamed_field') }}<span v-if="field.required" class="req"> *</span></span>
        <span class="fc-description">{{ field.instruction || (field.type === 'table' ? $t('fieldCard.column_count', { count: field.columns.length }) : $t('fieldCard.click_to_add_extraction_instructions')) }}</span>
      </span>
      <span class="fc-type">{{ typeLabel }}<span v-if="field.mode === 'inferred'" class="inferred-dot" :title="$t('fieldCard.model_inference')"> {{ $t('fieldCard.inferred') }}</span></span>
      <span class="fc-value" :class="{ dim: !hasValue }" :title="resultText">{{ resultText }}</span>
      <span class="fc-status" :class="statusTone">{{ statusLabel }}</span>
      <button type="button" class="icon danger-ico" :title="$t('fieldCard.delete_named_field', { name: field.name || $t('fieldCard.unnamed_field') })" :aria-label="$t('fieldCard.delete_named_field', { name: field.name || $t('fieldCard.unnamed_field') })" @click.stop="$emit('remove')">×</button>
      <span class="fc-chevron" aria-hidden="true">{{ expanded ? '⌃' : '⌄' }}</span>
    </div>
    <template v-else>
      <div class="fc-row">
        <span class="grip" :title="$t('fieldCard.press_and_drag_to_reorder_fields')" @pointerdown="$emit('grip-down', index, $event)">⠿</span>
        <span class="fc-idx dim">{{ index + 1 }}</span><span class="fc-name">{{ field.name }}</span>
        <span class="fc-type dim">（{{ typeLabel }}）</span><span v-if="field.required" class="req">*</span>
        <span v-if="field.mode === 'inferred'" class="badge-inferred">{{ $t('fieldCard.inferred_465edc') }}</span>
        <span class="fc-act"><button type="button" class="icon" :title="$t('fieldCard.edit')" @click="$emit('edit')">✎</button><button type="button" class="icon danger-ico" :title="$t('fieldCard.delete')" @click="$emit('remove')">×</button></span>
      </div>
      <p v-if="field.instruction" class="fc-instr dim">{{ field.instruction }}</p>
      <p v-if="field.enum_values.length" class="fc-extra dim">{{ $t('fieldCard.enum') }}{{ field.enum_values.join(' / ') }}</p>
      <p v-if="field.anchor_hints.length" class="fc-extra dim">{{ $t('fieldCard.location') }}{{ field.anchor_hints.join('；') }}</p>
    </template>
    <div v-if="inline && expanded" class="fc-editor">
      <FieldEditor :field="field" :is-column="depth > 0" @update="$emit('update', $event)" />
    </div>
    <div v-if="field.type === 'table' && (!inline || expanded)" ref="colsEl" class="children">
      <FieldCard v-for="(column, i) in field.columns" :key="i" :field="column" :index="i" :depth="depth + 1"
                 :inline="inline" :expanded="expandedColumn === i" :active="expandedColumn === i"
                 :result="columnResult(column.name)" :result-status="columnStatus(column.name)"
                 :drag-from="cols.from.value ?? -1" :drag-over="cols.over.value ?? -1"
                 @select="expandedColumn = expandedColumn === i ? -1 : i"
                 @update="updateColumn(i, $event)" @remove="removeColumn(i)"
                 @edit="$emit('edit-column', i)" @grip-down="startColDrag" />
      <button type="button" class="add-child" @click="addColumn">{{ $t('fieldCard.add_column_field') }}</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
const { t } = useI18n();

import { computed, ref } from "vue";
import type { FieldSpec } from "../api";
import { moveItem, useListReorder } from "../reorder";
import FieldEditor from "./FieldEditor.vue";

const props = withDefaults(defineProps<{
  field: FieldSpec; index?: number; depth?: number; dragFrom?: number; dragOver?: number;
  inline?: boolean; expanded?: boolean; result?: unknown; resultStatus?: string; active?: boolean;
}>(), { index: 0, depth: 0, dragFrom: -1, dragOver: -1, inline: false, expanded: false, resultStatus: "idle", active: false });
const emit = defineEmits<{
  (e: "edit"): void; (e: "remove"): void; (e: "select"): void;
  (e: "update", field: FieldSpec): void;
  (e: "edit-column", i: number): void; (e: "add-column"): void;
  (e: "grip-down", index: number, ev: PointerEvent): void;
}>();
const TYPE_KEYS: Record<string, string> = { string: "fieldCard.type_string", number: "fieldCard.type_number", date: "fieldCard.type_date", enum: "fieldCard.type_enum", table: "fieldCard.type_table" };
const typeLabel = computed(() => TYPE_KEYS[props.field.type] ? t(TYPE_KEYS[props.field.type]) : props.field.type);
const isDragging = computed(() => props.dragFrom === props.index);
const isOver = computed(() => props.dragFrom >= 0 && props.dragOver === props.index && props.dragFrom !== props.index);
const expandedColumn = ref(-1);
const colsEl = ref<HTMLElement>();
const cols = useListReorder();

function unwrap(value: unknown): unknown {
  if (value && typeof value === "object" && !Array.isArray(value) && "$value" in value) return (value as Record<string, unknown>).$value;
  return value;
}
function present(value: unknown): boolean {
  const unwrapped = unwrap(value);
  return unwrapped !== null && unwrapped !== undefined && unwrapped !== "" && (!Array.isArray(unwrapped) || unwrapped.some(present));
}
function display(value: unknown): string {
  const unwrapped = unwrap(value);
  if (!present(unwrapped)) return "—";
  if (Array.isArray(unwrapped)) return unwrapped.map(display).join(" / ");
  if (typeof unwrapped === "object") return JSON.stringify(unwrapped);
  return String(unwrapped);
}
const hasValue = computed(() => present(props.result));
const resultText = computed(() => {
  const value = unwrap(props.result);
  if (props.field.type === "table" && Array.isArray(value)) return t('fieldCard.table_dimensions', { rows: value.length, columns: props.field.columns.length });
  return hasValue.value ? display(value) : props.resultStatus === "idle" ? t('fieldCard.not_yet_extracted') : "—";
});
const STATUS_KEYS: Record<string, string> = { idle: "fieldCard.status_idle", running: "fieldCard.status_running", success: "fieldCard.status_success", extracted: "fieldCard.status_extracted", missing: "fieldCard.status_missing", stale: "fieldCard.status_stale", generated: "fieldCard.status_generated", review: "fieldCard.status_review" };
const STATUS_TONES: Record<string, string> = { success: "status-success", missing: "status-warning", review: "status-warning", stale: "status-warning", generated: "status-example" };
const statusLabel = computed(() => STATUS_KEYS[props.resultStatus] ? t(STATUS_KEYS[props.resultStatus]) : props.resultStatus);
const statusTone = computed(() => STATUS_TONES[props.resultStatus] ?? "");
function columnResult(name: string): unknown {
  const result = unwrap(props.result);
  if (Array.isArray(result)) return result.map((row) => {
    const data = unwrap(row);
    return data && typeof data === "object" ? (data as Record<string, unknown>)[name] : undefined;
  });
  return result && typeof result === "object" ? (result as Record<string, unknown>)[name] : undefined;
}
function columnStatus(name: string): string {
  if (["idle", "running", "stale", "generated"].includes(props.resultStatus)) return props.resultStatus;
  // The trial supplies location evidence for the table, not for individual cells.
  return present(columnResult(name)) ? "review" : "missing";
}
function updateColumns(columns: FieldSpec[]) { emit("update", { ...props.field, columns }); }
function updateColumn(index: number, field: FieldSpec) {
  const columns = [...props.field.columns];
  columns[index] = field;
  const previous = props.field.columns[index]?.name;
  // Keep configured table aliases attached when a column is renamed.
  if (previous && previous !== field.name && props.field.rule?.kind === "table") {
    const column_aliases = { ...props.field.rule.column_aliases };
    if (Object.prototype.hasOwnProperty.call(column_aliases, previous)) {
      column_aliases[field.name] = column_aliases[previous];
      delete column_aliases[previous];
    }
    emit("update", { ...props.field, columns, rule: { ...props.field.rule, column_aliases } });
  } else updateColumns(columns);
}
function removeColumn(index: number) {
  updateColumns(props.field.columns.filter((_, i) => i !== index));
  if (expandedColumn.value === index) expandedColumn.value = -1;
  else if (expandedColumn.value > index) expandedColumn.value--;
}
function addColumn() {
  if (!props.inline) { emit("add-column"); return; }
  let suffix = props.field.columns.length + 1;
  while (props.field.columns.some((column) => column.name === `column_${suffix}`)) suffix++;
  expandedColumn.value = props.field.columns.length;
  updateColumns([...props.field.columns, { name: `column_${suffix}`, type: "string", instruction: "", mode: "verbatim", required: false, anchor_hints: [], enum_values: [], columns: [] }]);
}
function startColDrag(index: number, event: PointerEvent) {
  cols.start(index, event, colsEl.value, (from, to) => {
    const columns = [...props.field.columns];
    moveItem(columns, from, to);
    if (expandedColumn.value === from) expandedColumn.value = to;
    else if (from < expandedColumn.value && to >= expandedColumn.value) expandedColumn.value--;
    else if (from > expandedColumn.value && to <= expandedColumn.value) expandedColumn.value++;
    updateColumns(columns);
  });
}
</script>

<style scoped>
.fc { border: 1px solid var(--border); border-left: 3px solid var(--accent); border-radius: 8px; padding: 10px 12px; background: var(--bg-panel); }
.fc.nested { border-left-color: var(--blue); background: var(--bg); }
.fc.dragging { opacity: .45; }
.fc.over { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent) inset; }
.fc-row { display: flex; align-items: center; gap: 8px; }
.grip { cursor: grab; font-size: 15px; color: var(--text-dim); touch-action: none; user-select: none; text-align: center; padding: 4px 0; }
.grip:hover { color: var(--accent); }
.grip:active { cursor: grabbing; }
.fc-idx { font-size: 11px; min-width: 14px; }
.fc-name { font-family: Consolas, monospace; font-weight: 600; color: var(--text); overflow: hidden; text-overflow: ellipsis; }
.fc-type { font-size: 11px; color: var(--text-dim); }
.req { color: var(--red); font-weight: 700; }
.fc-act { margin-left: auto; display: flex; gap: 4px; }
.icon { padding: 2px 6px; font-size: 17px; line-height: 1; background: transparent; border-color: transparent; color: var(--text-dim); }
.icon:hover { border-color: var(--accent); color: var(--text); }
.danger-ico:hover { border-color: var(--red); color: var(--red); }
.fc-instr { margin: 6px 0 0 20px; font-size: 12px; line-height: 1.7; white-space: pre-wrap; }
.fc-extra { margin: 4px 0 0 20px; font-size: 12px; }
.children { margin: 10px 0 2px 20px; display: flex; flex-direction: column; gap: 8px; border-left: 1px dashed var(--border); padding-left: 12px; }
.add-child { align-self: stretch; border-style: dashed; font-size: 12px; padding: 7px; background: transparent; }
.dim { color: var(--text-dim); }
.fc-inline { padding: 0; border: 0; border-bottom: 1px solid var(--border); border-radius: 0; background: transparent; }
.fc-inline.active, .fc-inline.expanded { background: color-mix(in srgb, var(--accent) 5%, var(--bg-panel)); }
.fc-inline.active { box-shadow: inset 3px 0 var(--accent); }
.fc-inline-row { display: grid; grid-template-columns: 16px minmax(0, 1.35fr) 48px minmax(0, 1fr) 48px 24px 10px; gap: 8px; align-items: center; padding: 13px 12px; cursor: pointer; min-height: 67px; outline-offset: -2px; }
.fc-inline-row:hover { background: color-mix(in srgb, var(--text) 3%, transparent); }
.fc-inline-row:focus-visible { outline: 2px solid var(--accent); }
.fc-summary { min-width: 0; display: flex; flex-direction: column; gap: 5px; }
.fc-inline .fc-name { font-size: 13px; white-space: nowrap; }
.fc-description { color: var(--text-dim); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fc-value { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fc-status { font-size: 10px; color: var(--text-dim); white-space: nowrap; }
.status-success { color: var(--green, #55b48c); }
.status-warning { color: var(--orange, #d6a45b); }
.status-example { color: var(--blue); }
.fc-chevron { color: var(--text-dim); font-size: 14px; }
.inferred-dot { display: block; margin-top: 3px; font-size: 10px; }
.fc-editor { padding: 16px 18px 16px 37px; border-top: 1px solid var(--border); }
.fc-inline > .children { margin: 0 12px 14px 25px; padding-left: 12px; }
.fc-inline.nested { background: transparent; }
.fc-inline.nested .fc-inline-row { padding-left: 4px; padding-right: 4px; grid-template-columns: 14px minmax(0, 1.35fr) 42px minmax(0, 1fr) 42px 20px 8px; gap: 6px; }
.fc-inline.nested .fc-editor { padding-left: 20px; padding-right: 4px; }
@media (max-width: 600px) {
  .fc-inline-row { grid-template-columns: 14px minmax(0, 1.3fr) 42px minmax(0, 1fr) 42px 20px; gap: 6px; padding: 12px 8px; }
  .fc-chevron { display: none; }
  .fc-editor { padding: 14px; }
  .fc-inline.nested .fc-inline-row { grid-template-columns: 12px minmax(0, 1.3fr) 38px minmax(0, 1fr) 40px 18px; gap: 4px; }
  .fc-inline > .children { margin-left: 10px; padding-left: 7px; }
}
</style>
