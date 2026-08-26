<template>
  <main class="page">
    <PageHeader title="任务" desc="全部处理任务的工作台：筛选、认领、进入校验。">
      <button class="ghost" title="刷新" @click="reload">⟳ 刷新</button>
      <router-link to="/upload"><button class="primary">＋ 上传文档</button></router-link>
    </PageHeader>

    <section class="card-panel block">
      <div class="bar">
        <span v-if="activeCount" class="dim">已应用 {{ activeCount }} 个筛选条件</span>
        <span v-else class="dim">在表头下方逐列筛选</span>
        <button class="ghost slim" :disabled="!activeCount" @click="clearFilters">
          清空筛选</button>
        <span class="dim total-note">共 {{ total }} 条</span>
      </div>

      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr><th>时间</th><th>技能</th><th>文件名</th><th>类型</th><th>大小</th>
                <th>页数</th><th>状态</th><th>更新时间</th><th>校验</th><th class="th-act"></th></tr>
            <!-- filter row: each control sits under the column it narrows, and
                 every one of them is applied server-side (P09 follow-up) -->
            <tr class="filter-row">
              <th>
                <input type="date" v-model="f.date_from" :max="f.date_to || undefined"
                       title="创建时间起" />
                <input type="date" v-model="f.date_to" :min="f.date_from || undefined"
                       title="创建时间止" />
              </th>
              <th>
                <select v-model="f.skill_code">
                  <option value="">全部</option>
                  <option v-for="s in skillList" :key="s.skill_code" :value="s.skill_code">
                    {{ s.name || s.skill_code }}</option>
                </select>
              </th>
              <th><input type="search" v-model="f.file_name" placeholder="文件名包含…" /></th>
              <th>
                <select v-model="f.file_type">
                  <option value="">全部</option>
                  <!-- /formats ships suffixes with the dot; the column shows
                       them bare, so strip it for the label only -->
                  <option v-for="t in typeOptions" :key="t" :value="t">
                    {{ t.replace(".", "").toUpperCase() }}</option>
                </select>
              </th>
              <th class="no-filter"><span class="dim">—</span></th>
              <th class="pages-cell">
                <input type="number" min="0" v-model="f.pages_min" placeholder="≥" />
                <input type="number" min="0" v-model="f.pages_max" placeholder="≤" />
              </th>
              <th>
                <select v-model="f.status">
                  <option value="">全部</option>
                  <option v-for="(label, key) in STATUS_LABELS" :key="key" :value="key">
                    {{ label }}</option>
                </select>
              </th>
              <th>
                <input type="date" v-model="f.updated_from" :max="f.updated_to || undefined"
                       title="更新时间起" />
                <input type="date" v-model="f.updated_to" :min="f.updated_from || undefined"
                       title="更新时间止" />
              </th>
              <th>
                <select v-model="f.verify">
                  <option value="">全部</option>
                  <option value="verified">已校验</option>
                  <option value="error">有错误</option>
                  <option value="none">未校验</option>
                </select>
              </th>
              <th class="th-act"></th>
            </tr>
          </thead>
          <tbody v-if="rows.length">
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

      <Skeleton v-if="!rows.length && isLoading" :rows="10" />
      <EmptyState v-else-if="!rows.length && activeCount" title="没有符合筛选条件的任务" glyph="🔍">
        换个条件，或清空筛选查看全部任务。
        <template #action>
          <button @click="clearFilters">清空筛选</button>
        </template>
      </EmptyState>
      <EmptyState v-else-if="!rows.length" title="还没有任务" glyph="🚀">
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
import { computed, onUnmounted, reactive, ref, watch } from "vue";
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

// One key per column of the table. Seeded from the URL so refresh, browser
// back, a shared link and the Home CTA (/tasks?status=…) all reproduce the view.
type Filters = {
  date_from: string; date_to: string; skill_code: string; file_name: string;
  file_type: string; pages_min: string; pages_max: string; status: string;
  updated_from: string; updated_to: string; verify: string;
};
const EMPTY: Filters = {
  date_from: "", date_to: "", skill_code: "", file_name: "", file_type: "",
  pages_min: "", pages_max: "", status: "", updated_from: "", updated_to: "",
  verify: "",
};
const fromQuery = (): Filters => {
  const out = { ...EMPTY };
  for (const k of Object.keys(EMPTY) as (keyof Filters)[])
    out[k] = String(route.query[k] ?? "");
  return out;
};
const f = reactive<Filters>(fromQuery());
// what actually queries — free-text columns debounce, pickers apply at once
const applied = ref<Filters>({ ...f });

