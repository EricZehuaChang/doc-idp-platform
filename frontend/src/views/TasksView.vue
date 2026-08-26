<template>
  <main class="page">
    <PageHeader title="任务" desc="全部处理任务的工作台：筛选、认领、进入校验。">
      <button class="ghost" title="刷新" @click="reload">⟳ 刷新</button>
      <router-link to="/upload"><button class="primary">＋ 上传文档</button></router-link>
    </PageHeader>

    <section class="card-panel block">
      <!-- condition bar: keyword search + what is currently narrowing the list.
           Column conditions live on the headers; this row is where they show up
           once applied, so the table itself keeps a normal single header. -->
      <div class="bar">
        <input class="search" v-model="f.q" type="search"
               placeholder="搜索文件名或技能代码…" @keyup.enter="applyNow" />
        <div class="chips">
          <span v-for="c in chips" :key="c.key" class="chip-f">
            {{ c.text }}
            <button class="chip-x" title="移除此条件" @click="c.clear()">✕</button>
          </span>
          <button v-if="chips.length" class="ghost slim" @click="clearFilters">清空全部</button>
          <span v-else class="dim hint">点表头的 ⏷ 按列筛选</span>
        </div>
        <span class="dim total-note">共 {{ total }} 条</span>
      </div>

      <div class="table-scroll" ref="scrollEl">
        <table class="data-table">
          <thead>
            <tr>
              <th v-for="col in COLUMNS" :key="col.key" :class="col.key === 'act' ? 'th-act' : ''">
                <span class="th-cell">
                  {{ col.label }}
                  <button v-if="col.filter" class="fbtn"
                          :class="{ on: activeCols.has(col.key), open: openCol === col.key }"
                          :ref="(el) => setTrigger(col.key, el as HTMLElement)"
                          :title="`按${col.label}筛选`"
                          @click.stop="toggleMenu(col.key)">⏷</button>
                </span>
              </th>
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
      <EmptyState v-else-if="!rows.length && chips.length" title="没有符合筛选条件的任务" glyph="🔍">
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

    <!-- Filter menus are teleported and position:fixed: the table scrolls
         horizontally, and an ancestor with overflow-x:auto also clips
         vertically, which would cut a menu off inside the table. -->
    <Teleport to="body">
      <div v-if="openCol" class="fmenu" :style="menuStyle" @click.stop>
        <template v-if="openCol === 'created'">
          <label>起<input type="date" v-model="f.date_from" :max="f.date_to || undefined" /></label>
          <label>止<input type="date" v-model="f.date_to" :min="f.date_from || undefined" /></label>
        </template>
        <template v-else-if="openCol === 'updated'">
          <label>起<input type="date" v-model="f.updated_from"
                          :max="f.updated_to || undefined" /></label>
          <label>止<input type="date" v-model="f.updated_to"
                          :min="f.updated_from || undefined" /></label>
        </template>
        <template v-else-if="openCol === 'skill'">
          <select v-model="f.skill_code" size="8" class="listbox">
            <option value="">全部技能</option>
            <option v-for="s in skillList" :key="s.skill_code" :value="s.skill_code">
              {{ s.name || s.skill_code }}</option>
          </select>
        </template>
        <template v-else-if="openCol === 'file_name'">
          <input type="search" v-model="f.file_name" placeholder="文件名包含…"
                 @keyup.enter="closeMenu" />
        </template>
        <template v-else-if="openCol === 'file_type'">
          <select v-model="f.file_type" size="6" class="listbox">
            <option value="">全部类型</option>
            <!-- /formats ships suffixes with the dot; show them bare -->
            <option v-for="t in typeOptions" :key="t" :value="t">
              {{ t.replace(".", "").toUpperCase() }}</option>
          </select>
        </template>
        <template v-else-if="openCol === 'pages'">
          <label>最少<input type="number" min="0" v-model="f.pages_min" placeholder="不限" /></label>
          <label>最多<input type="number" min="0" v-model="f.pages_max" placeholder="不限" /></label>
        </template>
        <template v-else-if="openCol === 'status'">
          <select v-model="f.status" size="7" class="listbox">
            <option value="">全部状态</option>
            <option v-for="(label, key) in STATUS_LABELS" :key="key" :value="key">
              {{ label }}</option>
          </select>
        </template>
        <template v-else-if="openCol === 'verify'">
          <select v-model="f.verify" size="4" class="listbox">
            <option value="">全部</option>
            <option value="verified">已校验</option>
            <option value="error">有错误</option>
            <option value="none">未校验</option>
          </select>
        </template>
        <div class="fmenu-act">
          <button class="ghost slim" @click="clearCol(openCol)">清除本列</button>
          <button class="primary slim" @click="closeMenu">完成</button>
        </div>
      </div>
    </Teleport>
  </main>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
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

