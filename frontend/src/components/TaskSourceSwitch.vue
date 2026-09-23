<template>
  <nav class="source-tabs" aria-label="任务来源">
    <button :class="{ primary: source === 'manual' }" @click="choose('manual')">手工</button>
    <button :class="{ primary: source === 'api' }" @click="choose('api')">API</button>
  </nav>
</template>
<script setup lang="ts">
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
const route = useRoute(); const router = useRouter();
const source = computed(() => route.query.source === 'api' ? 'api' : 'manual');
function choose(value: string) {
  router.replace({ query: { ...route.query, source: value, page: undefined, api_tab: undefined } });
}
</script>
<style scoped>.source-tabs { display: flex; gap: 8px; margin: 0 0 16px; }</style>
