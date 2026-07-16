<template>
  <main class="wrap">
    <h2>待人工校验 <span class="count">{{ items.length }}</span></h2>
    <table v-if="items.length">
      <thead>
        <tr><th>文件名</th><th>技能</th><th>页数</th><th>锁定</th><th>提交时间</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="it in items" :key="it.file_id" @mouseenter="prefetch(it)">
          <td>{{ it.file_name }}</td>
          <td>{{ it.skill_code }}</td>
          <td>{{ it.page_count }}</td>
          <td>{{ it.locked_by || "-" }}</td>
          <td>{{ new Date(it.created_at).toLocaleString() }}</td>
          <td class="ops">
            <router-link :to="`/review/${it.file_id}`"><button class="primary">Verify</button></router-link>
            <button v-if="!it.assignee" @click="claim(it)">认领</button>
            <span v-else class="dim">{{ it.assignee }}</span>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="empty">队列为空 —— 没有待校验文件。</p>
  </main>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed } from "vue";
import { api, type QueueItem } from "../api";

// TanStack Query replaces the hand-rolled setInterval poll: same 5s cadence,
// plus cache reuse when hopping back from the review page.
const qc = useQueryClient();
const { data } = useQuery({
  queryKey: ["queue"],
  queryFn: api.queue,
  refetchInterval: 5000,
  placeholderData: (prev) => prev,   // backend hiccup: keep last list
});
const items = computed<QueueItem[]>(() => data.value ?? []);

// hover prefetch (caching design §9.0 layer ②): warm the original image via
// the browser cache (download endpoint is immutable) so the review page's
// left pane renders instantly when the reviewer clicks Verify.
const prefetched = new Set<string>();
function prefetch(it: QueueItem) {
  if (prefetched.has(it.file_id)) return;
  prefetched.add(it.file_id);
  if (/\.(png|jpe?g|bmp|webp)$/i.test(it.file_name)) {
    new Image().src = api.downloadUrl(it.file_id);
  }
}

// claim = set assignee to me (PM item #2: assignee is "whose job", distinct
// from the review lock which is "who is editing right now")
async function claim(it: QueueItem) {
  await fetch(`/api/v1/review/${it.file_id}/assign`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Tenant-Id": "default",
               "X-User": api.currentUser },
    body: JSON.stringify({ assignee: api.currentUser }),
  });
  await qc.invalidateQueries({ queryKey: ["queue"] });
}
</script>

<style scoped>
.wrap { padding: 20px; max-width: 1100px; margin: 0 auto; }
.count { color: var(--accent); }
table { width: 100%; border-collapse: collapse; background: var(--bg-panel); border-radius: 8px; }
th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); }
th { color: var(--text-dim); font-weight: 600; }
.empty { color: var(--text-dim); }
</style>
