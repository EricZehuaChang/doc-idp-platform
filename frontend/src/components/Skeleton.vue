<template>
  <!-- loading skeleton (UX debt): pulse rows sized like the real content -->
  <div class="skel" :style="{ maxWidth: width }">
    <div v-for="i in rows" :key="i" class="bar" :style="{ width: barWidth(i) }"></div>
  </div>
</template>

<script setup lang="ts">
const props = withDefaults(defineProps<{ rows?: number; width?: string }>(),
                           { rows: 5, width: "100%" });
const rows = props.rows;
const width = props.width;
function barWidth(i: number): string {
  return `${100 - ((i * 13) % 34)}%`;      // deterministic ragged edge
}
</script>

<style scoped>
.skel { display: flex; flex-direction: column; gap: 12px; padding: 8px 0; }
.bar { height: 16px; border-radius: 6px; background: var(--bg-raised);
  animation: pulse 1.4s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { opacity: 0.5; } 50% { opacity: 1; } }
</style>
