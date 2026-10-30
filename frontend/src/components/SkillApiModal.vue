<template>
  <!-- per-skill API integration drawer: address + auth + copy-paste examples
       pre-filled with THIS skill's code (docs live where the skill lives) -->
  <div class="mask" @click.self="$emit('close')">
    <div class="drawer">
      <header>
        <strong>{{ $t('skillApiModal.api_access') }} {{ skillCode }}</strong>
        <button class="ghost" @click="$emit('close')">✕</button>
      </header>

      <section>
        <h3>{{ $t('skillApiModal.endpoint_and_authentication') }}</h3>
        <div class="kv"><span class="dim">Base URL</span>
          <code>{{ base }}</code><CopyBtn :text="base" /></div>
        <I18nT keypath="skillApiModal.authentication_guide" tag="p" scope="global" class="dim">
          <template #authorization><code>Authorization: Bearer &lt;{{ $t('skillApiModal.api_key_placeholder') }}&gt;</code></template>
          <template #settings><router-link to="/settings" @click="$emit('close')">{{ $t('skillApiModal.settings_api_keys') }}</router-link></template>
        </I18nT>
      </section>

      <section>
        <h3>{{ $t('skillApiModal.submit_document_processing') }}</h3>
        <p class="dim">{{ $t('skillApiModal.post_api_v1_process_multipart_upload_one_or_more') }}</p>
        <pre>{{ curlSubmit }}</pre><CopyBtn :text="curlSubmit" block />
        <pre>{{ pySubmit }}</pre><CopyBtn :text="pySubmit" block />
      </section>

      <section>
        <h3>{{ $t('skillApiModal.poll_results') }}</h3>
        <p class="dim">{{ $t('skillApiModal.get_api_v1_status_transaction_id_file_level_status') }}</p>
        <pre>{{ curlStatus }}</pre><CopyBtn :text="curlStatus" block />
        <p class="dim schema-head">{{ $t('skillApiModal.result_field_structure_each_field_is_an_object') }}</p>
        <pre>{{ SCHEMA }}</pre>
        <ul class="legend dim">
          <I18nT keypath="skillApiModal.confidence_legend" tag="li" scope="global"><template #confidence><code>$confidence</code></template></I18nT>
          <I18nT keypath="skillApiModal.position_legend" tag="li" scope="global"><template #bbox><code>$bbox</code></template><template #pages><code>$pages</code></template></I18nT>
          <I18nT keypath="skillApiModal.inference_legend" tag="li" scope="global"><template #inferred><code>inferred: true</code></template><template #reasoning><code>$reasoning</code></template></I18nT>
          <li>{{ $t('skillApiModal.the_value_of_a_detail_table_field_is_an') }}</li>
          <li>{{ $t('skillApiModal.state_machine_queued_processing_pending_verification_completed_passed_rejected') }}</li>
        </ul>
      </section>

      <section>
        <h3>{{ $t('skillApiModal.webhook_push_no_polling') }}</h3>
        <p class="dim">{{ $t('skillApiModal.after_registering_a_callback_actively_post_to_your_address') }}</p>
        <pre>{{ curlHook }}</pre><CopyBtn :text="curlHook" block />
      </section>

      <section>
        <h3>{{ $t('skillApiModal.data_export') }}</h3>
        <I18nT keypath="skillApiModal.export_guide" tag="p" scope="global" class="dim">
          <template #json><code>GET /api/v1/cabinet/{{ skillCode }}</code></template>
          <template #csv><code>GET /api/v1/cabinet/{{ skillCode }}/export.csv</code></template>
        </I18nT>
      </section>

      <I18nT keypath="skillApiModal.openapi_guide" tag="footer" scope="global" class="dim">
        <template #link><a :href="base + '/docs'" target="_blank">{{ base }}/docs</a></template>
      </I18nT>
    </div>
  </div>
</template>

<script setup lang="ts">
import { I18nT, useI18n } from "vue-i18n";
const { t } = useI18n();

import { computed, defineComponent, h, ref } from "vue";
import { copyText } from "../clipboard";
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

while True:                                   # ${t('skillApiModal.webhook_poll_comment')}
    st = requests.get(f"{BASE}/api/v1/status/{tid}", headers=KEY).json()
    if st["status"] not in ("queued", "processing"):
        break
    time.sleep(3)
print(st["files"][0]["result"])`);

const curlStatus = computed(() =>
  `curl ${base}/api/v1/status/{transaction_id} \\\n  -H "Authorization: Bearer $IDP_API_KEY"`);

const curlHook = computed(() => `curl -X POST ${base}/api/v1/webhooks \\
  -H "Authorization: Bearer $IDP_API_KEY" -H "Content-Type: application/json" \\
  -d '{"url": "https://example.com/callback", "secret": "EXAMPLE_SHARED_SECRET",
       "events": ["file.completed", "file.pending_verification", "file.passed"]}'`);

const SCHEMA = computed(() => `"invoice_no": {
  "$value": "INV-2026-001",   // ${t('skillApiModal.extracted_value_comment')}
  "$confidence": 3,           // ${t('skillApiModal.confidence_comment')}
  "$bbox": [413, 265, 616, 274],
  "$pages": 1
}`);

/** small copy-to-clipboard button */
const CopyBtn = defineComponent({
  props: { text: { type: String, required: true },
           block: { type: Boolean, default: false } },
  setup(p) {
    const done = ref(false);
    const copy = async () => {
      try {
        await copyText(p.text);
        done.value = true;
        setTimeout(() => (done.value = false), 1500);
      } catch { toast.error(t('skillApiModal.copy_failed_please_select_the_text_manually')); }
    };
    return () => h("button", { class: ["copy", p.block ? "copy-block" : ""],
                               onClick: copy },
                   done.value ? t('skillApiModal.copied') : t('skillApiModal.copy'));
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
