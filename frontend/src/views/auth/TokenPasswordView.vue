<template>
  <!-- shared by /reset (password reset) and /activate (invite acceptance):
       both are "token in the URL + choose a new password" -->
  <AuthCard :title="mode === 'activate' ? $t('tokenPassword.activate_account') : $t('tokenPassword.set_new_password')"
            :error="error" :notice="notice">
    <form v-if="!done" @submit.prevent="submit">
      <label>{{ $t('tokenPassword.new_password_at_least_8_characters') }}
        <input v-model="password" type="password" minlength="8" required autofocus
               autocomplete="new-password" />
      </label>
      <label>{{ $t('tokenPassword.confirm_new_password') }}
        <input v-model="confirm" type="password" minlength="8" required
               autocomplete="new-password" />
      </label>
      <button class="primary" type="submit" :disabled="busy">
        {{ mode === "activate" ? $t('tokenPassword.activate_and_set_password') : $t('tokenPassword.reset_password') }}</button>
    </form>
    <router-link v-else class="primary-link" to="/login">{{ $t('tokenPassword.go_to_login') }}</router-link>
  </AuthCard>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
const { t } = useI18n();

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
    error.value = t('tokenPassword.the_two_passwords_do_not_match');
    return;
  }
  if (!token.value) {
    error.value = t('tokenPassword.the_link_is_missing_a_token_please_open_the');
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await (props.mode === "activate"
      ? api.activate(token.value, password.value)
      : api.reset(token.value, password.value));
    notice.value = t('tokenPassword.password_set_please_log_in_with_your_new_password');
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
