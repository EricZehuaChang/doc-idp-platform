<template>
  <!-- per-skill API integration drawer: address + auth + copy-paste examples
       pre-filled with THIS skill's code (docs live where the skill lives) -->
  <div class="mask" @click.self="$emit('close')">
    <div class="drawer">
      <header>
        <strong>🔌 API 接入 — {{ skillCode }}</strong>
        <button class="ghost" @click="$emit('close')">✕</button>
      </header>

      <section>
        <h3>接入地址与认证</h3>
        <div class="kv"><span class="dim">Base URL</span>
          <code>{{ base }}</code><CopyBtn :text="base" /></div>
        <p class="dim">
          所有请求携带 <code>Authorization: Bearer &lt;API密钥&gt;</code>。
          密钥由管理员在 <router-link to="/settings" @click="$emit('close')">设置 → API 密钥</router-link>
          创建（仅创建时显示一次）。
        </p>
      </section>

      <section>
        <h3>① 提交文档处理</h3>
        <p class="dim">POST /api/v1/process — multipart 上传一个或多个文件，绑定本技能；返回 transaction_id。</p>
        <pre>{{ curlSubmit }}</pre><CopyBtn :text="curlSubmit" block />
        <pre>{{ pySubmit }}</pre><CopyBtn :text="pySubmit" block />
      </section>

      <section>
        <h3>② 轮询结果</h3>
        <p class="dim">GET /api/v1/status/{transaction_id} — 文件级状态与抽取结果。</p>
        <pre>{{ curlStatus }}</pre><CopyBtn :text="curlStatus" block />
        <p class="dim schema-head">结果字段结构（每个字段是一个对象）：</p>
        <pre>{{ SCHEMA }}</pre>
        <ul class="legend dim">
          <li><code>$confidence</code> 0-3 置信分级：3=原文精确定位；&lt;2 会进人工校验</li>
          <li><code>$bbox</code>/<code>$pages</code> 字段在原件中的位置（校验界面高亮同源）</li>
          <li><code>inferred: true</code> 为模型推断值，附 <code>$reasoning</code> 理由</li>
          <li>明细表字段的值是行对象数组</li>
          <li>状态机：queued → processing → pending_verification/completed → passed/rejected</li>
        </ul>
      </section>

      <section>
        <h3>③ Webhook 推送（免轮询）</h3>
        <p class="dim">注册回调后，文件完成/待审/通过/拒绝/失败/分割时主动 POST 到你的地址（HMAC-SHA256 签名头 X-IDP-Signature）。</p>
        <pre>{{ curlHook }}</pre><CopyBtn :text="curlHook" block />
      </section>

      <section>
        <h3>④ 数据导出</h3>
        <p class="dim">已通过数据可拉平导出：
          <code>GET /api/v1/cabinet/{{ skillCode }}</code>（JSON）或
          <code>GET /api/v1/cabinet/{{ skillCode }}/export.csv</code></p>
      </section>

      <footer class="dim">
        完整 OpenAPI 交互文档：<a :href="base + '/docs'" target="_blank">{{ base }}/docs</a>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, ref } from "vue";
import { toast } from "../toast";

const props = defineProps<{ skillCode: string }>();
defineEmits<{ (e: "close"): void }>();

const base = location.origin;

const curlSubmit = computed(() => `curl -X POST ${base}/api/v1/process \\
  -H "Authorization: Bearer $IDP_API_KEY" \\
  -F "files=@invoice.pdf" \\
  -F "skill_code=${props.skillCode}"`);

const pySubmit = computed(() => `import requests, time

BASE = "${base}"
KEY = {"Authorization": "Bearer " + IDP_API_KEY}

r = requests.post(f"{BASE}/api/v1/process", headers=KEY,
                  files={"files": open("invoice.pdf", "rb")},
                  data={"skill_code": "${props.skillCode}"})
tid = r.json()["transaction_id"]

while True:                                   # 或注册 Webhook 免轮询
    st = requests.get(f"{BASE}/api/v1/status/{tid}", headers=KEY).json()
    if st["status"] not in ("queued", "processing"):
        break
    time.sleep(3)
print(st["files"][0]["result"])`);

const curlStatus = computed(() =>
  `curl ${base}/api/v1/status/{transaction_id} \\\n  -H "Authorization: Bearer $IDP_API_KEY"`);

const curlHook = computed(() => `curl -X POST ${base}/api/v1/webhooks \\
  -H "Authorization: Bearer $IDP_API_KEY" -H "Content-Type: application/json" \\
  -d '{"url": "https://你的系统/callback", "secret": "共享密钥",
       "events": ["file.completed", "file.pending_verification", "file.passed"]}'`);

const SCHEMA = `"invoice_no": {
  "$value": "INV-2026-001",   // 抽取值
  "$confidence": 3,           // 0-3 置信
  "$bbox": [413, 265, 616, 274],
  "$pages": 1
}`;

/** small copy-to-clipboard button */
const CopyBtn = defineComponent({
  props: { text: { type: String, required: true },
           block: { type: Boolean, default: false } },
  setup(p) {
    const done = ref(false);
    const copy = async () => {
      try {
        await navigator.clipboard.writeText(p.text);
        done.value = true;
        setTimeout(() => (done.value = false), 1500);
      } catch { toast.error("复制失败，请手动选择文本"); }
    };
    return () => h("button", { class: ["copy", p.block ? "copy-block" : ""],
                               onClick: copy },
                   done.value ? "✓ 已复制" : "复制");
  },
});
</script>

<style scoped>
.mask { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5); z-index: 110;
  display: flex; justify-content: flex-end; }
.drawer { width: 620px; max-width: 94vw; height: 100%; overflow-y: auto;
  background: var(--bg-panel); border-left: 1px solid var(--border);
  padding: 18px 20px 28px; }
header { display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 6px; }
header strong { font-size: 15px; }
h3 { font-size: 14px; color: var(--accent); margin: 18px 0 8px; }
.kv { display: flex; gap: 10px; align-items: center; margin-bottom: 6px; }
.kv code { background: var(--bg-raised); padding: 2px 8px; border-radius: 5px; }
pre { background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 12px; font-size: 12px; line-height: 1.6; overflow-x: auto;
  margin: 6px 0 2px; white-space: pre; }
:deep(.copy) { padding: 1px 10px; font-size: 12px; }
:deep(.copy-block) { margin: 2px 0 6px; }
.schema-head { margin-top: 10px; }
.legend { padding-left: 18px; font-size: 12.5px; line-height: 1.9; margin: 6px 0; }
.legend code { background: var(--bg-raised); padding: 0 5px; border-radius: 4px; }
footer { margin-top: 18px; padding-top: 12px; border-top: 1px solid var(--border);
  font-size: 13px; }
p { font-size: 13px; margin: 4px 0; }
.dim { color: var(--text-dim); }
</style>
