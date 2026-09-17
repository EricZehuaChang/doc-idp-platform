<template>
  <div class="sample-panel" :class="{ collapsed }">
    <div class="sp-head" @click="collapsed = !collapsed">
      <strong>样本</strong>
      <span class="dim">{{ activeName || "未选择" }}</span>
      <button class="sp-toggle">{{ collapsed ? "▸" : "◂" }}</button>
    </div>
    <template v-if="!collapsed">
      <label class="file-btn slim">
        <input type="file" hidden @change="upload" :disabled="uploading" />
        <span class="btn-like">{{ uploading ? "⏳ 上传中…" : "＋ 上传样本" }}</span>
      </label>
      <ul class="sp-list">
        <li v-for="s in samples" :key="s.id"
            :class="{ on: s.id === activeId }" @click="select(s)">
          <span class="nm" :title="s.file_name">{{ s.file_name }}</span>
          <button class="del" title="删除样本"
                  @click.stop="removeSample(s.id)">×</button>
        </li>
        <li v-if="!samples.length" class="dim none">还没有样本</li>
      </ul>
      <div v-if="activeId" class="sp-stage">
        <DocStage :src="fileUrl" :file-name="activeName || 'sample'"
                  :pages="pages" :active-box="null" :active-page="1"
                  :annotate="false" />
      </div>
      <p v-if="parseError" class="dim sp-warn">预解析未成功（{{ parseError }}）——不影响手工对照。</p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { api } from "../api";
import DocStage from "./DocStage.vue";
import { toast } from "../toast";

type SampleRow = { id: string; file_name: string; skill_code: string | null;
                   created_at: string };
type PageDim = { page_no: number; width: number; height: number };

const props = defineProps<{ skillCode?: string }>();

const collapsed = ref(false);
const samples = ref<SampleRow[]>([]);
const activeId = ref("");
const activeName = ref("");
const pages = ref<PageDim[]>([]);
const parseError = ref<string | null>(null);
const uploading = ref(false);
/** ids uploaded while the skill code was still empty (D6) */
const pendingIds = ref<string[]>([]);

const fileUrl = ref("");

async function load() {
  // #20 (走查): the panel used to list every sample of the tenant and load
  // only once on mount. It now asks for THIS skill's samples and reloads when
  // the editor switches skill or a sample is uploaded.
  try {
    samples.value = (await api.studioSamples(props.skillCode || undefined)).samples;
    if (activeId.value && !samples.value.some((s) => s.id === activeId.value)) {
      activeId.value = "";
      fileUrl.value = "";
      pages.value = [];
    }
  } catch (e) { toast.error(e); }
}
onMounted(load);
watch(() => props.skillCode, async (code, prev) => {
  if (code === prev) return;
  await load();
  // D6: samples uploaded while creating a skill were unassigned — once the
  // skill exists, adopt this session's uploads into it.
  if (code && !prev) await adoptUnassigned();
});

/** D6 (#20): claim the session's unassigned uploads for the new skill code. */
async function adoptUnassigned() {
  const mine = pendingIds.value;
  if (!mine.length) return;
  try {
    for (const id of mine) await api.studioAdoptSample(id, props.skillCode!);
    pendingIds.value = [];
    await load();
  } catch { /* adoption is best-effort; the sample stays unassigned */ }
}

async function select(s: SampleRow) {
  activeId.value = s.id;
  activeName.value = s.file_name;
  parseError.value = null;
  pages.value = [];
  // original streams through the authenticated preview path used by DocStage
  fileUrl.value = api.studioSampleFileUrl(s.id);
}

async function upload(ev: Event) {
  const file = (ev.target as HTMLInputElement).files?.[0];
  if (!file) return;
  uploading.value = true;
  try {
    const r = await api.studioUploadSample(file, props.skillCode);
    if (!props.skillCode) pendingIds.value.push(r.id);   // adopt after create
    toast.ok("样本已上传");
    pages.value = r.pages;
    parseError.value = r.parse_error;
    await load();
    await select({ id: r.id, file_name: r.file_name, skill_code: null,
                   created_at: "" });
  } catch (e) { toast.error(e); }
  finally {
    uploading.value = false;
    (ev.target as HTMLInputElement).value = "";
  }
}

async function removeSample(id: string) {
  if (!confirm("删除该样本？仅影响编辑器里的参考文件。")) return;
  try {
    await api.studioDeleteSample(id);
    if (activeId.value === id) { activeId.value = ""; fileUrl.value = ""; }
    await load();
  } catch (e) { toast.error(e); }
}
</script>

<style scoped>
.sample-panel { border: 1px solid var(--border); border-radius: 10px;
  background: var(--bg-panel); padding: 10px; display: flex;
  flex-direction: column; gap: 8px; min-width: 280px; max-width: 420px; }
.sample-panel.collapsed { min-width: 0; max-width: 44px; cursor: pointer; }
.sp-head { display: flex; gap: 8px; align-items: center; cursor: pointer; }
.sp-toggle { margin-left: auto; border: 0; background: none; cursor: pointer; }
.sp-list { list-style: none; margin: 0; padding: 0; max-height: 130px;
  overflow: auto; }
.sp-list li { display: flex; align-items: center; gap: 6px; padding: 5px 8px;
  border-radius: 6px; cursor: pointer; font-size: 13px; }
.sp-list li.on, .sp-list li:hover { background: var(--bg-hover); }
.sp-list li.none { cursor: default; }
.nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.del { border: 0; background: none; color: var(--red); cursor: pointer; }
.sp-stage { height: 420px; }
.sp-warn { font-size: 12px; }
</style>
