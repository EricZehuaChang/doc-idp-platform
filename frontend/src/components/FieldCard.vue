<template>
  <!-- one field in the hierarchy: read-only card, edits go through the modal.
       table fields render their columns as indented child cards + add button -->
  <div class="fc" :class="{ nested: depth > 0 }">
    <div class="fc-row">
      <span class="grip dim">⠿</span>
      <span class="fc-name">{{ field.name }}</span>
      <span class="fc-type dim">（{{ typeLabel }}）</span>
      <span v-if="field.required" class="req">*</span>
      <span v-if="field.mode === 'inferred'" class="badge-inferred">推断</span>
      <span class="fc-act">
        <button class="icon" title="编辑" @click="$emit('edit')">✎</button>
        <button class="icon danger-ico" title="删除" @click="$emit('remove')">🗑</button>
      </span>
    </div>
    <p v-if="field.instruction" class="fc-instr dim">{{ field.instruction }}</p>
    <p v-if="field.enum_values.length" class="fc-extra dim">
      枚举：{{ field.enum_values.join(" / ") }}</p>
    <p v-if="field.anchor_hints.length" class="fc-extra dim">
      📍 {{ field.anchor_hints.join("；") }}</p>

    <div v-if="field.type === 'table'" class="children">
      <FieldCard v-for="(c, i) in field.columns" :key="i" :field="c" :depth="depth + 1"
                 @edit="$emit('edit-column', i)" @remove="field.columns.splice(i, 1)" />
      <button class="add-child" @click="$emit('add-column')">＋ 添加字段</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { FieldSpec } from "../api";

const props = withDefaults(defineProps<{ field: FieldSpec; depth?: number }>(),
                           { depth: 0 });
defineEmits<{ (e: "edit"): void; (e: "remove"): void;
              (e: "edit-column", i: number): void; (e: "add-column"): void }>();

const TYPE_LABELS: Record<string, string> = {
  string: "字符串", number: "数字", date: "日期", enum: "枚举", table: "数组",
};
const typeLabel = computed(() => TYPE_LABELS[props.field.type] ?? props.field.type);
</script>

<style scoped>
.fc { border: 1px solid var(--border); border-left: 3px solid var(--accent);
  border-radius: 8px; padding: 10px 12px; background: var(--bg-panel); }
.fc.nested { border-left-color: var(--blue); background: var(--bg); }
.fc-row { display: flex; align-items: center; gap: 8px; }
.grip { cursor: default; font-size: 12px; }
.fc-name { font-family: Consolas, monospace; font-weight: 700; color: var(--accent); }
.fc.nested .fc-name { color: var(--blue); }
.fc-type { font-size: 12px; }
.req { color: var(--red); font-weight: 700; }
.fc-act { margin-left: auto; display: flex; gap: 4px; }
.icon { padding: 1px 8px; font-size: 13px; background: transparent;
  border-color: transparent; }
.icon:hover { border-color: var(--accent); }
.danger-ico:hover { border-color: var(--red); }
.fc-instr { margin: 6px 0 0 20px; font-size: 12.5px; line-height: 1.7;
  white-space: pre-wrap; }
.fc-extra { margin: 4px 0 0 20px; font-size: 12px; }
.children { margin: 10px 0 2px 20px; display: flex; flex-direction: column;
  gap: 8px; border-left: 1px dashed var(--border); padding-left: 12px; }
.add-child { align-self: stretch; border-style: dashed; font-size: 12px;
  padding: 5px; background: transparent; }
</style>
