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
               placeholder="搜索文件名、技能名称或代码…" @keyup.enter="applyNow" />
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
        <table class="data-table ledger">
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
              <td class="dim nowrap">
                <span class="stamp">{{ tsDate(r.created_at) }}</span>
                <span class="stamp-time">{{ tsTime(r.created_at) }}</span>
              </td>
              <!-- 9.15 R20: display name first, code as the second small line -->
              <td class="skill-cell" :title="r.skill_name || r.skill_code">
                {{ r.skill_name || r.skill_code }}
                <div class="code-sub">{{ r.skill_code }}</div>
              </td>
              <!-- 9.15 R21: initiator snapshot; legacy rows render "—" -->
              <td class="initiator-cell" :title="initiatorTitle(r)">
                <template v-if="r.initiator_label">{{
                  whoLabel(r.initiator_label) }}</template>
                <span v-else class="dim">—</span>
              </td>
              <td class="fname" :title="r.file_name">
                {{ r.file_name }}
                <span v-if="r.child_count" class="split-note">
                  内含 {{ r.child_count }} 份单据<span v-if="r.pending_children">
                    · {{ r.pending_children }} 份待校验</span>
                </span>
              </td>
              <td><span class="ftype">{{ r.type }}</span></td>
              <td class="dim">{{ size(r.size) }}</td>
              <td>{{ r.page_count }}</td>
              <td class="nowrap" :title="speedTitle(r)">
                <span v-if="r.processing_seconds != null" class="speed">
                  {{ fmtSeconds(r.processing_seconds) }}
                  <template v-if="r.page_count > 0">
                    · {{ (r.page_count / r.processing_seconds).toFixed(2) }} 页/s</template>
                </span>
                <span v-else class="dim">—</span>
              </td>
              <td><span class="chip" :class="`chip-${r.status}`">{{ stLabel(r.status) }}</span></td>
              <td class="dim nowrap">
                <span class="stamp">{{ tsDate(r.updated_at) }}</span>
                <span class="stamp-time">{{ tsTime(r.updated_at) }}</span>
              </td>
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
                <!-- 2026-09-18 需求: 只有管理员能删除任务 -->
                <button v-if="isAdmin" class="del slim" data-testid="task-delete"
                        :title="`删除任务「${r.file_name}」`"
                        @click.stop="askDelete(r)">删除</button>
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

      <!-- 2026-09-18 需求: 页码可直接跳转，不再只有上/下一页。
           省略号只压缩中间，首末页永远可点；跳转框接受输入并回车/失焦生效。 -->
      <div class="pager" v-if="totalPages > 1">
        <span class="dim range">
          {{ (page - 1) * PAGE_SIZE + 1 }}–{{ Math.min(page * PAGE_SIZE, total) }}
          / 共 {{ total }} 条
        </span>
        <div class="pager-ctrl">
          <button class="pg" :disabled="page <= 1" title="第一页"
                  data-testid="page-first" @click="goPage(1)">«</button>
          <button class="pg" :disabled="page <= 1" title="上一页"
                  data-testid="page-prev" @click="goPage(page - 1)">‹</button>
          <template v-for="(p, i) in pageNumbers" :key="`${p}-${i}`">
            <span v-if="p === 0" class="ellipsis">…</span>
            <button v-else class="pg num" :class="{ on: p === page }"
                    :disabled="p === page" :title="`第 ${p} 页`"
                    :data-testid="`page-${p}`" @click="goPage(p)">{{ p }}</button>
          </template>
          <button class="pg" :disabled="page >= totalPages" title="下一页"
                  data-testid="page-next" @click="goPage(page + 1)">›</button>
          <button class="pg" :disabled="page >= totalPages" title="最后一页"
                  data-testid="page-last" @click="goPage(totalPages)">»</button>
        </div>
        <label class="jump">
          跳至
          <!-- 直接读输入框的值，不用 v-model：type=number 在值违反 min 时不发
               input 事件、纯数字文本经 v-model 取回也可能是空串（两者实测都会让
               跳转静默失效）。这里自己解析并夹取到 [1, totalPages]。 -->
          <input type="text" inputmode="numeric" autocomplete="off" ref="jumpEl"
                 :value="jumpTo" data-testid="page-jump" placeholder="页码"
                 @keyup.enter="applyJump" @blur="applyJump" />
          页
        </label>
      </div>
    </section>

    <!-- 管理员删除任务：不可恢复，所以先摆清楚删的是哪一份、会删掉什么 -->
    <AppModal v-if="delTarget" @close="closeDelete">
      <div class="del-modal" data-testid="task-delete-modal">
        <h4>删除任务</h4>
        <p class="del-lead">
          「{{ delTarget.file_name }}」将被<strong>永久删除</strong>，无法恢复。
        </p>
        <dl class="del-facts">
          <dt>技能</dt>
          <dd>{{ delTarget.skill_name || delTarget.skill_code }}
            <span class="dim">（{{ delTarget.skill_code }}）</span></dd>
          <dt>页数 / 类型</dt>
          <dd>{{ delTarget.page_count }} 页 · {{ delTarget.type }}</dd>
          <dt>上传时间</dt>
          <dd>{{ ts(delTarget.created_at) }}</dd>
          <dt v-if="delTarget.child_count">内含单据</dt>
          <dd v-if="delTarget.child_count">
            {{ delTarget.child_count }} 份（一并删除）</dd>
        </dl>
        <p class="del-scope dim">
          删除范围：任务记录、识别结果与人工修正、原件与拆分文件、
          已生成的产出文件。<template v-if="inFlight(delTarget)">
            该任务仍在处理中，删除后它会停止处理并释放占用的额度。</template>
        </p>
        <div class="modal-actions">
          <button class="ghost" data-testid="task-delete-cancel"
                  @click="closeDelete">取消</button>
          <button class="danger" data-testid="task-delete-confirm" :disabled="deleting"
                  @click="confirmDelete">{{ deleting ? "删除中…" : "确认删除" }}</button>
        </div>
      </div>
    </AppModal>

    <!-- Filter menus are teleported and position:fixed: the table scrolls
         horizontally, and an ancestor with overflow-x:auto also clips
         vertically, which would cut a menu off inside the table. -->
    <Teleport to="body">
      <div v-if="openCol" ref="menuEl" class="fmenu" :style="menuStyle" @click.stop>
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
              {{ s.name || s.skill_code }}（{{ s.skill_code }}）{{ s.state === "deleted" ? " · 已删除" : "" }}
            </option>
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
import AppModal from "../components/AppModal.vue";
import EmptyState from "../components/EmptyState.vue";
import PageHeader from "../components/PageHeader.vue";
import Skeleton from "../components/Skeleton.vue";
import { STATUS_LABELS, initiatorLabel } from "../labels";
import { session } from "../session";
import { toast } from "../toast";

