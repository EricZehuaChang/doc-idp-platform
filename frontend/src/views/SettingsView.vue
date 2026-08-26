<template>
  <main class="page">
    <PageHeader title="平台设置" desc="发信、账号、计费、模型密钥与单点登录的管理入口（仅管理员可见）。" />
    <div class="tabs">
      <button :class="{ primary: tab === 'email' }" @click="tab = 'email'">邮件（SMTP）</button>
      <button :class="{ primary: tab === 'users' }" @click="tab = 'users'">用户</button>
      <button :class="{ primary: tab === 'billing' }" @click="tab = 'billing'">计费</button>
      <button :class="{ primary: tab === 'byok' }" @click="tab = 'byok'">模型通道与密钥</button>
      <button :class="{ primary: tab === 'sso' }" @click="tab = 'sso'">单点登录（SSO）</button>
      <button :class="{ primary: tab === 'apikeys' }" @click="tab = 'apikeys'">API 密钥</button>
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
            <td class="row-ops">
              <button v-if="u.pending" @click="resend(u)">重发邀请</button>
              <button v-if="u.active" class="danger" @click="toggle(u, false)">停用</button>
              <button v-else @click="toggle(u, true)">恢复</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- —— billing (§12) —— -->
    <section v-if="tab === 'billing'" class="panel">
      <template v-if="acct">
        <!-- balance strip -->
        <div class="bill-strip">
          <div class="bill-card main">
            <span class="bill-label">可用余额</span>
            <span class="bill-num">{{ acct.available.toFixed(2) }}</span>
            <span class="bill-unit">credits</span>
          </div>
          <div class="bill-card"><span class="bill-label">充值余额</span>
            <span class="bill-num sm">{{ acct.paid_balance.toFixed(2) }}</span></div>
          <div class="bill-card"><span class="bill-label">赠送余额</span>
            <span class="bill-num sm">{{ acct.gift_balance.toFixed(2) }}</span></div>
          <div class="bill-card"><span class="bill-label">处理中冻结</span>
            <span class="bill-num sm">{{ acct.frozen.toFixed(2) }}</span></div>
          <div class="bill-card tags">
            <span class="state" :class="acct.mode === 'live' ? 'ok' : 'pend'">
              {{ acct.mode === "live" ? "正式计费" : "影子计费（只计量不扣费）" }}</span>
            <span v-if="acct.plan" class="state ok">套餐 {{ acct.plan }}</span>
            <span v-if="acct.byok" class="state ok">BYOK 费率</span>
          </div>
        </div>

        <!-- mode + rate card -->
        <details class="bill-config">
          <summary>计费模式与费率（平台管理）</summary>
          <div class="row">
            <label class="inline">模式
              <select v-model="cfgDraft.mode">
                <option value="shadow">shadow（影子计费）</option>
                <option value="live">live（正式扣费）</option>
              </select></label>
            <label class="inline">抽取费率/页
              <input v-model.number="cfgDraft.extract" type="number" step="0.1" min="0" class="num-in" /></label>
            <label class="inline">审核费率/页
              <input v-model.number="cfgDraft.audit" type="number" step="0.1" min="0" class="num-in" /></label>
            <label class="inline">BYOK 抽取/页
              <input v-model.number="cfgDraft.extract_byok" type="number" step="0.1" min="0" class="num-in" /></label>
            <label class="inline">BYOK 审核/页
              <input v-model.number="cfgDraft.audit_byok" type="number" step="0.1" min="0" class="num-in" /></label>
            <button class="primary" @click="saveBillingConfig">保存</button>
          </div>
          <p class="dim">影子模式只记账不扣费（私有化 license 交付默认）；live 模式提交预冻结、
            完成按实际页数扣费、失败不收费、余额不足返回 402。</p>
        </details>

        <!-- money ops -->
        <div class="bill-ops">
          <div class="op-card">
            <h4>人工充值 <span class="dim">（对公转账确认后登记）</span></h4>
            <input v-model.number="topupAmount" type="number" min="0" placeholder="金额（credits）" />
            <input v-model="topupVoucher" placeholder="转账凭证号（必填）" />
            <input v-model="topupNote" placeholder="备注（可选）" />
            <button class="primary" :disabled="!topupAmount || !topupVoucher"
                    @click="doTopup">入账</button>
          </div>
          <div class="op-card">
            <h4>余额调整 <span class="dim">（正负均可，全程留痕）</span></h4>
            <input v-model.number="adjAmount" type="number" placeholder="金额（负数=扣减）" />
            <select v-model="adjBucket">
              <option value="paid">充值桶</option><option value="gift">赠送桶</option>
            </select>
            <input v-model="adjReason" placeholder="调整原因（必填）" />
            <button class="primary" :disabled="!adjAmount || !adjReason"
                    @click="doAdjust">调整</button>
          </div>
          <div class="op-card">
            <h4>营销赠送 <span class="dim">（超 {{ acct.gift_review_threshold }} 需双人复核）</span></h4>
            <input v-model.number="giftAmount" type="number" min="0" placeholder="金额（credits）" />
            <input v-model="giftCampaign" placeholder="活动标签（必填）" />
            <input v-model="giftReason" placeholder="理由（必填）" />
            <button class="primary" :disabled="!giftAmount || !giftCampaign || !giftReason"
                    @click="doGift">赠送</button>
          </div>
        </div>

        <!-- pending gift approvals -->
        <div v-if="pendingGifts.length" class="gift-pending">
          <h4>待复核赠送（需另一位管理员审批）</h4>
          <table>
            <thead><tr><th>金额</th><th>活动</th><th>理由</th><th>发起人</th><th>时间</th><th></th></tr></thead>
            <tbody>
              <tr v-for="g in pendingGifts" :key="g.id">
                <td>{{ g.amount }}</td><td>{{ g.campaign }}</td><td class="dim">{{ g.reason }}</td>
                <td class="dim">{{ g.requested_by }}</td>
                <td class="dim">{{ g.created_at ? new Date(g.created_at).toLocaleString() : "-" }}</td>
                <td class="row-ops">
                  <button class="primary" @click="decideGift(g, 'approve')">批准</button>
                  <button class="danger" @click="decideGift(g, 'reject')">驳回</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- ledger -->
        <div class="row ledger-bar">
          <h4>credit 流水</h4>
          <select v-model="ledgerKind" @change="loadLedger(0)">
            <option value="">全部类型</option>
            <option v-for="(label, k) in KIND_LABELS" :key="k" :value="k">{{ label }}</option>
          </select>
          <span class="dim">共 {{ ledgerTotal }} 条</span>
        </div>
        <table v-if="ledger.length">
          <thead><tr><th>时间</th><th>类型</th><th>桶</th><th>金额</th><th>余额快照</th><th>说明</th></tr></thead>
          <tbody>
            <tr v-for="l in ledger" :key="l.id">
              <td class="dim">{{ l.created_at ? new Date(l.created_at).toLocaleString() : "-" }}</td>
              <td>{{ KIND_LABELS[l.kind] ?? l.kind }}</td>
              <td class="dim">{{ l.bucket === "gift" ? "赠送" : "充值" }}</td>
              <td class="num">{{ l.amount.toFixed(2) }}</td>
              <td class="num dim">{{ l.balance_snapshot?.toFixed(2) ?? "-" }}</td>
              <td class="dim note-cell">{{ l.note }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="dim">暂无流水。</p>
        <div class="row" v-if="ledgerTotal > ledger.length || ledgerOffset > 0">
          <button :disabled="ledgerOffset === 0" @click="loadLedger(ledgerOffset - 20)">上一页</button>
          <button :disabled="ledgerOffset + 20 >= ledgerTotal"
                  @click="loadLedger(ledgerOffset + 20)">下一页</button>
        </div>
      </template>
      <Skeleton v-else :rows="5" />
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

      <!-- —— custom channels: register any OpenAI-compatible endpoint —— -->
      <h3 class="sub">自定义模型通道</h3>
      <p class="dim">
        接入平台内置清单之外的模型：填名称、接口地址和 API Key 即可，登记后会出现在
        技能编辑器的模型下拉里。密钥加密存储、永不回显。适合试新模型或对接自建网关。</p>
      <table v-if="customs.length">
        <thead><tr><th>名称</th><th>模型 ID</th><th>接口地址</th><th>图像输入</th>
                   <th>密钥</th><th></th></tr></thead>
        <tbody>
          <tr v-for="c in customs" :key="c.name">
            <td class="code">{{ c.name }}</td>
            <td class="dim">{{ c.model }}</td>
            <td class="dim url">{{ c.base_url }}</td>
            <td>{{ c.vision ? "✓ 支持" : "—" }}</td>
            <td><span class="state ok">已配置</span></td>
            <td class="row-ops">
              <button :disabled="testingName === c.name" @click="testCustom(c.name)">
                {{ testingName === c.name ? "调用中…" : "测试连接" }}</button>
              <button class="danger" @click="removeCustom(c.name)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="dim">还没有自定义通道。</p>

      <div class="new-ch">
        <label>名称（通道标识）
          <input v-model="draft.name" placeholder="如 qwen-vl-max" /></label>
        <label>模型 ID（留空＝同名称）
          <input v-model="draft.model" placeholder="厂商文档里的 model 值" /></label>
        <label class="wide">接口地址（OpenAI 兼容的 base_url）
          <input v-model="draft.base_url"
                 placeholder="如 https://dashscope.aliyuncs.com/compatible-mode/v1" /></label>
        <label class="wide">API Key
          <input v-model="draft.api_key" type="password" placeholder="粘贴 Key，加密存储不回显" /></label>
        <label class="chk">
          <input type="checkbox" v-model="draft.vision" />
          该模型支持图像输入（勾选后，技能编辑器里对它试跑会把原件页面图一起发过去）
        </label>
        <div class="new-act">
          <button class="primary" :disabled="savingCustom || !draft.name || !draft.base_url
                                             || !draft.api_key"
                  @click="saveCustom">{{ savingCustom ? "登记中…" : "＋ 登记通道" }}</button>
          <span class="dim">登记后建议先点「测试连接」——保存成功不等于能调通。</span>
        </div>
      </div>
      <pre v-if="testOut" class="test-out">{{ testOut }}</pre>
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

    <!-- —— API keys —— -->
    <section v-if="tab === 'apikeys'" class="panel">
      <p class="dim">
        集成方调用平台 API（提交文档/查结果/Webhook）所用的凭据。
        接口文档与示例见 技能编辑器 →「🔌 API 接入」。</p>
      <div class="row">
        <input v-model="keyName" placeholder="密钥名称，如：ERP 集成" class="test-to" />
        <button class="primary" @click="createKey">＋ 创建密钥</button>
      </div>

      <!-- show-once panel: the only time the full key is visible -->
      <div v-if="freshKey" class="fresh-key">
        <p>⚠️ 完整密钥仅显示这一次，请立即复制保存：</p>
        <div class="key-line">
          <code>{{ freshKey }}</code>
          <button class="primary" @click="copyKey">{{ copied ? "✓ 已复制" : "复制" }}</button>
        </div>
      </div>

      <table v-if="apiKeys.length">
        <thead><tr><th>名称</th><th>前缀</th><th>状态</th><th>额度模式</th>
          <th>独立额度</th><th>创建时间</th><th></th></tr></thead>
        <tbody>
          <tr v-for="k in apiKeys" :key="k.id">
            <td>{{ k.name }}</td>
            <td class="code">idp_ak_{{ k.prefix }}…</td>
            <td><span class="state" :class="k.active ? 'ok' : 'off'">
              {{ k.active ? "启用" : "已吊销" }}</span></td>
            <td>
              <select :value="k.quota_mode"
                      @change="setQuotaMode(k, ($event.target as HTMLSelectElement).value)">
                <option value="pool">共享池</option>
                <option value="allocated">独立划拨</option>
              </select>
            </td>
            <td>
              <template v-if="k.quota_mode === 'allocated' || k.allocated_balance > 0">
                <span class="num">{{ k.allocated_balance.toFixed(2) }}</span>
                <span v-if="k.allocated_frozen" class="dim">（冻结 {{ k.allocated_frozen.toFixed(2) }}）</span>
              </template>
              <span v-else class="dim">—</span>
            </td>
            <td class="dim">{{ k.created_at ? new Date(k.created_at).toLocaleString() : "-" }}</td>
            <td class="row-ops">
              <template v-if="k.quota_mode === 'allocated' || k.allocated_balance > 0">
                <input v-model.number="allocDrafts[k.id]" type="number"
                       placeholder="±金额" class="num-in" />
                <button @click="allocate(k)">划拨</button>
              </template>
              <button v-if="k.active" class="danger" @click="revokeKey(k)">吊销</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="dim">还没有密钥。创建一把给你的系统接入用。</p>
    </section>
  </main>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, watch } from "vue";
