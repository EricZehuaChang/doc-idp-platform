<template>
  <main class="review" v-if="detail">
    <!-- left: original document with bbox overlay (Insavlo dual-screen shape) -->
    <section class="doc-pane">
      <div class="doc-head">
        <!-- explicit way back: the review page used to be a one-way door
             (browser back was the only exit) — P10 -->
        <button class="back" :title="$t('review.back_to_list')" @click="goBack">{{ $t('review.back') }}</button>
        <div class="nav-group">
          <button :disabled="queuePos <= 0" :title="$t('review.previous_file')" @click="goSibling(-1)">←</button>
          <span class="pos dim" v-if="queueIds.length">{{ queueLabel }}</span>
          <button :disabled="nextDisabled" :title="$t('review.next_file')" @click="goSibling(1)">→</button>
          <button class="fid" :title="t('review.copy-file-id', { id: detail.file_id })"
                  @click="copyFid">⧉</button>
        </div>
        <strong class="fname-head" :title="detail.file_name">{{ detail.file_name }}</strong>
        <span class="chip" :class="`chip-${detail.status}`">{{ statusLabel }}</span>
        <button class="anno" :class="{ primary: annotate }" :disabled="!locked"
                @click="annotate = !annotate" :title="$t('review.drag_to_select_field_area_b')">
          {{ $t(annotate ? 'review.annotation_active' : 'review.annotation_inactive') }}
        </button>
        <button class="anno" :class="{ primary: !!sealRegions }"
                :disabled="detecting || !detectSupported" @click="toggleDetect"
                :title="detectSupported ? $t('review.detect_seals_signatures_and_overlay_boxes_on_the_original')
                                        : $t('review.seal_detection_is_not_supported_for_this_format_pdf')">
          {{ $t('review.seals') }}{{ detecting ? "…" : sealRegions ? ` ×${sealRegions.length}` : "" }}
        </button>
      </div>
      <div class="doc-body">
        <DocStage ref="stage" :src="api.downloadUrl(detail.file_id)" :file-name="detail.file_name"
                  :preview-src="previewSrc"
                  :pages="detail.pages" :active-box="activeBox" :active-page="activePage"
                  :annotate="annotate" :regions="sealRegions"
                  :boxes="stageBoxes" :active-key="activeKey"
                  @box="onBoxDrawn" @pick="pickBox" />
      </div>
    </section>

    <!-- right: extracted fields panel -->
    <section class="field-pane">
      <div v-if="detail.children.length" class="parts">
        <div class="parts-head">
          <strong>{{ $t('review.recognized_as') }} {{ detail.child_count }} {{ $t('review.documents') }}</strong>
          <span class="dim">{{ $t('review.current') }} {{ activePartIdx + 1 }} / {{ detail.children.length }} {{ $t('review.copies_original_page') }} {{ partRange(activeItem!) }} {{ $t('review.same_original_file') }} {{ detail.page_count }} {{ $t('review.page') }}</span>
        </div>
        <div class="part-list">
          <button v-for="(part, i) in detail.children" :key="part.file_id"
                  :class="['part', { active: activeFileId === part.file_id }]"
                  @click="switchPart(part.file_id)">
            <span class="part-name" :title="part.document?.doc_type ?? ''">{{ $t('review.document') }} {{ i + 1 }}{{ part.document?.doc_type ? ` · ${part.document.doc_type}` : "" }}</span>
            <small>{{ $t('review.page_af50a0') }} {{ partRange(part) }} {{ $t('review.page') }}</small>
            <span class="chip" :class="`chip-${part.status}`">{{ stLabel(part.status) }}</span>
          </button>
        </div>
      </div>
      <div class="tabs">
        <button :class="{ primary: tab === 'all' }" @click="tab = 'all'">{{ $t('review.all_fields') }}</button>
        <button :class="{ primary: tab === 'review' }" @click="tab = 'review'">
          {{ $t('review.pending_review') }} <span v-if="reviewCount" class="badge-r">{{ reviewCount }}</span>
        </button>
        <button v-if="detectMeta" :class="{ primary: tab === 'seal' }" @click="tab = 'seal'">
          {{ $t('review.seals_signatures') }} <span class="badge-r">{{ detectMeta.regions.length }}</span>
        </button>
        <span class="lock-state dim">{{ lockMsg }}</span>
      </div>

      <!-- seal / signature structured results (P06): detection is not the whole
           answer — the reviewer needs type, page, position and confidence as
           data, and an explicit "text not readable" state instead of a guess -->
      <div v-if="tab === 'seal'" class="fields">
        <p class="dim seal-note">
          {{ $t('review.detector') }}{{ detectMeta?.detector }} {{ $t('review.scan') }} {{ detectMeta?.pages_scanned }}/{{
            detectMeta?.page_count }} {{ $t('review.page') }}<template v-if="detectMeta?.truncated">{{ $t('review.page_limit_reached_remaining_pages_not_detected') }}</template>
        </p>
        <p v-if="!detectMeta?.regions.length" class="dim">{{ $t('review.no_seals_or_signatures_detected') }}</p>
        <div v-for="(r, i) in detectMeta?.regions ?? []" :key="`seal-${i}`" class="field"
             :class="{ active: activeKey === `r:${i}` }" @click="pickBox(`r:${i}`)">
          <div class="field-head">
            <span class="fname">{{ r.label === "seal" ? $t('review.seal') : $t('review.signature') }} #{{ i + 1 }}</span>
            <span class="dim">{{ $t('review.page_af50a0') }} {{ r.page }} {{ $t('review.page') }}</span>
            <span class="conf dim">{{ $t('review.confidence') }} {{ (r.score * 100).toFixed(0) }}%</span>
          </div>
          <p class="seal-pos dim">
            {{ $t('review.position_x') }} {{ r.x.toFixed(1) }}% · y {{ r.y.toFixed(1) }}{{ $t('review.width') }} {{ r.w.toFixed(1) }}{{ $t('review.height') }} {{ r.h.toFixed(1) }}%
          </p>
          <p class="seal-text dim">
            {{ r.label === "seal" ? $t('review.seal_text') : $t('review.signer') }}{{ $t('review.not_recognized_current_channel_only_performs_layout_detection_not') }}</p>
        </div>
        <p v-if="detectMeta?.misses?.length" class="rulefail">
          {{ $t('review.disabled_detectors') }}{{ detectMeta.misses.join("、") }}
        </p>
      </div>

      <p v-if="classifyOnly && tab !== 'seal'" class="dim co-note">
        {{ $t('review.classification_only_document_no_fields_to_validate_simply_click') }}</p>
      <div v-else class="fields" ref="fieldsEl">
        <div v-for="(f, i) in visibleFields" :key="f.name" class="field"
             :class="{ active: activeKey === `f:${f.name}` }" :data-key="`f:${f.name}`"
             @click="focusField(f)">
          <div class="field-head">
            <span class="fname">{{ f.name }}</span>
            <span v-if="f.cell.$source" class="dim">{{ ({rule: $t('review.rule'), model: $t('review.model'), review: $t('review.pending_review')} as Record<string, string>)[f.cell.$source] }}</span>
            <span v-if="f.cell.inferred" class="badge-inferred">{{ $t('review.reference_result') }}</span>
            <span v-if="f.cell.$confidence !== null && f.cell.$confidence < 2" class="badge-r">R</span>
            <span v-if="pendingBoxes[f.name]" class="badge-box" :title="$t('review.bounding_boxes_redrawn_pending_save')">▣</span>
            <span class="conf dim">{{ f.cell.$confidence === null ? $t('review.unscored') : `c${f.cell.$confidence}` }}</span>
          </div>
          <textarea v-model="edits[f.name]" :disabled="!locked" rows="2" wrap="soft"
                    :ref="(el) => setInput(i, el as HTMLTextAreaElement)"
                    :title="edits[f.name]"
                    @focus="focusField(f)" @keydown.esc.prevent="blurInput" />
          <div v-if="f.cell.$reasoning" class="reasoning dim">{{ f.cell.$reasoning }}</div>
          <div v-if="f.cell.$rule_failures" class="rulefail">{{ f.cell.$rule_failures.join("; ") }}</div>
        </div>

        <!-- line-item tables: editable in place while locked (UX debt) -->
        <div v-for="t in tableNames" :key="t" class="field">
          <div class="field-head">
            <span class="fname">{{ t }}</span>
            <span class="dim">{{ $t('review.detail_table') }} {{ tableEdits[t]?.length ?? 0 }} {{ $t('review.row') }}</span>
            <span v-if="tableDirty(t)" class="badge-box" :title="$t('review.details_modified_pending_save')">✎</span>
            <button v-if="locked" class="mini" @click.stop="addRow(t)">{{ $t('review.row_bc9735') }}</button>
          </div>
          <div class="table-scroll" v-if="tableEdits[t]?.length">
            <table>
              <thead>
                <tr>
                  <th v-for="c in tableCols(t)" :key="c">{{ c }}</th>
                  <th v-if="locked"></th>
                </tr>
              </thead>
              <tbody>
                <!-- a table row is a selectable target too: for entity-list
                     skills (PII sweeps) it is the ONLY thing on the page, and
                     without this nothing could ever be selected (P02) -->
                <tr v-for="(row, ri) in tableEdits[t]" :key="ri"
                    :class="{ 'row-active': activeKey.startsWith(`t:${t}:${ri}:`) }"
                    :data-key="`t:${t}:${ri}`">
                  <td v-for="c in tableCols(t)" :key="c"
                      :class="{ 'cell-hit': hasHit(t, ri, c) }"
                      :title="hasHit(t, ri, c) ? $t('review.click_to_locate_in_the_original_document') : ''"
                      @click="pickCell(t, ri, c)">
                    <input v-if="locked" v-model="row[c]" class="cell-input"
                           @focus="pickCell(t, ri, c)" />
                    <template v-else>{{ row[c] }}</template>
                  </td>
                  <td v-if="locked" class="row-ops">
                    <button class="mini danger" :title="$t('review.delete_row')" @click.stop="delRow(t, ri)">✕</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="dim empty-rows">{{ $t('review.no_detail_rows') }}<template v-if="locked">{{ $t('review.click_row_to_add') }}</template></p>
        </div>
      </div>

      <div class="actions">
        <!-- export is read-only and independent of the lock: an already-decided
             document is exactly the one people want to hand downstream -->
        <button class="ghost" :disabled="!activeItem"
                :title="$t('review.export_current_document_recognition_results_json_field_values_and')"
                @click="exportResult('json')">{{ $t('review.export_json') }}</button>
        <details v-if="artifacts.length" class="art-dropdown"
                 data-testid="art-dropdown">
          <summary>{{ $t('review.output_files') }}{{ artifacts.length }}）</summary>
          <ul>
            <li v-for="a in artifacts" :key="a.artifact_id"
                :title="a.error ?? ''">
              <span class="art-name">{{ a.name }}</span>
              <span class="dim">{{ a.size != null ? Math.round(a.size / 1024) + " KB" : "" }}
                {{ a.status === "error" ? $t('review.failed') : a.searchable ? $t('review.searchable') : "" }}</span>
              <button v-if="a.status === 'ready'" class="ghost"
                      @click="api.artifactDownload(a.artifact_id, a.name)">{{ $t('review.download') }}</button>
            </li>
          </ul>
        </details>
        <button class="ghost" :disabled="!activeItem"
                :title="$t('review.export_current_document_recognition_results_markdown_table_easy_to')"
                @click="exportResult('md')">{{ $t('review.export_md') }}</button>
        <button v-if="!locked && reviewable" class="primary big" @click="acquire">
          {{ $t('review.start_validation_lock_l') }}</button>
        <span v-else-if="!locked" class="dim decided-note">{{ $t('review.this_document_has_been_processed_and_is_read_only') }}</span>
        <template v-else>
          <button class="ghost" @click="saveEdits" :disabled="!dirty">
            {{ $t('review.save_corrections_s') }}<span v-if="dirty" class="dot">●</span></button>
          <!-- non-conclusive exits: a reviewer who is not ready to judge must
               not be forced to press 通过/拒绝 just to get out (P03) -->
          <button class="ghost" @click="exitReview()" :title="dirty
            ? $t('review.save_the_corrections_made_exit_without_changing_the_review') : $t('review.unlock_and_return_without_changing_the_review_status')">
            {{ dirty ? $t('review.save_and_exit') : $t('review.exit_verification') }}</button>
          <button v-if="dirty" class="ghost" :title="$t('review.discard_this_change_and_exit')"
                  @click="exitReview(true)">{{ $t('review.discard_changes_and_exit') }}</button>
          <span class="spacer" />
          <button class="danger" @click="decide('reject')">{{ $t('review.reject_x') }}</button>
          <button class="confirm big" @click="decide('confirm')">✓ {{ confirmLabel }} C</button>
        </template>
        <span class="keys dim" :title="$t('review.j_k_field_enter_edit_esc_exit_edit_t')">⌨</span>
      </div>
    </section>
  </main>

  <!-- loading skeleton / not-found guidance -->
  <main v-else class="review-fallback">
    <template v-if="isLoading">
      <Skeleton :rows="3" width="40%" />
      <Skeleton :rows="8" />
    </template>
    <div v-else class="empty-state">
      <p>{{ $t('review.the_file_does_not_exist_or_has_already_been') }}</p>
      <router-link to="/tasks"><button class="primary">{{ $t('review.back_to_task_list') }}</button></router-link>
    </div>
  </main>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
