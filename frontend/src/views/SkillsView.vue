<template>
  <main class="page">
    <PageHeader :title="$t('skills.skill_center')" :desc="$t('skills.a_skill_is_the_extraction_contract_for_a_document')">
      <!-- U-01 (2026-09-23): one 导入技能 entry; the format is chosen inside, so a
           future import source adds a menu item instead of another button -->
      <details class="import-menu" ref="importMenuEl">
        <summary class="btn-like" data-testid="import-menu">{{ $t('skills.import_skill') }}</summary>
        <div class="import-pop">
          <label class="file-btn">
            <input type="file" accept=".yaml,.yml" hidden @change="importYaml" />
            <span class="btn-like">{{ $t('skills.import_yaml_file') }}</span>
          </label>
          <button class="ghost" data-testid="import-package" @click="openPackageImport">
            {{ $t('skills.import_encrypted_skill_package') }}</button>
        </div>
      </details>
      <!-- one class or the other: `ghost primary` together leaves primary's dark
           text on ghost's transparent background, i.e. an invisible button -->
      <button :class="showGallery ? 'primary' : 'ghost'"
              @click="showGallery = !showGallery">{{ $t('skills.create_from_template') }}</button>
      <router-link to="/skills/new"><button class="primary">{{ $t('skills.new_skill') }}</button></router-link>
    </PageHeader>

    <!-- —— 9.15 WP7: import dialog (two-step, DropZone + passphrase) —— -->
    <AppModal v-if="importOpen" @close="closeImport">
      <div class="pkg-modal" data-testid="import-modal">
        <template v-if="!importPreview">
          <h4>{{ $t('skills.import_encrypted_skill_package') }}</h4>
          <DropZone accept=".zip" @add="onImportFiles" />
          <label class="lbl">{{ $t('skills.passphrase') }}</label>
          <input v-model="importPass" class="txt" type="password"
                 :placeholder="$t('skills.one_time_passphrase_generated_on_export')" />
          <div class="modal-actions">
            <button class="ghost" @click="closeImport">{{ $t('skills.cancel') }}</button>
            <button class="primary" data-testid="import-parse"
                    :disabled="!importFile || !importPass" @click="parsePackage">
              {{ $t('skills.parse') }}</button>
          </div>
        </template>
        <template v-else>
          <h4>{{ $t('skills.confirm_import') }}</h4>
          <p class="dim">
            {{ importPreview.skill.name }} ·
            {{ importPreview.skill.mode === "advanced" ? $t('skills.advanced') : $t('skills.standard') }} ·
            {{ importPreview.skill.field_count }} {{ $t('skills.fields') }}
            {{ importPreview.skill.category_count }} {{ $t('skills.category_source_v') }}{{ importPreview.skill.version }}（{{ importPreview.skill.status }}）</p>
          <div v-if="importPreview.missing_channels.length" class="pkg-sec">
            <b>{{ $t('skills.missing_model_channel') }}</b>
            <div v-for="ch in importPreview.missing_channels" :key="ch" class="pkg-row">
              <span>{{ ch }}</span>
              <select v-model="channelMap[ch]">
                <option value="">{{ $t('skills.use_platform_default') }}</option>
                <option v-for="a in importPreview.available_channels" :key="a"
                        :value="a">{{ a }}</option>
              </select>
            </div>
          </div>
          <div v-if="importPreview.references.length" class="pkg-sec">
            <b>{{ $t('skills.referenced_dependencies') }}</b>
            <div v-for="rf in importPreview.references" :key="rf.category_id"
                 class="pkg-row">
              <span>{{ rf.doc_type }} ← {{ rf.skill_code }}</span>
              <select v-model="refMap[rf.category_id]">
                <option value="inline">{{ $t('skills.convert_to_inline_fields_in_this_skill') }}</option>
                <option v-for="a in refOptions" :key="a" :value="'skill:' + a">
                  {{ $t('skills.map_to') }} {{ a }}</option>
              </select>
            </div>
          </div>
          <div class="pkg-sec">
            <b>{{ $t('skills.name_conflict') }}</b>
            <p v-if="!importPreview.conflicts.length" class="dim">{{ $t('skills.no_skill_with_the_same_name_a_new_one') }}</p>
            <template v-else>
              <label class="chk"><input v-model="conflict" type="radio"
                                        value="rename" /> {{ $t('skills.rename_and_import') }}</label>
              <label class="chk"><input v-model="conflict" type="radio"
                                        value="overwrite" /> {{ $t('skills.overwrite_existing_skill_create_new_draft') }}</label>
              <div v-if="conflict === 'overwrite'" class="pkg-row">
                <span>{{ $t('skills.overwrite_target') }}</span>
                <select v-model="overwriteTarget">
                  <option v-for="cf in importPreview.conflicts" :key="cf.code"
                          :value="cf.code" :disabled="cf.recoverable">
                    {{ cf.name }}{{ cf.recoverable ? $t('skills.restore_required_first') : "" }}</option>
                </select>
              </div>
            </template>
          </div>
          <template v-if="conflict === 'rename'">
            <label class="lbl">{{ $t('skills.new_skill_code_editable_must_be_globally_unique') }}</label>
            <input v-model="newCode" class="txt" data-testid="import-new-code" />
          </template>
          <div class="modal-actions">
            <button class="ghost" @click="closeImport">{{ $t('skills.cancel') }}</button>
            <button class="primary" data-testid="import-commit"
                    :disabled="conflict === 'overwrite' && !overwriteTarget"
                    @click="commitImport">{{ $t('skills.confirm_import') }}</button>
          </div>
        </template>
      </div>
    </AppModal>

    <!-- —— 9.15 WP7: export dialog (version pick + one-time passphrase) —— -->
    <AppModal v-if="exportOpen" @close="closeExport">
      <div class="pkg-modal" data-testid="export-modal">
        <template v-if="!exportResult">
          <h4>{{ $t('skills.export_encrypted_skill_package') }}</h4>
          <p class="dim">{{ $t('skills.included_and_excluded_skill_definitions_and_referenced_packages_are') }}</p>
          <label class="lbl">{{ $t('skills.version') }}</label>
          <select v-model="exportVersion" class="txt">
            <option v-for="v in exportVersions" :key="v.key" :value="v.version">
              {{ v.label }}</option>
          </select>
          <div class="modal-actions">
            <button class="ghost" @click="closeExport">{{ $t('skills.cancel') }}</button>
            <button class="primary" data-testid="export-generate"
                    @click="doExport">{{ $t('skills.generate_encrypted_package') }}</button>
          </div>
        </template>
        <template v-else>
          <h4>{{ $t('skills.package_generated') }}</h4>
          <p class="dim">{{ exportResult.file_name }}（{{ exportResult.size }} {{ $t('skills.bytes') }}</p>
          <I18nT keypath="skills.package_passphrase" tag="p" scope="global"><template #passphrase><code class="pkg-pass" data-testid="export-passphrase">{{ exportResult.passphrase }}</code></template></I18nT>
          <p class="dim">{{ $t('skills.the_passphrase_is_shown_only_once_send_it_separately') }}</p>
          <div class="modal-actions">
            <button class="ghost" @click="copyPass">{{ $t('skills.copy_passphrase') }}</button>
            <button class="primary" data-testid="export-download"
                    @click="downloadExport">{{ $t('skills.download_zip') }}</button>
            <!-- #9: the dialog used to be unclosable after generating -->
            <button data-testid="export-done" @click="closeExport">{{ $t('skills.completed') }}</button>
          </div>
        </template>
      </div>
    </AppModal>

    <!-- gallery on demand; the empty state opens it by itself below -->
    <section v-if="showGallery && items.length" class="card-panel pad">
      <h3 class="gal-title">{{ $t('skills.pick_the_closest_document_type') }}</h3>
      <p class="dim gal-hint">{{ $t('skills.after_import_it_is_a_draft_fields_can_be') }}</p>
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
          {{ $t('skills.deleted_skills_retain_all_versions_and_history_and_can') }}</span>
      </div>
      <!-- 9.15 R01/R02: instant search + 6-way sort, state lives in the URL -->
      <div class="roster-bar">
        <input class="search" v-model="q" type="search"
               :placeholder="$t('skills.search_skill_name_or_code')" />
        <select v-model="sort" class="sort" :aria-label="$t('skills.sort_by')">
          <option v-for="o in SORTS" :key="o.key" :value="o.key">{{ o.label }}</option>
        </select>
      </div>
      <table v-if="items.length" class="data-table">
        <thead>
          <tr><th>{{ $t('skills.skill_code') }}</th><th>{{ $t('skills.name') }}</th><th>{{ $t('skills.description') }}</th><th>{{ $t('skills.publish_version') }}</th><th>{{ $t('skills.status') }}</th><th class="th-act"></th></tr>
        </thead>
        <tbody>
          <tr v-for="s in items" :key="s.skill_code" class="row-link"
              @click="$router.push(`/skills/${s.skill_code}`)">
            <td class="code">{{ s.skill_code }}</td>
            <td>{{ s.name }}</td>
            <td class="desc" :title="s.description || $t('skills.this_skill_has_no_description')">
              <template v-if="s.description">
                {{ s.description }}
                <small v-if="s.description_source?.startsWith('draft')"
                       class="dim">{{ $t('skills.draft_wording') }}</small>
              </template>
              <span v-else class="dim">—</span>
            </td>
            <td class="dim ver">
              {{ s.published_version != null ? `v${s.published_version}` : $t('skills.unpublished') }}
            </td>
            <td><span class="chip" :class="`chip-${s.state}`">
              {{ s.state === "active" ? $t('skills.enabled_f4f0ea') : s.state === "deleted" ? $t('skills.deleted') : $t('skills.disabled') }}</span></td>
            <!-- 9.15 R22: actions collapse into one ⋯ menu -->
            <td class="row-act" @click.stop>
              <RowActionMenu :items="menuItems(s)" @select="onMenu(s, $event)" />
            </td>
          </tr>
        </tbody>
      </table>
      <Skeleton v-else-if="isLoading" :rows="5" />
      <EmptyState v-else-if="q.trim()" :title="$t('skills.no_matching_skills_found')" glyph="🔍">
        {{ $t('skills.try_another_keyword_or_clear_the_search_to_view') }}
        <template #action>
          <button @click="q = ''">{{ $t('skills.clear_search') }}</button>
        </template>
      </EmptyState>
      <EmptyState v-else-if="bucket === 'deleted'" :title="$t('skills.no_deleted_skills')" glyph="🗄️">
        {{ $t('skills.deleted_skills_appear_here_with_all_versions_and_history') }}
      </EmptyState>
      <EmptyState v-else-if="allCount > 0" :title="$t('skills.no_skills_in_this_tab')" glyph="⚙️">
        {{ $t('skills.switch_to_another_tab_to_view_enabled_or_disabled') }}
      </EmptyState>
      <div v-else class="first-run">
        <EmptyState :title="$t('skills.no_skills_yet')" glyph="⚙️">
          {{ $t('skills.a_skill_which_fields_to_extract_from_a_type') }}
          <template #action>
            <router-link to="/skills/new">
              <button class="ghost">{{ $t('skills.create_from_blank') }}</button></router-link>
          </template>
        </EmptyState>
        <SkillTemplateGallery class="first-gallery" />
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { I18nT, useI18n } from "vue-i18n";
const { t } = useI18n();
import { translateMessage, type MessageDescriptor } from "../i18n";

