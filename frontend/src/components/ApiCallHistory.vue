<!-- R3 (2026-09-22): API view → 接口调用 tab. Metadata-only history of key
     calls to /detect and /locate (D4: no file names, no content); the server
     scopes rows to the viewer's own keys unless they are an admin. -->
<template>
  <section class="card-panel calls">
    <div class="filters">
      <input v-model="keyId" placeholder="Key ID" aria-label="Key ID" class="key-in" />
      <select v-model="endpoint" aria-label="接口">
        <option value="">全部接口</option>
        <option value="/api/v1/detect">印章签名检测</option>
        <option value="/api/v1/locate">版面定位</option>
      </select>
      <input v-model="from" type="date" aria-label="开始日期" />
      <span class="dim">至</span>
      <input v-model="to" type="date" aria-label="结束日期" />
      <button class="primary" @click="page = 1; load()">查询</button>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
    <table class="data-table">
      <thead><tr><th>时间</th><th>Key</th><th>接口</th><th>状态码</th>
        <th>耗时</th><th>页数</th><th>大小</th></tr></thead>
      <tbody>
        <tr v-for="row in rows" :key="row.id">
          <td>{{ new Date(row.created_at).toLocaleString() }}</td>
          <td :title="row.key_id">{{ row.key_name }}</td>
          <td>{{ ENDPOINTS[row.endpoint] ?? row.endpoint }}</td>
          <td :class="{ bad: row.status_code >= 400 }">{{ row.status_code }}</td>
          <td>{{ row.duration_ms }} ms</td>
          <td>{{ row.page_count ?? "—" }}</td>
          <td>{{ size(row.size_bytes) }}</td>
        </tr>
      </tbody>
    </table>
    <p v-if="!rows.length" class="dim">{{ loading ? "加载中…" : "暂无接口调用" }}</p>
    <div class="filters pager">
      <span class="dim">共 {{ total }} 条</span>
      <button :disabled="page <= 1" @click="page--; load()">上一页</button>
      <span>第 {{ page }} 页</span>
      <button :disabled="page * PAGE_SIZE >= total" @click="page++; load()">下一页</button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, type ApiCallRow } from "../api";

const PAGE_SIZE = 20;   // server default page size of GET /api-calls
const ENDPOINTS: Record<string, string> = {
  "/api/v1/detect": "印章签名检测", "/api/v1/locate": "版面定位" };
const rows = ref<ApiCallRow[]>([]), total = ref(0), page = ref(1);
const loading = ref(false), error = ref("");
const keyId = ref(""), endpoint = ref(""), from = ref(""), to = ref("");

async function load() {
  loading.value = true; error.value = "";
  try {
    const r = await api.apiCalls({ page: String(page.value), key_id: keyId.value,
      endpoint: endpoint.value, date_from: from.value, date_to: to.value });
    rows.value = r.data; total.value = r.total;
  } catch (e) { error.value = String(e); } finally { loading.value = false; }
}

/** Same unit ladder as the task ledger's 大小 column. */
function size(n: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(2)} MB`;
}
onMounted(load);
</script>

<style scoped>
.calls { padding: 16px; overflow-x: auto; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 0 0 12px; }
.filters input, .filters select { width: auto; }
.key-in { min-width: 240px; }
.pager { margin: 12px 0 0; }
.bad { color: var(--danger, #e5484d); }
</style>
