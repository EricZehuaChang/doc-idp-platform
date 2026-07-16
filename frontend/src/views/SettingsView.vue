<template>
  <main class="wrap">
    <div class="tabs">
      <button :class="{ primary: tab === 'email' }" @click="tab = 'email'">邮件（SMTP）</button>
      <button :class="{ primary: tab === 'users' }" @click="tab = 'users'">用户</button>
      <button :class="{ primary: tab === 'byok' }" @click="tab = 'byok'">模型密钥（BYOK）</button>
      <button :class="{ primary: tab === 'sso' }" @click="tab = 'sso'">单点登录（SSO）</button>
    </div>

    <!-- —— SMTP —— -->
    <section v-if="tab === 'email'" class="panel">
      <p class="dim">平台发信配置：邀请激活、找回密码都靠它。未配置时自动降级为「管理员直接建号」模式。</p>
      <div class="grid2">
        <label>SMTP 主机<input v-model="smtp.host" placeholder="smtp.example.com" /></label>
        <label>端口<input v-model.number="smtp.port" type="number" /></label>
        <label>加密方式
          <select v-model="smtp.security">
            <option value="ssl">SSL</option><option value="starttls">STARTTLS</option>
            <option value="none">无</option>
          </select></label>
        <label>用户名<input v-model="smtp.username" /></label>
        <label>密码 <span class="dim">{{ smtpInfo?.has_password ? "（已保存，留空不改）" : "" }}</span>
          <input v-model="smtp.password" type="password" autocomplete="new-password" /></label>
        <label>发件人地址<input v-model="smtp.from_addr" placeholder="noreply@example.com" /></label>
        <label>发件人显示名<input v-model="smtp.from_name" /></label>
        <label>回复地址（可选）<input v-model="smtp.reply_to" /></label>
      </div>
      <div class="row">
        <button class="primary" @click="saveSmtp">保存配置</button>
        <input v-model="testTo" placeholder="测试收件邮箱" class="test-to" />
        <button @click="testSmtp" :disabled="testing">{{ testing ? "发送中…" : "发送测试邮件" }}</button>
      </div>
    </section>

    <!-- —— users —— -->
    <section v-if="tab === 'users'" class="panel">
      <div class="row">
        <input v-model="invEmail" placeholder="邮箱" class="test-to" />
        <select v-model="invRole">
          <option value="viewer">viewer（只读）</option>
          <option value="operator">operator（审单）</option>
          <option value="admin">admin（管理）</option>
        </select>
        <button class="primary" @click="invite">邮件邀请</button>
        <span class="dim">或</span>
        <input v-model="invPassword" type="password" placeholder="初始密码（直接建号）" class="test-to" />
        <button @click="createDirect">直接建号</button>
      </div>
      <table v-if="users.length">
        <thead><tr><th>邮箱</th><th>角色</th><th>状态</th><th>登录方式</th><th></th></tr></thead>
        <tbody>
          <tr v-for="u in users" :key="u.id">
            <td>{{ u.email }}</td>
            <td>
              <select :value="u.role" @change="setRole(u, ($event.target as HTMLSelectElement).value)">
                <option value="viewer">viewer</option><option value="operator">operator</option>
                <option value="admin">admin</option>
              </select>
            </td>
            <td>
              <span v-if="u.pending" class="state pend">待激活</span>
              <span v-else-if="u.active" class="state ok">正常</span>
              <span v-else class="state off">已停用</span>
            </td>
            <td class="dim">{{ u.auth_provider }}</td>
            <td>
              <button v-if="u.active" class="danger" @click="toggle(u, false)">停用</button>
              <button v-else @click="toggle(u, true)">恢复</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- —— BYOK —— -->
    <section v-if="tab === 'byok'" class="panel">
      <p class="dim">
        自带模型密钥（BYOK）：本租户可为任一模型通道填入自己的 API Key，密钥加密存储、
        永不回显；未填则使用平台密钥。</p>
      <table v-if="providers.length">
        <thead><tr><th>通道</th><th>模型</th><th>平台密钥</th><th>租户密钥</th><th></th></tr></thead>
        <tbody>
          <tr v-for="p in providers" :key="p.name">
            <td class="code">{{ p.name }} <span v-if="p.active" class="state ok">默认</span></td>
            <td class="dim">{{ p.model }}</td>
            <td>{{ p.platform_key ? "✓" : "—" }}</td>
            <td>
              <span v-if="p.byok_set" class="state ok">已配置</span>
              <input v-else v-model="keyDrafts[p.name]" type="password"
                     placeholder="粘贴 API Key" class="key-in" />
            </td>
            <td>
              <button v-if="!p.byok_set" class="primary"
                      :disabled="!keyDrafts[p.name]" @click="setKey(p.name)">保存</button>
              <button v-else class="danger" @click="removeKey(p.name)">移除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <Skeleton v-else :rows="4" />
    </section>
    <!-- —— OIDC SSO —— -->
    <section v-if="tab === 'sso'" class="panel">
      <p class="dim">
        OIDC 单点登录：绑定企业 IdP（Keycloak / Azure AD / Okta / Authing）。
        用户首次 SSO 登录自动开通账号（JIT），身份以 IdP subject 硬关联。</p>
      <label class="chk-row">
        <input type="checkbox" v-model="oidc.enabled" /> 启用 SSO 登录入口
      </label>
      <div class="grid2">
        <label>Issuer 地址
          <input v-model="oidc.issuer" placeholder="https://idp.example.com/realms/main" /></label>
        <label>Client ID<input v-model="oidc.client_id" /></label>
        <label>Client Secret <span class="dim">{{ oidcInfo?.has_secret ? "（已保存，留空不改）" : "" }}</span>
          <input v-model="oidc.client_secret" type="password" autocomplete="new-password" /></label>
        <label>回调地址（配到 IdP）
          <input :value="callbackUrl" readonly /></label>
      </div>
      <div class="row"><button class="primary" @click="saveOidc">保存 SSO 配置</button></div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, watch } from "vue";
