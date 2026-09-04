<template>
  <!-- Starter gallery (onboarding P0): a blank skill editor asks the customer to
       invent a field list for a concept they have not met yet. Picking a
       ready-made document type and fixing two fields is the same outcome with
       none of that. Import lands on a draft, exactly like YAML import. -->
  <div class="gallery">
    <Skeleton v-if="isLoading" :rows="3" />
    <p v-else-if="!templates.length" class="dim none">
      没有内置模板可用，请从空白新建。
    </p>
    <div v-else class="cards">
      <button v-for="t in templates" :key="t.id" class="card"
              :disabled="importing !== null"
              @click="use(t)">
        <span class="card-head">
          <span class="name">{{ t.name }}</span>
          <span v-if="importing === t.id" class="dim tag">导入中…</span>
          <span v-else class="tag">{{ t.field_count }} 个字段</span>
        </span>
        <span class="desc">{{ t.description }}</span>
        <span class="meta dim">
          <span v-if="t.table_count">明细表 {{ t.column_count }} 列</span>
          <span v-if="t.validator_count">校验 {{ t.validator_count }} 条</span>
          <span v-if="t.doc_type_hint" class="hint-type">{{ t.doc_type_hint }}</span>
        </span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { api, type SkillTemplate } from "../api";
import Skeleton from "./Skeleton.vue";
import { toast } from "../toast";

const router = useRouter();
const qc = useQueryClient();
// null = idle; a template id while its import is in flight (the whole gallery
// disables, so a double click cannot burn two skill seats)
const importing = ref<string | null>(null);

const { data, isLoading } = useQuery({
  queryKey: ["skill-templates"],
  queryFn: () => api.skillTemplates(),
  staleTime: Infinity,            // ships with the build, never changes at runtime
});
const templates = computed<SkillTemplate[]>(() => data.value?.templates ?? []);

async function use(t: SkillTemplate) {
  if (importing.value) return;
  importing.value = t.id;
  try {
    const r = await api.skillTemplateImport(t.id);
    toast.ok(`已从模板「${t.name}」创建草稿，核对字段后发布即可使用`);
    qc.invalidateQueries({ queryKey: ["skills"] });
    router.push(`/skills/${r.skill_code}`);
  } catch (e) {
    toast.error(e);
  } finally {
    importing.value = null;
  }
}
</script>

<style scoped>
.gallery { width: 100%; }
.none { font-size: 13px; }
.cards { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }
.card { display: flex; flex-direction: column; gap: 6px; text-align: left;
  padding: 14px 16px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg-raised); cursor: pointer; font: inherit; color: inherit; }
.card:hover:not(:disabled) { border-color: var(--accent); }
.card:disabled { opacity: 0.6; cursor: default; }
.card-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.name { font-weight: 600; font-size: 14px; }
.tag { font-size: 12px; white-space: nowrap; }
.desc { font-size: 12.5px; line-height: 1.7; }
.meta { display: flex; flex-wrap: wrap; gap: 10px; font-size: 11.5px; }
.hint-type { font-family: Consolas, monospace; }
</style>
