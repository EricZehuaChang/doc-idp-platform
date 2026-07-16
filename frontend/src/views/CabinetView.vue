<template>
  <main class="page">
    <PageHeader title="数据柜" desc="已通过校验的结构化数据，按技能归档，可导出对接下游。">
      <select v-model="skill">
        <option value="" disabled>选择技能</option>
        <option v-for="s in skills" :key="s.skill_code" :value="s.skill_code">
          {{ s.name }}（{{ s.skill_code }}）</option>
      </select>
      <button v-if="skill && rows.length" class="primary"
              @click="downloadFile(api.cabinetCsvUrl(skill), `${skill}.csv`)">导出 CSV</button>
    </PageHeader>

    <section class="card-panel">
      <div class="count-bar" v-if="skill && rows.length">
        <span class="dim">{{ rows.length }} 条已通过数据</span>
      </div>
      <div class="table-scroll" v-if="rows.length">
        <table class="data-table">
          <thead><tr><th v-for="c in columns" :key="c">{{ c }}</th></tr></thead>
          <tbody>
            <tr v-for="(r, i) in rows" :key="i">
              <td v-for="c in columns" :key="c" :title="r[c]" class="cell">{{ short(r[c]) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <Skeleton v-else-if="skill && isLoading" :rows="6" />
      <EmptyState v-else-if="skill" title="该技能暂无已通过数据" glyph="📦">
        文件在校验页「通过」后进入数据柜；直通（免人审）完成的文件也会出现在这里。
      </EmptyState>
      <EmptyState v-else title="选择一个技能" glyph="📦">
        数据柜按技能归档。从右上角选择技能查看其结构化数据。
      </EmptyState>
    </section>
  </main>
</template>

<script setup lang="ts">
import { useQuery } from "@tanstack/vue-query";
import { computed, ref, watch } from "vue";
import { api, downloadFile, type SkillInfo } from "../api";
import EmptyState from "../components/EmptyState.vue";
import PageHeader from "../components/PageHeader.vue";
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
.count-bar { padding: 10px 14px 0; }
.table-scroll { overflow-x: auto; padding: 6px 0; }
.cell { white-space: nowrap; max-width: 280px; overflow: hidden; text-overflow: ellipsis; }
</style>
