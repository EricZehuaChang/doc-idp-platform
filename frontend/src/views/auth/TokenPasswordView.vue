<template>
  <!-- shared by /reset (password reset) and /activate (invite acceptance):
       both are "token in the URL + choose a new password" -->
  <AuthCard :title="mode === 'activate' ? '激活账号' : '设置新密码'"
            :error="error" :notice="notice">
    <form v-if="!done" @submit.prevent="submit">
      <label>新密码（至少 8 位）
        <input v-model="password" type="password" minlength="8" required autofocus
               autocomplete="new-password" />
      </label>
      <label>确认新密码
        <input v-model="confirm" type="password" minlength="8" required
               autocomplete="new-password" />
      </label>
      <button class="primary" type="submit" :disabled="busy">
        {{ mode === "activate" ? "激活并设置密码" : "重置密码" }}</button>
    </form>
    <router-link v-else class="primary-link" to="/login">前往登录 →</router-link>
  </AuthCard>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import { api } from "../../api";
import AuthCard from "./AuthCard.vue";

const props = defineProps<{ mode: "reset" | "activate" }>();
const route = useRoute();
const token = computed(() => String(route.query.token || ""));
const password = ref("");
const confirm = ref("");
const error = ref("");
const notice = ref("");
const busy = ref(false);
const done = ref(false);

async function submit() {
  if (password.value !== confirm.value) {
    error.value = "两次输入的密码不一致";
    return;
  }
  if (!token.value) {
    error.value = "链接缺少令牌，请从邮件里的完整链接进入";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await (props.mode === "activate"
      ? api.activate(token.value, password.value)
      : api.reset(token.value, password.value));
    notice.value = "密码已设置，请用新密码登录。";
    done.value = true;
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
.primary-link { font-weight: 600; }
</style>
