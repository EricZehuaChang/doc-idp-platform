<template>
  <main class="page">
    <PageHeader title="技能中心" desc="技能 = 一类文档的抽取契约：字段、校验规则与模型绑定。">
      <!-- U-01 (2026-09-23): one 导入技能 entry; the format is chosen inside, so a
           future import source adds a menu item instead of another button -->
      <details class="import-menu" ref="importMenuEl">
        <summary class="btn-like" data-testid="import-menu">导入技能 ▾</summary>
        <div class="import-pop">
          <label class="file-btn">
            <input type="file" accept=".yaml,.yml" hidden @change="importYaml" />
            <span class="btn-like">导入 YAML 文件</span>
          </label>
          <button class="ghost" data-testid="import-package" @click="openPackageImport">
            导入加密技能包</button>
        </div>
      </details>
      <!-- one class or the other: `ghost primary` together leaves primary's dark
           text on ghost's transparent background, i.e. an invisible button -->
      <button :class="showGallery ? 'primary' : 'ghost'"
              @click="showGallery = !showGallery">从模板新建</button>
      <router-link to="/skills/new"><button class="primary">＋ 新建技能</button></router-link>
    </PageHeader>

    <!-- —— 9.15 WP7: import dialog (two-step, DropZone + passphrase) —— -->
    <AppModal v-if="importOpen" @close="closeImport">
      <div class="pkg-modal" data-testid="import-modal">
        <template v-if="!importPreview">
          <h4>导入加密技能包</h4>
          <DropZone accept=".zip" @add="onImportFiles" />
          <label class="lbl">口令</label>
          <input v-model="importPass" class="txt" type="password"
                 placeholder="导出时生成的一次性口令" />
          <div class="modal-actions">
            <button class="ghost" @click="closeImport">取消</button>
            <button class="primary" data-testid="import-parse"
                    :disabled="!importFile || !importPass" @click="parsePackage">
              解析</button>
          </div>
        </template>
        <template v-else>
          <h4>确认导入</h4>
          <p class="dim">
            {{ importPreview.skill.name }} ·
            {{ importPreview.skill.mode === "advanced" ? "高级" : "标准" }} ·
            {{ importPreview.skill.field_count }} 字段 ·
            {{ importPreview.skill.category_count }} 类别 ·
            来源 v{{ importPreview.skill.version }}（{{ importPreview.skill.status }}）</p>
          <div v-if="importPreview.missing_channels.length" class="pkg-sec">
            <b>缺失的模型通道</b>
            <div v-for="ch in importPreview.missing_channels" :key="ch" class="pkg-row">
              <span>{{ ch }}</span>
              <select v-model="channelMap[ch]">
                <option value="">使用平台默认</option>
                <option v-for="a in importPreview.available_channels" :key="a"
                        :value="a">{{ a }}</option>
              </select>
            </div>
          </div>
          <div v-if="importPreview.references.length" class="pkg-sec">
            <b>引用依赖</b>
            <div v-for="rf in importPreview.references" :key="rf.category_id"
                 class="pkg-row">
              <span>{{ rf.doc_type }} ← {{ rf.skill_code }}</span>
              <select v-model="refMap[rf.category_id]">
                <option value="inline">转为本技能内联字段</option>
                <option v-for="a in refOptions" :key="a" :value="'skill:' + a">
                  映射到 {{ a }}</option>
              </select>
            </div>
          </div>
          <div class="pkg-sec">
            <b>同名冲突</b>
            <p v-if="!importPreview.conflicts.length" class="dim">无同名技能，将新建。</p>
            <template v-else>
              <label class="chk"><input v-model="conflict" type="radio"
                                        value="rename" /> 重命名后导入</label>
              <label class="chk"><input v-model="conflict" type="radio"
                                        value="overwrite" /> 覆盖现有技能（新建草稿）</label>
              <div v-if="conflict === 'overwrite'" class="pkg-row">
                <span>覆盖目标</span>
                <select v-model="overwriteTarget">
                  <option v-for="cf in importPreview.conflicts" :key="cf.code"
                          :value="cf.code" :disabled="cf.recoverable">
                    {{ cf.name }}{{ cf.recoverable ? "（需先恢复）" : "" }}</option>
                </select>
              </div>
            </template>
          </div>
          <template v-if="conflict === 'rename'">
            <label class="lbl">新技能代码（可修改，需全局唯一）</label>
            <input v-model="newCode" class="txt" data-testid="import-new-code" />
          </template>
          <div class="modal-actions">
            <button class="ghost" @click="closeImport">取消</button>
            <button class="primary" data-testid="import-commit"
                    :disabled="conflict === 'overwrite' && !overwriteTarget"
                    @click="commitImport">确认导入</button>
          </div>
        </template>
      </div>
    </AppModal>

    <!-- —— 9.15 WP7: export dialog (version pick + one-time passphrase) —— -->
    <AppModal v-if="exportOpen" @close="closeExport">
      <div class="pkg-modal" data-testid="export-modal">
        <template v-if="!exportResult">
          <h4>导出加密技能包</h4>
          <p class="dim">包含与不包含：技能定义与被引用包会打包；金样本、样本原件、
            运行结果、任何密钥一律不包含。</p>
          <label class="lbl">版本</label>
          <select v-model="exportVersion" class="txt">
            <option v-for="v in exportVersions" :key="v.key" :value="v.version">
              {{ v.label }}</option>
          </select>
          <div class="modal-actions">
            <button class="ghost" @click="closeExport">取消</button>
            <button class="primary" data-testid="export-generate"
                    @click="doExport">生成加密包</button>
          </div>
        </template>
        <template v-else>
          <h4>包已生成</h4>
          <p class="dim">{{ exportResult.file_name }}（{{ exportResult.size }} 字节）</p>
          <p>口令：<code class="pkg-pass" data-testid="export-passphrase">{{
            exportResult.passphrase }}</code></p>
          <p class="dim">口令只显示这一次，请与文件分开发送。</p>
          <div class="modal-actions">
            <button class="ghost" @click="copyPass">复制口令</button>
            <button class="primary" data-testid="export-download"
                    @click="downloadExport">下载 .zip</button>
            <!-- #9: the dialog used to be unclosable after generating -->
            <button data-testid="export-done" @click="closeExport">完成</button>
          </div>
        </template>
      </div>
    </AppModal>

    <!-- gallery on demand; the empty state opens it by itself below -->
    <section v-if="showGallery && items.length" class="card-panel pad">
      <h3 class="gal-title">选一个最接近的单据类型</h3>
      <p class="dim gal-hint">导入后是草稿，字段可改可删，确认无误再发布。</p>
      <SkillTemplateGallery />
    </section>

    <section class="card-panel">
      <!-- lifecycle buckets (需求7 + WP4 P19-23): roster, disabled, archive -->
      <div class="tabs">
        <button v-for="b in buckets" :key="b.key"
                :class="{ primary: bucket === b.key }" @click="bucket = b.key">
          {{ b.label }} <span v-if="b.count" class="badge-r">{{ b.count }}</span>
        </button>
        <span v-if="bucket === 'deleted'" class="dim hint">
          已删除的技能保留全部版本与历史，可一键恢复。</span>
      </div>
      <!-- 9.15 R01/R02: instant search + 6-way sort, state lives in the URL -->
      <div class="roster-bar">
        <input class="search" v-model="q" type="search"
               placeholder="搜索技能名称或代码" />
        <select v-model="sort" class="sort" aria-label="排序方式">
          <option v-for="o in SORTS" :key="o.key" :value="o.key">{{ o.label }}</option>
        </select>
      </div>
      <table v-if="items.length" class="data-table">
        <thead>
          <tr><th>技能代码</th><th>名称</th><th>简介</th><th>发布版本</th><th>状态</th><th class="th-act"></th></tr>
        </thead>
        <tbody>
          <tr v-for="s in items" :key="s.skill_code" class="row-link"
              @click="$router.push(`/skills/${s.skill_code}`)">
            <td class="code">{{ s.skill_code }}</td>
            <td>{{ s.name }}</td>
            <td class="desc" :title="s.description || '该技能未填写简介'">
              <template v-if="s.description">
                {{ s.description }}
                <small v-if="s.description_source?.startsWith('draft')"
                       class="dim">(草稿措辞)</small>
              </template>
              <span v-else class="dim">—</span>
            </td>
            <td class="dim ver">
              {{ s.published_version != null ? `v${s.published_version}` : "未发布" }}
            </td>
            <td><span class="chip" :class="`chip-${s.state}`">
              {{ s.state === "active" ? "启用" : s.state === "deleted" ? "已删除" : "已停用" }}</span></td>
            <!-- 9.15 R22: actions collapse into one ⋯ menu -->
            <td class="row-act" @click.stop>
              <RowActionMenu :items="menuItems(s)" @select="onMenu(s, $event)" />
            </td>
          </tr>
        </tbody>
      </table>
      <Skeleton v-else-if="isLoading" :rows="5" />
      <EmptyState v-else-if="q.trim()" title="未找到相关技能" glyph="🔍">
        换个关键词试试，或清空搜索查看本页签全部技能。
        <template #action>
          <button @click="q = ''">清空搜索</button>
        </template>
      </EmptyState>
      <EmptyState v-else-if="bucket === 'deleted'" title="没有已删除的技能" glyph="🗄️">
        删除技能后它会出现在这里，版本与历史全部保留，可随时恢复。
      </EmptyState>
      <EmptyState v-else-if="allCount > 0" title="此页签下没有技能" glyph="⚙️">
        切到其他页签查看启用中或已停用的技能。
      </EmptyState>
      <div v-else class="first-run">
        <EmptyState title="还没有技能" glyph="⚙️">
          技能 = 一类单据抽哪些字段。从下面挑一个最接近的开始，比从空白想字段快得多；
          导入后是草稿，字段可改可删，确认无误再发布。
          <template #action>
            <router-link to="/skills/new">
              <button class="ghost">从空白新建</button></router-link>
          </template>
        </EmptyState>
        <SkillTemplateGallery class="first-gallery" />
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, downloadFile, type SkillInfo } from "../api";
import EmptyState from "../components/EmptyState.vue";
import PageHeader from "../components/PageHeader.vue";
import AppModal from "../components/AppModal.vue";
import DropZone from "../components/DropZone.vue";
import RowActionMenu, { type RowMenuItem } from "../components/RowActionMenu.vue";
import Skeleton from "../components/Skeleton.vue";
import SkillTemplateGallery from "../components/SkillTemplateGallery.vue";
import { toast } from "../toast";