import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, downloadFile, type SkillInfo } from "../api";
import { copyText } from "../clipboard";
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
const SORTS = computed<{ key: SortKey; label: string }[]>(() => [
  { key: "updated_desc", label: t('skills.recently_updated') },
  { key: "updated_asc", label: t('skills.oldest_updated') },
  { key: "created_desc", label: t('skills.recently_created') },
  { key: "created_asc", label: t('skills.oldest_created') },
  { key: "name_asc", label: t('skills.name_ascending') },
  { key: "name_desc", label: t('skills.name_descending') },
]);
const DEFAULT_SORT: SortKey = "updated_desc";

const bucket = ref<"active" | "disabled" | "deleted">(
  (["active", "disabled", "deleted"] as const).includes(route.query.bucket as any)
    ? route.query.bucket as any : "active");
const q = ref(String(route.query.q ?? ""));
const sort = ref((SORTS.value.some((o) => o.key === route.query.sort)
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
  const val = SORTS.value.some((o) => o.key === v) ? v as SortKey : DEFAULT_SORT;
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
  { key: "active" as const, label: t('skills.enabled'),
    count: (allData.value ?? []).filter((s) => s.state === "active").length },
  { key: "disabled" as const, label: t('skills.disabled'),
    count: (allData.value ?? []).filter((s) => s.state === "disabled").length },
  { key: "deleted" as const, label: t('skills.deleted'),
    count: deletedData.value?.length ?? 0 },
]);