const { t } = useI18n();

import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from "vue-router";
import { api, fetchBlob, type Artifact, type DetectResult, type FieldCell,
         type RegionOverlay,
         type ReviewItem, type StageBox } from "../api";
import DocStage from "../components/DocStage.vue";
import Skeleton from "../components/Skeleton.vue";
import { copyText } from "../clipboard";
import { downloadResult, type ResultExportInput } from "../resultExport";
import { toast } from "../toast";

const props = defineProps<{ fileId: string }>();
const route = useRoute();
const router = useRouter();
const qc = useQueryClient();
const stage = ref<InstanceType<typeof DocStage>>();

// server state via TanStack Query (§9.0 layer ①)
const { data: detail, isLoading, refetch } = useQuery({
  queryKey: computed(() => ["review", props.fileId]),
  queryFn: () => api.detail(props.fileId),
  retry: false,
});
// queue order drives Previous/Next (UX debt: 无 Previous/Next). Other reviewers
// decide files all the time, so keep the worklist fresh instead of letting the
// arrows go stale and dead (需求9).
const { data: queueData } = useQuery({
  queryKey: ["queue"], queryFn: api.queue,
  refetchInterval: 10_000, refetchOnWindowFocus: true,
});
const queueIds = computed(() => (queueData.value ?? []).map((q) => q.file_id));
const queuePos = computed(() => queueIds.value.indexOf(detail.value?.file_id ?? props.fileId));
/** Position label (WP3): the M in "N / M" is the size of the returned pending
 *  batch, never the total task count — and a file opened from the task ledger
 *  says so instead of showing a meaningless dash. */
