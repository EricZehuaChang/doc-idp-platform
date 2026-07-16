<template>
  <AuthCard title="找回密码" :error="error" :notice="notice">
    <form @submit.prevent="submit">
      <label>账号邮箱
        <input v-model="email" type="email" required autofocus />
      </label>
      <button class="primary" type="submit" :disabled="busy">发送重置邮件</button>
    </form>
    <router-link class="aux" to="/login">返回登录</router-link>
  </AuthCard>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { api } from "../../api";
import AuthCard from "./AuthCard.vue";

const email = ref("");
const error = ref("");
const notice = ref("");
const busy = ref(false);

async function submit() {
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await api.forgot(email.value);
    notice.value = "若该邮箱存在账号，重置邮件已发送，请查收（含垃圾箱）。";
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
