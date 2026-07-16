<template>
  <AuthCard title="登录" :error="error">
    <form @submit.prevent="submit">
      <label>邮箱
        <input v-model="email" type="email" required autocomplete="username" autofocus />
      </label>
      <label>密码
        <input v-model="password" type="password" required autocomplete="current-password" />
      </label>
      <button class="primary" type="submit" :disabled="busy">
        {{ busy ? "登录中…" : "登 录" }}</button>
    </form>
    <router-link class="aux" to="/forgot">忘记密码？</router-link>
    <a v-if="ssoEnabled" class="sso" href="/api/v1/auth/oidc/login">🔑 使用企业账号登录（SSO）</a>
  </AuthCard>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../../api";
import { setSession } from "../../session";
import AuthCard from "./AuthCard.vue";

const router = useRouter();
const route = useRoute();
const email = ref("");
const password = ref("");
const error = ref("");
const busy = ref(false);
const ssoEnabled = ref(false);

const SSO_ERRORS: Record<string, string> = {
  sso_state: "SSO 会话已过期，请重试",
  sso_exchange: "SSO 令牌交换失败，请联系管理员检查 IdP 配置",
  sso_denied: "SSO 账号不可用（可能已被停用）",
};
onMounted(async () => {
  const err = String(route.query.error || "");
  if (err) error.value = SSO_ERRORS[err] ?? err;
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