// Columns of the task table. `filter` names the menu each header opens; the
// keys it owns drive both the ⏷ highlight and the condition chip.
const COLUMNS = [
  { key: "created", label: "时间", filter: true, keys: ["date_from", "date_to"] },
  { key: "skill", label: "技能", filter: true, keys: ["skill_code"] },
  { key: "file_name", label: "文件名", filter: true, keys: ["file_name"] },
  { key: "file_type", label: "类型", filter: true, keys: ["file_type"] },
  { key: "size", label: "大小", filter: false, keys: [] },
  { key: "pages", label: "页数", filter: true, keys: ["pages_min", "pages_max"] },
  { key: "status", label: "状态", filter: true, keys: ["status"] },
  { key: "updated", label: "更新时间", filter: true, keys: ["updated_from", "updated_to"] },
  { key: "verify", label: "校验", filter: true, keys: ["verify"] },
  { key: "act", label: "", filter: false, keys: [] },
] as const;

type FilterKey = "q" | "date_from" | "date_to" | "skill_code" | "file_name" | "file_type"
  | "pages_min" | "pages_max" | "status" | "updated_from" | "updated_to" | "verify";
const EMPTY: Record<FilterKey, string> = {
  q: "", date_from: "", date_to: "", skill_code: "", file_name: "", file_type: "",
  pages_min: "", pages_max: "", status: "", updated_from: "", updated_to: "", verify: "",
};
const KEYS = Object.keys(EMPTY) as FilterKey[];

// seeded from the URL so refresh, back, a shared link and the Home CTA
// (/tasks?status=…) all reproduce the same view
const f = reactive<Record<FilterKey, string>>(
  Object.fromEntries(KEYS.map((k) => [k, String(route.query[k] ?? "")])) as
    Record<FilterKey, string>);
const applied = ref<Record<FilterKey, string>>({ ...f });

// free text debounces; pickers apply the moment they change
const TEXT_KEYS: FilterKey[] = ["q", "file_name", "pages_min", "pages_max"];
let timer: ReturnType<typeof setTimeout> | undefined;
watch(f, () => {
  const instant = KEYS.some((k) => !TEXT_KEYS.includes(k) && f[k] !== applied.value[k]);
  clearTimeout(timer);
  if (instant) applied.value = { ...f };
  else timer = setTimeout(() => { applied.value = { ...f }; }, 350);
}, { deep: true });
function applyNow() {
  clearTimeout(timer);
  applied.value = { ...f };
}
onUnmounted(() => clearTimeout(timer));

watch(applied, () => { page.value = 1; }, { deep: true });
watch([page, applied], () => {
  const q: Record<string, string> = {};
  for (const k of KEYS) if (f[k]) q[k] = applied.value[k];
  for (const k of Object.keys(q)) if (!q[k]) delete q[k];
  if (page.value > 1) q.page = String(page.value);
  router.replace({ path: "/tasks", query: q });
}, { deep: true });
watch(() => route.query.status, (v) => {
  if (route.path === "/tasks" && String(v || "") !== f.status) f.status = String(v || "");
});

// —— per-column filter menus ——
const openCol = ref("");
const triggers = new Map<string, HTMLElement>();
const menuStyle = ref<Record<string, string>>({});
function setTrigger(key: string, el: HTMLElement | null) {
  if (el) triggers.set(key, el);
}
const activeCols = computed(() => {
  const s = new Set<string>();
  for (const c of COLUMNS)
    if (c.keys.some((k) => f[k as FilterKey])) s.add(c.key);
  return s;
});
function place(key: string) {
  const r = triggers.get(key)?.getBoundingClientRect();
  if (!r) return;
  // keep the menu inside the viewport when the column sits near the right edge
  const left = Math.min(r.left, window.innerWidth - 250);
  menuStyle.value = { top: `${r.bottom + 6}px`, left: `${Math.max(8, left)}px` };
}
function toggleMenu(key: string) {
  if (openCol.value === key) { closeMenu(); return; }
  openCol.value = key;
  place(key);
}
function closeMenu() {
  applyNow();
  openCol.value = "";
}
function clearCol(key: string) {
  const col = COLUMNS.find((c) => c.key === key);
  for (const k of col?.keys ?? []) f[k as FilterKey] = "";
  closeMenu();
}
function onEsc(e: KeyboardEvent) { if (e.key === "Escape") closeMenu(); }
onMounted(() => {
  document.addEventListener("click", closeMenu);
  window.addEventListener("keydown", onEsc);
  window.addEventListener("resize", closeMenu);
  window.addEventListener("scroll", closeMenu, true);
});
onUnmounted(() => {
  document.removeEventListener("click", closeMenu);
  window.removeEventListener("keydown", onEsc);
  window.removeEventListener("resize", closeMenu);
  window.removeEventListener("scroll", closeMenu, true);
});