const queueLabel = computed(() =>
  queuePos.value >= 0
    ? t('review.queue-position', { position: queuePos.value + 1, total: queueIds.value.length })
    : t('review.the_current_file_is_not_in_a_pending_approval'));
const nextDisabled = computed(() => {
  if (queuePos.value < 0) return queueIds.value.length === 0;   // not in list: jump to first
  return queuePos.value >= queueIds.value.length - 1;
});

const STATUS_LABELS = computed<Record<string, string>>(() => ({
  pending_verification: t('review.pending_validation'), completed: t('review.completed'), passed: t('review.approved'),
  rejected: t('review.rejected'), error: t('review.processing_failed'), processing: t('review.processing'), split: t('review.split'),
}));
const stLabel = (status: string) => STATUS_LABELS.value[status] ?? status;
const statusLabel = computed(() =>
  stLabel(detail.value?.status ?? ""));

// A split file stays one review route.  Its child records are internal work
// units and this selector decides which unit's fields/actions are currently
// shown while DocStage keeps rendering the original root file.
const activeFileId = ref("");
const activeItem = computed<ReviewItem | null>(() => {
  if (!detail.value) return null;
  if (!detail.value.children.length) return detail.value;
  return detail.value.children.find((c) => c.file_id === activeFileId.value)
    ?? detail.value.children[0];
});
const reviewable = computed(() => activeItem.value?.status === "pending_verification");
const activePartIdx = computed(() => {
  const kids = detail.value?.children ?? [];
  const idx = kids.findIndex((c) => c.file_id === activeItem.value?.file_id);
  return idx >= 0 ? idx : 0;
});
async function copyFid() {
  try {
    await copyText(detail.value?.file_id ?? props.fileId);
    toast.ok(t('review.copied_file_id'));
  } catch { toast.error(t('review.copy_failed_please_copy_manually_from_the_task_list')); }
}
const pageOffset = computed(() => activeItem.value?.page_offset ?? 0);
// 9.15 WP4: advanced children carry source_pages (original-file pages, maybe
// non-consecutive) — the fact source; legacy children fall back to the
// cumulative page_offset
function srcPages(part: ReviewItem | null | undefined): number[] | null {
  const sp = part?.document?.source_pages;
  return sp && sp.length ? sp : null;
}
const toRootPage = (page: number) => {
  const sp = srcPages(activeItem.value);
  if (sp) return sp[Math.max(0, Math.min(page, sp.length) - 1)];
  return page + pageOffset.value;
};
const toLocalPage = (page: number) => {
  const sp = srcPages(activeItem.value);
  if (sp) return Math.max(1, sp.indexOf(page) + 1 || 1);
  return Math.max(1, page - pageOffset.value);
};
function partRange(part: ReviewItem): string {
  const sp = srcPages(part);
  if (sp) return sp.length === 1 ? String(sp[0])
    : `${sp[0]}–${sp[sp.length - 1]}` + (sp.some((p, i) => i && p !== sp[i - 1] + 1)
        ? `（${sp.join(",")}）` : "");
  const start = (part.page_offset ?? 0) + 1;
  const end = start + Math.max(part.page_count, 1) - 1;
  return start === end ? String(start) : `${start}–${end}`;
}
const classifyOnly = computed(() =>
  activeItem.value?.document?.extraction_status === "not_requested");

// tab preference survives reloads (caching design §9.0 layer ⑤)
const tab = ref<"all" | "review" | "seal">(
  (localStorage.getItem("idp_review_tab") as "all" | "review") || "all");
