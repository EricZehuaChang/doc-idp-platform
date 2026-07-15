<template>
  <header class="topbar">
    <span class="logo">DOC·IDP</span>
    <nav><router-link to="/queue">待审队列</router-link></nav>
    <span class="user">{{ user }}</span>
  </header>
  <router-view />
</template>

<script setup lang="ts">
import { api } from "./api";
const user = api.currentUser;
</script>

<style scoped>
.topbar {
  display: flex; align-items: center; gap: 24px;
  padding: 10px 20px; background: var(--bg-panel);
  border-bottom: 1px solid var(--border);
}
.logo { font-weight: 800; letter-spacing: 1px; color: var(--accent); }
.user { margin-left: auto; color: var(--text-dim); }
</style>
