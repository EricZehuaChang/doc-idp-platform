<template>
  <main class="page">
    <PageHeader :title="$t('cabinet.data_cabinet')" :desc="$t('cabinet.validated_structured_data_archived_by_skill_exportable_for_downstream')">
      <select v-model="skill">
        <option value="" disabled>{{ $t('cabinet.select_skill') }}</option>
        <option v-for="s in skills" :key="s.skill_code" :value="s.skill_code">
          {{ s.name }}（{{ s.skill_code }}）</option>
      </select>
      <button v-if="skill && rows.length" class="primary"
              @click="downloadFile(api.cabinetCsvUrl(skill), `${skill}.csv`)">{{ $t('cabinet.export_csv') }}</button>
    </PageHeader>

    <section class="card-panel">
      <div class="count-bar" v-if="skill && rows.length">
        <span class="dim">{{ rows.length }} {{ $t('cabinet.approved_records') }}</span>
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
      <EmptyState v-else-if="skill" :title="$t('cabinet.no_approved_data_for_this_skill_yet')" glyph="📦">
        {{ $t('cabinet.files_enter_the_data_vault_after_being_approved_on') }}
      </EmptyState>
      <EmptyState v-else :title="$t('cabinet.select_a_skill')" glyph="📦">
        {{ $t('cabinet.the_data_vault_is_archived_by_skill_select_a') }}
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