const route = useRoute();
const router = useRouter();
const qc = useQueryClient();
const showGallery = ref(false);

// —— 9.15 R01/R02: search + sort, mirrored into the URL so refresh/back/share
// reproduce the view; search and sort only ever apply inside the active bucket
type SortKey = "name_asc" | "name_desc" | "updated_desc" | "updated_asc"
  | "created_desc" | "created_asc";
const SORTS: { key: SortKey; label: string }[] = [
  { key: "updated_desc", label: "最近更新" },
  { key: "updated_asc", label: "最早更新" },
  { key: "created_desc", label: "最近创建" },
  { key: "created_asc", label: "最早创建" },
  { key: "name_asc", label: "名称 A→Z" },
  { key: "name_desc", label: "名称 Z→A" },
];
const DEFAULT_SORT: SortKey = "updated_desc";

const bucket = ref<"active" | "disabled" | "deleted">(
  (["active", "disabled", "deleted"] as const).includes(route.query.bucket as any)
    ? route.query.bucket as any : "active");
const q = ref(String(route.query.q ?? ""));
const sort = ref((SORTS.some((o) => o.key === route.query.sort)
                  ? route.query.sort as SortKey : DEFAULT_SORT));

watch([q, sort, bucket], () => {
  const query: Record<string, string> = {};
  if (q.value.trim()) query.q = q.value.trim();
  if (sort.value !== DEFAULT_SORT) query.sort = sort.value;
  if (bucket.value !== "active") query.bucket = bucket.value;
  router.replace({ query });
});

