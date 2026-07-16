<template>
  <main class="wrap">
    <div class="bar">
      <h2>技能中心</h2>
      <router-link to="/skills/new"><button class="primary">＋ 新建技能</button></router-link>
      <label class="import-btn">
        <input type="file" accept=".yaml,.yml" hidden @change="importYaml" />
        <span class="btn-like">导入 YAML</span>
      </label>
    </div>

    <table v-if="items.length">
      <thead>
        <tr><th>技能代码</th><th>名称</th><th>类型</th><th>状态</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="s in items" :key="s.skill_code">
          <td class="code">{{ s.skill_code }}</td>
          <td>{{ s.name }}</td>
          <td>{{ s.kind === "extract" ? "抽取" : s.kind }}</td>
          <td><span class="state" :class="s.state">{{ s.state === "active" ? "启用" : s.state }}</span></td>
          <td class="ops">
            <router-link :to="`/skills/${s.skill_code}`"><button class="primary">编辑</button></router-link>
            <a :href="api.skillExportUrl(s.skill_code)" download><button>导出 YAML</button></a>
          </td>
        </tr>
      </tbody>
    </table>
    <Skeleton v-else-if="isLoading" :rows="5" />
    <div v-else class="empty-state">
      <p class="big">还没有技能</p>
      <p>技能 = 一类文档的抽取契约（字段 + 校验规则 + 模型绑定）。定义 1-2 个样本即可上线。</p>
      <p>点击右上「＋ 新建技能」，或用「导入 YAML」恢复已有技能包。</p>
    </div>
  </main>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed } from "vue";
import { useRouter } from "vue-router";
import { api, type SkillInfo } from "../api";
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
.wrap { padding: 20px; max-width: 1100px; margin: 0 auto; }
.bar { display: flex; gap: 12px; align-items: center; margin-bottom: 14px; }
.bar h2 { margin-right: auto; }
.import-btn .btn-like { border: 1px solid var(--border); background: var(--bg-raised);
  border-radius: 6px; padding: 6px 14px; cursor: pointer; display: inline-block; }
.import-btn .btn-like:hover { border-color: var(--accent); }
table { width: 100%; border-collapse: collapse; background: var(--bg-panel);
  border-radius: 8px; }
th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); }
th { color: var(--text-dim); font-weight: 600; }
.code { font-family: Consolas, monospace; color: var(--blue); }
.ops { display: flex; gap: 8px; }
.state.active { color: var(--green); }
.empty-state { color: var(--text-dim); line-height: 2; margin-top: 24px; }
.empty-state .big { font-size: 18px; color: var(--text); }
</style>