const route = useRoute();
const router = useRouter();
const qc = useQueryClient();
const page = ref(Math.max(1, Number(route.query.page) || 1));
/** Rows per page — the ledger endpoint is pinned to this in api.files(). */
const PAGE_SIZE = 20;

// Columns of the task table. `filter` names the menu each header opens; the
// keys it owns drive both the ⏷ highlight and the condition chip.
const COLUMNS = [
  { key: "created", label: "时间", filter: true, keys: ["date_from", "date_to"] },
  { key: "skill", label: "技能", filter: true, keys: ["skill_code"] },
  { key: "initiator", label: "发起人", filter: false, keys: [] },
  { key: "file_name", label: "文件名", filter: true, keys: ["file_name"] },
  { key: "file_type", label: "类型", filter: true, keys: ["file_type"] },
  { key: "size", label: "大小", filter: false, keys: [] },
  { key: "pages", label: "页数", filter: true, keys: ["pages_min", "pages_max"] },
  { key: "speed", label: "处理速度", filter: false, keys: [] },
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
const menuEl = ref<HTMLElement>();
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
  // Guard: closeMenu is wired to every document click / Esc / resize / scroll.
  // Without it, ANY click anywhere would run applyNow() -> a fresh `applied`
  // object -> the applied-watcher resets the list to page 1 — that is exactly
  // why the pager's 下一页 never seemed to work (需求9 root cause).
  if (!openCol.value) return;
  applyNow();
  openCol.value = "";
}
function clearCol(key: string) {
  const col = COLUMNS.find((c) => c.key === key);
  for (const k of col?.keys ?? []) f[k as FilterKey] = "";
  closeMenu();
}
function onEsc(e: KeyboardEvent) { if (e.key === "Escape") closeMenu(); }
/** The catch-all scroll closer must ignore scrolls INSIDE the open menu: the
 *  multi-row <select> listboxes scroll, and treating them as "scrolled away"
 *  closed the menu mid-scroll — wheel and scrollbar drag both died (需求2). */