// URL → state sync for in-SPA navigation (back/forward, deep links that land
// on an already-mounted view); guarded so it cannot loop with the writer above
watch(() => route.query.bucket, (v) => {
  const val = (["active", "disabled", "deleted"] as const).includes(v as "active")
    ? v as typeof bucket.value : "active";
  if (val !== bucket.value) bucket.value = val;
});
watch(() => route.query.q, (v) => {
  const val = String(v ?? "");
  if (val !== q.value) q.value = val;
});
watch(() => route.query.sort, (v) => {
  const val = SORTS.some((o) => o.key === v) ? v as SortKey : DEFAULT_SORT;
  if (val !== sort.value) sort.value = val;
});

const { data, isLoading } = useQuery({
  queryKey: computed(() => ["skills", bucket.value]),
  queryFn: () => bucket.value === "deleted" ? api.skillsByState("deleted") : api.skills(),
});
const byName = (a: SkillInfo, b: SkillInfo) =>
  a.name.localeCompare(b.name, "zh-Hans-CN", { numeric: true })
  || a.skill_code.localeCompare(b.skill_code);
const items = computed<SkillInfo[]>(() => {
  const rows = data.value ?? [];
  const inBucket = bucket.value === "deleted"
    ? rows
    : rows.filter((s) => s.state === bucket.value);
  const kw = q.value.trim().toLowerCase();
  const hits = kw
    ? inBucket.filter((s) => s.name.toLowerCase().includes(kw)
                        || s.skill_code.toLowerCase().includes(kw))
    : inBucket;
  const dir = sort.value.endsWith("_desc") ? -1 : 1;
  const key = sort.value.startsWith("name") ? "name" : sort.value.startsWith("updated") ? "updated_at" : "created_at";
  return [...hits].sort((a, b) => {
    if (key === "name") return dir * byName(a, b);
    const av = (a[key as "created_at" | "updated_at"] ?? "");
    const bv = (b[key as "created_at" | "updated_at"] ?? "");
    return dir * (av < bv ? -1 : av > bv ? 1 : byName(a, b));
  });
});