watch(tab, (v) => { if (v !== "seal") localStorage.setItem("idp_review_tab", v); });
const locked = ref(false);
const edits = ref<Record<string, string>>({});
const original = ref<Record<string, string>>({});
const tableEdits = ref<Record<string, Record<string, string>[]>>({});
const tableOriginal = ref<Record<string, string>>({});     // JSON snapshots
const activeKey = ref("");                                 // selection identity
const activeBox = ref<number[] | null>(null);
const activePage = ref(1);
const annotate = ref(false);
const pendingBoxes = ref<Record<string, { page: number; bbox: number[] }>>({});

// —— seal/signature detection overlay (/detect, design 2026-08-07 §3) ——
const detecting = ref(false);
const sealRegions = ref<RegionOverlay[] | null>(null);
const detectMeta = ref<DetectResult | null>(null);
// suffix gate mirrors the backend's (detect.py): other formats would only
// round-trip to a guaranteed 422, so the button greys out with a tooltip
const detectSupported = computed(() =>
  !!detail.value && /\.(pdf|png|jpe?g|bmp|webp)$/i.test(detail.value.file_name));

// Office originals render through the server's converted PDF (需求3: Doc 格式
// 预览); PDF/images stream as-is and need no second URL
const OFFICE_PREVIEW = /\.(docx?|xlsx?|pptx?)$/i;
const previewSrc = computed(() =>
  detail.value && OFFICE_PREVIEW.test(detail.value.file_name)
    ? api.previewUrl(detail.value.file_id) : "");
async function toggleDetect() {
  if (sealRegions.value) {                     // second click clears
    sealRegions.value = null;
    detectMeta.value = null;
    if (tab.value === "seal") tab.value = "all";
    return;
  }
  if (!detail.value) return;
  // capture identity at call start: detection takes seconds and the reviewer
  // can ←/→ away mid-flight — a settling stale response must never overlay
  // another document's boxes (the fileId watch reset alone can't stop that)
  const fid = detail.value.file_id;
  const fname = detail.value.file_name;
  detecting.value = true;
  try {
    // re-fetch the original through the authenticated blob channel and run
    // the pure-compute /detect on it (no Transaction, no charge)
    const blob = await fetchBlob(api.downloadUrl(fid));
    const res = await api.detect(blob, fname);
    if (detail.value?.file_id !== fid) return; // switched away: discard stale result
    detectMeta.value = res;
    sealRegions.value = res.regions.map((r) => {
      const p = res.pages.find((x) => x.page === r.page);
      return { ...r, pageWidth: p?.width ?? 0, pageHeight: p?.height ?? 0 };
    });
    const seals = res.regions.filter((r) => r.label === "seal").length;
    const sigs = res.regions.filter((r) => r.label === "signature").length;
    const cut = res.truncated ? t('review.detection-limit', { total: res.page_count, scanned: res.pages_scanned }) : "";
    if (res.regions.length) tab.value = "seal";
    toast.ok(res.regions.length
      ? t('review.detection-result', { seals, signatures: sigs, detector: res.detector, limit: cut })
      : t('review.detection-empty', { limit: cut }));
  } catch (e) {
    if (detail.value?.file_id === fid) toast.error(e);
  } finally {
    detecting.value = false;
  }
}
const fieldsEl = ref<HTMLDivElement>();
const inputEls = new Map<number, HTMLTextAreaElement>();
function setInput(i: number, el: HTMLTextAreaElement | null) {
  if (el) inputEls.set(i, el);
}

interface ScalarField { name: string; cell: FieldCell }
const scalarFields = computed<ScalarField[]>(() => {
  if (!activeItem.value?.result) return [];
  return Object.entries(activeItem.value.result)
    .filter(([, v]) => !Array.isArray(v))
    .map(([name, cell]) => ({ name, cell: cell as FieldCell }));
});
const tableNames = computed(() => {
  if (!activeItem.value?.result) return [];
  return Object.entries(activeItem.value.result)
    .filter(([, v]) => Array.isArray(v)).map(([name]) => name);
});
function tableCols(t: string): string[] {
  const rows = tableEdits.value[t] ?? [];
  const cols = new Set<string>();
  // $-prefixed keys are engine metadata ($cells locations), not columns
  for (const r of rows) Object.keys(r).filter((c) => !c.startsWith("$")).forEach((c) => cols.add(c));
  return [...cols];
}
function tableDirty(t: string): boolean {
  return JSON.stringify(tableEdits.value[t] ?? []) !== (tableOriginal.value[t] ?? "[]");
}
function addRow(t: string) {
  const cols = tableCols(t);
  const blank: Record<string, string> = {};
  for (const c of cols) blank[c] = "";
  (tableEdits.value[t] ??= []).push(blank);
}
function delRow(t: string, i: number) {
  tableEdits.value[t]?.splice(i, 1);
}

// —— cell locations: rows carry $cells[col].$hits [{page,bbox,...}] ——
interface CellHit { page?: number; bbox?: number[] }
function cellHits(t: string, ri: number, col: string): CellHit[] {
  const row = tableEdits.value[t]?.[ri] as unknown as
    Record<string, { $hits?: CellHit[] }> | undefined;
  const cells = (row as unknown as { $cells?: Record<string, { $hits?: CellHit[] }> })?.$cells;
  return (cells?.[col]?.$hits ?? []).filter((h) => h?.bbox?.length === 4);
}
function cellHit(t: string, ri: number, col: string): CellHit | null {
  return cellHits(t, ri, col)[0] ?? null;
}
const hasHit = (t: string, ri: number, col: string) => !!cellHit(t, ri, col);

const reviewCount = computed(() =>
  scalarFields.value.filter((f) => f.cell.$confidence !== null && f.cell.$confidence < 2).length);
const visibleFields = computed(() =>
  tab.value === "review" ? scalarFields.value.filter((f) => f.cell.$confidence !== null && f.cell.$confidence < 2)
    : scalarFields.value);
const dirty = computed(() =>
  Object.keys(edits.value).some((k) => edits.value[k] !== original.value[k])
  || Object.keys(pendingBoxes.value).length > 0
  || tableNames.value.some((t) => tableDirty(t)));
const lockMsg = computed(() => {
  if (locked.value) return t('review.locked_15_minutes');
  const by = activeItem.value?.locked_by;
  return by && by !== api.currentUser ? t('review.locked-by', { name: by }) : t('review.unlocked');
});