import { api, type BillingAccount, type CustomProvider, type GiftRequestRow,
         type LedgerRow, type SmtpInfo } from "../api";
import PageHeader from "../components/PageHeader.vue";
import Skeleton from "../components/Skeleton.vue";
import { toast } from "../toast";

const tab = ref<"email" | "users" | "billing" | "byok" | "sso" | "apikeys">("email");

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
async function resend(u: UserRow) {
  try {
    await api.invite(u.email, u.role);   // backend reissues the token (60s cooldown)
    toast.ok(`邀请已重发至 ${u.email}`);
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

// —— billing (§12) ——
const KIND_LABELS: Record<string, string> = {
  shadow_meter: "计量", freeze: "冻结", charge: "实扣", unfreeze: "解冻",
  topup: "充值", adjust: "调整", gift: "赠送", key_transfer: "Key 划拨",
};
const acct = ref<BillingAccount | null>(null);
const cfgDraft = reactive({ mode: "shadow", extract: 1, audit: 2,
                            extract_byok: 0.5, audit_byok: 1 });
const topupAmount = ref<number | null>(null);
const topupVoucher = ref("");
const topupNote = ref("");
const adjAmount = ref<number | null>(null);
const adjBucket = ref("paid");
const adjReason = ref("");
const giftAmount = ref<number | null>(null);
const giftCampaign = ref("");
const giftReason = ref("");
const pendingGifts = ref<GiftRequestRow[]>([]);
const ledger = ref<LedgerRow[]>([]);
const ledgerTotal = ref(0);
const ledgerOffset = ref(0);
const ledgerKind = ref("");

async function loadBilling() {
  try {
    acct.value = await api.billingAccount();
    Object.assign(cfgDraft, {
      mode: acct.value.mode,
      extract: acct.value.rates.extract ?? 1,
      audit: acct.value.rates.audit ?? 2,
      extract_byok: acct.value.rates_byok.extract ?? 0.5,
      audit_byok: acct.value.rates_byok.audit ?? 1,
    });
    pendingGifts.value = await api.billingGiftRequests("pending");
    await loadLedger(0);
  } catch (e) { toast.error(e); }
}
async function loadLedger(offset: number) {
  try {
    const d = await api.billingLedger(offset, 20, ledgerKind.value || undefined);
    ledger.value = d.items;
    ledgerTotal.value = d.total;
    ledgerOffset.value = offset;
  } catch (e) { toast.error(e); }
}
async function saveBillingConfig() {
  try {
    await api.billingConfig({
      mode: cfgDraft.mode,
      rates: { extract: cfgDraft.extract, audit: cfgDraft.audit },
      rates_byok: { extract: cfgDraft.extract_byok, audit: cfgDraft.audit_byok },
    });
    toast.ok(cfgDraft.mode === "live" ? "已切换为正式计费" : "已保存（影子计费）");
    await loadBilling();
  } catch (e) { toast.error(e); }
}
async function doTopup() {
  try {
    await api.billingTopup(topupAmount.value!, topupVoucher.value, topupNote.value);
    toast.ok(`已入账 ${topupAmount.value} credits`);
    topupAmount.value = null; topupVoucher.value = ""; topupNote.value = "";
    await loadBilling();
  } catch (e) { toast.error(e); }
}
async function doAdjust() {
  try {
    await api.billingAdjust(adjAmount.value!, adjBucket.value, adjReason.value);
    toast.ok("调整已入账");
    adjAmount.value = null; adjReason.value = "";
    await loadBilling();
  } catch (e) { toast.error(e); }
}
async function doGift() {
  try {
    const r = await api.billingGift(giftAmount.value!, giftCampaign.value, giftReason.value);
    toast.ok(r.status === "approved" ? "赠送已到账" : "已提交，等待另一位管理员复核");
    giftAmount.value = null; giftCampaign.value = ""; giftReason.value = "";
    await loadBilling();
  } catch (e) { toast.error(e); }
}
async function decideGift(g: GiftRequestRow, decision: "approve" | "reject") {
  try {
    await api.billingGiftDecide(g.id, decision);
    toast.ok(decision === "approve" ? "已批准，赠送到账" : "已驳回");
    await loadBilling();
  } catch (e) { toast.error(e); }
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

// —— custom channels ——
const customs = ref<CustomProvider[]>([]);
const draft = reactive({ name: "", model: "", base_url: "", api_key: "", vision: false });
const savingCustom = ref(false);
const testingName = ref("");
const testOut = ref("");

async function loadCustoms() {
  try { customs.value = (await api.listCustomProviders()).providers; }
  catch (e) { toast.error(e); }
}
async function saveCustom() {
  savingCustom.value = true;
  try {
    await api.putCustomProvider(draft.name.trim(), {
      base_url: draft.base_url.trim(), model: draft.model.trim(),
      api_key: draft.api_key, vision: draft.vision });
    toast.ok(`通道 ${draft.name.trim()} 已登记，建议点「测试连接」验证`);
    Object.assign(draft, { name: "", model: "", base_url: "", api_key: "", vision: false });
    await loadCustoms();
  } catch (e) { toast.error(e); }
  finally { savingCustom.value = false; }
}
async function removeCustom(name: string) {
  if (!confirm(`删除通道 ${name}？绑定了它的技能下次运行会报「provider not configured」。`))
    return;
  try {
    await api.deleteCustomProvider(name);
    toast.ok(`通道 ${name} 已删除`);
    await loadCustoms();
  } catch (e) { toast.error(e); }
}
async function testCustom(name: string) {
  testingName.value = name;
  testOut.value = "";
  try {
    const r = await api.testCustomProvider(name);
    testOut.value = `✓ ${name}（${r.model}）调通，模型返回：${JSON.stringify(r.reply)}`
      + `\n  用量：${JSON.stringify(r.usage)}`;
    toast.ok(`${name} 调通`);
  } catch (e) {
    testOut.value = `✗ ${name} 调用失败：${e instanceof Error ? e.message : String(e)}`;
    toast.error(e);
  } finally { testingName.value = ""; }
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

// —— API keys ——
interface KeyRow { id: string; name: string; prefix: string; active: boolean;
                   quota_mode: string; allocated_balance: number;
                   allocated_frozen: number; created_at: string | null }
const allocDrafts = reactive<Record<string, number | null>>({});

async function setQuotaMode(k: KeyRow, mode: string) {
  try {
    await api.keyQuotaMode(k.id, mode);
    toast.ok(mode === "allocated"
             ? `「${k.name}」改为独立额度——请划拨预算，用尽独立 402`
             : `「${k.name}」改回共享池计费`);
    await loadKeys();
  } catch (e) { toast.error(e); }
}
async function allocate(k: KeyRow) {
  const amt = allocDrafts[k.id];
  if (!amt) { toast.error("填写划拨金额：正数=池→Key，负数=回收"); return; }
  try {
    await api.keyAllocate(k.id, amt);
    toast.ok(amt > 0 ? `已划拨 ${amt} credits 给「${k.name}」` : `已回收 ${-amt} credits`);
    allocDrafts[k.id] = null;
    await loadKeys();
    if (acct.value) await loadBilling();     // pool balance moved too
  } catch (e) { toast.error(e); }
}
const apiKeys = ref<KeyRow[]>([]);
const keyName = ref("");
const freshKey = ref("");
const copied = ref(false);

async function loadKeys() {
  try { apiKeys.value = await api.listApiKeys(); } catch (e) { toast.error(e); }
}
async function createKey() {
  try {
    const r = await api.createApiKey(keyName.value);
    freshKey.value = r.api_key;
    copied.value = false;
    keyName.value = "";
    toast.ok("密钥已创建——完整密钥仅显示这一次");
    await loadKeys();
  } catch (e) { toast.error(e); }
}
async function copyKey() {
  try {
    await navigator.clipboard.writeText(freshKey.value);
    copied.value = true;
  } catch { toast.error("复制失败，请手动选择文本"); }
}
async function revokeKey(k: KeyRow) {
  if (!confirm(`吊销密钥「${k.name}」？使用它的集成将立即失效。`)) return;
  try { await api.revokeApiKey(k.id); toast.ok("已吊销"); await loadKeys(); }
  catch (e) { toast.error(e); }
}

onMounted(() => { loadSmtp(); loadUsers(); loadProviders(); loadCustoms(); loadOidc();
                  loadKeys(); });
watch(tab, (t) => {
  if (t === "users") loadUsers();
  if (t === "billing") loadBilling();
  if (t === "byok") { loadProviders(); loadCustoms(); }
  if (t === "sso") loadOidc();
  if (t === "apikeys") loadKeys();
});
</script>

<style scoped>
.tabs { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.sub { margin: 18px 0 0; font-size: 14px; color: var(--accent);
  border-top: 1px solid var(--border); padding-top: 14px; }
.url { font-family: Consolas, monospace; font-size: 11.5px; word-break: break-all; }
.row-ops { display: flex; gap: 6px; justify-content: flex-end; }
.new-ch { display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
  border: 1px dashed var(--border); border-radius: 8px; padding: 12px; }
.new-ch label { display: flex; flex-direction: column; gap: 4px; font-size: 12px;
  color: var(--text-dim); }
.new-ch .wide { grid-column: span 2; }
.new-ch .chk { grid-column: span 2; flex-direction: row; align-items: center; gap: 8px; }
.new-ch .chk input { width: auto; }
.new-act { grid-column: span 2; display: flex; gap: 12px; align-items: center;
  font-size: 12px; }
.test-out { background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px; font-size: 12px; white-space: pre-wrap; word-break: break-all;
  margin: 0; }
.panel { background: var(--bg-panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px; display: flex; flex-direction: column; gap: 12px; }
/* forms cap their own width for readability; tables stretch full width */
.grid2 { max-width: 940px; }
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
.row-ops { display: flex; gap: 6px; }
.fresh-key { border: 1px solid var(--accent); border-radius: 8px; padding: 12px;
  background: var(--bg-raised); }
.fresh-key p { margin: 0 0 8px; color: var(--accent); font-size: 13px; }
.key-line { display: flex; gap: 10px; align-items: center; }
.key-line code { background: var(--bg); padding: 6px 10px; border-radius: 6px;
  font-size: 13px; word-break: break-all; flex: 1; }

/* —— billing tab —— */
.bill-strip { display: flex; gap: 12px; flex-wrap: wrap; }
.bill-card { background: var(--bg-raised); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 18px; display: flex; flex-direction: column;
  gap: 4px; min-width: 130px; }
.bill-card.main { border-color: var(--accent); }
.bill-card.tags { justify-content: center; gap: 6px; }
.bill-label { font-size: 12px; color: var(--text-dim); }
.bill-num { font-size: 26px; font-weight: 600; color: var(--text); }
.bill-num.sm { font-size: 20px; }
.bill-unit { font-size: 12px; color: var(--text-dim); }
.bill-config summary { cursor: pointer; color: var(--text-dim); font-size: 13px; }
.bill-config[open] summary { margin-bottom: 10px; }
label.inline { flex-direction: row; align-items: center; gap: 6px; }
.num-in { max-width: 90px; }
.bill-ops { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px; max-width: 1100px; }
.op-card { background: var(--bg-raised); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.op-card h4 { margin: 0; font-size: 13px; }
.gift-pending h4, .ledger-bar h4 { margin: 0; font-size: 13px; }
.num { font-variant-numeric: tabular-nums; }
.note-cell { max-width: 420px; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
</style>
