<template>
  <div class="sample-panel" :class="{ collapsed }">
    <header class="sp-head">
      <template v-if="!collapsed">
        <strong>{{ $t('samplePanel.reference_sample') }}</strong>
        <select v-if="samples.length" :value="activeId" :aria-label="$t('samplePanel.current_reference_sample')"
                @change="selectById(($event.target as HTMLSelectElement).value)">
          <option v-for="s in samples" :key="s.id" :value="s.id">{{ s.file_name }}</option>
        </select>
        <span v-else class="dim">{{ $t('samplePanel.not_uploaded_yet') }}</span>
      </template>
      <button class="sp-toggle" :aria-expanded="!collapsed" :title="collapsed ? $t('samplePanel.expand_sample') : $t('samplePanel.collapse_sample')"
              @click="toggleCollapse">{{ collapsed ? $t('samplePanel.expand_sample_c5a235') : $t('samplePanel.collapse') }}</button>
    </header>
    <template v-if="!collapsed">
      <div v-if="activeId" class="sp-stage">
        <DocStage :src="fileUrl" :file-name="activeName" :pages="stagePages?.length ? stagePages : pages"
                  :active-box="activeBox" :active-page="activePage ?? 1" :annotate="false"
                  :boxes="boxes" :active-key="activeKey || undefined" compact fit-width
                  @pick="emit('pick', $event)" />
      </div>
      <label v-else class="sp-empty">
        <span class="upload-mark">＋</span><strong>{{ $t('samplePanel.upload_a_reference_sample') }}</strong>
        <span class="dim">{{ $t('samplePanel.define_fields_against_the_original_document_or_generate_suggestions') }}</span>
        <span class="btn-like">{{ uploading ? $t('samplePanel.uploading') : $t('samplePanel.choose_file') }}</span>
        <input type="file" hidden :disabled="uploading" @change="upload" />
      </label>
      <footer v-if="activeId" class="sp-footer">
        <span class="dim">{{ samples.length }} {{ $t('samplePanel.samples_click_an_annotation_on_the_original_to_locate') }}</span>
        <label class="file-btn slim"><input type="file" hidden @change="upload" :disabled="uploading" />
          <span class="btn-like">{{ uploading ? $t('samplePanel.uploading') : $t('samplePanel.upload') }}</span></label>
        <button class="delete-sample" @click="removeSample(activeId)">{{ $t('samplePanel.delete_sample') }}</button>
      </footer>
      <p v-if="parseError" class="dim sp-warn">{{ $t('samplePanel.pre_parsing_failed') }}{{ parseError }}{{ $t('samplePanel.you_can_still_compare_manually') }}</p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
const { t } = useI18n();

