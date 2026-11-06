<template>
  <main class="login-page">
    <aside class="product">
      <div class="wordmark">
        <img src="/favicon.svg" alt="" width="38" height="38" />
        <span>DOC·IDP</span>
      </div>
      <div class="product-body">
        <div class="product-intro">
          <h2>{{ $t('login.product_first') }}<br />{{ $t('login.product_second') }}</h2>
          <p>{{ $t('login.product_description') }}</p>
        </div>
        <InvoiceScan />
      </div>
    </aside>

    <section class="entry" aria-labelledby="login-heading">
      <div class="language-switch" role="group" :aria-label="$t('settings.language')">
        <svg aria-hidden="true" viewBox="0 0 24 24" class="globe">
          <circle cx="12" cy="12" r="9" /><ellipse cx="12" cy="12" rx="4" ry="9" /><path d="M3 12h18" />
        </svg>
        <button lang="zh" type="button" :aria-pressed="$i18n.locale === 'zh'" @click="setLanguage('zh')">中文</button>
        <span aria-hidden="true">/</span>
        <button lang="en" type="button" :aria-pressed="$i18n.locale === 'en'" @click="setLanguage('en')">English</button>
      </div>
      <div class="entry-body">
        <header>
          <h1 id="login-heading">{{ $t('login.log_in') }}</h1>
          <p>{{ $t('login.login_help') }}</p>
        </header>
        <form @submit.prevent="submit">
          <div class="field">
            <label for="login-email">{{ $t('login.email') }}</label>
            <input id="login-email" v-model="email" type="email" required autocomplete="username"
              autocapitalize="none" spellcheck="false" placeholder="name@example.com" autofocus />
          </div>
          <div class="field">
            <div class="field-heading">
              <label for="login-password">{{ $t('login.password') }}</label>
              <router-link class="forgot" to="/forgot">{{ $t('login.forgot_password') }}</router-link>
            </div>
            <div class="password-box">
              <input id="login-password" v-model="password" :type="showPassword ? 'text' : 'password'"
                required autocomplete="current-password" :placeholder="$t('login.password_hint')" />
              <button type="button" class="visibility" @click="showPassword = !showPassword"
                :aria-label="$t(showPassword ? 'login.hide_password' : 'login.show_password')">
                <svg aria-hidden="true" viewBox="0 0 24 24">
                  <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z" />
                  <circle cx="12" cy="12" r="3" /><path v-if="showPassword" d="m3 3 18 18" />
                </svg>
              </button>
            </div>
          </div>
          <div class="field">
            <label for="login-tenant">{{ $t('login.tenant_id_optional') }}</label>
            <input id="login-tenant" v-model.trim="tenantId" type="text" autocomplete="off"
              autocapitalize="none" spellcheck="false" :placeholder="$t('login.tenant_hint')" />
          </div>
          <p v-if="displayError" class="error" role="alert">{{ displayError }}</p>
          <button class="submit" type="submit" :disabled="busy">
            <span>{{ busy ? $t('login.signing_in') : $t('login.sign_in') }}</span>
            <svg v-if="!busy" aria-hidden="true" viewBox="0 0 24 24"><path d="M4 12h15m-6-6 6 6-6 6" /></svg>
          </button>
          <a v-if="ssoEnabled" class="sso" href="/api/v1/auth/oidc/login">
            {{ $t('login.sign_in_with_enterprise_account_sso') }}
          </a>
        </form>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import InvoiceScan from "./InvoiceScan.vue";
import { api, LocalizedApiError } from "../../api";
import { setSession } from "../../session";
import { setLanguage, translateMessage, type DisplayMessage } from "../../i18n";

const router = useRouter();
const route = useRoute();
const email = ref("");
const password = ref("");
const tenantId = ref("");
const error = ref<DisplayMessage>("");
const displayError = computed(() => translateMessage(error.value));
const busy = ref(false);
const showPassword = ref(false);
const ssoEnabled = ref(false);

const SSO_ERRORS: Record<string, string> = {
  sso_state: "login.sso_session_expired",
  sso_exchange: "login.sso_exchange_failed",
  sso_denied: "login.sso_account_unavailable",
};
onMounted(async () => {
  const err = String(route.query.error || "");
  if (err) error.value = SSO_ERRORS[err] ? { key: SSO_ERRORS[err] } : err;
  try { ssoEnabled.value = (await api.oidcEnabled()).enabled; } catch { /* probe only */ }
});

