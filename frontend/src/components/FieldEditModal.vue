<template>
  <!-- card-style field editor (Insavlo interaction): all edits go through this
       modal; the field cards themselves are read-only presentation -->
  <div class="mask" @click.self="$emit('cancel')">
    <div class="modal">
      <header>
        <strong>{{ isNew ? "添加字段" : "编辑字段" }}</strong>
        <button class="ghost x" @click="$emit('cancel')">✕</button>
      </header>

      <label>字段名称 *
        <input v-model="draft.name" placeholder="例如：invoice_number" autofocus />
        <span class="hint dim">建议小写字母、数字和下划线；将作为输出 JSON 的键名</span>
      </label>

      <label>字段类型 *
        <select v-model="draft.type">
          <option value="string">字符串</option>
          <option value="number">数字</option>
          <option value="date">日期</option>
          <option value="enum">枚举</option>
          <option v-if="!isColumn" value="table">明细表（数组）</option>
        </select>
      </label>

      <label>抽取模式
        <select v-model="draft.mode">
          <option value="verbatim">原文抄录（值须可在原文定位）</option>
          <option value="inferred">模型推断（判断/分类/推导，带理由）</option>
        </select>
      </label>

      <label>描述
        <textarea v-model="draft.instruction" rows="5"
                  placeholder="抽取说明：寻找关键词 → 清洗规则 → 输出格式"></textarea>
        <span class="hint dim">包含抽取规则与任何特殊说明；可保存后用「✨ AI 补全说明」扩写</span>
      </label>

      <label v-if="draft.type === 'enum'">枚举值
        <input :value="draft.enum_values.join(',')" placeholder="逗号分隔，如：USD,EUR,JPY"
               @input="draft.enum_values = split(($event.target as HTMLInputElement).value)" />
      </label>

      <label>位置提示
        <input :value="draft.anchor_hints.join(',')" placeholder="例如：右上角、页脚"
               @input="draft.anchor_hints = split(($event.target as HTMLInputElement).value)" />
        <span class="hint dim">关于此字段在文档中典型位置的可选提示</span>
      </label>

      <label class="chk">
        <input type="checkbox" v-model="draft.required" /> 必填
      </label>

      <footer>
        <button @click="$emit('cancel')">取消</button>
        <button class="primary" :disabled="!draft.name.trim()" @click="save">保存</button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive } from "vue";
import type { FieldSpec } from "../api";

const props = defineProps<{ field: FieldSpec; isNew: boolean; isColumn: boolean }>();
const emit = defineEmits<{ (e: "save", field: FieldSpec): void; (e: "cancel"): void }>();

// edit a working copy: cancel discards, save hands the copy back
const draft = reactive<FieldSpec>(JSON.parse(JSON.stringify(props.field)));

const split = (v: string) =>
  v.replace(/，/g, ",").split(",").map((s) => s.trim()).filter(Boolean);

function save() {
  if (draft.type !== "table") draft.columns = [];
  if (draft.type !== "enum") draft.enum_values = [];
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
footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 4px; }
</style>
