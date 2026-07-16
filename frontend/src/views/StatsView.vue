<template>
  <main class="wrap">
    <h2>技能质量看板</h2>
    <p class="dim">修正率 = 人工改动字段数 / 已决文件数（准确率代理指标）；直通率 = 免人审直接完成占比。</p>
    <div class="cards">
      <div v-for="s in stats" :key="s.skill_code" class="card">
        <div class="card-head">
          <strong>{{ s.skill_code }}</strong>
          <span class="dim">{{ s.total }} 文件</span>
        </div>
        <div class="metrics">
          <div class="metric">
            <span class="num">{{ pct(s.straight_through_rate) }}</span>
            <span class="dim">直通率</span>
          </div>
          <div class="metric">
            <span class="num" :class="{ warn: (s.correction_rate ?? 0) > 0.3 }">
              {{ pct(s.correction_rate) }}</span>
            <span class="dim">修正率</span>
          </div>
          <div class="metric">
            <span class="num">{{ s.by_status["pending_verification"] || 0 }}</span>
            <span class="dim">待审积压</span>
          </div>
        </div>
        <div v-if="s.top_corrected_fields.length" class="hot">
          <span class="dim">高频被改：</span>
          <span v-for="f in s.top_corrected_fields" :key="f.field" class="chip">
            {{ f.field }} ×{{ f.count }}</span>
        </div>
      </div>
    </div>
    <p v-if="!stats.length" class="dim">暂无数据——跑一些任务后再来。</p>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, type SkillStat } from "../api";

const stats = ref<SkillStat[]>([]);
const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);
onMounted(async () => { stats.value = (await api.stats()).skills; });
</script>

<style scoped>
.wrap { padding: 20px; max-width: 1100px; margin: 0 auto; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
.card { background: var(--bg-panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
.card-head { display: flex; justify-content: space-between; margin-bottom: 10px; }
.metrics { display: flex; gap: 24px; }
.metric { display: flex; flex-direction: column; }
.num { font-size: 22px; font-weight: 700; color: var(--accent); }
.num.warn { color: var(--red); }
.hot { margin-top: 10px; }
.chip { background: var(--bg-raised); border: 1px solid var(--border); border-radius: 5px;
  padding: 1px 8px; margin-right: 6px; font-size: 12px; }
.dim { color: var(--text-dim); }
</style>
