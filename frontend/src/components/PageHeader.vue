<template>
  <!-- shared page header: every routed page opens with the same hierarchy —
       title + one-line purpose left, primary actions right -->
  <header class="page-head">
    <div class="titles">
      <h1>{{ title }}</h1>
      <p v-if="desc" class="dim">{{ desc }}</p>
    </div>
    <div class="actions"><slot /></div>
  </header>
</template>

<script setup lang="ts">
defineProps<{ title: string; desc?: string }>();
</script>

<style scoped>
.page-head { display: flex; align-items: flex-start; gap: 16px; margin-bottom: 18px;
  flex-wrap: wrap; }
.titles { flex: 1; min-width: 240px; }
h1 { margin: 0; font-size: 20px; font-weight: 700; }
p { margin: 4px 0 0; font-size: 13px; }
.actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
</style>
