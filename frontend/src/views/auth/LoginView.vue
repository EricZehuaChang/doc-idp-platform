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
  </AuthCard>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "../../api";
import { setSession } from "../../session";
import AuthCard from "./AuthCard.vue";

const router = useRouter();
const email = ref("");
const password = ref("");
const error = ref("");
const busy = ref(false);

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
</style>
