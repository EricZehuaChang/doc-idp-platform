<template>
  <!-- full-screen centered card shared by all account entry pages -->
  <div class="auth-wrap">
    <div class="auth-card">
      <div class="brand">DOC·IDP</div>
      <h2>{{ title }}</h2>
      <slot />
      <p v-if="error" class="err">{{ error }}</p>
      <p v-if="notice" class="ok">{{ notice }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ title: string; error?: string; notice?: string }>();
</script>

<style scoped>
.auth-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center;
  background: var(--bg); }
.auth-card { width: 360px; background: var(--bg-panel); border: 1px solid var(--border);
  border-radius: 12px; padding: 32px; display: flex; flex-direction: column; gap: 14px; }
.brand { font-weight: 800; letter-spacing: 1px; color: var(--accent); font-size: 18px; }
h2 { margin: 0; font-size: 17px; }
.err { color: var(--red); font-size: 13px; margin: 0; }
.ok { color: var(--green); font-size: 13px; margin: 0; }
</style>