function onScroll(e: Event) {
  if (menuEl.value && e.target instanceof Node && menuEl.value.contains(e.target)) return;
  closeMenu();
}
onMounted(() => {
  document.addEventListener("click", closeMenu);
  window.addEventListener("keydown", onEsc);
  window.addEventListener("resize", closeMenu);
  window.addEventListener("scroll", onScroll, true);
});
onUnmounted(() => {
  document.removeEventListener("click", closeMenu);
  window.removeEventListener("keydown", onEsc);
  window.removeEventListener("resize", closeMenu);
  window.removeEventListener("scroll", onScroll, true);
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
// 9.15 R20: the ledger keeps rows whose skill has since been deleted — the
// filter must cover them too, or a visible value could never be selected.
const { data: deletedSkillData } = useQuery({
  queryKey: ["skills", "deleted"],
  queryFn: () => api.skillsByState("deleted"),
});
const skillList = computed<SkillInfo[]>(() => [
  ...(skillData.value ?? []),
  ...(deletedSkillData.value ?? []),
]);
// the type list reads the upload capability contract — the same single source
// of truth the upload page uses, so the two can never drift
const { data: limits } = useQuery({ queryKey: ["formats"], queryFn: api.formats });
const typeOptions = computed(() => limits.value?.extensions ?? []);

/** One place for the ledger query so the delete flow can re-read the page it
 *  just changed without duplicating the filter plumbing. */
function filesPage(p: number, cond: Record<FilterKey, string>) {
  return api.files(p, {
    ...cond,
    pages_min: cond.pages_min ? Number(cond.pages_min) : undefined,
    pages_max: cond.pages_max ? Number(cond.pages_max) : undefined,
  });
}

const { data: files, isLoading } = useQuery({
  queryKey: computed(() => ["files", page.value, applied.value]),
  queryFn: () => filesPage(page.value, applied.value),
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

// —— 2026-09-18 需求: 页码跳转 ——
/** Windowed page list for the pager: 0 renders as an ellipsis. The window
 *  keeps a constant width while paging (grown on the short side near an end)
 *  so the buttons never reflow under the cursor. */
const WINDOW = 9;
const pageNumbers = computed<number[]>(() => {
  const last = totalPages.value;
  const cur = Math.min(Math.max(page.value, 1), last);
  if (last <= WINDOW + 2) return Array.from({ length: last }, (_, i) => i + 1);
  const span = WINDOW - 2;                       // slots between the two ends
  let lo = Math.max(2, cur - Math.floor(span / 2));
  let hi = Math.min(last - 1, lo + span - 1);
  lo = Math.max(2, hi - span + 1);
  const out: number[] = [1];
  if (lo > 2) out.push(0);
  for (let p = lo; p <= hi; p++) out.push(p);
  if (hi < last - 1) out.push(0);
  out.push(last);
  return out;
});

const jumpTo = ref("");
const jumpEl = ref<HTMLInputElement>();
/** Clear the box: the DOM value (not just the model) is reset by hand —
 *  an unchanged empty model never patches an identical empty vnode value, so
 *  the typed text would otherwise stay on screen after a rejected jump. */
function clearJump() {
  jumpTo.value = "";
  if (jumpEl.value) jumpEl.value.value = "";
}
function goPage(p: number) {
  const target = Math.min(Math.max(Math.round(p) || 1, 1), totalPages.value);
  clearJump();
  if (target !== page.value) page.value = target;
}
/** Enter or blur in the 跳至 box. Reads the field itself so a stale/absent
 *  model value can never swallow a jump; out-of-range numbers clamp, garbage
 *  is dropped. */
function applyJump(e: Event) {
  const raw = (e.target as HTMLInputElement | null)?.value?.trim() ?? "";
  const n = Number(raw);
  if (!raw || !Number.isFinite(n) || n < 1) { clearJump(); return; }
  goPage(n);
}

// —— 2026-09-18: admin task deletion ——
// The role gate is server-side (require_role("admin")); this only decides
// whether the button is offered. auth-required=false (lite/dev) is admin by
// definition, the same rule App.vue uses for the 平台设置 entry.
const isAdmin = computed(() => !session.authRequired || session.role === "admin");
const delTarget = ref<FileRow | null>(null);
const deleting = ref(false);
const inFlight = (r: FileRow) => r.status === "queued" || r.status === "processing";

function askDelete(r: FileRow) { delTarget.value = r; }
function closeDelete() { if (!deleting.value) delTarget.value = null; }

async function confirmDelete() {
  const r = delTarget.value;
  if (!r) return;
  deleting.value = true;
  try {
    const res = await api.deleteTask(r.file_id);
    toast.ok(res.deleted_children
      ? `任务「${r.file_name}」已删除（含 ${res.deleted_children} 份子单据）`
      : `任务「${r.file_name}」已删除`);
    delTarget.value = null;
    // the current page may now be empty: re-read it and step back if so
    const left = (await qc.fetchQuery({
      queryKey: ["files", page.value, applied.value],
      queryFn: () => filesPage(page.value, applied.value),
    })).data.length;
    if (!left && page.value > 1) page.value -= 1;
    qc.invalidateQueries({ queryKey: ["files"] });
    qc.invalidateQueries({ queryKey: ["skills"] });
  } catch (e) {
    toast.error(e);
  } finally {
    deleting.value = false;
  }
}

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
/** Ledger timestamps are stacked date-over-time: at 1280 the two 165px
 *  single-line stamps alone pushed the table 127px past the viewport. */
const tsDate = (v: string | null) =>
  v ? new Date(v).toLocaleDateString("zh-CN") : "-";
const tsTime = (v: string | null) =>
  v ? new Date(v).toLocaleTimeString("zh-CN", { hour12: false }) : "";
function size(n: number | null): string {
  if (n == null) return "-";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(2)} MB`;
}
const stLabel = (s: string) => STATUS_LABELS[s] ?? s;

// 9.15 R21: legacy rows (predating the initiator snapshot) show "—" with a
// hint instead of a fabricated name
function initiatorTitle(r: FileRow): string {
  if (r.initiator_label) return `发起人：${whoLabel(r.initiator_label)}`;
  return "该任务早于发起人记录功能";
}
const whoLabel = initiatorLabel;

// —— per-document processing speed (需求1) ——
function fmtSeconds(sec: number): string {
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}m ${s}s`;
}
function speedTitle(r: FileRow): string {
  if (r.processing_seconds == null) return "处理尚未完成";
  const pages = r.page_count > 0 ? ` · ${(r.page_count / r.processing_seconds).toFixed(2)} 页/s` : "";
  return `处理用时 ${fmtSeconds(r.processing_seconds)}${pages}`;
}
</script>

<style scoped>
.block { padding: 14px 16px; }
/* 2026-09-18 布局: 任务清单要在 1280 窗口里不出现横向滚动。实测溢出 127px，
   来源是列内边距(24px×12=288)与两个单行时间戳(165px×2)——在不删列、不丢信息
   的前提下收紧：内边距 24→18，时间改「日期 / 时间」两行。 */
.ledger th, .ledger td { padding: 7px 9px; }
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
/* 9.15 R19: keep the whole table inside the viewport so its horizontal
   scrollbar is always visible (it used to sit at the page bottom, below the
   fold) and the header stays readable while scrolling both axes. */
.table-scroll { overflow: auto; max-height: calc(100vh - 250px); }
.table-scroll thead th { position: sticky; top: 0; background: var(--bg-panel);
  z-index: 2; }
/* 操作列 sticky 右侧：窄窗口横向滚动时「查看/Verify」始终可达 */
.table-scroll .th-act { position: sticky; right: 0; background: var(--bg-panel);
  z-index: 3; }
.table-scroll .row-act { position: sticky; right: 0; background: var(--bg-panel);
  box-shadow: inset 8px 0 8px -8px rgba(0, 0, 0, 0.35); }
/* the filter affordance lives in the header cell itself — no extra row */
.th-cell { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
.fbtn { padding: 0 3px; font-size: 9px; line-height: 1.6; background: transparent;
  border-color: transparent; color: var(--text-dim); }
.fbtn:hover { color: var(--accent); border-color: transparent; }
.fbtn.on { color: var(--accent); }
.fbtn.on::after { content: "•"; font-size: 13px; line-height: 0; }
.fbtn.open { color: var(--accent); background: var(--bg-raised); }
.fname { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 9.15 R20/R19: skill name capped so long names cannot push the table wide
   (2026-09-18: 走查里最长的技能名 168px 会把「文件名」挤到 250px 上限，
   这里收到 14 个汉字上下——超出的部分由 title 提示补足) */
.skill-cell { max-width: 150px; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
/* 9.15 R21: initiator column — clipped, hint on hover via title */
.initiator-cell { max-width: 150px; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.code-sub { font-size: 10.5px; color: var(--text-dim); font-family: Consolas, monospace; }
/* stacked ledger timestamps: the date carries the weight, the clock is a hint */
.stamp { display: block; font-size: 12.5px; }
.stamp-time { display: block; font-size: 11px; color: var(--text-dim); }
.split-note { margin-left: 6px; color: var(--accent); font-size: 11px; }
.speed { font-size: 12px; color: var(--text); white-space: nowrap; }
.nowrap { white-space: nowrap; }
.ftype { font-size: 11px; color: var(--red); font-weight: 700; }
.err { color: var(--red); font-size: 12px; cursor: help; }
.row-act, .th-act { text-align: right; }
.slim { padding: 2px 12px; font-size: 12px; }
/* 2026-09-18: admin-only delete sits beside the view action but reads as a
   quiet text link — a destructive action must not look like the second button
   of a pair. Red only on hover/focus. */
.del { background: transparent; border-color: transparent; color: var(--text-dim);
  margin-left: 4px; padding: 2px 8px; }
.del:hover, .del:focus { color: var(--red); border-color: transparent;
  background: rgba(229, 83, 75, 0.10); }
.pager { display: flex; gap: 12px; align-items: center; justify-content: flex-end;
  margin-top: 12px; font-size: 13px; flex-wrap: wrap; }
.range { margin-right: auto; font-size: 12px; }
.pager-ctrl { display: flex; align-items: center; gap: 4px; }
/* 页码按钮：当前页高亮且不可点，省略号不可点 */
.pg { min-width: 30px; padding: 3px 7px; font-size: 12.5px; line-height: 1.5;
  background: transparent; border-color: var(--border); color: var(--text); }
.pg.num { font-variant-numeric: tabular-nums; }
.pg:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.pg.on { background: var(--accent); border-color: var(--accent);
  color: var(--accent-text); font-weight: 700; }
.ellipsis { padding: 0 2px; color: var(--text-dim); }
.jump { display: inline-flex; align-items: center; gap: 6px; color: var(--text-dim);
  font-size: 12.5px; }
.jump input { width: 62px; text-align: center; padding: 3px 6px; font-size: 12.5px; }
.dim { color: var(--text-dim); }
/* —— 删除确认弹窗 —— */
.del-modal { width: 520px; max-width: 92vw; }
.del-lead { font-size: 13.5px; margin: 0 0 12px; }
.del-lead strong { color: var(--red); }
.del-facts { display: grid; grid-template-columns: 84px 1fr; gap: 6px 10px;
  margin: 0 0 12px; font-size: 13px; }
.del-facts dt { color: var(--text-dim); }
.del-facts dd { margin: 0; overflow-wrap: anywhere; }
.del-scope { font-size: 12.5px; line-height: 1.6; margin: 0; }
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
