<template>
  <main class="wrap">
    <!-- metric strip (Insavlo home shape) -->
    <div class="strip">
      <div class="metric"><span class="mlabel">剩余 Credits</span>
        <span class="mval accent">{{ fmt(stats?.remaining_credits) }}</span></div>
      <div class="metric"><span class="mlabel">已用 Credits</span>
        <span class="mval">{{ fmt(stats?.used_credits) }}</span></div>
      <div class="metric"><span class="mlabel">今日用量</span>
        <span class="mval">{{ fmt(stats?.today_usage) }}</span></div>
      <div class="metric"><span class="mlabel">已通过页数</span>
        <span class="mval">{{ fmt(stats?.passed_pages) }}</span></div>
      <div class="metric"><span class="mlabel">已通过文档</span>
        <span class="mval">{{ fmt(stats?.passed_docs) }}</span></div>
      <div class="metric"><span class="mlabel">待人工校验</span>
        <span class="mval warn">{{ fmt(stats?.pending_verification) }}</span></div>
    </div>

    <!-- task ledger -->
    <section class="panel">
      <div class="bar">
        <h2>任务 <button class="refresh" title="刷新" @click="reload">⟳</button></h2>
        <select v-model="statusFilter" class="status-sel">
          <option value="">全部状态</option>
          <option value="queued">排队中</option>
          <option value="processing">处理中</option>
          <option value="pending_verification">待校验</option>
          <option value="completed">已完成</option>
          <option value="passed">已通过</option>
          <option value="rejected">已拒绝</option>
          <option value="split">已分割</option>
          <option value="error">失败</option>
        </select>
        <span class="live dim">
          排队 {{ stats?.queued ?? 0 }} · 处理中 {{ stats?.processing ?? 0 }} ·
          今日完成 {{ stats?.today_completed ?? 0 }}</span>
      </div>

      <div class="table-scroll" v-if="rows.length">
        <table>
          <thead>
            <tr><th>时间</th><th>技能</th><th>文件名</th><th>类型</th><th>大小</th>
                <th>页数</th><th>状态</th><th>更新时间</th><th>校验</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.file_id">
              <td class="dim nowrap">{{ ts(r.created_at) }}</td>
              <td>{{ r.skill_code }}</td>
              <td class="fname" :title="r.file_name">{{ r.file_name }}</td>
              <td><span class="ftype">{{ r.type }}</span></td>
              <td class="dim">{{ size(r.size) }}</td>
              <td>{{ r.page_count }}</td>
              <td><span class="st" :class="`st-${r.status}`">{{ stLabel(r.status) }}</span></td>
              <td class="dim nowrap">{{ ts(r.updated_at) }}</td>
              <td>
                <router-link v-if="r.status === 'pending_verification'"
                             :to="`/review/${r.file_id}`">
                  <button class="primary vbtn">Verify</button></router-link>
                <span v-else-if="r.verified_by" class="dim">✓ {{ r.verified_by }}</span>
                <span v-else class="dim">-</span>
              </td>
              <td>
                <router-link :to="`/review/${r.file_id}`" title="查看">
                  <button class="mini">👁</button></router-link>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <Skeleton v-else-if="isLoading" :rows="8" />
      <div v-else class="empty-state">
        <p class="big">还没有任务</p>
        <p>通过 API <code>POST /api/v1/process</code> 上传文档，或先到
          <router-link to="/skills">技能</router-link> 定义抽取字段。</p>
      </div>

      <div class="pager" v-if="totalPages > 1">
        <span class="dim">共 {{ total }} 条</span>
        <button :disabled="page <= 1" @click="page--">上一页</button>
        <span>{{ page }} / {{ totalPages }}</span>
        <button :disabled="page >= totalPages" @click="page++">下一页</button>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, ref, watch } from "vue";
import { api } from "../api";
import Skeleton from "../components/Skeleton.vue";

const qc = useQueryClient();
const page = ref(1);
const statusFilter = ref("");
watch(statusFilter, () => { page.value = 1; });

const { data: stats } = useQuery({
  queryKey: ["home-stats"], queryFn: api.homeStats, refetchInterval: 10_000 });
const { data: files, isLoading } = useQuery({
  queryKey: computed(() => ["files", page.value, statusFilter.value]),
  queryFn: () => api.files(page.value, statusFilter.value || undefined),
  refetchInterval: 8_000,
  placeholderData: (prev) => prev,
});
const rows = computed(() => files.value?.data ?? []);
const total = computed(() => files.value?.total ?? 0);
const totalPages = computed(() => files.value?.total_pages ?? 1);

function reload() {
  qc.invalidateQueries({ queryKey: ["home-stats"] });
  qc.invalidateQueries({ queryKey: ["files"] });
}
const fmt = (v: number | undefined) => v == null ? "—" : v.toLocaleString();
const ts = (v: string | null) => v ? new Date(v).toLocaleString() : "-";
function size(n: number | null): string {
  if (n == null) return "-";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(2)} MB`;
}
const LABELS: Record<string, string> = {
  queued: "排队中", processing: "处理中", pending_verification: "待校验",
  completed: "已完成", passed: "已通过", rejected: "已拒绝", split: "已分割",
  error: "失败",
};
const stLabel = (s: string) => LABELS[s] ?? s;
</script>

<style scoped>
.wrap { padding: 20px 24px; max-width: 1500px; margin: 0 auto; width: 100%; }
.strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px; background: var(--bg-panel); border: 1px solid var(--border);
  border-radius: 10px; padding: 16px 20px; margin-bottom: 16px; }
.metric { display: flex; flex-direction: column; gap: 4px; }
.mlabel { font-size: 12px; color: var(--text-dim); }
.mval { font-size: 24px; font-weight: 700; }
.mval.accent { color: var(--accent); }
.mval.warn { color: var(--blue); }
.panel { background: var(--bg-panel); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 16px; }
.bar { display: flex; gap: 14px; align-items: center; margin-bottom: 10px; }
.bar h2 { margin: 0; font-size: 17px; display: flex; gap: 8px; align-items: center; }
.refresh { padding: 0 8px; font-size: 14px; }
.status-sel { background: var(--bg-raised); color: var(--text);
  border: 1px solid var(--border); border-radius: 6px; padding: 5px 10px; }
.live { margin-left: auto; font-size: 12px; }
.table-scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
th { color: var(--text-dim); font-weight: 600; white-space: nowrap; }
.fname { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nowrap { white-space: nowrap; }
.ftype { font-size: 11px; color: var(--red); font-weight: 700; }
.st { font-size: 12px; border-radius: 4px; padding: 1px 8px; white-space: nowrap; }
.st-pending_verification { color: var(--blue); border: 1px solid var(--blue); }
.st-completed, .st-passed { color: var(--green); border: 1px solid var(--green); }
.st-error, .st-rejected { color: var(--red); border: 1px solid var(--red); }
.st-processing, .st-queued { color: var(--accent); border: 1px solid var(--accent); }
.st-split { color: var(--text-dim); border: 1px solid var(--text-dim); }
.vbtn { padding: 2px 12px; font-size: 12px; }
.mini { padding: 1px 8px; font-size: 12px; }
.pager { display: flex; gap: 12px; align-items: center; justify-content: flex-end;
  margin-top: 10px; font-size: 13px; }
.empty-state { color: var(--text-dim); line-height: 2; padding: 24px 0; }
.empty-state .big { font-size: 18px; color: var(--text); }
.dim { color: var(--text-dim); }
</style>