import { computed, onMounted, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../api";
import DocStage from "./DocStage.vue";
import { toast } from "../toast";
type SampleRow = { id: string; file_name: string; skill_code: string | null; created_at: string };
type PageDim = { page_no: number; width: number; height: number };
const props = defineProps<{
  skillCode?: string;
  boxes?: { key: string; label: string; page: number; bbox: number[] }[];
  activeKey?: string | null;
  activePage?: number;
  stagePages?: PageDim[];
}>();
const emit = defineEmits<{
  (e: "select", sample: { id: string; file_name: string } | null): void;
  (e: "pick", key: string): void;
  (e: "collapse", collapsed: boolean): void;
  (e: "uploading", uploading: boolean): void;
}>();
const collapsed = ref(false);
const samples = ref<SampleRow[]>([]);
const activeId = ref("");
const activeName = computed(() => samples.value.find(s => s.id === activeId.value)?.file_name || "sample");
const fileUrl = computed(() => activeId.value ? api.studioSampleFileUrl(activeId.value) : "");
const activeBox = computed(() => props.boxes?.find(b => b.key === props.activeKey)?.bbox ?? null);
const pages = ref<PageDim[]>([]);
const parseError = ref<string | null>(null);
const uploading = ref(false);
const pendingIds = ref<string[]>([]);
const metadata = new Map<string, { pages: PageDim[]; parse_error: string | null }>();
let loadId = 0;
let alive = true;
let adoption: Promise<void> | null = null;
function toggleCollapse() { collapsed.value = !collapsed.value; emit("collapse", collapsed.value); }
function selectById(id: string) {
  const sample = samples.value.find(s => s.id === id);
  activeId.value = sample?.id ?? "";
  pages.value = metadata.get(id)?.pages ?? [];
  parseError.value = metadata.get(id)?.parse_error ?? null;
  emit("select", sample ? { id: sample.id, file_name: sample.file_name } : null);
}
async function load() {
  const request = ++loadId;
  const code = props.skillCode;
  try {
    // New skills show only this editor session's uploads, never tenant-wide samples.
    const rows = code ? (await api.studioSamples(code)).samples : samples.value.filter(s => pendingIds.value.includes(s.id));
    if (!alive || request !== loadId || code !== props.skillCode) return;
    samples.value = rows;
    selectById(rows.some(s => s.id === activeId.value) ? activeId.value : rows[0]?.id ?? "");
  } catch (e) { if (alive && request === loadId) toast.error(e); }
}
// Await before navigating a new skill, so session uploads survive a remount.
async function adoptSamples(skillCode: string) {
  if (adoption) return adoption;
  adoption = (async () => {
    for (const id of [...pendingIds.value]) {
      await api.studioAdoptSample(id, skillCode);
      pendingIds.value = pendingIds.value.filter(x => x !== id);
      const sample = samples.value.find(s => s.id === id);
      if (sample) sample.skill_code = skillCode;
    }
  })();
  try { await adoption; } finally { adoption = null; }
}
defineExpose({ adoptSamples });
onMounted(load);
watch(() => props.skillCode, async (code, prev) => {
  ++loadId;
  if (code && !prev) { try { await adoptSamples(code); } catch (e) { toast.error(e); return; } }
  await load();
});
onBeforeUnmount(() => { alive = false; ++loadId; });
async function upload(ev: Event) {
  const input = ev.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  const code = props.skillCode;
  uploading.value = true;
  emit("uploading", true);
  try {
    const r = await api.studioUploadSample(file, code);
    if (!alive || code !== props.skillCode) return;
    if (!code) pendingIds.value.push(r.id);
    metadata.set(r.id, { pages: r.pages, parse_error: r.parse_error });
    samples.value.push({ id: r.id, file_name: r.file_name, skill_code: code || null, created_at: "" });
    activeId.value = r.id;
    await load();
    toast.ok(t('samplePanel.sample_uploaded'));
  } catch (e) { if (alive) toast.error(e); }
  finally { uploading.value = false; if (alive) emit("uploading", false); input.value = ""; }
}
async function removeSample(id: string) {
  if (!confirm(t('samplePanel.delete_this_sample_only_affects_reference_files_in_the'))) return;
  const code = props.skillCode;
  try {
    await api.studioDeleteSample(id);
    if (!alive || code !== props.skillCode) return;
    pendingIds.value = pendingIds.value.filter(x => x !== id);
    metadata.delete(id);
    samples.value = samples.value.filter(s => s.id !== id);
    await load();
  } catch (e) { if (alive) toast.error(e); }
}
</script>

<style scoped>
.sample-panel { border: 1px solid var(--border); border-radius: 12px; background: var(--bg-panel);
  display: flex; flex-direction: column; min-width: 0; overflow: hidden; }
.sp-head { display: flex; gap: 10px; align-items: center; min-height: 55px; padding: 10px 14px;
  border-bottom: 1px solid var(--border); }
.sp-head strong { white-space: nowrap; font-size: 13px; }
.sp-head select { flex: 1; min-width: 0; max-width: 100%; font-size: 12px; }
.sp-toggle { margin-left: auto; white-space: nowrap; font-size: 12px; background: transparent; }
.collapsed .sp-head { border: 0; padding: 10px 6px; }
.collapsed .sp-toggle { writing-mode: vertical-rl; padding: 10px 5px; }
.sp-stage { flex: 1; min-height: 360px; background: var(--bg); padding: 6px; }
.sp-empty { flex: 1; min-height: 360px; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 14px; padding: 28px; text-align: center; cursor: pointer; }
.upload-mark { font-size: 30px; color: var(--accent); }
.sp-empty .dim { font-size: 13px; line-height: 1.8; }
.sp-footer { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 10px 12px;
  border-top: 1px solid var(--border); font-size: 11px; }
.sp-footer > .dim { margin-right: auto; }
.sp-footer button, .sp-footer .btn-like { font-size: 12px; padding: 5px 8px; }
.delete-sample { color: var(--text-dim); background: transparent; border-color: transparent; }
.delete-sample:hover { color: var(--red); }
.sp-warn { padding: 0 12px; font-size: 12px; }
.dim { color: var(--text-dim); }
@media(max-width: 600px) { .sp-head { gap: 6px; padding: 10px; } .sp-head strong { display: none; } }
</style>
