<template>
  <!-- shared empty state: icon + headline + guidance + optional action -->
  <div class="empty">
    <div class="glyph">{{ glyph }}</div>
    <p class="head">{{ title }}</p>
    <p class="hint dim"><slot /></p>
    <div class="act"><slot name="action" /></div>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ title: string; glyph?: string }>(), { glyph: "🗂" });
</script>

<style scoped>
.empty { display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 48px 20px; text-align: center; }
.glyph { font-size: 34px; opacity: 0.6; }
.head { margin: 4px 0 0; font-size: 16px; font-weight: 600; }
.hint { margin: 0; font-size: 13px; max-width: 460px; line-height: 1.8; }
.act { margin-top: 10px; }
</style>