const TEXT_KEYS: (keyof Filters)[] = ["file_name", "pages_min", "pages_max"];
const activeCount = computed(() =>
  (Object.keys(EMPTY) as (keyof Filters)[]).filter((k) => f[k] !== "").length);

let timer: ReturnType<typeof setTimeout> | undefined;
watch(f, () => {
  const instant = (Object.keys(EMPTY) as (keyof Filters)[])
    .some((k) => !TEXT_KEYS.includes(k) && f[k] !== applied.value[k]);
  clearTimeout(timer);
  if (instant) applied.value = { ...f };
  else timer = setTimeout(() => { applied.value = { ...f }; }, 350);
}, { deep: true });
onUnmounted(() => clearTimeout(timer));

watch(applied, () => { page.value = 1; }, { deep: true });

// keep the URL in step: refresh / back / copy-link reproduce the same view
watch([page, applied], () => {
  const q: Record<string, string> = {};
  for (const [k, v] of Object.entries(applied.value)) if (v) q[k] = v;
  if (page.value > 1) q.page = String(page.value);
  router.replace({ path: "/tasks", query: q });
}, { deep: true });
// external navigation into /tasks?status=… (Home CTA) still steers the filter
watch(() => route.query.status, (v) => {
  if (route.path === "/tasks" && String(v || "") !== f.status) f.status = String(v || "");
});

const { data: skillData } = useQuery({ queryKey: ["skills"], queryFn: api.skills });
const skillList = computed<SkillInfo[]>(() => skillData.value ?? []);
// the type dropdown reads the upload capability contract — the same single
// source of truth the upload page uses, so the two can never drift
const { data: limits } = useQuery({ queryKey: ["formats"], queryFn: api.formats });
const typeOptions = computed(() => limits.value?.extensions ?? []);

const { data: files, isLoading } = useQuery({
  queryKey: computed(() => ["files", page.value, applied.value]),
  queryFn: () => api.files(page.value, {
    ...applied.value,
    pages_min: applied.value.pages_min ? Number(applied.value.pages_min) : undefined,
    pages_max: applied.value.pages_max ? Number(applied.value.pages_max) : undefined,
  }),
  refetchInterval: 8_000,
  placeholderData: (prev) => prev,
});
const rows = computed<FileRow[]>(() => files.value?.data ?? []);
const total = computed(() => files.value?.total ?? 0);
const totalPages = computed(() => files.value?.total_pages ?? 1);

function clearFilters() {
  Object.assign(f, EMPTY);
  clearTimeout(timer);
  applied.value = { ...EMPTY };
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
.bar { display: flex; gap: 12px; align-items: center; margin-bottom: 10px;
  font-size: 12px; }
.total-note { margin-left: auto; }
.table-scroll { overflow-x: auto; }
/* the filter row is part of the header: it scrolls horizontally with the
   columns it belongs to, so a control never drifts away from its column */
.filter-row th { padding: 4px 6px 8px; font-weight: 400; vertical-align: top; }
.filter-row input, .filter-row select { width: 100%; min-width: 84px;
  font-size: 11.5px; padding: 2px 4px; }
.filter-row input[type="date"] { min-width: 116px; }
.filter-row input + input { margin-top: 3px; }
.filter-row .pages-cell input { min-width: 52px; display: inline-block; width: 48%; }
.filter-row .pages-cell input + input { margin: 0 0 0 4%; }
.filter-row .no-filter { text-align: center; }
.fname { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nowrap { white-space: nowrap; }
.ftype { font-size: 11px; color: var(--red); font-weight: 700; }
.err { color: var(--red); font-size: 12px; cursor: help; }
.row-act, .th-act { text-align: right; }
.slim { padding: 2px 12px; font-size: 12px; }
.pager { display: flex; gap: 12px; align-items: center; justify-content: flex-end;
  margin-top: 12px; font-size: 13px; }
.dim { color: var(--text-dim); }
</style>
