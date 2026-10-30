<template>
  <AuthCard :title="$t('oidcCallback.sso_login')" :error="error">
    <p class="dim">{{ error ? "" : $t('oidcCallback.completing_single_sign_on') }}</p>
  </AuthCard>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
const { t } = useI18n();

// backend callback redirects here with ?token=<platform JWT>: store it, load
// the profile, and enter the app — one session model for all login channels.
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { session, setSession } from "../../session";
import AuthCard from "./AuthCard.vue";

const route = useRoute();
const router = useRouter();
const error = ref("");

onMounted(async () => {
  const token = String(route.query.token || "");
  if (!token) { error.value = t('oidcCallback.missing_login_token'); return; }
  try {
    const r = await fetch("/api/v1/auth/me", { headers: { Authorization: `Bearer ${token}` } });
    if (!r.ok) throw new Error(t('oidcCallback.invalid_token'));
    const me = await r.json();
    setSession({ access_token: token, email: me.email, role: me.role,
                 tenant_id: me.tenant_id });
    session.authRequired = true;
    router.replace("/queue");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
});
</script>

<style scoped>
.dim { color: var(--text-dim); }
</style>
