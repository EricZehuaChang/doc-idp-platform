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
    <Skeleton v-else-if="isLoading" :rows="6" />
    <div v-else class="empty-state">
      <p class="big">队列为空 🎉</p>
      <p>没有待人工校验的文件。文档通过 API 提交处理后，低置信结果会出现在这里。</p>
      <p>
        还没有技能？先到 <router-link to="/skills">技能中心</router-link> 定义抽取字段并发布；
        接入方式见右下角 <a href="#" @click.prevent>帮助</a> 或 API 文档 <code>/docs</code>。
      </p>
    </div>
  </main>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed } from "vue";
import { api, type QueueItem } from "../api";
import Skeleton from "../components/Skeleton.vue";

// TanStack Query replaces the hand-rolled setInterval poll: same 5s cadence,
// plus cache reuse when hopping back from the review page.
const qc = useQueryClient();
const { data, isLoading } = useQuery({
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
  await api.assign(it.file_id, api.currentUser);
  await qc.invalidateQueries({ queryKey: ["queue"] });
}
</script>

<style scoped>
.wrap { padding: 20px; max-width: 1100px; margin: 0 auto; }
.count { color: var(--accent); }
table { width: 100%; border-collapse: collapse; background: var(--bg-panel); border-radius: 8px; }
th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); }
th { color: var(--text-dim); font-weight: 600; }
.empty-state { color: var(--text-dim); line-height: 2; margin-top: 24px; }
.empty-state .big { font-size: 18px; color: var(--text); }
</style>