import { api, type SmtpInfo } from "../api";
import Skeleton from "../components/Skeleton.vue";
import { toast } from "../toast";

const tab = ref<"email" | "users" | "byok" | "sso">("email");

// —— SMTP ——
const smtpInfo = ref<SmtpInfo | null>(null);
const smtp = reactive({ host: "", port: 587, security: "starttls", username: "",
                        password: "", from_addr: "", from_name: "", reply_to: "" });
const testTo = ref("");
const testing = ref(false);

async function loadSmtp() {
  try {
    const d = await api.getSmtp();
    smtpInfo.value = d;
    if (d.configured) Object.assign(smtp, { ...d, password: "" });
  } catch (e) { toast.error(e); }
}
async function saveSmtp() {
  try {
    smtpInfo.value = await api.putSmtp({ ...smtp, password: smtp.password || undefined });
    smtp.password = "";
    toast.ok("SMTP 配置已保存");
  } catch (e) { toast.error(e); }
}
async function testSmtp() {
  if (!testTo.value) { toast.error("填写测试收件邮箱"); return; }
  testing.value = true;
  try { await api.testSmtp(testTo.value); toast.ok("测试邮件已发送，请查收"); }
  catch (e) { toast.error(e); }
  finally { testing.value = false; }
}

// —— users ——
interface UserRow { id: string; email: string; role: string; active: boolean;
                    pending: boolean; auth_provider: string }
const users = ref<UserRow[]>([]);
const invEmail = ref("");
const invRole = ref("operator");
const invPassword = ref("");

