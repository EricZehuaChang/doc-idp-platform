<template>
  <AuthCard title="SSO 登录" :error="error">
    <p class="dim">{{ error ? "" : "正在完成单点登录…" }}</p>
  </AuthCard>
</template>

<script setup lang="ts">
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
  if (!token) { error.value = "缺少登录令牌"; return; }
  try {
    const r = await fetch("/api/v1/auth/me", { headers: { Authorization: `Bearer ${token}` } });
    if (!r.ok) throw new Error("令牌无效");
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
