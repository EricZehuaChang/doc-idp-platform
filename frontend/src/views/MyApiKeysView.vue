<template>
  <div class="wrap">
    <h1>我的 API Key</h1>
    <p class="dim">
      创建受限凭据，供 Agent 或集成客户端使用：按管理员授权选择提交查询、印章签名检测和版面定位；任务仅限本 Key 提交的。
      完整 Key 仅在创建时显示一次。</p>

    <div class="panel">
      <div class="row">
        <input v-model="name" maxlength="100" placeholder="Key 名称，如：前台扫描仪" class="name-in" />
        <input v-model="skills" placeholder="可用技能代码，逗号分隔；留空 = 全部" class="skills-in" />
        <button class="primary" :disabled="!grants.allow_create || !groups.length" @click="createKey">＋ 创建 Key</button>
      </div>

      <p v-if="!grants.allow_create" class="dim">管理员已关闭创建与使用个人 Key 的权限。</p>
      <p v-if="grants.allowed_skill_codes" class="dim">可用技能：{{ grants.allowed_skill_codes.join('、') || '无' }}（留空使用此范围）</p>
      <div class="row"><label v-for="g in groupOptions" :key="g.value" :title="grants.groups.includes(g.value) ? '' : '管理员未授权'">
        <input type="checkbox" v-model="groups" :value="g.value" :disabled="!grants.groups.includes(g.value)" /> {{ g.label }}
      </label></div>
      <!-- show-once panel -->
      <div v-if="freshKey" class="fresh-key">
        <p>⚠️ 完整 Key 仅显示这一次，请立即复制保存：</p>
        <div class="key-line">
          <code>{{ freshKey }}</code>
          <button class="primary" @click="copyKey">{{ copied ? "✓ 已复制" : "复制" }}</button>
        </div>
      </div>

      <table v-if="keys.length">
        <thead><tr><th>名称</th><th>权限</th><th>技能范围</th><th>最近使用</th><th>创建时间</th><th></th></tr></thead>
        <tbody>
          <tr v-for="k in keys" :key="k.id">
            <td>{{ k.name }}</td>
            <td class="dim">{{ k.scopes.join("、") || "—" }}</td>
            <td class="dim">{{ k.allowed_skill_codes ? k.allowed_skill_codes.join("、") : "全部" }}</td>
            <td class="dim">{{ k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "—" }}</td>
            <td class="dim">{{ k.created_at ? new Date(k.created_at).toLocaleString() : "—" }}</td>
            <td><button class="danger" @click="revoke(k)">吊销</button></td>
          </tr>
        </tbody>
      </table>
      <p v-else class="dim empty">还没有 Key。创建后把它填进 Agent 或集成客户端的连接设置。</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, type ApiGrants } from "../api";
import { toast } from "../toast";

type MyKeyRow = {
  id: string; name: string; key_type: string; key_prefix: string;
  scopes: string[]; allowed_skill_codes: string[] | null;
  created_at: string | null; last_used_at: string | null;
};

const grants = ref<ApiGrants>({ allow_create: false, groups: [], allowed_skill_codes: null });
const groups = ref<string[]>(['process']);
const groupOptions = [{ value: 'process', label: '提交与查询' }, { value: 'detect', label: '印章签名检测' }, { value: 'locate', label: '版面定位' }];
const keys = ref<MyKeyRow[]>([]);
const name = ref("");
const skills = ref("");
const freshKey = ref("");
const copied = ref(false);

async function load() {
  try { const r = await api.myKeys(); keys.value = r.keys; grants.value = r.grants; groups.value = groups.value.filter(g => r.grants.groups.includes(g)); } catch (e) { toast.error(e); }
}
async function createKey() {
  const codes = skills.value.split(/[，,]/).map(s => s.trim()).filter(Boolean);
  try {
    const r = await api.createMyKey(name.value, codes.length ? codes : grants.value.allowed_skill_codes, groups.value);
    freshKey.value = r.key;
    copied.value = false;
    name.value = "";
    skills.value = "";
    toast.ok("Key 已创建——完整 Key 仅显示这一次");
    await load();
  } catch (e) { toast.error(e); }
}
async function copyKey() {
  try {
    await navigator.clipboard.writeText(freshKey.value);
    copied.value = true;
  } catch { toast.error("复制失败，请手动选择文本"); }
}
async function revoke(k: MyKeyRow) {
  if (!confirm(`吊销 Key「${k.name}」？使用它的终端将立即失效。`)) return;
  try { await api.revokeMyKey(k.id); toast.ok("已吊销"); await load(); }
  catch (e) { toast.error(e); }
}
onMounted(load);
</script>

<style scoped>
.wrap { max-width: 880px; margin: 0 auto; padding: 24px; }
h1 { font-size: 20px; margin-bottom: 6px; }
.panel { margin-top: 16px; }
.row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.name-in { width: 220px; }
.skills-in { flex: 1; min-width: 240px; }
.fresh-key { border: 1px solid var(--accent); border-radius: 8px;
  padding: 12px 16px; margin: 8px 0 14px; background: var(--bg-hover); }
.key-line { display: flex; align-items: center; gap: 10px; }
.key-line code { flex: 1; word-break: break-all; font-size: 14px; }
.empty { padding: 12px 0; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
th { color: var(--text-dim, #888); font-weight: 600; }
</style>
