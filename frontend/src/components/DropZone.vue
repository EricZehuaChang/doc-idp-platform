<template>
  <!-- File intake: click, drag-drop, or keyboard. Emits raw File objects;
       validation belongs to the caller (limits come from the server). -->
  <div class="drop" :class="{ hot, disabled }" tabindex="0" role="button"
       :aria-label="$t('dropZone.choose_supported', { accept })"
       @click="pick" @keydown.enter.prevent="pick" @keydown.space.prevent="pick"
       @dragover.prevent="hot = true" @dragenter.prevent="hot = true"
       @dragleave.prevent="hot = false" @drop.prevent="onDrop">
    <div class="glyph">📄</div>
    <I18nT keypath="dropZone.drop_or_choose" tag="div" scope="global" class="main"><template #choose><b>{{ $t('dropZone.click_to_choose') }}</b></template></I18nT>
    <div class="fmt">{{ $t('dropZone.supported') }} {{ accept.replace(/\./g, "").toUpperCase().replace(/,/g, " · ") }}</div>
    <div class="lim"><slot name="limits" /></div>
    <input ref="input" type="file" multiple :accept="accept" hidden
           @change="onPick" />
  </div>
</template>

<script setup lang="ts">
import { I18nT } from "vue-i18n";
import { ref } from "vue";

const props = defineProps<{ accept: string; disabled?: boolean }>();
const emit = defineEmits<{ (e: "add", files: File[]): void }>();

const input = ref<HTMLInputElement>();
const hot = ref(false);

function pick() {
  if (props.disabled) return;
  input.value?.click();
}
function onPick(e: Event) {
  const el = e.target as HTMLInputElement;
  emit("add", Array.from(el.files ?? []));
  el.value = "";            // same file can be picked again after a removal
}
function onDrop(e: DragEvent) {
  hot.value = false;
  if (props.disabled) return;
  // only files: dragging a directory yields a zero-size entry we cannot read
  emit("add", Array.from(e.dataTransfer?.files ?? []));
}
</script>

<style scoped>
.drop { border: 2px dashed var(--border); border-radius: 10px; padding: 26px 20px;
  text-align: center; transition: border-color .15s, background .15s; cursor: pointer; }
.drop:hover:not(.disabled), .drop:focus-visible { border-color: var(--accent); outline: none; }
.drop.hot { border-color: var(--accent); background: rgba(240, 180, 41, .05); }
.drop.disabled { opacity: .5; cursor: not-allowed; }
.glyph { font-size: 30px; line-height: 1; }
.main { margin-top: 8px; font-size: 14px; }
.main b { color: var(--accent); }
.fmt { margin-top: 8px; font-size: 12px; color: var(--text-dim); }
.lim { margin-top: 3px; font-size: 11.5px; color: var(--text-dim); }
</style>
