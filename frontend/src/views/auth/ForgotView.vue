<template>
  <AuthCard :title="$t('forgot.reset_password')" :error="error" :notice="notice">
    <form @submit.prevent="submit">
      <label>{{ $t('forgot.account_email') }}
        <input v-model="email" type="email" required autofocus />
      </label>
      <button class="primary" type="submit" :disabled="busy">{{ $t('forgot.send_reset_email') }}</button>
    </form>
    <router-link class="aux" to="/login">{{ $t('forgot.back_to_login') }}</router-link>
  </AuthCard>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
const { t } = useI18n();

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
    notice.value = t('forgot.if_an_account_exists_for_this_email_a_reset');
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
