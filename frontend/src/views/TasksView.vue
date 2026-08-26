<template>
  <main class="page">
    <PageHeader title="任务" desc="全部处理任务的工作台：筛选、认领、进入校验。">
      <button class="ghost" title="刷新" @click="reload">⟳ 刷新</button>
      <router-link to="/upload"><button class="primary">＋ 上传文档</button></router-link>
    </PageHeader>

    <section class="card-panel block">
      <!-- filters: every condition is applied server-side and mirrored into the
           URL query, so refresh / back / a shared link all reproduce the view -->
      <div class="toolbar">
        <input class="search" v-model="kw" type="search"
               placeholder="搜索文件名或技能代码…" @keyup.enter="applyNow" />
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="(label, key) in STATUS_LABELS" :key="key" :value="key">
            {{ label }}</option>
        </select>
        <select v-model="skillFilter">
          <option value="">全部技能</option>
          <option v-for="s in skillList" :key="s.skill_code" :value="s.skill_code">
            {{ s.name || s.skill_code }}</option>
        </select>
        <label class="date-lbl">从
          <input type="date" v-model="dateFrom" :max="dateTo || undefined" /></label>
        <label class="date-lbl">到
          <input type="date" v-model="dateTo" :min="dateFrom || undefined" /></label>
        <button class="ghost slim" :disabled="!hasFilters" @click="clearFilters">
          清空筛选</button>
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
                             :to="reviewLink(r.file_id)">
                  <button class="primary slim">Verify</button></router-link>
                <router-link v-else :to="reviewLink(r.file_id)">
                  <button class="ghost slim">查看</button></router-link>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <Skeleton v-else-if="isLoading" :rows="10" />
      <EmptyState v-else-if="hasFilters" title="没有符合筛选条件的任务" glyph="🔍">
        换个条件，或清空筛选查看全部任务。
        <template #action>
          <button @click="clearFilters">清空筛选</button>
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
import { computed, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, fetchBlob, type FileRow, type SkillInfo } from "../api";
import EmptyState from "../components/EmptyState.vue";
import PageHeader from "../components/PageHeader.vue";
import Skeleton from "../components/Skeleton.vue";
import { STATUS_LABELS } from "../labels";

const route = useRoute();
const router = useRouter();
const qc = useQueryClient();
const page = ref(Math.max(1, Number(route.query.page) || 1));

// filter state seeded from the URL: /tasks?status=…&q=…&skill=…&from=…&to=…
// (Home's CTA deep-links here, and refresh must not drop the view)
const statusFilter = ref(String(route.query.status || ""));
const skillFilter = ref(String(route.query.skill || ""));
const dateFrom = ref(String(route.query.from || ""));
const dateTo = ref(String(route.query.to || ""));
const kw = ref(String(route.query.q || ""));
const kwApplied = ref(kw.value);        // debounced copy that actually queries

const hasFilters = computed(() =>
  !!(statusFilter.value || skillFilter.value || dateFrom.value || dateTo.value || kw.value));

// typing shouldn't fire a request per keystroke
let kwTimer: ReturnType<typeof setTimeout> | undefined;
watch(kw, (v) => {
  clearTimeout(kwTimer);
  kwTimer = setTimeout(() => { kwApplied.value = v; }, 350);
});
function applyNow() {
  clearTimeout(kwTimer);
  kwApplied.value = kw.value;
}
onUnmounted(() => clearTimeout(kwTimer));

// any narrowing invalidates the current page number
watch([statusFilter, skillFilter, dateFrom, dateTo, kwApplied], () => { page.value = 1; });

// keep the URL in step so refresh/back/copy-link all reproduce the view
watch([page, statusFilter, skillFilter, dateFrom, dateTo, kwApplied], () => {
  const q: Record<string, string> = {};
  if (statusFilter.value) q.status = statusFilter.value;
  if (skillFilter.value) q.skill = skillFilter.value;
  if (dateFrom.value) q.from = dateFrom.value;
  if (dateTo.value) q.to = dateTo.value;
  if (kwApplied.value) q.q = kwApplied.value;
  if (page.value > 1) q.page = String(page.value);
  router.replace({ path: "/tasks", query: q });
});
// external navigation into /tasks?status=… (Home CTA) still steers the filter
watch(() => route.query.status, (v) => {
  if (route.path === "/tasks") statusFilter.value = String(v || "");
});

const { data: skillData } = useQuery({ queryKey: ["skills"], queryFn: api.skills });
const skillList = computed<SkillInfo[]>(() => skillData.value ?? []);

const { data: files, isLoading } = useQuery({
  queryKey: computed(() => ["files", page.value, statusFilter.value, skillFilter.value,
                            dateFrom.value, dateTo.value, kwApplied.value]),
  queryFn: () => api.files(page.value, {
    status: statusFilter.value || undefined,
    skill_code: skillFilter.value || undefined,
    date_from: dateFrom.value || undefined,
    date_to: dateTo.value || undefined,
    q: kwApplied.value || undefined,
  }),
  refetchInterval: 8_000,
  placeholderData: (prev) => prev,
});
const rows = computed<FileRow[]>(() => files.value?.data ?? []);
const total = computed(() => files.value?.total ?? 0);
const totalPages = computed(() => files.value?.total_pages ?? 1);

function clearFilters() {
  statusFilter.value = "";
  skillFilter.value = "";
  dateFrom.value = "";
  dateTo.value = "";
  kw.value = "";
  applyNow();
}
function reload() { qc.invalidateQueries({ queryKey: ["files"] }); }

/** Carry the current list view into the review page so its 返回 comes back to
 *  the same filtered page instead of a reset list (P09/P10). */
function reviewLink(fileId: string) {
  return { path: `/review/${fileId}`, query: { back: route.fullPath } };
}

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
.toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px;
  flex-wrap: wrap; }
.search { min-width: 220px; flex: 1 1 220px; max-width: 340px; }
.date-lbl { display: inline-flex; align-items: center; gap: 5px; font-size: 12px;
  color: var(--text-dim); }
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
