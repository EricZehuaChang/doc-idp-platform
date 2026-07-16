<template>
  <main class="wrap">
    <h2>修改密码</h2>
    <p v-if="session.mustChangePassword" class="force-tip">
      管理员为您设置了初始密码，请先修改为自己的密码再继续使用。
    </p>
    <form @submit.prevent="submit">
      <label>原密码
        <input v-model="oldPw" type="password" required autocomplete="current-password" />
      </label>
      <label>新密码（至少 8 位）
        <input v-model="newPw" type="password" minlength="8" required
               autocomplete="new-password" />
      </label>
      <label>确认新密码
        <input v-model="confirm" type="password" minlength="8" required
               autocomplete="new-password" />
      </label>
      <button class="primary" type="submit" :disabled="busy">保存</button>
      <span v-if="error" class="err">{{ error }}</span>
      <span v-if="notice" class="ok">{{ notice }}</span>
    </form>
  </main>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "../../api";
import { session } from "../../session";

const router = useRouter();
const oldPw = ref("");
const newPw = ref("");
const confirm = ref("");
const error = ref("");
const notice = ref("");
const busy = ref(false);

async function submit() {
  if (newPw.value !== confirm.value) {
    error.value = "两次输入的密码不一致";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await api.changePassword(oldPw.value, newPw.value);
    session.mustChangePassword = false;
    notice.value = "密码已更新";
    setTimeout(() => router.push("/queue"), 600);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.wrap { padding: 24px; max-width: 420px; }
form { display: flex; flex-direction: column; gap: 12px; margin-top: 12px; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--text-dim); }
.force-tip { color: var(--accent); font-size: 13px; }
.err { color: var(--red); font-size: 13px; }
.ok { color: var(--green); font-size: 13px; }
</style>
