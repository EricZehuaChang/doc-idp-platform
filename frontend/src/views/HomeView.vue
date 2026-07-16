<template>
  <main class="page">
    <PageHeader title="总览" desc="平台运行一目了然：用量、吞吐与待办。" />

    <!-- metric strip -->
    <div class="strip card-panel">
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

    <!-- call to action: the reviewer's next thing to do -->
    <div v-if="(stats?.pending_verification ?? 0) > 0" class="cta card-panel">
      <span>📋 有 <strong>{{ stats!.pending_verification }}</strong> 份文件等待人工校验</span>
      <router-link to="/tasks?status=pending_verification">
        <button class="primary">开始审单 →</button></router-link>
    </div>

    <!-- recent activity: a slice, not the working list -->
    <section class="card-panel block">
      <div class="block-head">
        <h2>最近任务</h2>
        <span class="live dim">
          排队 {{ stats?.queued ?? 0 }} · 处理中 {{ stats?.processing ?? 0 }} ·
          今日完成 {{ stats?.today_completed ?? 0 }}</span>
        <router-link to="/tasks" class="more">全部任务 →</router-link>
      </div>
      <div class="table-scroll" v-if="rows.length">
        <table class="data-table">
          <thead>
            <tr><th>时间</th><th>技能</th><th>文件名</th><th>页数</th><th>状态</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.file_id">
              <td class="dim nowrap">{{ ts(r.created_at) }}</td>
              <td>{{ r.skill_code }}</td>
              <td class="fname" :title="r.file_name">{{ r.file_name }}</td>
              <td>{{ r.page_count }}</td>
              <td><span class="chip" :class="`chip-${r.status}`">{{ stLabel(r.status) }}</span></td>
              <td class="row-act">
                <router-link v-if="r.status === 'pending_verification'"
                             :to="`/review/${r.file_id}`">
                  <button class="primary slim">Verify</button></router-link>
                <router-link v-else :to="`/review/${r.file_id}`">
                  <button class="ghost slim">查看</button></router-link>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <Skeleton v-else-if="isLoading" :rows="6" />
      <EmptyState v-else title="还没有任务" glyph="🚀">
        通过 API <code>POST /api/v1/process</code> 上传文档开始处理，
        或先到技能中心定义抽取字段。
        <template #action>
          <router-link to="/skills"><button class="primary">去技能中心</button></router-link>
        </template>
      </EmptyState>
    </section>
  </main>
</template>

<script setup lang="ts">
import { useQuery } from "@tanstack/vue-query";
import { computed } from "vue";
import { api } from "../api";
import EmptyState from "../components/EmptyState.vue";
import PageHeader from "../components/PageHeader.vue";
import Skeleton from "../components/Skeleton.vue";
import { STATUS_LABELS } from "../labels";

const { data: stats } = useQuery({
  queryKey: ["home-stats"], queryFn: api.homeStats, refetchInterval: 10_000 });
const { data: files, isLoading } = useQuery({
  queryKey: ["files", 1, ""],
  queryFn: () => api.files(1),
  refetchInterval: 8_000,
  placeholderData: (prev) => prev,
});
const rows = computed(() => (files.value?.data ?? []).slice(0, 8));

const fmt = (v: number | undefined) => v == null ? "—" : v.toLocaleString();
const ts = (v: string | null) => v ? new Date(v).toLocaleString() : "-";
const stLabel = (s: string) => STATUS_LABELS[s] ?? s;
</script>

<style scoped>
.strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px; padding: 18px 20px; margin-bottom: 14px; }
.metric { display: flex; flex-direction: column; gap: 4px; }
.mlabel { font-size: 12px; color: var(--text-dim); }
.mval { font-size: 26px; font-weight: 700; }
.mval.accent { color: var(--accent); }
.mval.warn { color: var(--blue); }
.cta { display: flex; align-items: center; gap: 16px; padding: 14px 20px;
  margin-bottom: 14px; border-left: 3px solid var(--accent); }
.cta span { flex: 1; }
.cta strong { color: var(--accent); font-size: 16px; }
.block { padding: 14px 16px; }
.block-head { display: flex; align-items: baseline; gap: 14px; margin-bottom: 8px; }
.block-head h2 { margin: 0; font-size: 15px; }
.live { font-size: 12px; }
.more { margin-left: auto; font-size: 13px; }
.table-scroll { overflow-x: auto; }
.fname { max-width: 340px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nowrap { white-space: nowrap; }
.row-act { text-align: right; }
.slim { padding: 2px 12px; font-size: 12px; }
</style>