// badge counts: one list call covers both roster buckets; the archive is cheap
const { data: allData } = useQuery({
  queryKey: ["skills", "counts"],
  queryFn: () => api.skills(),
});
const { data: deletedData } = useQuery({
  queryKey: ["skills", "deleted-count"],
  queryFn: () => api.skillsByState("deleted"),
});
const allCount = computed(() => allData.value?.length ?? 0);
const buckets = computed(() => [
  { key: "active" as const, label: "启用中",
    count: (allData.value ?? []).filter((s) => s.state === "active").length },
  { key: "disabled" as const, label: "已停用",
    count: (allData.value ?? []).filter((s) => s.state === "disabled").length },
  { key: "deleted" as const, label: "已删除",
    count: deletedData.value?.length ?? 0 },
]);

// —— 9.15 R22: per-state menu items (删除二次确认沿用 del()) ——
function menuItems(s: SkillInfo): RowMenuItem[] {
  const common: RowMenuItem[] = [
    { key: "edit", label: "编辑" },
    { key: "export", label: "导出" },
  ];
  if (s.state === "active") {
    return [...common, { key: "disable", label: "停用" }, { key: "delete", label: "删除", danger: true }];
  }
  if (s.state === "disabled") {
    return [...common, { key: "enable", label: "启用" }, { key: "delete", label: "删除", danger: true }];
  }
  return [{ key: "restore", label: "恢复" }, { key: "edit", label: "编辑（查看）" }];
}
// —— 9.15 WP7: encrypted package export / two-step import ——
const exportOpen = ref(false);
const exportVersions = ref<{ key: string; version: number; label: string }[]>([]);
const exportVersion = ref<number | null>(null);
const exportResult = ref<{ file_name: string; content_base64: string;
                           passphrase: string; sha256: string; size: number }
  | null>(null);
let exportCode = "";

async function openExport(s: SkillInfo) {
  exportCode = s.skill_code;
  exportResult.value = null;
  try {
    const d = await api.skillDetail(s.skill_code);
    exportVersions.value = d.versions.map((v) => ({
      key: `${v.version}-${v.status}`, version: v.version,
      label: `v${v.version}${v.status === "published" ? "" : "（草稿）"}` }));
    exportVersion.value = d.selected_version ?? d.versions[0]?.version ?? null;
    exportOpen.value = true;
  } catch (e) { toast.error(e); }
}

async function doExport() {
  if (!exportVersion.value) return;
  try {
    exportResult.value = await api.skillPackageExport(exportCode,
                                                      exportVersion.value);
  } catch (e) { toast.error(e); }
}

