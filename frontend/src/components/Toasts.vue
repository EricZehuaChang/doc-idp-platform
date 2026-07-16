<template>
  <div class="toasts">
    <TransitionGroup name="t">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="t.kind">
        <span class="mark">{{ t.kind === "ok" ? "✓" : "✕" }}</span>{{ t.text }}
      </div>
    </TransitionGroup>
  </div>
</template>

<script setup lang="ts">
import { toasts } from "../toast";
</script>

<style scoped>
.toasts { position: fixed; top: 58px; right: 16px; z-index: 200;
  display: flex; flex-direction: column; gap: 8px; max-width: 380px; }
.toast { background: var(--bg-raised); border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 14px; font-size: 13px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35); display: flex; gap: 8px;
  align-items: baseline; }
.toast.error { border-color: var(--red); }
.toast.ok { border-color: var(--green); }
.mark { font-weight: 700; }
.error .mark { color: var(--red); }
.ok .mark { color: var(--green); }
.t-enter-active, .t-leave-active { transition: all 0.25s ease; }
.t-enter-from, .t-leave-to { opacity: 0; transform: translateX(16px); }
</style>