async function loadUsers() {
  try { users.value = await api.listUsers(); } catch (e) { toast.error(e); }
}
async function invite() {
  if (!invEmail.value) return;
  try {
    await api.invite(invEmail.value, invRole.value);
    toast.ok("邀请邮件已发送");
    invEmail.value = "";
    await loadUsers();
  } catch (e) { toast.error(e); }
}
async function createDirect() {
  if (!invEmail.value || !invPassword.value) { toast.error("直接建号需填邮箱和初始密码"); return; }
  try {
    await api.createUser(invEmail.value, invPassword.value, invRole.value);
    toast.ok("账号已创建（用户首次登录需改密）");
    invEmail.value = "";
    invPassword.value = "";
    await loadUsers();
  } catch (e) { toast.error(e); }
}
async function toggle(u: UserRow, active: boolean) {
  try { await api.patchUser(u.id, { active }); await loadUsers(); }
  catch (e) { toast.error(e); }
}
async function setRole(u: UserRow, role: string) {
  try { await api.patchUser(u.id, { role }); toast.ok(`${u.email} → ${role}`); await loadUsers(); }
  catch (e) { toast.error(e); }
}

// —— BYOK ——
interface ProviderRow { name: string; model: string; active: boolean;
                        platform_key: boolean; byok_set: boolean }
const providers = ref<ProviderRow[]>([]);
const keyDrafts = reactive<Record<string, string>>({});

async function loadProviders() {
  try { providers.value = (await api.listProviders()).providers; }
  catch (e) { toast.error(e); }
}
async function setKey(name: string) {
  try {
    await api.putByok(name, keyDrafts[name]);
    keyDrafts[name] = "";
    toast.ok(`${name} 租户密钥已保存（加密存储）`);
    await loadProviders();
  } catch (e) { toast.error(e); }
}
async function removeKey(name: string) {
  try {
    await api.deleteByok(name);
    toast.ok(`${name} 租户密钥已移除，回退平台密钥`);
    await loadProviders();
  } catch (e) { toast.error(e); }
}

// —— OIDC ——
interface OidcInfo { enabled: boolean; issuer?: string; client_id?: string;
                     has_secret?: boolean }
const oidcInfo = ref<OidcInfo | null>(null);
const oidc = reactive({ enabled: false, issuer: "", client_id: "", client_secret: "" });
const callbackUrl = `${location.origin}/api/v1/auth/oidc/callback`;

async function loadOidc() {
  try {
    const d = await api.getOidc();
    oidcInfo.value = d;
    oidc.enabled = d.enabled;
    oidc.issuer = d.issuer ?? "";
    oidc.client_id = d.client_id ?? "";
  } catch (e) { toast.error(e); }
}
async function saveOidc() {
  if (!oidc.issuer || !oidc.client_id) { toast.error("Issuer 和 Client ID 必填"); return; }
  try {
    oidcInfo.value = await api.putOidc({ enabled: oidc.enabled, issuer: oidc.issuer,
                                         client_id: oidc.client_id,
                                         client_secret: oidc.client_secret || undefined }) as OidcInfo;
    oidc.client_secret = "";
    toast.ok("SSO 配置已保存");
  } catch (e) { toast.error(e); }
}

onMounted(() => { loadSmtp(); loadUsers(); loadProviders(); loadOidc(); });
watch(tab, (t) => {
  if (t === "users") loadUsers();
  if (t === "byok") loadProviders();
  if (t === "sso") loadOidc();
});
</script>

<style scoped>
.wrap { padding: 20px; max-width: 1000px; margin: 0 auto; }
.tabs { display: flex; gap: 8px; margin-bottom: 16px; }
.panel { background: var(--bg-panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px; display: flex; flex-direction: column; gap: 12px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 13px;
  color: var(--text-dim); }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.test-to { max-width: 220px; }
select { background: var(--bg-raised); color: var(--text); border: 1px solid var(--border);
  border-radius: 6px; padding: 5px 8px; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border);
  font-size: 13px; }
th { color: var(--text-dim); }
.code { font-family: Consolas, monospace; color: var(--blue); }
.key-in { max-width: 220px; }
.state { font-size: 12px; border-radius: 4px; padding: 1px 8px; }
.state.ok { color: var(--green); border: 1px solid var(--green); }
.state.off { color: var(--red); border: 1px solid var(--red); }
.state.pend { color: var(--accent); border: 1px solid var(--accent); }
.dim { color: var(--text-dim); }
.chk-row { flex-direction: row; align-items: center; gap: 8px; }
.chk-row input { width: auto; }
</style>
