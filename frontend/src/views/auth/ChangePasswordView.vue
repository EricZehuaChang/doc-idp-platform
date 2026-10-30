<template>
  <main class="wrap">
    <h2>{{ $t('changePassword.change_password') }}</h2>
    <p v-if="session.mustChangePassword" class="force-tip">
      {{ $t('changePassword.an_administrator_has_set_an_initial_password_for_you') }}
    </p>
    <form @submit.prevent="submit">
      <label>{{ $t('changePassword.current_password') }}
        <input v-model="oldPw" type="password" required autocomplete="current-password" />
      </label>
      <label>{{ $t('changePassword.new_password_at_least_8_characters') }}
        <input v-model="newPw" type="password" minlength="8" required
               autocomplete="new-password" />
      </label>
      <label>{{ $t('changePassword.confirm_new_password') }}
        <input v-model="confirm" type="password" minlength="8" required
               autocomplete="new-password" />
      </label>
      <button class="primary" type="submit" :disabled="busy">{{ $t('changePassword.save') }}</button>
      <span v-if="error" class="err">{{ error }}</span>
      <span v-if="notice" class="ok">{{ notice }}</span>
    </form>
  </main>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
const { t } = useI18n();

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
    error.value = t('changePassword.the_two_passwords_do_not_match');
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await api.changePassword(oldPw.value, newPw.value);
    session.mustChangePassword = false;
    notice.value = t('changePassword.password_updated');
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