function fieldPage(cell: FieldCell): number {
  const p = cell.$pages;
  if (typeof p === "number") return toRootPage(p);
  const n = parseInt(String(p), 10);
  return toRootPage(Number.isFinite(n) && n > 0 ? n : 1);
}

// —— every locatable thing, drawn on the document and clickable (P02) ——
const stageBoxes = computed<StageBox[]>(() => {
  const out: StageBox[] = [];
  for (const f of scalarFields.value) {
    const pending = pendingBoxes.value[f.name];
    const bbox = pending?.bbox ?? (f.cell.$bbox?.length === 4 ? f.cell.$bbox : null);
    if (bbox) out.push({ key: `f:${f.name}`, page: pending?.page ?? fieldPage(f.cell),
                         bbox, label: `${f.name}: ${f.cell.$value}` });
  }
  for (const t of tableNames.value) {
    const rows = tableEdits.value[t] ?? [];
    rows.forEach((row, ri) => {
      for (const col of Object.keys(row).filter((c) => !c.startsWith("$"))) {
        // every occurrence gets its own box — a table renders as per-cell /
        // per-region boxes, never one table-wide block (需求6)
        cellHits(t, ri, col).forEach((hit, hi) => {
          if (!hit.page) return;
          out.push({ key: `t:${t}:${ri}:${col}:${hi}`, page: toRootPage(hit.page),
                     bbox: hit.bbox!, label: `${col}: ${row[col]}` });
        });
      }
    });
  }
  return out;
});

