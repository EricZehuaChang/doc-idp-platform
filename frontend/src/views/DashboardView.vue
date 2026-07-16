<template>
  <main class="page">
    <PageHeader title="看板" desc="用量趋势与技能质量：credits 消耗、直通率与修正率。" />

    <div class="charts">
      <!-- credits by day -->
      <section class="panel">
        <div class="chart-head">
          <h3>Credits 消耗趋势</h3>
          <div class="ranges">
            <button v-for="r in RANGES" :key="r.d" class="range"
                    :class="{ primary: days === r.d }" @click="days = r.d">{{ r.label }}</button>
          </div>
        </div>
        <BarChart :items="byDay" color="var(--accent)" unit="credits" />
      </section>

      <!-- credits by skill -->
      <section class="panel">
        <div class="chart-head">
          <h3>按技能用量</h3>
          <select v-model="skillFilter" class="sel">
            <option value="">全部技能</option>
            <option v-for="s in skills" :key="s.skill_code" :value="s.skill_code">
              {{ s.name }}</option>
          </select>
        </div>
        <BarChart :items="bySkill" color="var(--blue)" unit="credits" />
      </section>
    </div>

    <!-- skill quality (former stats page) -->
    <h2 class="q-head">技能质量</h2>
    <p class="dim">修正率 = 人工改动字段数 / 已决文件数；直通率 = 免人审直接完成占比。</p>
    <div class="cards">
      <div v-for="s in quality" :key="s.skill_code" class="card">
        <div class="card-head">
          <strong>{{ s.skill_code }}</strong>
          <span class="dim">{{ s.total }} 文件</span>
        </div>
        <div class="metrics">
          <div class="metric"><span class="num">{{ pct(s.straight_through_rate) }}</span>
            <span class="dim">直通率</span></div>
          <div class="metric">
            <span class="num" :class="{ warn: (s.correction_rate ?? 0) > 0.3 }">
              {{ pct(s.correction_rate) }}</span>
            <span class="dim">修正率</span></div>
          <div class="metric"><span class="num">{{ s.by_status["pending_verification"] || 0 }}</span>
            <span class="dim">待审积压</span></div>
        </div>
        <div v-if="s.top_corrected_fields.length" class="hot">
          <span class="dim">高频被改：</span>
          <span v-for="f in s.top_corrected_fields" :key="f.field" class="chip">
            {{ f.field }} ×{{ f.count }}</span>
        </div>
      </div>
    </div>
    <p v-if="!quality.length" class="dim">暂无质量数据——跑一些任务并人工校验后出现。</p>
  </main>
</template>

<script setup lang="ts">
import { useQuery } from "@tanstack/vue-query";
import { computed, defineComponent, h, ref } from "vue";
import { api, type SkillStat } from "../api";
import PageHeader from "../components/PageHeader.vue";

const RANGES = [{ d: 7, label: "7D" }, { d: 30, label: "1M" }, { d: 365, label: "1Y" }];
const days = ref(7);
const skillFilter = ref("");

const { data: usage } = useQuery({
  queryKey: computed(() => ["usage", days.value, skillFilter.value]),
  queryFn: () => api.usageStats(days.value, skillFilter.value || undefined),
});
const { data: skillsData } = useQuery({ queryKey: ["skills"], queryFn: api.skills });
const skills = computed(() => skillsData.value ?? []);
const { data: statsData } = useQuery({ queryKey: ["stats"], queryFn: api.stats });
const quality = computed<SkillStat[]>(() => statsData.value?.skills ?? []);

const byDay = computed(() =>
  (usage.value?.by_day ?? []).map((d) => ({ label: d.date, value: d.credits })));
const bySkill = computed(() =>
  (usage.value?.by_skill ?? []).map((d) => ({ label: d.skill_code, value: d.credits })));

const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);

/** minimal themed SVG bar chart — no chart-lib dependency */
const BarChart = defineComponent({
  props: { items: { type: Array as () => { label: string; value: number }[], required: true },
           color: { type: String, default: "var(--accent)" },
           unit: { type: String, default: "" } },
  setup(props) {
    return () => {
      const items = props.items;
      if (!items.length)
        return h("p", { class: "dim", style: "padding:30px 0;text-align:center" },
                 "暂无数据");
      const W = 640, H = 220, padL = 44, padB = 34, padT = 12;
      const max = Math.max(...items.map((i) => i.value), 1);
      const bw = Math.min(56, (W - padL) / items.length * 0.6);
      const step = (W - padL) / items.length;
      const bars = items.map((it, i) => {
        const hgt = (it.value / max) * (H - padB - padT);
        const x = padL + i * step + (step - bw) / 2;
        const y = H - padB - hgt;
        return h("g", [
          h("rect", { x, y, width: bw, height: Math.max(hgt, 1), rx: 4,
                      fill: props.color, "fill-opacity": 0.85 }),
          h("text", { x: x + bw / 2, y: H - padB + 14, "text-anchor": "middle",
                      class: "tick" }, it.label.length > 12
                        ? it.label.slice(0, 11) + "…" : it.label),
          h("text", { x: x + bw / 2, y: y - 5, "text-anchor": "middle",
                      class: "val" }, String(it.value)),
        ]);
      });
      const gridLines = [0, 0.25, 0.5, 0.75, 1].map((f) => {
        const y = H - padB - f * (H - padB - padT);
        return h("g", [
          h("line", { x1: padL, y1: y, x2: W, y2: y, class: "grid" }),
          h("text", { x: padL - 6, y: y + 4, "text-anchor": "end", class: "tick" },
            String(Math.round(max * f))),
        ]);
      });
      return h("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart",
                        role: "img", "aria-label": `bar chart (${props.unit})` },
               [...gridLines, ...bars]);
    };
  },
});
</script>

<style scoped>
.charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.panel { background: var(--bg-panel); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 16px; }
.chart-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.chart-head h3 { margin: 0; font-size: 15px; flex: 1; }
.ranges { display: flex; gap: 4px; }
.range { padding: 2px 10px; font-size: 12px; }
.sel { background: var(--bg-raised); color: var(--text); border: 1px solid var(--border);
  border-radius: 6px; padding: 4px 8px; max-width: 220px; }
:deep(.chart) { width: 100%; height: auto; }
:deep(.grid) { stroke: var(--border); stroke-width: 1; }
:deep(.tick) { fill: var(--text-dim); font-size: 10px; }
:deep(.val) { fill: var(--text); font-size: 10px; }
.q-head { margin-top: 24px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 14px; margin-top: 8px; }
.card { background: var(--bg-panel); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px; }
.card-head { display: flex; justify-content: space-between; margin-bottom: 10px; }
.metrics { display: flex; gap: 24px; }
.metric { display: flex; flex-direction: column; }
.num { font-size: 22px; font-weight: 700; color: var(--accent); }
.num.warn { color: var(--red); }
.hot { margin-top: 10px; }
.chip { background: var(--bg-raised); border: 1px solid var(--border); border-radius: 5px;
  padding: 1px 8px; margin-right: 6px; font-size: 12px; }
.dim { color: var(--text-dim); }
@media (max-width: 1000px) { .charts { grid-template-columns: 1fr; } }
</style>