function downloadExport() {
  const r = exportResult.value;
  if (!r) return;
  const bytes = Uint8Array.from(atob(r.content_base64), (ch) => ch.charCodeAt(0));
  const blob = new Blob([bytes], { type: "application/zip" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = r.file_name;
  a.click();
  URL.revokeObjectURL(a.href);
}

function closeExport() {
  exportOpen.value = false;
  exportResult.value = null;      // the one-time passphrase must not linger
}

function closeImport() {
  importOpen.value = false;
  importPreview.value = null;
  importFile.value = null;
  importPass.value = "";
}

async function copyPass() {
  const r = exportResult.value;
  if (!r) return;
  try { await navigator.clipboard.writeText(r.passphrase); toast.ok("口令已复制"); }
  catch { toast.error("复制失败，请手动选择口令"); }
}

const importOpen = ref(false);
const importMenuEl = ref<HTMLDetailsElement>();
/** Picking an import format closes the 导入技能 menu behind it. */
function closeImportMenu() {
  if (importMenuEl.value) importMenuEl.value.open = false;
}
function openPackageImport() {
  closeImportMenu();
  importOpen.value = true;
}
const importFile = ref<File | null>(null);
const importPass = ref("");
const importPreview = ref<Awaited<ReturnType<typeof api.skillPackageImportPreview>>
  | null>(null);
const channelMap = ref<Record<string, string>>({});
const refMap = ref<Record<string, string>>({});
const conflict = ref<"rename" | "overwrite">("rename");
const overwriteTarget = ref("");
const newCode = ref("");
const refOptions = ref<string[]>([]);

function onImportFiles(files: File[]) {
  const f = files[0];
  if (!f) return;
  if (!f.name.toLowerCase().endsWith(".zip")) {
    toast.error("请选择 .zip 加密技能包（.yaml 仍走旧版导入）");
    return;
  }
  importFile.value = f;
}

async function parsePackage() {
  if (!importFile.value) return;
  try {
    const prev = await api.skillPackageImportPreview(importFile.value,
                                                     importPass.value);
    importPreview.value = prev;
    conflict.value = prev.conflicts.length ? "rename" : "rename";
    overwriteTarget.value = prev.conflicts.find((x) => !x.recoverable)?.code ?? "";
    const suggested = (prev.skill.code || "imported").toLowerCase()
      .replace(/[^a-z0-9_]/g, "_").slice(0, 24);
    newCode.value = `${suggested}_${Math.random().toString(16).slice(2, 8)}`;
    channelMap.value = Object.fromEntries(
      prev.missing_channels.map((c) => [c, ""]));
    refMap.value = Object.fromEntries(
      prev.references.map((r) => [r.category_id, "inline"]));
    try {
      refOptions.value = (await api.referenceSkills()).skills
        .map((x) => x.skill_code);
    } catch { refOptions.value = []; }
  } catch (e) { toast.error(e); }
}

async function commitImport() {
  const prev = importPreview.value;
  if (!prev) return;
  try {
    const r = await api.skillPackageImportCommit({
      import_token: prev.import_token, sha256: prev.sha256,
      channel_map: channelMap.value, ref_map: refMap.value,
      conflict: conflict.value,
      new_code: conflict.value === "rename" ? newCode.value : undefined,
      new_name: conflict.value === "rename" ? undefined : undefined,
      overwrite_target: conflict.value === "overwrite"
        ? overwriteTarget.value : undefined });
    importOpen.value = false;
    importPreview.value = null;
    toast.ok(`已导入为 v${r.version} 草稿`);
    router.push(`/skills/${r.skill_code}`);
    qc.invalidateQueries({ queryKey: ["skills"] });
  } catch (e) { toast.error(e); }
}

function onMenu(s: SkillInfo, key: string) {
  if (key === "edit") router.push(`/skills/${s.skill_code}`);
  else if (key === "export") openExport(s);
  else if (key === "disable") toggle(s, "disable");
  else if (key === "enable") toggle(s, "enable");
  else if (key === "delete") del(s);
  else if (key === "restore") restore(s);
}

async function toggle(s: SkillInfo, action: "disable" | "enable") {
  const verb = action === "disable" ? "停用" : "启用";
  const why = action === "disable"
    ? "停用后不能再提交，已有任务不受影响。" : "启用后重新占用套餐技能席位。";
  if (!confirm(`${verb}技能 ${s.name || s.skill_code}？${why}`)) return;
  try {
    await api.skillSetState(s.skill_code, action);
    toast.ok(`技能 ${s.skill_code} 已${verb}`);
    qc.invalidateQueries({ queryKey: ["skills"] });
  } catch (e) { toast.error(e); }
}

async function del(s: SkillInfo) {
  if (!confirm(`删除技能 ${s.name || s.skill_code}？它会进入归档页签，版本与历史保留，可随时恢复。`)) return;
  try {
    await api.skillDelete(s.skill_code);
    toast.ok(`技能 ${s.skill_code} 已删除（可在归档中恢复）`);
    qc.invalidateQueries({ queryKey: ["skills"] });
  } catch (e) { toast.error(e); }
}

async function restore(s: SkillInfo) {
  if (!confirm(`恢复技能 ${s.name || s.skill_code}？恢复后重新出现在启用列表并可直接提交。`)) return;
  try {
    await api.skillRestore(s.skill_code);
    toast.ok(`技能 ${s.skill_code} 已恢复`);
    qc.invalidateQueries({ queryKey: ["skills"] });
  } catch (e) { toast.error(e); }
}

async function importYaml(ev: Event) {
  closeImportMenu();
  const file = (ev.target as HTMLInputElement).files?.[0];
  if (!file) return;
  try {
    const r = await api.skillImport(file);
    toast.ok(`已导入 ${r.skill_code}（v${r.version} 草稿）`);
    qc.invalidateQueries({ queryKey: ["skills"] });
    router.push(`/skills/${r.skill_code}`);
  } catch (e) { toast.error(e); }
  (ev.target as HTMLInputElement).value = "";
}
</script>

<style scoped>
.pad { padding: 14px 16px 18px; }
.gal-title { margin: 0 0 2px; font-size: 14px; }
.gal-hint { margin: 0 0 12px; font-size: 12px; }
.first-run { padding: 8px 16px 24px; }
.first-gallery { margin-top: 4px; }
.tabs { display: flex; gap: 8px; align-items: center; padding: 12px 14px 0; }
.tabs .badge-r { margin-left: 4px; }
.tabs .hint { margin-left: auto; font-size: 12px; }
.roster-bar { display: flex; gap: 10px; align-items: center; padding: 10px 14px 2px; }
.roster-bar .search { max-width: 260px; }
.roster-bar .sort { width: auto; font-size: 12.5px; }
.file-btn .btn-like { border: 1px solid var(--border); background: var(--bg-raised);
  border-radius: 6px; padding: 6px 14px; cursor: pointer; display: inline-block; }
.file-btn .btn-like:hover { border-color: var(--accent); }
.import-menu { position: relative; }
/* the summary looks like the header's ghost buttons (style.css `button`) */
.import-menu summary { list-style: none; cursor: pointer; border: 1px solid var(--border);
  background: transparent; color: var(--text); border-radius: 6px; padding: 6px 14px; }
.import-menu summary:hover { border-color: var(--accent); }
.import-menu summary::-webkit-details-marker { display: none; }
.import-pop { position: absolute; z-index: 60; top: calc(100% + 4px); right: 0;
  background: var(--bg-panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 8px; display: flex; flex-direction: column; gap: 6px; min-width: 170px;
  box-shadow: 0 8px 24px rgba(0,0,0,.12); }
.import-pop .file-btn .btn-like, .import-pop button { width: 100%; text-align: left; }
.code { font-family: Consolas, monospace; color: var(--blue); }
.desc { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ver { font-variant-numeric: tabular-nums; white-space: nowrap; }
.row-link { cursor: pointer; }
.row-act, .th-act { text-align: right; white-space: nowrap; }
.slim { padding: 2px 12px; font-size: 12px; }

/* —— 9.15 WP7 package dialogs —— */
.pkg-modal { width: 560px; max-width: 92vw; max-height: 84vh; overflow: auto;
  display: flex; flex-direction: column; gap: 10px; }
.pkg-sec { display: flex; flex-direction: column; gap: 6px;
  border-top: 1px solid var(--border); padding-top: 8px; }
.pkg-row { display: flex; align-items: center; justify-content: space-between;
  gap: 10px; font-size: 13px; }
.pkg-row select { max-width: 260px; }
.chk { display: flex; gap: 6px; align-items: center; font-size: 13px; }
.pkg-pass { user-select: all; font-size: 15px; letter-spacing: 1px; }
.lbl { font-size: 13px; }
</style>
