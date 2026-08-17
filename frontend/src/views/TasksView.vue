<template>
  <main class="page">
    <PageHeader title="任务" desc="全部处理任务的工作台：筛选、认领、进入校验。">
      <button class="ghost" title="刷新" @click="reload">⟳ 刷新</button>
      <router-link to="/upload"><button class="primary">＋ 上传文档</button></router-link>
    </PageHeader>

    <section class="card-panel block">
      <!-- toolbar: filters left, count right -->
      <div class="toolbar">
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="(label, key) in STATUS_LABELS" :key="key" :value="key">
            {{ label }}</option>
        </select>
        <span class="dim total-note">共 {{ total }} 条</span>
      </div>

      <div class="table-scroll" v-if="rows.length">
        <table class="data-table">
          <thead>
            <tr><th>时间</th><th>技能</th><th>文件名</th><th>类型</th><th>大小</th>
                <th>页数</th><th>状态</th><th>更新时间</th><th>校验</th><th class="th-act"></th></tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.file_id" @mouseenter="prefetch(r)">
              <td class="dim nowrap">{{ ts(r.created_at) }}</td>
              <td>{{ r.skill_code }}</td>
              <td class="fname" :title="r.file_name">{{ r.file_name }}</td>
              <td><span class="ftype">{{ r.type }}</span></td>
              <td class="dim">{{ size(r.size) }}</td>
              <td>{{ r.page_count }}</td>
              <td><span class="chip" :class="`chip-${r.status}`">{{ stLabel(r.status) }}</span></td>
              <td class="dim nowrap">{{ ts(r.updated_at) }}</td>
              <td>
                <span v-if="r.verified_by" class="dim">✓ {{ r.verified_by }}</span>
                <span v-else-if="r.error" class="err" :title="r.error">⚠ 错误</span>
                <span v-else class="dim">-</span>
              </td>
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
      <Skeleton v-else-if="isLoading" :rows="10" />
      <EmptyState v-else-if="statusFilter" title="该状态下没有任务" glyph="🔍">
        换个状态筛选，或清除筛选查看全部任务。
        <template #action>
          <button @click="statusFilter = ''">清除筛选</button>
        </template>
      </EmptyState>
      <EmptyState v-else title="还没有任务" glyph="🚀">
        上传第一份文档开始处理，或通过 API <code>POST /api/v1/process</code> 提交。
        <template #action>
          <router-link to="/upload"><button class="primary">上传文档</button></router-link>
        </template>
      </EmptyState>

      <div class="pager" v-if="totalPages > 1">
        <button :disabled="page <= 1" @click="page--">上一页</button>
        <span class="dim">{{ page }} / {{ totalPages }}</span>
        <button :disabled="page >= totalPages" @click="page++">下一页</button>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api, fetchBlob, type FileRow } from "../api";
import EmptyState from "../components/EmptyState.vue";
import PageHeader from "../components/PageHeader.vue";
import Skeleton from "../components/Skeleton.vue";
import { STATUS_LABELS } from "../labels";

const route = useRoute();
const qc = useQueryClient();
const page = ref(1);
// deep-linkable filter: /tasks?status=pending_verification (Home CTA uses it)
const statusFilter = ref(String(route.query.status || ""));
watch(statusFilter, () => { page.value = 1; });
watch(() => route.query.status, (v) => { statusFilter.value = String(v || ""); });

const { data: files, isLoading } = useQuery({
  queryKey: computed(() => ["files", page.value, statusFilter.value]),
  queryFn: () => api.files(page.value, statusFilter.value || undefined),
  refetchInterval: 8_000,
  placeholderData: (prev) => prev,
});
const rows = computed<FileRow[]>(() => files.value?.data ?? []);
const total = computed(() => files.value?.total ?? 0);
const totalPages = computed(() => files.value?.total_pages ?? 1);

function reload() { qc.invalidateQueries({ queryKey: ["files"] }); }

// hover prefetch (caching §9.0 layer ②): warm the original before Verify
const prefetched = new Set<string>();
function prefetch(r: FileRow) {
  if (r.status !== "pending_verification" || prefetched.has(r.file_id)) return;
  prefetched.add(r.file_id);
  if (/\.(png|jpe?g|bmp|webp)$/i.test(r.file_name)) {
    fetchBlob(api.downloadUrl(r.file_id)).catch(() => prefetched.delete(r.file_id));
  }
}

const ts = (v: string | null) => v ? new Date(v).toLocaleString() : "-";
function size(n: number | null): string {
  if (n == null) return "-";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(2)} MB`;
}
const stLabel = (s: string) => STATUS_LABELS[s] ?? s;
</script>

<style scoped>
.block { padding: 14px 16px; }
.toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 10px; }
.total-note { margin-left: auto; font-size: 12px; }
.table-scroll { overflow-x: auto; }
.fname { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nowrap { white-space: nowrap; }
.ftype { font-size: 11px; color: var(--red); font-weight: 700; }
.err { color: var(--red); font-size: 12px; cursor: help; }
.row-act, .th-act { text-align: right; }
.slim { padding: 2px 12px; font-size: 12px; }
.pager { display: flex; gap: 12px; align-items: center; justify-content: flex-end;
  margin-top: 12px; font-size: 13px; }
</style>
