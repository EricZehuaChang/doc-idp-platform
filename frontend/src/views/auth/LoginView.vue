<template>
  <AuthCard :title="$t('login.log_in')" :error="displayError">
    <form @submit.prevent="submit">
      <label>{{ $t('login.email') }}
        <input v-model="email" type="email" required autocomplete="username" autofocus />
      </label>
      <label>{{ $t('login.password') }}
        <input v-model="password" type="password" required autocomplete="current-password" />
      </label>
      <button class="primary" type="submit" :disabled="busy">
        {{ busy ? $t('login.signing_in') : $t('login.sign_in') }}</button>
    </form>
    <router-link class="aux" to="/forgot">{{ $t('login.forgot_password') }}</router-link>
    <a v-if="ssoEnabled" class="sso" href="/api/v1/auth/oidc/login">{{ $t('login.sign_in_with_enterprise_account_sso') }}</a>
  </AuthCard>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../../api";
import { setSession } from "../../session";
import AuthCard from "./AuthCard.vue";
import { translateMessage, type DisplayMessage } from "../../i18n";

const router = useRouter();
const route = useRoute();
const email = ref("");
const password = ref("");
const error = ref<DisplayMessage>("");
const displayError = computed(() => translateMessage(error.value));
const busy = ref(false);
const ssoEnabled = ref(false);

const SSO_ERRORS: Record<string, string> = {
  sso_state: "login.sso_session_expired",
  sso_exchange: "login.sso_exchange_failed",
  sso_denied: "login.sso_account_unavailable",
};
onMounted(async () => {
  const err = String(route.query.error || "");
  if (err) error.value = SSO_ERRORS[err] ? { key: SSO_ERRORS[err] } : err;
  try { ssoEnabled.value = (await api.oidcEnabled()).enabled; } catch { /* probe only */ }
});

async function submit() {
  busy.value = true;
  error.value = "";
  try {
    const res = await api.login(email.value, password.value);
    setSession(res);
    // admin-created accounts must set their own password before anything else
    router.push(res.must_change_password ? "/change-password" : "/queue");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
form { display: flex; flex-direction: column; gap: 12px; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--text-dim); }
.aux { font-size: 13px; align-self: flex-start; }
.sso { font-size: 13px; text-align: center; border: 1px solid var(--border);
  border-radius: 6px; padding: 8px; color: var(--text); }
.sso:hover { border-color: var(--accent); }
</style>
