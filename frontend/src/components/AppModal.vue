<template>
  <div ref="maskEl" class="modal-mask" tabindex="-1" data-testid="modal-mask"
       @click.self="$emit('close')" @keydown.esc="$emit('close')">
    <div class="modal" :class="{ 'modal-wide': wide }" role="dialog"
         aria-modal="true" data-testid="modal-panel">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 走查 #9: one shared dialog shell — fixed overlay, centred panel, internal
 * scroll, closes on Esc or a mask click. Four dialogs (import/export, run
 * history, run detail, generate fields) previously rendered inline because the
 * styles lived in a single component's scoped block.
 */
import { onBeforeUnmount, onMounted, ref } from "vue";

defineProps<{ wide?: boolean }>();
const emit = defineEmits<{ (e: "close"): void }>();

const maskEl = ref<HTMLElement>();

function onKey(e: KeyboardEvent) {
  if (e.key === "Escape") emit("close");
}
onMounted(() => {
  window.addEventListener("keydown", onKey);
  maskEl.value?.focus();
});
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>
