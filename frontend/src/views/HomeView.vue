<template>
  <main class="page">
    <PageHeader :title="$t('home.home')" :desc="$t('home.platform_operations_at_a_glance_usage_throughput_and_to')" />

    <TaskSourceSwitch />

    <!-- metric strip -->
    <div class="strip card-panel">
      <div class="metric"><span class="mlabel">{{ $t('home.remaining_credits') }}</span>
        <span class="mval accent">{{ fmt(stats?.remaining_credits) }}</span></div>
      <div class="metric"><span class="mlabel">{{ $t('home.credits_used') }}</span>
        <span class="mval">{{ fmt(stats?.used_credits) }}</span></div>
      <div class="metric"><span class="mlabel">{{ $t('home.today_s_usage') }}</span>
        <span class="mval">{{ fmt(stats?.today_usage) }}</span></div>
      <div class="metric"><span class="mlabel">{{ $t('home.approved_pages') }}</span>
        <span class="mval">{{ fmt(stats?.passed_pages) }}</span></div>
      <div class="metric"><span class="mlabel">{{ $t('home.approved_documents') }}</span>
        <span class="mval">{{ fmt(stats?.passed_docs) }}</span></div>
      <div class="metric"><span class="mlabel">{{ $t('home.pending_manual_review') }}</span>
        <span class="mval warn">{{ fmt(stats?.pending_verification) }}</span></div>
    </div>

    <!-- call to action: the reviewer's next thing to do -->
    <div v-if="(stats?.pending_verification ?? 0) > 0" class="cta card-panel">
      <I18nT keypath="home.pending-review" tag="span" scope="global"><template #count><strong>{{ stats!.pending_verification }}</strong></template></I18nT>
      <router-link :to="{ path: '/tasks', query: { status: 'pending_verification', source } }">
        <button class="primary">{{ $t('home.start_reviewing') }}</button></router-link>
    </div>

    <!-- recent activity: a slice, not the working list -->
    <section class="card-panel block">
      <div class="block-head">
        <h2>{{ $t('home.recent_tasks') }}</h2>
        <span class="live dim">
          {{ $t('home.queued') }} {{ stats?.queued ?? 0 }} {{ $t('home.processing') }} {{ stats?.processing ?? 0 }} {{ $t('home.completed_today') }} {{ stats?.today_completed ?? 0 }}</span>
        <!-- filtering lives on the task workbench; make the way there
             obvious from the home slice (P09) -->
        <router-link :to="{ path: '/tasks', query: { source } }" class="more">{{ $t('home.filter_all_tasks') }}</router-link>
      </div>
      <div class="table-scroll" v-if="rows.length">
        <table class="data-table">
          <colgroup><col style="width: 18%" /><col style="width: 20%" />
            <col style="width: 32%" /><col style="width: 7%" />
            <col style="width: 15%" /><col style="width: 8%" /></colgroup>
          <thead>
            <tr><th>{{ $t('home.time') }}</th><th>{{ $t('home.skills') }}</th><th>{{ $t('home.file_name') }}</th><th>{{ $t('home.pages') }}</th><th>{{ $t('home.status') }}</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.file_id">
              <td class="dim nowrap">{{ ts(r.created_at) }}</td>
              <td>{{ r.skill_code }}</td>
              <td class="fname" :title="r.file_name">
                <span class="file-label">{{ r.file_name }}</span>
                <span v-if="r.child_count" class="split-note">{{ $t('home.contains') }} {{ r.child_count }} {{ $t('home.documents') }}</span>
              </td>
              <td>{{ r.page_count }}</td>
              <td><span class="chip" :class="`chip-${r.status}`">{{ stLabel(r.status) }}</span></td>
              <td class="row-act">
                <router-link v-if="r.status === 'pending_verification'"
                             :to="reviewLink(r.file_id)">
                  <button class="primary slim">Verify</button></router-link>
                <router-link v-else :to="reviewLink(r.file_id)">
                  <button class="ghost slim">{{ $t('home.view') }}</button></router-link>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <Skeleton v-else-if="isLoading" :rows="6" />
      <EmptyState v-else :title="$t('home.no_tasks_yet')" glyph="🚀">
        {{ $t('home.upload_your_first_document_to_start_processing_or_go') }}
        <template #action>
          <router-link to="/upload"><button class="primary">{{ $t('home.upload_document') }}</button></router-link>
          <router-link to="/skills"><button class="ghost">{{ $t('home.go_to_skill_center') }}</button></router-link>
        </template>
      </EmptyState>
    </section>
  </main>
</template>

<script setup lang="ts">
import { I18nT } from "vue-i18n";
import { useQuery } from "@tanstack/vue-query";
import TaskSourceSwitch from "../components/TaskSourceSwitch.vue";
import { useRoute } from "vue-router";
import { computed } from "vue";
import { api } from "../api";
import EmptyState from "../components/EmptyState.vue";
import PageHeader from "../components/PageHeader.vue";
import Skeleton from "../components/Skeleton.vue";
import { STATUS_LABELS } from "../labels";

const route = useRoute();
const source = computed(() => route.query.source === "api" ? "api" : "manual");
const { data: stats } = useQuery({
  queryKey: computed(() => ["home-stats", source.value]), queryFn: () => api.homeStats(source.value), refetchInterval: 10_000 });
const { data: files, isLoading } = useQuery({
  queryKey: computed(() => ["files", "home", source.value]),
  queryFn: () => api.files(1, { source: source.value }),
  refetchInterval: 8_000,
});
const rows = computed(() => (files.value?.data ?? []).slice(0, 8));

/** Tell the review page where 返回 should land (P10). */
const reviewLink = (fileId: string) =>
  ({ path: `/review/${fileId}`, query: { back: `/home?source=${source.value}` } });

const fmt = (v: number | undefined) => v == null ? "—" : v.toLocaleString();
const ts = (v: string | null) => v ? new Date(v).toLocaleString() : "-";
const stLabel = (s: string) => STATUS_LABELS()[s] ?? s;
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
.data-table { table-layout: fixed; min-width: 720px; }
.data-table td { overflow: hidden; text-overflow: ellipsis; }
.fname, .file-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-label { display: block; }
.split-note { display: block; margin-top: 3px; color: var(--accent); font-size: 11px; }
.nowrap { white-space: nowrap; }
.row-act { text-align: right; }
.slim { padding: 2px 12px; font-size: 12px; }
</style>