// —— applied conditions, as removable chips in the top bar ——
const VERIFY_LABELS: Record<string, string> = {
  verified: "已校验", error: "有错误", none: "未校验",
};
const chips = computed(() => {
  const out: { key: string; text: string; clear: () => void }[] = [];
  const range = (key: string, label: string, a: FilterKey, b: FilterKey) => {
    if (!f[a] && !f[b]) return;
    out.push({ key, text: `${label} ${f[a] || "不限"} ~ ${f[b] || "不限"}`,
               clear: () => { f[a] = ""; f[b] = ""; } });
  };
  if (f.q) out.push({ key: "q", text: `关键字「${f.q}」`, clear: () => { f.q = ""; } });
  range("created", "时间", "date_from", "date_to");
  if (f.skill_code) {
    const s = skillList.value.find((x) => x.skill_code === f.skill_code);
    out.push({ key: "skill", text: `技能 ${s?.name || f.skill_code}`,
               clear: () => { f.skill_code = ""; } });
  }
  if (f.file_name) out.push({ key: "file_name", text: `文件名含「${f.file_name}」`,
                              clear: () => { f.file_name = ""; } });
  if (f.file_type) out.push({ key: "file_type",
                              text: `类型 ${f.file_type.replace(".", "").toUpperCase()}`,
                              clear: () => { f.file_type = ""; } });
  range("pages", "页数", "pages_min", "pages_max");
  if (f.status) out.push({ key: "status", text: `状态 ${STATUS_LABELS[f.status] ?? f.status}`,
                           clear: () => { f.status = ""; } });
  range("updated", "更新时间", "updated_from", "updated_to");
  if (f.verify) out.push({ key: "verify", text: `校验 ${VERIFY_LABELS[f.verify] ?? f.verify}`,
                           clear: () => { f.verify = ""; } });
  return out;
});

const { data: skillData } = useQuery({ queryKey: ["skills"], queryFn: api.skills });
const skillList = computed<SkillInfo[]>(() => skillData.value ?? []);
// the type list reads the upload capability contract — the same single source
// of truth the upload page uses, so the two can never drift
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
.bar { display: flex; gap: 12px; align-items: center; margin-bottom: 10px;
  flex-wrap: wrap; }
.search { min-width: 220px; flex: 0 1 280px; }
.chips { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; flex: 1;
  min-width: 0; }
.chip-f { display: inline-flex; align-items: center; gap: 4px; font-size: 11.5px;
  background: var(--bg-raised); border: 1px solid var(--border); border-radius: 999px;
  padding: 2px 4px 2px 10px; color: var(--text); }
.chip-x { padding: 0 4px; font-size: 11px; background: transparent;
  border-color: transparent; line-height: 1.4; }
.chip-x:hover { color: var(--red); border-color: transparent; }
.hint, .total-note { font-size: 12px; }
.total-note { margin-left: auto; }
.table-scroll { overflow-x: auto; }
/* the filter affordance lives in the header cell itself — no extra row */
.th-cell { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
.fbtn { padding: 0 3px; font-size: 9px; line-height: 1.6; background: transparent;
  border-color: transparent; color: var(--text-dim); }
.fbtn:hover { color: var(--accent); border-color: transparent; }
.fbtn.on { color: var(--accent); }
.fbtn.on::after { content: "•"; font-size: 13px; line-height: 0; }
.fbtn.open { color: var(--accent); background: var(--bg-raised); }
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

<style>
/* teleported to body, so not scoped */
.fmenu { position: fixed; z-index: 200; min-width: 210px; max-width: 260px;
  background: var(--bg-raised); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px; display: flex; flex-direction: column; gap: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45); }
.fmenu label { display: flex; align-items: center; gap: 8px; font-size: 12px;
  color: var(--text-dim); }
.fmenu label input { flex: 1; min-width: 0; }
.fmenu input, .fmenu select { font-size: 12px; padding: 3px 6px; }
.fmenu .listbox { width: 100%; }
.fmenu-act { display: flex; gap: 8px; justify-content: space-between;
  border-top: 1px solid var(--border); padding-top: 8px; }
</style>
