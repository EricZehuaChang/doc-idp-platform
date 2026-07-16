<template>
  <main class="wrap">
    <h2>数据柜 Cabinet</h2>
    <div class="bar">
      <select v-model="skill">
        <option value="" disabled>选择技能</option>
        <option v-for="s in skills" :key="s.skill_code" :value="s.skill_code">
          {{ s.name }}（{{ s.skill_code }}）
        </option>
      </select>
      <a v-if="skill && rows.length" :href="api.cabinetCsvUrl(skill)">
        <button>导出 CSV</button>
      </a>
      <span class="dim" v-if="skill">{{ rows.length }} 条</span>
    </div>
    <div class="table-scroll" v-if="rows.length">
      <table>
        <thead><tr><th v-for="c in columns" :key="c">{{ c }}</th></tr></thead>
        <tbody>
          <tr v-for="(r, i) in rows" :key="i">
            <td v-for="c in columns" :key="c" :title="r[c]">{{ short(r[c]) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <Skeleton v-else-if="skill && isLoading" :rows="6" />
    <p v-else-if="skill" class="dim">
      该技能暂无已通过数据——文件在校验页“通过”后会进入数据柜，可导出 CSV。
    </p>
    <p v-else class="dim">还没有任何技能。先到技能中心创建并发布一个抽取技能。</p>
  </main>
</template>

<script setup lang="ts">
import { useQuery } from "@tanstack/vue-query";
import { computed, ref, watch } from "vue";
import { api, type SkillInfo } from "../api";
import Skeleton from "../components/Skeleton.vue";

const skill = ref("");

const { data: skillsData } = useQuery({ queryKey: ["skills"], queryFn: api.skills });
const skills = computed<SkillInfo[]>(() => skillsData.value ?? []);
watch(skills, (list) => { if (list.length && !skill.value) skill.value = list[0].skill_code; },
      { immediate: true });

const { data: cabinetData, isLoading } = useQuery({
  queryKey: computed(() => ["cabinet", skill.value]),
  queryFn: () => api.cabinet(skill.value),
  enabled: computed(() => !!skill.value),
});
const rows = computed(() => cabinetData.value?.rows ?? []);

const columns = computed(() => (rows.value[0] ? Object.keys(rows.value[0]) : []));
const short = (v: string) => (v && v.length > 40 ? v.slice(0, 40) + "…" : v);
</script>

<style scoped>
.wrap { padding: 20px; max-width: 1300px; margin: 0 auto; }
.bar { display: flex; gap: 12px; align-items: center; margin-bottom: 14px; }
select { background: var(--bg-raised); color: var(--text); border: 1px solid var(--border);
  border-radius: 6px; padding: 6px 10px; }
.table-scroll { overflow-x: auto; background: var(--bg-panel); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border);
  white-space: nowrap; max-width: 300px; overflow: hidden; text-overflow: ellipsis; }
th { color: var(--text-dim); position: sticky; top: 0; background: var(--bg-panel); }
.dim { color: var(--text-dim); }
</style>
