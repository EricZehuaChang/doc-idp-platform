<template>
  <main class="wrap">
    <h2>待人工校验 <span class="count">{{ items.length }}</span></h2>
    <table v-if="items.length">
      <thead>
        <tr><th>文件名</th><th>技能</th><th>页数</th><th>锁定</th><th>提交时间</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="it in items" :key="it.file_id">
          <td>{{ it.file_name }}</td>
          <td>{{ it.skill_code }}</td>
          <td>{{ it.page_count }}</td>
          <td>{{ it.locked_by || "-" }}</td>
          <td>{{ new Date(it.created_at).toLocaleString() }}</td>
          <td><router-link :to="`/review/${it.file_id}`"><button class="primary">Verify</button></router-link></td>
        </tr>
      </tbody>
    </table>
    <p v-else class="empty">队列为空 —— 没有待校验文件。</p>
  </main>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { api, type QueueItem } from "../api";

const items = ref<QueueItem[]>([]);
let timer: number | undefined;

async function load() {
  try { items.value = await api.queue(); } catch { /* backend offline: keep last */ }
}
onMounted(() => { load(); timer = window.setInterval(load, 5000); });
onUnmounted(() => window.clearInterval(timer));
</script>

<style scoped>
.wrap { padding: 20px; max-width: 1100px; margin: 0 auto; }
.count { color: var(--accent); }
table { width: 100%; border-collapse: collapse; background: var(--bg-panel); border-radius: 8px; }
th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); }
th { color: var(--text-dim); font-weight: 600; }
.empty { color: var(--text-dim); }
</style>
