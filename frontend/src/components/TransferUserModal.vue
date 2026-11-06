<template>
  <AppModal @close="close">
    <h4>{{ t('settings.transfer_account', { email: user.email, tenant: user.tenant_id }) }}</h4>
    <p>{{ t('settings.transfer_effects') }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="loading">{{ t('apiCallHistory.loading') }}</p>
    <p v-else-if="!error && !tenants.length">{{ t('settings.no_transfer_tenants') }}</p>
    <label v-if="tenants.length" class="lbl" for="transfer-tenant">{{ t('settings.transfer_destination') }}</label>
    <select v-if="tenants.length" id="transfer-tenant" v-model="selected" :disabled="busy || showRegister">
      <option value="" disabled>{{ t('settings.select_tenant') }}</option>
      <option v-for="tenant in tenants" :key="tenant.id" :value="tenant.id">
        {{ tenant.name && tenant.name !== tenant.id ? tenant.name + ' (' + tenant.id + ')' : tenant.id }}
      </option>
    </select>
    <button v-if="!showRegister && !loading" class="register-link" :disabled="busy" @click="showRegister = true">
      {{ t('settings.register_tenant') }}
    </button>
    <form v-if="showRegister" class="register-form" @submit.prevent="register">
      <label>{{ t('settings.new_tenant_id') }}
        <input v-model.trim="newId" required maxlength="64" :disabled="busy" autocapitalize="none"
               spellcheck="false" pattern="[A-Za-z0-9][A-Za-z0-9._\-]*" :title="t('settings.tenant_id_format')" />
      </label>
      <small>{{ t('settings.tenant_id_format') }}</small>
      <label>{{ t('settings.new_tenant_name') }}
        <input v-model.trim="newName" required maxlength="200" :disabled="busy" />
      </label>
      <div class="modal-actions">
        <button type="button" :disabled="busy" @click="showRegister = false">{{ t('settings.cancel') }}</button>
        <button type="submit" :disabled="busy || !newId || !newName">{{ t('settings.register_tenant') }}</button>
      </div>
    </form>
    <div v-else class="modal-actions">
      <button @click="close" :disabled="busy">{{ t('settings.cancel') }}</button>
      <button class="primary" :disabled="!selected || busy" @click="transfer">
        {{ t(saving ? 'settings.transferring' : 'settings.transfer_confirm') }}
      </button>
    </div>
  </AppModal>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { api, type TransferTenant, type UserRow } from "../api";
import { toast } from "../toast";
import AppModal from "./AppModal.vue";

const props = defineProps<{ user: UserRow }>();
const emit = defineEmits<{ close: []; moved: [userId: string] }>();
const { t } = useI18n();
const tenants = ref<TransferTenant[]>([]);
const selected = ref("");
const loading = ref(true);
const saving = ref(false);
const error = ref("");
const showRegister = ref(false);
const newId = ref("");
const newName = ref("");
const registering = ref(false);
const busy = computed(() => saving.value || registering.value);

onMounted(async () => {
  try { tenants.value = await api.transferTenants(); }
  catch (e) { error.value = e instanceof Error ? e.message : String(e); }
  finally { loading.value = false; }
});

function close() { if (!busy.value) emit("close"); }

async function register() {
  if (busy.value || !newId.value || !newName.value) return;
  registering.value = true;
  error.value = "";
  try {
    const tenant = await api.registerTransferTenant(newId.value, newName.value);
    tenants.value.push(tenant);
    selected.value = tenant.id;
    newId.value = "";
    newName.value = "";
    showRegister.value = false;
  } catch (e) { error.value = e instanceof Error ? e.message : String(e); }
  finally { registering.value = false; }
}

async function transfer() {
  if (!selected.value || busy.value || showRegister.value) return;
  saving.value = true;
  error.value = "";
  try {
    await api.transferUser(props.user.id, selected.value);
    toast.ok(t("settings.transfer_complete", { email: props.user.email, tenant: selected.value }));
    emit("moved", props.user.id);
  } catch (e) { error.value = e instanceof Error ? e.message : String(e); }
  finally { saving.value = false; }
}
</script>

<style scoped>
.register-link { display: block; margin-top: 12px; }
.register-form { margin-top: 14px; border-top: 1px solid var(--border); padding-top: 12px; }
.register-form label { display: grid; gap: 4px; margin: 6px 0; }
.register-form small { color: var(--text-dim); }
</style>