function focusField(f: ScalarField) {
  activeKey.value = `f:${f.name}`;
  const pending = pendingBoxes.value[f.name];
  if (pending) {
    activeBox.value = pending.bbox;
    activePage.value = pending.page;
  } else {
    activeBox.value = f.cell.$bbox?.length === 4 ? f.cell.$bbox : null;
    activePage.value = fieldPage(f.cell);
  }
}
function pickCell(t: string, ri: number, col: string) {
  activeKey.value = `t:${t}:${ri}:${col}`;
  const hit = cellHit(t, ri, col);
  activeBox.value = hit?.bbox ?? null;
  if (hit?.page) activePage.value = toRootPage(hit.page);
}
/** Canvas -> field: the other half of the selection loop the UI never had. */
function pickBox(key: string) {
  activeKey.value = key;
  if (key.startsWith("f:")) {
    const f = scalarFields.value.find((x) => `f:${x.name}` === key);
    if (f) {
      if (tab.value === "seal") tab.value = "all";
      focusField(f);
    }
  } else if (key.startsWith("t:")) {
    const [, t, ri, col] = key.split(":");
    if (tab.value === "seal") tab.value = "all";
    pickCell(t, Number(ri), col);
  } else if (key.startsWith("r:")) {
    const r = detectMeta.value?.regions[Number(key.slice(2))];
    if (r) { activePage.value = r.page; activeBox.value = null; }
    tab.value = "seal";
  }
  scrollToSelection();
}
async function scrollToSelection() {
  await new Promise((r) => setTimeout(r, 0));
  const key = activeKey.value;
  const sel = key.startsWith("t:")
    ? `[data-key="${key.split(":").slice(0, 3).join(":")}"]` : `[data-key="${key}"]`;
  document.querySelector(sel)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function onBoxDrawn(page: number, bbox: number[]) {
  const key = activeKey.value;
  if (key.startsWith("f:")) {
    const name = key.slice(2);
    pendingBoxes.value = { ...pendingBoxes.value, [name]: { page, bbox } };
    activeBox.value = bbox;
    activePage.value = page;
    toast.ok(t('review.field-box-redrawn', { name }));
    return;
  }
  if (key.startsWith("t:")) {
    const [, tableName, ri, col] = key.split(":");
    if (!setCellHit(tableName, Number(ri), col, page, bbox)) {
      toast.error(t('review.this_cell_has_no_writable_localization_structure_so_the'));
      return;
    }
    activeBox.value = bbox;
    activePage.value = page;
    toast.ok(t('review.cell-box-redrawn', { row: Number(ri) + 1, column: col }));
    return;
  }
  toast.error(t('review.first_select_a_field_or_detail_cell_on_the'));
}

/** Write a redrawn box back into the row's $cells metadata. Rows are PATCHed
 *  whole, so this rides along with the normal table save — no new API. The
 *  percent form is what the masking consumers read, so keep both in step. */
function setCellHit(t: string, ri: number, col: string, page: number, bbox: number[]): boolean {
  const rows = tableEdits.value[t];
  const row = rows?.[ri] as unknown as
    { $cells?: Record<string, { $confidence?: number; $hits?: unknown[] }> } | undefined;
  if (!row) return false;
  const dim = detail.value?.pages.find((p) => p.page_no === page);
  const hit: Record<string, number | number[]> = { page: toLocalPage(page), bbox };
  if (dim?.width && dim?.height) {
    hit.x = +(bbox[0] / dim.width * 100).toFixed(2);
    hit.y = +(bbox[1] / dim.height * 100).toFixed(2);
    hit.w = +((bbox[2] - bbox[0]) / dim.width * 100).toFixed(2);
    hit.h = +((bbox[3] - bbox[1]) / dim.height * 100).toFixed(2);
  }
  const cells = (row.$cells ??= {});
  const cell = (cells[col] ??= { $confidence: 3, $hits: [] });
  cell.$hits = [hit];
  cell.$confidence = 3;                       // human-placed box is ground truth
  tableEdits.value = { ...tableEdits.value, [t]: [...rows!] };
  return true;
}

// unsaved-draft persistence (caching design §9.0 layer ④)
const draftKey = () => `idp_draft_${activeItem.value?.file_id ?? props.fileId}`;
function saveDraft() {
  if (dirty.value) sessionStorage.setItem(draftKey(), JSON.stringify(edits.value));
}
function restoreDraft() {
  const raw = sessionStorage.getItem(draftKey());
  if (!raw) return;
  try {
    const draft = JSON.parse(raw) as Record<string, string>;
    for (const k of Object.keys(edits.value))
      if (k in draft && draft[k] !== edits.value[k]) edits.value[k] = draft[k];
    toast.ok(t('review.restored_unsaved_draft'));
  } catch { sessionStorage.removeItem(draftKey()); }
}

function syncFromDetail() {
  const vals: Record<string, string> = {};
  for (const f of scalarFields.value) vals[f.name] = f.cell.$value ?? "";
  edits.value = { ...vals };
  original.value = { ...vals };
  const tEdits: Record<string, Record<string, string>[]> = {};
  const tOrig: Record<string, string> = {};
  for (const [name, v] of Object.entries(activeItem.value?.result ?? {})) {
    if (Array.isArray(v)) {
      tEdits[name] = JSON.parse(JSON.stringify(v));
      tOrig[name] = JSON.stringify(v);
    }
  }
  tableEdits.value = tEdits;
  tableOriginal.value = tOrig;
  restoreDraft();
}
function resetPartState() {
  locked.value = false;
  activeKey.value = "";
  activeBox.value = null;
  activePage.value = 1;
  annotate.value = false;
  pendingBoxes.value = {};
  inputEls.clear();
}
watch(detail, (v) => {
  if (!v) return;
  const valid = !v.children.length || v.children.some((c) => c.file_id === activeFileId.value);
  if (!valid || !activeFileId.value) activeFileId.value = v.active_file_id;
}, { immediate: true });
watch(activeItem, (v) => { if (v) syncFromDetail(); }, { immediate: true });

// —— 9.15 WP6: output artifacts of the open file (名称/大小/状态/下载) ——
const artifacts = ref<Artifact[]>([]);
watch(activeItem, async (v) => {
  artifacts.value = [];
  if (!v) return;
  try { artifacts.value = (await api.fileArtifacts(v.file_id)).artifacts; }
  catch { /* artifacts are optional; never block the review page */ }
}, { immediate: true });
watch(edits, saveDraft, { deep: true });

// —— navigation destination, one source of truth (WP3): the browse arrows and
// the post-decision jump used to be two independently coded orderings. Both go
// through here; `queue` overrides the (possibly stale) polled snapshot. ——
function resolveDestination(intent: "browse" | "afterDecision", delta: number,
                            rootId: string,
                            queue?: { file_id: string; locked_by?: string | null }[]):
  string | null {
  const rows = queue ?? queueData.value ?? [];
  const ids = rows.map((q) => q.file_id);
  if (intent === "browse") {
    const pos = ids.indexOf(rootId);
    return pos < 0 ? (ids[0] ?? null) : (ids[pos + delta] ?? null);
  }
  // after a decision: the next operable root — files locked by someone else
  // are not actually reachable, so skip them instead of dead-ending there
  const locks = new Map(rows.map((q) => [q.file_id, q.locked_by ?? null]));
  return ids.find((id) => id !== rootId
    && (!locks.get(id) || locks.get(id) === api.currentUser)) ?? null;
}

/** Where the ✓ button will actually take you — shown on the button itself so
 *  the two review actions stop looking interchangeable (WP3). */
const confirmLabel = computed(() => {
  const cur = activeItem.value?.file_id;
  const hasSiblingPart = !!detail.value?.children.some(
    (c) => c.status === "pending_verification" && c.file_id !== cur);
  if (hasSiblingPart) return t('review.approve_and_validate_next_document');
  const rootId = detail.value?.file_id ?? props.fileId;
  return resolveDestination("afterDecision", 0, rootId)
    ? t('review.approve_and_open_next_file') : t('review.approve_and_return_to_list');
});

async function acquire() {
  if (!activeItem.value || !reviewable.value) return;
  try { await api.lock(activeItem.value.file_id); locked.value = true; }
  catch (e) { toast.error(e); }
}
async function saveEdits(): Promise<boolean> {
  const changed: { field: string; value: string; bbox?: number[]; page?: number;
                   rows?: Record<string, string>[] }[] =
    Object.keys(edits.value)
      .filter((k) => edits.value[k] !== original.value[k] || pendingBoxes.value[k])
      .map((k) => ({ field: k, value: edits.value[k],
                     bbox: pendingBoxes.value[k]?.bbox,
                     page: pendingBoxes.value[k]
                       ? toLocalPage(pendingBoxes.value[k].page) : undefined }));
  for (const t of tableNames.value)
    if (tableDirty(t)) changed.push({ field: t, value: "", rows: tableEdits.value[t] });
  if (!changed.length) return true;
  const fileId = activeItem.value?.file_id;
  if (!fileId) return false;
  const dkey = draftKey();                       // captured: the reviewer may switch
  try {                                          // parts while the PATCH is in flight
    await api.patchFields(fileId, changed);
    if (activeItem.value?.file_id !== fileId) return true;   // switched away: never
    sessionStorage.removeItem(dkey);                         // touch the NEW part's state
    pendingBoxes.value = {};
    toast.ok(t('review.corrections-saved', { count: changed.length }));
    await refetch();
    return true;
  } catch (e) { toast.error(e); return false; }
}

// —— result export (识别结果导出: JSON / Markdown) ——
/** The document on screen as export input — what the reviewer is looking at
 *  right now, unsaved edits included. A field the human touched counts as
 *  confidence 3, the same rule the save path applies ($corrected). */
function exportInput(): ResultExportInput | null {
  const item = activeItem.value;
  if (!item) return null;
  return {
    fileId: item.file_id,
    fileName: item.file_name,
    status: item.status,
    statusLabel: stLabel(item.status),
    pageCount: item.page_count,
    verifiedBy: item.verified_by,
    fields: scalarFields.value.map((f) => ({
      name: f.name,
      value: edits.value[f.name] ?? f.cell.$value ?? "",
      confidence: edits.value[f.name] !== original.value[f.name]
        ? 3 : f.cell.$confidence ?? 0,
    })),
    tables: tableNames.value.map((t) => ({ name: t, rows: tableEdits.value[t] ?? [] })),
  };
}

function exportResult(format: "json" | "md") {
  const input = exportInput();
  if (!input) return;
  downloadResult(input, format);
  toast.ok(format === "json" ? t('review.json_exported') : t('review.markdown_exported'));
}

// —— exits (P03) ——
/** Where 返回 goes: the list we came from, else the task workbench. */
const backTarget = computed(() => String(route.query.back || "/tasks"));
let leavingCleanly = false;         // suppresses the unsaved-changes guard

async function releaseLock(fileId = activeItem.value?.file_id) {
  if (!locked.value) return;
  if (fileId) {
    try { await api.unlock(fileId); } catch { /* TTL will reclaim it anyway */ }
  }
  locked.value = false;
}

async function switchPart(fileId: string) {
  if (navBusy.value) return;
  if (fileId === activeItem.value?.file_id) {
    activePage.value = toRootPage(1);
    stage.value?.gotoPage(activePage.value);
    return;
  }
  const oldFileId = activeItem.value?.file_id;
  navBusy.value = true;
  try {
    if (dirty.value && !(await saveEdits())) return;
    await releaseLock(oldFileId);
    resetPartState();
    activeFileId.value = fileId;
    // 需求5: 点击右侧单据,左侧原件跳到该单据的起始页
    const start = toRootPage(1);
    activePage.value = start;
    await nextTick();
    stage.value?.gotoPage(start);
  } finally {
    navBusy.value = false;
  }
}

/** Leave without judging the file. `discard` drops local edits; otherwise they
 *  are persisted first. Neither path touches the review status. */
async function exitReview(discard = false) {
  if (discard) {
    if (!confirm(t('review.discard_unsaved_changes_and_exit'))) return;
    sessionStorage.removeItem(draftKey());
    pendingBoxes.value = {};
    syncFromDetail();
  } else if (dirty.value && !(await saveEdits())) {
    return;                                   // save failed: stay put
  }
  await releaseLock();
  qc.invalidateQueries({ queryKey: ["queue"] });
  leavingCleanly = true;
  router.push(backTarget.value);
}
function goBack() {
  if (locked.value) { exitReview(); return; }
  router.push(backTarget.value);
}

// one busy gate for every decision-shaped action (WP3): double clicks, the C/X
// hotkeys and a stray second press must not queue two decide() runs — a
// settled first run is the only thing allowed to navigate, exactly once
const navBusy = ref(false);

async function decide(kind: "confirm" | "reject") {
  if (navBusy.value) return;
  navBusy.value = true;
  try {
    const rootAtStart = detail.value?.file_id ?? props.fileId;
    if (dirty.value && !(await saveEdits())) return;
    const fileId = activeItem.value?.file_id;
    if (!fileId) return;
    await (kind === "confirm" ? api.confirm(fileId) : api.reject(fileId));
    // the reviewer may have ←/→-ed away while the request flew: a settling
    // stale decision must never navigate or reset the NEW file (WP3)
    if ((detail.value?.file_id ?? props.fileId) !== rootAtStart) return;
    toast.ok(kind === "confirm" ? t('review.approved') : t('review.rejected'));
    locked.value = false;
    qc.invalidateQueries({ queryKey: ["queue"] });
    // Keep reviewing the same original file until all of its internal units
    // are decided; only then advance to the next root-file task.
    const refreshed = await refetch();
    if ((detail.value?.file_id ?? props.fileId) !== rootAtStart) return;
    const nextPart = refreshed.data?.children.find(
      (c) => c.status === "pending_verification" && c.file_id !== fileId);
    if (nextPart) {
      resetPartState();
      activeFileId.value = nextPart.file_id;
      activePage.value = toRootPage(1);
      await nextTick();
      stage.value?.gotoPage(activePage.value);   // 需求5: 跳到下一份的原件首页
      toast.ok(t('review.pending-documents', { count: refreshed.data?.pending_children ?? 0 }));
      return;
    }
    const next = resolveDestination("afterDecision", 0, rootAtStart, await api.queue());
    if ((detail.value?.file_id ?? props.fileId) !== rootAtStart) return;
    leavingCleanly = true;
    router.push(next
      ? { path: `/review/${next}`, query: route.query.back ? { back: route.query.back } : {} }
      : backTarget.value);
  } catch (e) { toast.error(e); } finally { navBusy.value = false; }
}
function goSibling(delta: number) {
  // A file opened from the task ledger may not be on the pending worklist
  // (queuePos === -1): both arrows then lead to the head of the list instead
  // of being dead buttons (需求9).
  const target = resolveDestination("browse", delta,
    detail.value?.file_id ?? props.fileId);
  if (target) {
    router.push({ path: `/review/${target}`,
                  query: route.query.back ? { back: route.query.back } : {} });
  }
}

// leaving with unsaved work (browser back, nav click) must not silently drop it
onBeforeRouteLeave(() => {
  if (leavingCleanly || !dirty.value) return true;
  return confirm(t('review.unsaved-leave'));
});
// same-route param change (←/→ 文件) reuses this component — the guard must
// cover it too, or the arrows would bypass the unsaved-changes protection (WP3)
onBeforeRouteUpdate(() => {
  if (leavingCleanly || !dirty.value) return true;
  return confirm(t('review.unsaved-switch'));
});
// closing the tab / reloading with unsaved work
function beforeUnload(ev: BeforeUnloadEvent) {
  if (dirty.value) { ev.preventDefault(); ev.returnValue = ""; }
}

// —— reviewer keyboard flow ——
function blurInput() { (document.activeElement as HTMLElement)?.blur(); }
function moveField(delta: number) {
  const list = visibleFields.value;
  if (!list.length) return;
  const cur = list.findIndex((f) => `f:${f.name}` === activeKey.value);
  const next = Math.min(Math.max(cur + delta, 0), list.length - 1);
  focusField(list[next]);
  fieldsEl.value?.querySelectorAll(".field")[next]
    ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
function onKey(ev: KeyboardEvent) {
  const typing = (ev.target as HTMLElement)?.tagName === "INPUT"
    || (ev.target as HTMLElement)?.tagName === "TEXTAREA";
  if (typing) {
    if (ev.key === "s" && ev.ctrlKey) { ev.preventDefault(); saveEdits(); }
    return;
  }
  switch (ev.key.toLowerCase()) {
    case "j": case "arrowdown": ev.preventDefault(); moveField(1); break;
    case "k": case "arrowup": ev.preventDefault(); moveField(-1); break;
    case "arrowleft": ev.preventDefault(); goSibling(-1); break;
    case "arrowright": ev.preventDefault(); goSibling(1); break;
    case "enter": {
      ev.preventDefault();
      const i = visibleFields.value.findIndex((f) => `f:${f.name}` === activeKey.value);
      if (i >= 0) inputEls.get(i)?.focus();
      break;
    }
    case "t": tab.value = tab.value === "all" ? "review" : "all"; break;
    case "b": if (locked.value) annotate.value = !annotate.value; break;
    case "l": if (!locked.value) acquire(); break;
    case "s": if (locked.value) saveEdits(); break;
    case "q": goBack(); break;
    case "c": if (locked.value) decide("confirm"); break;
    case "x": if (locked.value) decide("reject"); break;
  }
}

// same component instance is reused across /review/:fileId — reset on switch
watch(() => props.fileId, () => {
  resetPartState();
  activeFileId.value = "";
  sealRegions.value = null;      // detection overlay belongs to one file
  detectMeta.value = null;
  if (tab.value === "seal") tab.value = "all";
});
onMounted(() => {
  window.addEventListener("keydown", onKey);
  window.addEventListener("beforeunload", beforeUnload);
});
onUnmounted(() => {
  window.removeEventListener("keydown", onKey);
  window.removeEventListener("beforeunload", beforeUnload);
});
</script>

<style scoped>
/* Fixed work area, one scroll container per column. Two things are load-bearing
   here and both were wrong before (P08): the height must subtract the REAL
   navbar height (52px — it was 46px, so the shell grew a second scrollbar), and
   every flex/grid ancestor of a scroller needs min-height:0, otherwise
   min-height:auto lets the column grow to its content and the whole page
   scrolls instead of the pane under the cursor. */
.review { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(420px, 1fr);
  height: calc(100vh - 52px); min-height: 0; overflow: hidden; }
.doc-pane { border-right: 1px solid var(--border); display: flex; flex-direction: column;
  min-width: 0; min-height: 0; }
.doc-head { padding: 8px 14px; background: var(--bg-panel); display: flex; gap: 12px;
  align-items: center; flex-shrink: 0; }
.back { padding: 2px 10px; }
.nav-group { display: flex; align-items: center; gap: 6px; }
.nav-group button { padding: 2px 10px; }
.nav-group .fid { padding: 2px 7px; font-size: 12px; }
.pos { font-size: 12px; }
.fname-head { overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  max-width: 34%; }
.anno { margin-left: auto; flex-shrink: 0; }
.anno + .anno { margin-left: 0; }
/* WP2: the stage manages its own scroll viewport now — this body just gives it
   a definite flex height (min-height:0 chain) and stops double-scrolling */
.doc-body { flex: 1 1 auto; min-height: 0; overflow: hidden; padding: 0 12px 12px;
  overscroll-behavior: contain; display: flex; flex-direction: column; }
.doc-body > :deep(.stage) { flex: 1 1 auto; min-height: 0; }
.field-pane { display: flex; flex-direction: column; background: var(--bg-panel);
  min-width: 0; min-height: 0; }
.parts { padding: 10px 12px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.parts-head { display: flex; justify-content: space-between; gap: 10px; align-items: baseline;
  margin-bottom: 8px; font-size: 13px; }
.parts-head .dim { font-size: 11px; }
.part-list { display: flex; gap: 6px; overflow-x: auto; padding-bottom: 2px; }
.part { display: flex; flex: 0 0 auto; flex-direction: column; gap: 3px; align-items: flex-start;
  min-width: 124px; max-width: 220px; padding: 6px 8px; text-align: left;
  border-color: var(--border); font-size: 12px; }
.part-name { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.part small { color: var(--text-dim); }
.part .chip { font-size: 10px; }
.part.active { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
.tabs { display: flex; gap: 8px; align-items: center; padding: 12px;
  border-bottom: 1px solid var(--border); flex-shrink: 0; }
.lock-state { margin-left: auto; font-size: 12px; }
.fields { flex: 1 1 auto; min-height: 0; overflow: auto; padding: 12px; display: flex;
  flex-direction: column; gap: 10px; overscroll-behavior: contain; }
.field { background: var(--bg-raised); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px; cursor: pointer; }
.field.active { border-color: var(--accent); }
/* recognized-value box (需求4): soft-wraps long values and is drag-resizable
   (corner handle) so the whole content can be revealed */
.field textarea { min-height: 42px; max-height: 320px; resize: both; overflow: auto;
  line-height: 1.5; font: inherit; width: 100%; white-space: pre-wrap;
  word-break: break-word; }
.field-head { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.fname { color: var(--blue); font-weight: 600; }
.conf { margin-left: auto; font-size: 12px; }
.badge-box { color: var(--blue); font-size: 12px; }
.reasoning { font-size: 12px; margin-top: 6px; }
.rulefail { color: var(--red); font-size: 12px; margin-top: 4px; }
.mini { padding: 1px 8px; font-size: 12px; margin-left: auto; }
.mini.danger { margin-left: 0; }
.table-scroll { overflow-x: auto; }
.table-scroll table { border-collapse: collapse; font-size: 12px; width: 100%; }
.table-scroll th, .table-scroll td { border: 1px solid var(--border); padding: 2px 6px;
  white-space: nowrap; }
.table-scroll tr.row-active td { background: rgba(240, 180, 41, 0.12); }
.cell-hit { cursor: pointer; }
.cell-hit:hover { background: rgba(90, 170, 255, 0.14); }
.cell-input { border: none; background: transparent; padding: 3px 4px; min-width: 70px;
  font-size: 12px; }
.cell-input:focus { background: var(--bg); }
.row-ops { text-align: center; }
.empty-rows { font-size: 12px; margin: 4px 0 0; }
.seal-note { font-size: 12px; margin: 0 0 4px; }
.seal-pos, .seal-text { margin: 2px 0 0; font-size: 12px; }
.actions { display: flex; gap: 10px; align-items: center; padding: 12px;
  border-top: 1px solid var(--border); flex-wrap: wrap; flex-shrink: 0; }
.actions .big { padding: 8px 18px; font-size: 14px; }
.spacer { flex: 1; }
.dot { color: var(--accent); margin-left: 4px; font-size: 10px; }
.keys { font-size: 14px; cursor: help; }
.dim { color: var(--text-dim); }
.review-fallback { padding: 24px; }
.empty-state { display: flex; flex-direction: column; gap: 14px; align-items: flex-start;
  color: var(--text-dim); }

@media (max-width: 1000px) {
  .review { grid-template-columns: 1fr; grid-template-rows: 45vh 1fr; height: auto;
    overflow: visible; }
  .doc-pane { border-right: none; border-bottom: 1px solid var(--border); }
}
</style>