async function submit() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const res = await api.login(email.value, password.value, tenantId.value || undefined);
    setSession(res);
    // admin-created accounts must set their own password before anything else
    router.push(res.must_change_password ? "/change-password" : "/queue");
  } catch (e) {
    if (e instanceof LocalizedApiError) error.value = e.display;
    else error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.login-page {
  --ink: #172330;
  --muted: #586570;
  --paper: #faf9f6;
  --amber: #f0b429;
  min-height: 100vh;
  min-height: 100dvh;
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr);
  padding: 14px;
  background: var(--paper);
  color: var(--ink);
  font-family: "Helvetica Neue", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
}
.product {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: clamp(28px, 3.6vw, 64px);
  background: var(--ink);
  color: #faf7ef;
  border-radius: 12px;
}
.wordmark { display: inline-flex; align-items: center; gap: 10px; font-size: 23px; font-weight: 720; letter-spacing: -.04em; }
.wordmark img { display: block; }
.product-body { width: 100%; max-width: 620px; margin: auto; padding-top: clamp(32px, 6vh, 80px); }
.product-intro h2 {
  margin: 0;
  font-size: clamp(44px, min(4.7vw, 7.3vh), 78px);
  line-height: 1.14;
  font-weight: 650;
  letter-spacing: -.04em;
  text-wrap: balance;
}
.product-intro h2:lang(en) { font-size: clamp(35px, 3.95vw, 65px); line-height: 1.02; }
.product-intro p { margin: 18px 0 0; color: #c3cbd1; font-size: 15px; max-width: 40ch; }
.entry { position: relative; min-width: 0; display: grid; place-items: center; padding: 92px clamp(32px, 5vw, 88px); }
.language-switch {
  position: absolute; top: 24px; right: clamp(16px, 2.5vw, 44px);
  display: flex; align-items: center; gap: 4px; font-size: 12px; color: #7b858a;
}
.login-page svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.language-switch .globe { width: 16px; height: 16px; color: var(--muted); margin-right: 3px; }
.language-switch button {
  border: 0; padding: 8px 4px; background: transparent; color: var(--muted);
  font: inherit; border-radius: 3px;
}
.language-switch button[aria-pressed="true"] { color: var(--ink); font-weight: 700; }
.language-switch button:hover, .forgot:hover { text-decoration: underline; text-underline-offset: 3px; }
.entry-body { width: 100%; max-width: 386px; }
.entry-body h1 { font-size: clamp(32px, 3vw, 44px); line-height: 1.12; letter-spacing: -.045em; margin: 0; font-weight: 680; text-wrap: balance; }
.entry-body header p { margin: 12px 0 0; font-size: 15px; color: var(--muted); }
form { margin-top: 34px; display: grid; gap: 19px; }
.field { display: grid; gap: 7px; }
.field label { font-size: 14px; font-weight: 600; }
.field-heading { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.forgot { font-size: 13px; color: #4a5d6b; }
.login-page .field input {
  width: 100%; min-width: 0; height: 48px; border-radius: 6px; border: 1px solid #adb4b8;
  padding: 0 14px; font: inherit; font-size: 15px; background: #fff; color: var(--ink);
}
.field input::placeholder { color: #69757e; opacity: 1; }
.login-page .field input:focus { border-color: #86600e; outline: 2px solid #ad790d; outline-offset: 2px; }
.password-box { position: relative; }
.login-page .password-box input { padding-right: 48px; }
.visibility {
  position: absolute; inset: 2px 2px 2px auto; width: 44px;
  display: grid; place-items: center; color: var(--muted); padding: 0; border: 0; background: transparent;
}
.visibility:hover { color: var(--ink); background: #f5f3ef; }
.submit, .sso {
  display: flex; gap: 10px; align-items: center; justify-content: center;
  min-height: 48px; border-radius: 6px; font: inherit; font-size: 15px; font-weight: 650;
}
.submit { margin-top: 5px; background: var(--amber); color: var(--ink); border: 0; }
.submit:not(:disabled):hover { background: #e8a921; }
.submit:not(:disabled):active { transform: translateY(1px); }
.sso { color: var(--ink); border: 1px solid #adb4b8; }
.sso:hover { background: #efede7; border-color: #63707b; }
.error {
  color: #8e2a23; background: #faece8; border-left: 3px solid #b24437;
  padding: 9px 12px; margin: 0; font-size: 14px; overflow-wrap: anywhere;
}
.login-page :is(a, button):focus-visible { outline: 2px solid #8a630a; outline-offset: 3px; }

@media (max-width: 760px) {
  .login-page { display: block; padding: 0; }
  .product { background: transparent; color: var(--ink); border-radius: 0; padding: 23px 24px; border-bottom: 1px solid #deded6; }
  .product-body { display: none; }
  .wordmark { font-size: 21px; width: fit-content; gap: 8px; }
  .wordmark img { width: 32px; height: 32px; }
  .entry { position: static; padding: clamp(44px, 8vh, 82px) 24px 40px; }
  .language-switch { right: 20px; top: 23px; }
  .login-page .field input { font-size: 16px; }
}
@media (prefers-reduced-motion: reduce) {
  .login-page button { transition: none; }
  .submit:not(:disabled):active { transform: none; }
}
</style>
