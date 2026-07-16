<template>
  <main class="page">
    <PageHeader title="技能中心" desc="技能 = 一类文档的抽取契约：字段、校验规则与模型绑定。">
      <label class="file-btn">
        <input type="file" accept=".yaml,.yml" hidden @change="importYaml" />
        <span class="btn-like">导入 YAML</span>
      </label>
      <router-link to="/skills/new"><button class="primary">＋ 新建技能</button></router-link>
    </PageHeader>

    <section class="card-panel">
      <table v-if="items.length" class="data-table">
        <thead>
          <tr><th>技能代码</th><th>名称</th><th>类型</th><th>状态</th><th class="th-act"></th></tr>
        </thead>
        <tbody>
          <tr v-for="s in items" :key="s.skill_code" class="row-link"
              @click="$router.push(`/skills/${s.skill_code}`)">
            <td class="code">{{ s.skill_code }}</td>
            <td>{{ s.name }}</td>
            <td class="dim">{{ s.kind === "extract" ? "抽取" : s.kind }}</td>
            <td><span class="chip" :class="`chip-${s.state}`">
              {{ s.state === "active" ? "启用" : s.state }}</span></td>
            <td class="row-act" @click.stop>
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
      <EmptyState v-else title="还没有技能" glyph="⚙️">
        定义 1-2 个样本即可上线一个技能：上传样本预标注 → 核对字段 → 试跑 → 发布。
        <template #action>
          <router-link to="/skills/new"><button class="primary">＋ 新建技能</button></router-link>
        </template>
      </EmptyState>
    </section>
  </main>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed } from "vue";
import { useRouter } from "vue-router";
import { api, downloadFile, type SkillInfo } from "../api";
import EmptyState from "../components/EmptyState.vue";
import PageHeader from "../components/PageHeader.vue";
import Skeleton from "../components/Skeleton.vue";
import { toast } from "../toast";

const router = useRouter();
const qc = useQueryClient();
const { data, isLoading } = useQuery({ queryKey: ["skills"], queryFn: api.skills });
const items = computed<SkillInfo[]>(() => data.value ?? []);

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
.file-btn .btn-like { border: 1px solid var(--border); background: var(--bg-raised);
  border-radius: 6px; padding: 6px 14px; cursor: pointer; display: inline-block; }
.file-btn .btn-like:hover { border-color: var(--accent); }
.code { font-family: Consolas, monospace; color: var(--blue); }
.row-link { cursor: pointer; }
.row-act, .th-act { text-align: right; white-space: nowrap; }
.slim { padding: 2px 12px; font-size: 12px; }
</style>
