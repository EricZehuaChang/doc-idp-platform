<template>
  <main class="wrap">
    <h2>数据柜 Cabinet</h2>
    <div class="bar">
      <select v-model="skill" @change="load">
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
    <p v-else-if="skill" class="dim">该技能暂无已通过数据。</p>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, type SkillInfo } from "../api";

const skills = ref<SkillInfo[]>([]);
const skill = ref("");
const rows = ref<Record<string, string>[]>([]);

const columns = computed(() => (rows.value[0] ? Object.keys(rows.value[0]) : []));
const short = (v: string) => (v && v.length > 40 ? v.slice(0, 40) + "…" : v);

async function load() {
  if (!skill.value) return;
  rows.value = (await api.cabinet(skill.value)).rows;
}
onMounted(async () => {
  skills.value = await api.skills();
  if (skills.value.length) { skill.value = skills.value[0].skill_code; await load(); }
});
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
