<template>
  <div class="wrap">
    <h1>{{ $t('myApiKeys.my_api_keys') }}</h1>
    <p class="dim">
      {{ $t('myApiKeys.create_restricted_credentials_for_agents_or_integration_clients_choose') }}</p>

    <div class="panel">
      <div class="row">
        <input v-model="name" maxlength="100" :placeholder="$t('myApiKeys.key_name_e_g_front_desk_scanner')" class="name-in" />
        <input v-model="skills" :placeholder="$t('myApiKeys.available_skill_codes_comma_separated_leave_blank_all')" class="skills-in" />
        <button class="primary" :disabled="!grants.allow_create || !groups.length" @click="createKey">{{ $t('myApiKeys.create_key') }}</button>
      </div>

      <p v-if="!grants.allow_create" class="dim">{{ $t('myApiKeys.the_administrator_has_disabled_the_creation_and_use_of') }}</p>
      <p v-if="grants.allowed_skill_codes" class="dim">{{ $t('myApiKeys.available_skills') }}{{ grants.allowed_skill_codes.join('、') || $t('myApiKeys.none') }}{{ $t('myApiKeys.leave_blank_to_use_this_scope') }}</p>
      <div class="row"><label v-for="g in groupOptions" :key="g.value" :title="grants.groups.includes(g.value) ? '' : $t('myApiKeys.not_authorized_by_administrator')">
        <input type="checkbox" v-model="groups" :value="g.value" :disabled="!grants.groups.includes(g.value)" /> {{ g.label }}
      </label></div>
      <!-- show-once panel -->
      <div v-if="freshKey" class="fresh-key">
        <p>{{ $t('myApiKeys.the_full_key_is_shown_only_once_copy_and') }}</p>
        <div class="key-line">
          <code>{{ freshKey }}</code>
          <button class="primary" @click="copyKey">{{ copied ? $t('myApiKeys.copied') : $t('myApiKeys.copy') }}</button>
        </div>
      </div>

      <table v-if="keys.length">
        <thead><tr><th>{{ $t('myApiKeys.name') }}</th><th>{{ $t('myApiKeys.permissions') }}</th><th>{{ $t('myApiKeys.skill_scope') }}</th><th>{{ $t('myApiKeys.last_used') }}</th><th>{{ $t('myApiKeys.created_at') }}</th><th></th></tr></thead>
        <tbody>
          <tr v-for="k in keys" :key="k.id">
            <td>{{ k.name }}</td>
            <td class="dim">{{ k.scopes.join("、") || "—" }}</td>
            <td class="dim">{{ k.allowed_skill_codes ? k.allowed_skill_codes.join("、") : $t('myApiKeys.all') }}</td>
            <td class="dim">{{ k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "—" }}</td>
            <td class="dim">{{ k.created_at ? new Date(k.created_at).toLocaleString() : "—" }}</td>
            <td><button class="danger" @click="revoke(k)">{{ $t('myApiKeys.revoke') }}</button></td>
          </tr>
        </tbody>
      </table>
      <p v-else class="dim empty">{{ $t('myApiKeys.no_keys_yet_after_creating_one_enter_it_in') }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
const { t } = useI18n();

import { computed, onMounted, ref } from "vue";
import { api, type ApiGrants } from "../api";
import { toast } from "../toast";
import { copyText } from "../clipboard";

type MyKeyRow = {
  id: string; name: string; key_type: string; key_prefix: string;
  scopes: string[]; allowed_skill_codes: string[] | null;
  created_at: string | null; last_used_at: string | null;
};

const grants = ref<ApiGrants>({ allow_create: false, groups: [], allowed_skill_codes: null });
const groups = ref<string[]>(['process']);
const groupOptions = computed(() => [{ value: 'process', label: t('myApiKeys.submit_query') }, { value: 'detect', label: t('myApiKeys.seal_signature_detection') }, { value: 'locate', label: t('myApiKeys.layout_location') }]);
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
    toast.ok(t('myApiKeys.key_created_the_full_key_is_shown_only_once'));
    await load();
  } catch (e) { toast.error(e); }
}
async function copyKey() {
  copied.value = false;
  try {
    await copyText(freshKey.value);
    copied.value = true;
  } catch { toast.error(t('myApiKeys.copy_failed_please_select_the_text_manually')); }
}
async function revoke(k: MyKeyRow) {
  if (!confirm(t('myApiKeys.revoke_confirm', { name: k.name }))) return;
  try { await api.revokeMyKey(k.id); toast.ok(t('myApiKeys.revoked')); await load(); }
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
