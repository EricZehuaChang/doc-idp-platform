<template>
  <main class="page">
    <PageHeader title="技能中心" desc="技能 = 一类文档的抽取契约：字段、校验规则与模型绑定。">
      <label class="file-btn">
        <input type="file" accept=".yaml,.yml" hidden @change="importYaml" />
        <span class="btn-like">导入 YAML</span>
      </label>
      <!-- one class or the other: `ghost primary` together leaves primary's dark
           text on ghost's transparent background, i.e. an invisible button -->
      <button :class="showGallery ? 'primary' : 'ghost'"
              @click="showGallery = !showGallery">从模板新建</button>
      <router-link to="/skills/new"><button class="primary">＋ 新建技能</button></router-link>
    </PageHeader>

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
            <td class="row-act" @click.stop>
              <button v-if="s.state === 'active'" class="ghost slim"
                      title="停用后列表置灰，不能再提交；已有任务不受影响"
                      @click="toggle(s, 'disable')">停用</button>
              <button v-if="s.state === 'disabled'" class="primary slim"
                      title="恢复为可提交状态（占用套餐技能席位）"
                      @click="toggle(s, 'enable')">启用</button>
              <button v-if="s.state === 'deleted'" class="primary slim"
                      title="恢复为启用状态，版本与历史都在"
                      @click="restore(s)">恢复</button>
              <button v-if="s.state !== 'deleted'" class="ghost slim"
                      title="软删除：进入归档页签，版本与历史保留，可随时恢复"
                      @click="del(s)">删除</button>
              <router-link :to="`/skills/${s.skill_code}`">
                <button class="ghost slim">编辑</button></router-link>
              <button class="ghost slim"
                      @click="downloadFile(api.skillExportUrl(s.skill_code),
                                           `${s.skill_code}.yaml`)">导出</button>
            </td>
          </tr>
        </tbody>
      </table>
      <Skeleton v-else-if="isLoading" :rows="5" />
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
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { api, downloadFile, type SkillInfo } from "../api";
import EmptyState from "../components/EmptyState.vue";
import PageHeader from "../components/PageHeader.vue";
import Skeleton from "../components/Skeleton.vue";
import SkillTemplateGallery from "../components/SkillTemplateGallery.vue";
import { toast } from "../toast";

const router = useRouter();
const qc = useQueryClient();
const showGallery = ref(false);
// lifecycle bucket (WP4): active roster / disabled / deleted archive
const bucket = ref<"active" | "disabled" | "deleted">("active");
const { data, isLoading } = useQuery({
  queryKey: computed(() => ["skills", bucket.value]),
  queryFn: () => bucket.value === "deleted" ? api.skillsByState("deleted") : api.skills(),
});
const items = computed<SkillInfo[]>(() => {
  const rows = data.value ?? [];
  return bucket.value === "deleted"
    ? rows
    : rows.filter((s) => s.state === bucket.value);
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
.file-btn .btn-like { border: 1px solid var(--border); background: var(--bg-raised);
  border-radius: 6px; padding: 6px 14px; cursor: pointer; display: inline-block; }
.file-btn .btn-like:hover { border-color: var(--accent); }
.code { font-family: Consolas, monospace; color: var(--blue); }
.desc { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ver { font-variant-numeric: tabular-nums; white-space: nowrap; }
.row-link { cursor: pointer; }
.row-act, .th-act { text-align: right; white-space: nowrap; }
.slim { padding: 2px 12px; font-size: 12px; }
</style>