// —— 9.15 R22: per-state menu items (删除二次确认沿用 del()) ——
function menuDefinitions(s: SkillInfo): (Omit<RowMenuItem, "label"> & { message: MessageDescriptor })[] {
  const common: (Omit<RowMenuItem, "label"> & { message: MessageDescriptor })[] = [
    { key: "edit", message: { key: 'skills.menu_edit' } },
    { key: "export", message: { key: 'skills.menu_export' } },
  ];
  if (s.state === "active") {
    return [...common, { key: "disable", message: { key: 'skills.menu_disable' } }, { key: "delete", message: { key: 'skills.menu_delete' }, danger: true }];
  }
  if (s.state === "disabled") {
    return [...common, { key: "enable", message: { key: 'skills.menu_enable' } }, { key: "delete", message: { key: 'skills.menu_delete' }, danger: true }];
  }
  return [{ key: "restore", message: { key: 'skills.menu_restore' } }, { key: "edit", message: { key: 'skills.menu_edit_view' } }];
}
const menuItems = (skill: SkillInfo): RowMenuItem[] =>
  menuDefinitions(skill).map(({ message, ...item }) => ({ ...item, label: translateMessage(message) }));
// —— 9.15 WP7: encrypted package export / two-step import ——
const exportOpen = ref(false);
const exportVersionRows = ref<{ version: number; status: string }[]>([]);
const exportVersions = computed(() => exportVersionRows.value.map(v => ({
  key: String(v.version) + "-" + v.status, version: v.version,
  label: t(v.status === 'published' ? 'skills.published_version' : 'skills.draft_version', { version: v.version }),
})));
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
    exportVersionRows.value = d.versions;
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
  try { await copyText(r.passphrase); toast.ok(t('skills.passphrase_copied')); }
  catch { toast.error(t('skills.copy_failed_please_select_the_passphrase_manually')); }
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
    toast.error(t('skills.please_select_an_encrypted_zip_skill_package_yaml_still'));
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
    toast.ok(t('skills.imported_draft', { version: r.version }));
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
  const verb = action === "disable" ? t('skills.disable') : t('skills.enabled_f4f0ea');
  const why = action === "disable"
    ? t('skills.after_disabling_it_can_no_longer_be_submitted_existing') : t('skills.after_enabling_it_will_occupy_a_plan_skill_seat');
  if (!confirm(t('skills.change_state_confirm', { action: verb, name: s.name || s.skill_code, reason: why }))) return;
  try {
    await api.skillSetState(s.skill_code, action);
    toast.ok(t('skills.state_changed', { code: s.skill_code, action: verb }));
    qc.invalidateQueries({ queryKey: ["skills"] });
  } catch (e) { toast.error(e); }
}

async function del(s: SkillInfo) {
  if (!confirm(t('skills.delete_confirm', { name: s.name || s.skill_code }))) return;
  try {
    await api.skillDelete(s.skill_code);
    toast.ok(t('skills.deleted_restore_available', { code: s.skill_code }));
    qc.invalidateQueries({ queryKey: ["skills"] });
  } catch (e) { toast.error(e); }
}

async function restore(s: SkillInfo) {
  if (!confirm(t('skills.restore_confirm', { name: s.name || s.skill_code }))) return;
  try {
    await api.skillRestore(s.skill_code);
    toast.ok(t('skills.restored', { code: s.skill_code }));
    qc.invalidateQueries({ queryKey: ["skills"] });
  } catch (e) { toast.error(e); }
}

async function importYaml(ev: Event) {
  closeImportMenu();
  const file = (ev.target as HTMLInputElement).files?.[0];
  if (!file) return;
  try {
    const r = await api.skillImport(file);
    toast.ok(t('skills.skill_imported', { code: r.skill_code, version: r.version }));
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
