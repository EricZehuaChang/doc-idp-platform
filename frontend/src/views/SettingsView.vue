<template>
  <main class="page">
    <PageHeader :title="$t('settings.settings-title')" :desc="isAdmin ? $t('settings.management_entry_for_email_accounts_billing_model_keys_and') : $t('settings.language-description')" />
    <section class="panel language-panel">
      <label for="interface-language">{{ t('settings.language') }}</label>
      <select id="interface-language" :value="locale" @change="setLanguage(($event.target as HTMLSelectElement).value)">
        <option value="zh">{{ t('language.chinese') }}</option>
        <option value="en">English</option>
      </select>
      <p class="dim">{{ t('settings.language-description') }}</p>
    </section>
    <template v-if="isAdmin">
    <div class="tabs">
      <button :class="{ primary: tab === 'email' }" @click="tab = 'email'">{{ $t('settings.email_smtp') }}</button>
      <button :class="{ primary: tab === 'users' }" @click="tab = 'users'">{{ $t('settings.users') }}</button>
      <button :class="{ primary: tab === 'logs' }" @click="tab = 'logs'">{{ $t('settings.audit_log') }}</button>
      <button :class="{ primary: tab === 'billing' }" @click="tab = 'billing'">{{ $t('settings.billing') }}</button>
      <button :class="{ primary: tab === 'byok' }" @click="tab = 'byok'">{{ $t('settings.model_channels_keys') }}</button>
      <button :class="{ primary: tab === 'sso' }" @click="tab = 'sso'">{{ $t('settings.single_sign_on_sso') }}</button>
      <button :class="{ primary: tab === 'apikeys' }" @click="tab = 'apikeys'">{{ $t('settings.api_keys') }}</button>
    </div>

    <!-- —— SMTP —— -->
    <section v-if="tab === 'email'" class="panel">
      <p class="dim">{{ $t('settings.platform_email_configuration_used_for_invitation_activation_and_password') }}</p>
      <div class="grid2">
        <label>{{ $t('settings.smtp_host') }}<input v-model="smtp.host" placeholder="smtp.example.com" /></label>
        <label>{{ $t('settings.port') }}<input v-model.number="smtp.port" type="number" /></label>
        <label>{{ $t('settings.encryption') }}
          <select v-model="smtp.security">
            <option value="ssl">SSL</option><option value="starttls">STARTTLS</option>
            <option value="none">{{ $t('settings.none') }}</option>
          </select></label>
        <label>{{ $t('settings.username') }}<input v-model="smtp.username" /></label>
        <label>{{ $t('settings.password') }} <span class="dim">{{ smtpInfo?.has_password ? $t('settings.saved_leave_blank_to_keep_unchanged') : "" }}</span>
          <input v-model="smtp.password" type="password" autocomplete="new-password" /></label>
        <label>{{ $t('settings.sender_address') }}<input v-model="smtp.from_addr" placeholder="noreply@example.com" /></label>
        <label>{{ $t('settings.sender_display_name') }}<input v-model="smtp.from_name" /></label>
        <label>{{ $t('settings.reply_to_address_optional') }}<input v-model="smtp.reply_to" /></label>
      </div>
      <div class="row">
        <button class="primary" @click="saveSmtp">{{ $t('settings.save_configuration') }}</button>
        <input v-model="testTo" :placeholder="$t('settings.test_recipient_email')" class="test-to" />
        <button @click="testSmtp" :disabled="testing">{{ testing ? $t('settings.sending') : $t('settings.send_test_email') }}</button>
      </div>
    </section>

    <!-- —— users —— -->
    <section v-if="tab === 'users'" class="panel">
      <div class="row">
        <input v-model="invEmail" :placeholder="$t('settings.email')" class="test-to" />
        <select v-model="invRole">
          <option value="viewer">{{ $t('settings.viewer_read_only') }}</option>
          <option value="operator">{{ $t('settings.operator_review') }}</option>
          <option value="admin">{{ $t('settings.admin_manage') }}</option>
        </select>
        <button class="primary" @click="invite">{{ $t('settings.email_invitation') }}</button>
        <span class="dim">{{ $t('settings.or') }}</span>
        <input v-model="invPassword" type="password" :placeholder="$t('settings.initial_password_direct_creation')" class="test-to" />
        <button @click="createDirect">{{ $t('settings.create_directly') }}</button>
      </div>
      <table v-if="users.length">
        <thead><tr><th>{{ $t('settings.email') }}</th><th>{{ $t('settings.role') }}</th><th>{{ $t('settings.status') }}</th><th>{{ $t('settings.login_method') }}</th><th></th></tr></thead>
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
              <span v-if="u.pending" class="state pend">{{ $t('settings.pending_activation') }}</span>
              <span v-else-if="u.active" class="state ok">{{ $t('settings.active') }}</span>
              <span v-else class="state off">{{ $t('settings.disabled') }}</span>
            </td>
            <td class="dim">{{ u.auth_provider }}</td>
            <td class="row-ops">
              <button v-if="u.role !== 'admin'" @click="editGrant(u)">{{ $t('settings.api_permissions') }}</button>
              <button v-if="u.pending" @click="resend(u)">{{ $t('settings.resend_invitation') }}</button>
              <button v-if="!u.pending && u.auth_provider === 'local' && u.active"
                      @click="resetPw(u)" :title="$t('settings.reset_password_the_user_must_change_their_password_at')">
                {{ $t('settings.reset_password') }}</button>
              <button v-if="u.active" class="danger" @click="toggle(u, false)">{{ $t('settings.disable') }}</button>
              <button v-else @click="toggle(u, true)">{{ $t('settings.restore') }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- R2 (2026-09-22): the admin sets a member's personal-key ceiling; a
         key's live reach = what it was issued for ∩ this grant -->
    <AppModal v-if="grantUser" @close="grantUser = null">
      <h2>{{ $t('settings.api_permissions_9605a0') }} {{ grantUser.email }}</h2>
      <div class="grant-form">
        <label class="chk-row"><input type="checkbox" v-model="grantDraft.allow_create" />
          {{ $t('settings.allow_creating_and_using_personal_keys') }}</label>
        <div class="grant-groups">
          <label v-for="g in grantGroups" :key="g.value" class="chk-row">
            <input type="checkbox" v-model="grantDraft.groups" :value="g.value"
                   :disabled="!grantDraft.allow_create" /> {{ g.label }}</label>
        </div>
        <label>{{ $t('settings.available_skill_codes_comma_separated_leave_blank_all') }}<input v-model="grantSkills" /></label>
        <p class="dim">{{ $t('settings.saving_immediately_restricts_personal_keys_already_issued_by_this') }}</p>
        <div class="row-ops">
          <button @click="grantUser = null">{{ $t('settings.cancel') }}</button>
          <button class="primary" @click="saveGrant">{{ $t('settings.save_authorization') }}</button>
        </div>
      </div>
    </AppModal>

    <!-- —— operation log (2026-09-09) —— -->
    <section v-if="tab === 'logs'" class="panel">
      <div class="row">
        <select v-model="logAction" @change="loadLogs(1)" class="log-sel">
          <option value="">{{ $t('settings.all_actions') }}</option>
          <option v-for="(label, fam) in LOG_ACTIONS" :key="fam" :value="fam">
            {{ label }}</option>
        </select>
        <select v-model="logActor" @change="loadLogs(1)" :aria-label="$t('settings.operator_account')"><option value="">{{ $t('settings.all_accounts') }}</option><option v-for="a in logActors" :key="a">{{ a }}</option></select>
        <input type="date" v-model="logFrom" :aria-label="$t('settings.start_date')" /><input type="date" v-model="logTo" :aria-label="$t('settings.end_date')" />
        <input v-model="logQ" :placeholder="$t('settings.search_by_operator_or_action')" class="test-to"
               @keyup.enter="loadLogs(1)" />
        <button class="primary" @click="loadLogs(1)">{{ $t('settings.search') }}</button>
        <span class="dim log-count">{{ $t('settings.total') }} {{ logTotal }} {{ $t('settings.items') }}</span>
        <span class="grow"></span>
        <button :disabled="logPage <= 1" @click="loadLogs(logPage - 1)">{{ $t('settings.previous') }}</button>
        <span class="dim">{{ $t('settings.page') }} {{ logPage }} {{ $t('settings.page_d24d3c') }}</span>
        <button :disabled="logPage * logLimit >= logTotal" @click="loadLogs(logPage + 1)">
          {{ $t('settings.next') }}</button>
      </div>
      <p class="dim log-hint">{{ $t('settings.key_operations_for_login_user_and_skill_management_review') }}</p>
      <table v-if="logRows.length" class="log-table">
        <thead><tr><th>{{ $t('settings.time') }}</th><th>{{ $t('settings.operator') }}</th><th>{{ $t('settings.action') }}</th><th>{{ $t('settings.details') }}</th></tr></thead>
        <tbody>
          <tr v-for="r in logRows" :key="r.id">
            <td class="dim log-time">{{ fmtTime(r.created_at) }}</td>
            <td class="log-actor">{{ r.actor }}</td>
            <td>{{ actionLabel(r.action) }}</td>
            <td class="log-det"><code>{{ fmtDetail(r.detail) }}</code></td>
          </tr>
        </tbody>
      </table>
      <EmptyState v-else :title="$t('settings.no_operation_records')" glyph="🗒️">
        {{ $t('settings.no_operation_logs_under_this_filter_key_operations_for') }}
      </EmptyState>
    </section>

    <!-- —— billing (§12) —— -->
    <section v-if="tab === 'billing'" class="panel">
      <template v-if="acct">
        <!-- balance strip -->
        <div class="bill-strip">
          <div class="bill-card main">
            <span class="bill-label">{{ $t('settings.available_balance') }}</span>
            <span class="bill-num">{{ acct.available.toFixed(2) }}</span>
            <span class="bill-unit">credits</span>
          </div>
          <div class="bill-card"><span class="bill-label">{{ $t('settings.recharged_balance') }}</span>
            <span class="bill-num sm">{{ acct.paid_balance.toFixed(2) }}</span></div>
          <div class="bill-card"><span class="bill-label">{{ $t('settings.bonus_balance') }}</span>
            <span class="bill-num sm">{{ acct.gift_balance.toFixed(2) }}</span></div>
          <div class="bill-card"><span class="bill-label">{{ $t('settings.frozen_in_progress') }}</span>
            <span class="bill-num sm">{{ acct.frozen.toFixed(2) }}</span></div>
          <div class="bill-card tags">
            <span class="state" :class="acct.mode === 'live' ? 'ok' : 'pend'">
              {{ acct.mode === "live" ? $t('settings.live_billing') : $t('settings.shadow_billing_metered_only_no_charge') }}</span>
            <span v-if="acct.plan" class="state ok">{{ $t('settings.plan') }} {{ acct.plan }}</span>
            <span v-if="acct.byok" class="state ok">{{ $t('settings.byok_rates') }}</span>
          </div>
        </div>

        <!-- mode + rate card -->
        <details class="bill-config">
          <summary>{{ $t('settings.billing_mode_and_rates_platform_admin') }}</summary>
          <div class="row">
            <label class="inline">{{ $t('settings.mode') }}
              <select v-model="cfgDraft.mode">
                <option value="shadow">{{ $t('settings.shadow_shadow_billing') }}</option>
                <option value="live">{{ $t('settings.live_live_charging') }}</option>
              </select></label>
            <label class="inline">{{ $t('settings.extraction_rate_page') }}
              <input v-model.number="cfgDraft.extract" type="number" step="0.1" min="0" class="num-in" /></label>
            <label class="inline">{{ $t('settings.review_rate_page') }}
              <input v-model.number="cfgDraft.audit" type="number" step="0.1" min="0" class="num-in" /></label>
            <label class="inline">{{ $t('settings.byok_extraction_page') }}
              <input v-model.number="cfgDraft.extract_byok" type="number" step="0.1" min="0" class="num-in" /></label>
            <label class="inline">{{ $t('settings.byok_review_page') }}
              <input v-model.number="cfgDraft.audit_byok" type="number" step="0.1" min="0" class="num-in" /></label>
            <button class="primary" @click="saveBillingConfig">{{ $t('settings.save') }}</button>
          </div>
          <p class="dim">{{ $t('settings.shadow_mode_only_records_but_does_not_charge_default') }}</p>
        </details>

        <!-- money ops -->
        <div class="bill-ops">
          <div class="op-card">
            <h4>{{ $t('settings.manual_recharge') }} <span class="dim">{{ $t('settings.registered_after_corporate_transfer_confirmation') }}</span></h4>
            <input v-model.number="topupAmount" type="number" min="0" :placeholder="$t('settings.amount_credits')" />
            <input v-model="topupVoucher" :placeholder="$t('settings.transfer_reference_number_required')" />
            <input v-model="topupNote" :placeholder="$t('settings.note_optional')" />
            <button class="primary" :disabled="!topupAmount || !topupVoucher"
                    @click="doTopup">{{ $t('settings.credit') }}</button>
          </div>
          <div class="op-card">
            <h4>{{ $t('settings.balance_adjustment') }} <span class="dim">{{ $t('settings.positive_or_negative_fully_audited') }}</span></h4>
            <input v-model.number="adjAmount" type="number" :placeholder="$t('settings.amount_negative_deduction')" />
            <select v-model="adjBucket">
              <option value="paid">{{ $t('settings.top_up_bucket') }}</option><option value="gift">{{ $t('settings.bonus_bucket') }}</option>
            </select>
            <input v-model="adjReason" :placeholder="$t('settings.adjustment_reason_required')" />
            <button class="primary" :disabled="!adjAmount || !adjReason"
                    @click="doAdjust">{{ $t('settings.adjust') }}</button>
          </div>
          <div class="op-card">
            <h4>{{ $t('settings.marketing_bonus') }} <span class="dim">{{ $t('settings.over') }} {{ acct.gift_review_threshold }} {{ $t('settings.requires_dual_approval') }}</span></h4>
            <input v-model.number="giftAmount" type="number" min="0" :placeholder="$t('settings.amount_credits')" />
            <input v-model="giftCampaign" :placeholder="$t('settings.campaign_tag_required')" />
            <input v-model="giftReason" :placeholder="$t('settings.reason_required')" />
            <button class="primary" :disabled="!giftAmount || !giftCampaign || !giftReason"
                    @click="doGift">{{ $t('settings.grant') }}</button>
          </div>
        </div>

        <!-- pending gift approvals -->
        <div v-if="pendingGifts.length" class="gift-pending">
          <h4>{{ $t('settings.pending_bonus_approval_requires_another_admin_s_approval') }}</h4>
          <table>
            <thead><tr><th>{{ $t('settings.amount') }}</th><th>{{ $t('settings.campaign') }}</th><th>{{ $t('settings.reason') }}</th><th>{{ $t('settings.requested_by') }}</th><th>{{ $t('settings.time') }}</th><th></th></tr></thead>
            <tbody>
              <tr v-for="g in pendingGifts" :key="g.id">
                <td>{{ g.amount }}</td><td>{{ g.campaign }}</td><td class="dim">{{ g.reason }}</td>
                <td class="dim">{{ g.requested_by }}</td>
                <td class="dim">{{ g.created_at ? new Date(g.created_at).toLocaleString() : "-" }}</td>
                <td class="row-ops">
                  <button class="primary" @click="decideGift(g, 'approve')">{{ $t('settings.approve') }}</button>
                  <button class="danger" @click="decideGift(g, 'reject')">{{ $t('settings.reject') }}</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- ledger -->
        <div class="row ledger-bar">
          <h4>{{ $t('settings.credit_transactions') }}</h4>
          <select v-model="ledgerKind" @change="loadLedger(0)">
            <option value="">{{ $t('settings.all_types') }}</option>
            <option v-for="(label, k) in KIND_LABELS" :key="k" :value="k">{{ label }}</option>
          </select>
          <span class="dim">{{ $t('settings.total') }} {{ ledgerTotal }} {{ $t('settings.items') }}</span>
        </div>
        <table v-if="ledger.length">
          <thead><tr><th>{{ $t('settings.time') }}</th><th>{{ $t('settings.type') }}</th><th>{{ $t('settings.bucket') }}</th><th>{{ $t('settings.amount') }}</th><th>{{ $t('settings.balance_snapshot') }}</th><th>{{ $t('settings.description') }}</th></tr></thead>
          <tbody>
            <tr v-for="l in ledger" :key="l.id">
              <td class="dim">{{ l.created_at ? new Date(l.created_at).toLocaleString() : "-" }}</td>
              <td>{{ KIND_LABELS[l.kind] ?? l.kind }}</td>
              <td class="dim">{{ l.bucket === "gift" ? $t('settings.grant') : $t('settings.top_up') }}</td>
              <td class="num">{{ l.amount.toFixed(2) }}</td>
              <td class="num dim">{{ l.balance_snapshot?.toFixed(2) ?? "-" }}</td>
              <td class="dim note-cell">{{ l.note }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="dim">{{ $t('settings.no_transactions_yet') }}</p>
        <div class="row" v-if="ledgerTotal > ledger.length || ledgerOffset > 0">
          <button :disabled="ledgerOffset === 0" @click="loadLedger(ledgerOffset - 20)">{{ $t('settings.previous') }}</button>
          <button :disabled="ledgerOffset + 20 >= ledgerTotal"
                  @click="loadLedger(ledgerOffset + 20)">{{ $t('settings.next') }}</button>
        </div>
      </template>
      <Skeleton v-else :rows="5" />
    </section>

    <!-- —— BYOK —— -->
    <section v-if="tab === 'byok'" class="panel">
      <p class="dim">
        {{ $t('settings.bring_your_own_key_byok_this_tenant_can_provide') }}</p>
      <table v-if="providers.length">
        <thead><tr><th>{{ $t('settings.channel') }}</th><th>{{ $t('settings.model') }}</th><th>{{ $t('settings.platform_key') }}</th><th>{{ $t('settings.tenant_key') }}</th><th></th></tr></thead>
        <tbody>
          <tr v-for="p in providers" :key="p.name">
            <td class="code">{{ p.name }} <span v-if="p.active" class="state ok">{{ $t('settings.default') }}</span></td>
            <td class="dim">{{ p.model }}</td>
            <td>{{ p.platform_key ? "✓" : "—" }}</td>
            <td>
              <span v-if="p.byok_set" class="state ok">{{ $t('settings.configured') }}</span>
              <input v-else v-model="keyDrafts[p.name]" type="password"
                     :placeholder="$t('settings.paste_api_key')" class="key-in" />
            </td>
            <td>
              <button v-if="!p.byok_set" class="primary"
                      :disabled="!keyDrafts[p.name]" @click="setKey(p.name)">{{ $t('settings.save') }}</button>
              <button v-else class="danger" @click="removeKey(p.name)">{{ $t('settings.remove') }}</button>
            </td>
          </tr>
        </tbody>
      </table>
      <Skeleton v-else :rows="4" />

      <!-- —— custom channels: register any OpenAI-compatible endpoint —— -->
      <h3 class="sub">{{ $t('settings.custom_model_channel') }}</h3>
      <p class="dim">
        {{ $t('settings.add_models_beyond_the_platform_s_built_in_list') }}</p>
      <table v-if="customs.length">
        <thead><tr><th>{{ $t('settings.name') }}</th><th>{{ $t('settings.model_id') }}</th><th>{{ $t('settings.endpoint_url') }}</th><th>{{ $t('settings.image_input') }}</th>
                   <th>{{ $t('settings.key') }}</th><th></th></tr></thead>
        <tbody>
          <tr v-for="c in customs" :key="c.name">
            <td class="code">{{ c.name }}</td>
            <td class="dim">{{ c.model }}</td>
            <td class="dim url">{{ c.base_url }}</td>
            <td>{{ c.vision ? $t('settings.supported') : "—" }}</td>
            <td>
              <span v-if="c.has_key" class="state ok">{{ $t('settings.configured') }}</span>
              <span v-else-if="c.no_key" class="state pend">{{ $t('settings.no_key_required') }}</span>
              <span v-else class="state off">{{ $t('settings.missing') }}</span>
            </td>
            <td class="row-ops">
              <button :disabled="testingName === c.name" @click="testCustom(c.name)">
                {{ testingName === c.name ? $t('settings.calling') : $t('settings.test_connection') }}</button>
              <button class="danger" @click="removeCustom(c.name)">{{ $t('settings.delete') }}</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="dim">{{ $t('settings.no_custom_channels_yet') }}</p>

      <div class="new-ch">
        <label>{{ $t('settings.name_channel_identifier') }}
          <input v-model="draft.name" :placeholder="$t('settings.e_g_qwen_vl_max')" /></label>
        <label>{{ $t('settings.model_id_leave_blank_same_as_name') }}
          <input v-model="draft.model" :placeholder="$t('settings.the_model_value_from_the_vendor_s_docs')" /></label>
        <label class="wide">{{ $t('settings.endpoint_url_openai_compatible_base_url') }}
          <input v-model="draft.base_url"
                 :placeholder="$t('settings.e_g_https_dashscope_aliyuncs_com_compatible_mode_v1')" /></label>
        <label class="wide">API Key
          <input v-model="draft.api_key" type="password" :disabled="draft.no_key"
                 :placeholder="draft.no_key ? $t('settings.this_endpoint_requires_no_authentication') : $t('settings.paste_the_key_stored_encrypted_and_never_displayed')" /></label>
        <label class="chk">
          <input type="checkbox" v-model="draft.no_key" />
          {{ $t('settings.this_endpoint_requires_no_api_key_self_hosted_vllm') }}
        </label>
        <label class="chk">
          <input type="checkbox" v-model="draft.vision" />
          {{ $t('settings.this_model_supports_image_input_when_checked_test_runs') }}
        </label>
        <div class="new-act">
          <button class="primary" :disabled="savingCustom || !draft.name || !draft.base_url
                                             || (!draft.api_key && !draft.no_key)"
                  @click="saveCustom">{{ savingCustom ? $t('settings.registering') : $t('settings.register_channel') }}</button>
          <span class="dim">{{ $t('settings.after_registering_we_recommend_clicking_test_connection_first_a') }}</span>
        </div>
      </div>
      <pre v-if="testOut" class="test-out">{{ testOut }}</pre>
    </section>
    <!-- —— OIDC SSO —— -->
    <section v-if="tab === 'sso'" class="panel">
      <p class="dim">
        {{ $t('settings.oidc_single_sign_on_bind_an_enterprise_idp_keycloak') }}</p>
      <label class="chk-row">
        <input type="checkbox" v-model="oidc.enabled" /> {{ $t('settings.enable_sso_login_entry') }}
      </label>
      <div class="grid2">
        <label>{{ $t('settings.issuer_url') }}
          <input v-model="oidc.issuer" placeholder="https://idp.example.com/realms/main" /></label>
        <label>Client ID<input v-model="oidc.client_id" /></label>
        <label>Client Secret <span class="dim">{{ oidcInfo?.has_secret ? $t('settings.saved_leave_blank_to_keep_unchanged') : "" }}</span>
          <input v-model="oidc.client_secret" type="password" autocomplete="new-password" /></label>
        <label>{{ $t('settings.callback_url_configure_in_idp') }}
          <input :value="callbackUrl" readonly /></label>
      </div>
      <div class="row"><button class="primary" @click="saveOidc">{{ $t('settings.save_sso_configuration') }}</button></div>
    </section>

    <!-- —— API keys —— -->
    <section v-if="tab === 'apikeys'" class="panel">
      <p class="dim">
        {{ $t('settings.credentials_used_by_integrators_to_call_the_platform_api') }}</p>
      <div class="row">
        <input v-model="keyName" :placeholder="$t('settings.key_name_e_g_erp_integration')" class="test-to" />
        <select v-model="keyType" class="test-to">
          <option value="application">{{ $t('settings.integration_key_broader_permissions') }}</option>
          <option value="agent">{{ $t('settings.agent_key_restricted_recommended_for_endpoint_distribution') }}</option>
        </select>
        <input v-if="keyType === 'agent'" v-model="keySkills"
               :placeholder="$t('settings.available_skill_codes_comma_separated_leave_blank_all_f7fd57')" class="test-to wide-in" />
        <button class="primary" @click="createKey">{{ $t('settings.create_key') }}</button>
      </div>
      <p v-if="keyType === 'agent'" class="dim">
        {{ $t('settings.agent_keys_can_only_call_whitelisted_endpoints_submit_tasks') }}</p>

      <!-- show-once panel: the only time the full key is visible -->
      <div v-if="freshKey" class="fresh-key">
        <p>{{ $t('settings.the_full_key_is_shown_only_once_copy_and') }}</p>
        <div class="key-line">
          <code>{{ freshKey }}</code>
          <button class="primary" @click="copyKey">{{ copied ? $t('settings.copied') : $t('settings.copy') }}</button>
        </div>
      </div>

      <table v-if="apiKeys.length">
        <thead><tr><th>{{ $t('settings.name') }}</th><th>{{ $t('settings.type') }}</th><th>{{ $t('settings.prefix') }}</th><th>{{ $t('settings.status') }}</th><th>{{ $t('settings.owner_skill_scope') }}</th>
          <th>{{ $t('settings.quota_mode') }}</th><th>{{ $t('settings.dedicated_quota') }}</th><th>{{ $t('settings.last_used') }}</th><th>{{ $t('settings.created_at') }}</th><th></th></tr></thead>
        <tbody>
          <tr v-for="k in apiKeys" :key="k.id">
            <td>{{ k.name }}</td>
            <td><span class="state" :class="k.key_type === 'agent' ? 'off' : ''">
              {{ k.key_type === "agent" ? "Agent" : $t('settings.integration') }}</span></td>
            <td class="code">idp_ak_{{ k.prefix }}…</td>
            <td><span class="state" :class="k.active ? 'ok' : 'off'">
              {{ k.active ? $t('settings.enabled') : $t('settings.revoked') }}</span></td>
            <td class="own-cell">
              <template v-if="k.owner">{{ k.owner.email || k.owner.user_id }}</template>
              <span v-else-if="k.key_type === 'agent'" class="dim">{{ $t('settings.admin_issued') }}</span>
              <span v-else class="dim">{{ $t('settings.service_integration') }}</span>
              <span v-if="k.allowed_skill_codes" class="dim scope-line">
                {{ $t('settings.limited_to') }}{{ k.allowed_skill_codes.join("、") }}</span>
            </td>
            <td>
              <select :value="k.quota_mode"
                      @change="setQuotaMode(k, ($event.target as HTMLSelectElement).value)">
                <option value="pool">{{ $t('settings.shared_pool') }}</option>
                <option value="allocated">{{ $t('settings.dedicated_allocation') }}</option>
              </select>
            </td>
            <td>
              <template v-if="k.quota_mode === 'allocated' || k.allocated_balance > 0">
                <span class="num">{{ k.allocated_balance.toFixed(2) }}</span>
                <span v-if="k.allocated_frozen" class="dim">{{ $t('settings.frozen') }} {{ k.allocated_frozen.toFixed(2) }}）</span>
              </template>
              <span v-else class="dim">—</span>
            </td>
            <td class="dim">{{ k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "—" }}</td>
            <td class="dim">{{ k.created_at ? new Date(k.created_at).toLocaleString() : "-" }}</td>
            <td class="row-ops">
              <template v-if="k.quota_mode === 'allocated' || k.allocated_balance > 0">
                <input v-model.number="allocDrafts[k.id]" type="number"
                       :placeholder="$t('settings.amount_17a89e')" class="num-in" />
                <button @click="allocate(k)">{{ $t('settings.transfer') }}</button>
              </template>
              <button v-if="k.active" class="danger" @click="revokeKey(k)">{{ $t('settings.revoke') }}</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="dim">{{ $t('settings.no_keys_yet_create_one_for_your_system_integration') }}</p>
    </section>
    </template>
  </main>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
const { t, locale } = useI18n();
import { setLanguage } from "../i18n";
import { session } from "../session";
const isAdmin = computed(() => !session.authRequired || session.role === "admin");

import AppModal from "../components/AppModal.vue";
import { computed, onMounted, reactive, ref, watch } from "vue";
import { api, type ApiGrants, type AuditRow, type BillingAccount, type CustomProvider,
         type GiftRequestRow, type LedgerRow, type SmtpInfo } from "../api";
import EmptyState from "../components/EmptyState.vue";
import PageHeader from "../components/PageHeader.vue";
import Skeleton from "../components/Skeleton.vue";
import { toast } from "../toast";
import { copyText } from "../clipboard";

const tab = ref<"email" | "users" | "logs" | "billing" | "byok" | "sso" | "apikeys">("email");

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
    toast.ok(t('settings.smtp_configuration_saved'));
  } catch (e) { toast.error(e); }
}
async function testSmtp() {
  if (!testTo.value) { toast.error(t('settings.enter_a_test_recipient_email')); return; }
  testing.value = true;
  try { await api.testSmtp(testTo.value); toast.ok(t('settings.test_email_sent_please_check_your_inbox')); }
  catch (e) { toast.error(e); }
  finally { testing.value = false; }
}

// —— users ——
interface UserRow { id: string; email: string; role: string; active: boolean;
                    pending: boolean; auth_provider: string; api_grants?: ApiGrants | null }
const users = ref<UserRow[]>([]);
const grantUser = ref<UserRow | null>(null);
const grantDraft = reactive<ApiGrants>({ allow_create: true, groups: ['process'], allowed_skill_codes: null });
const grantSkills = ref('');
const grantGroups = computed(() => [{ value: 'process', label: t('settings.submission_query') }, { value: 'detect', label: t('settings.seal_signature_detection') }, { value: 'locate', label: t('settings.layout_positioning') }]);
function editGrant(u: UserRow) {
  grantUser.value = u;
  Object.assign(grantDraft, JSON.parse(JSON.stringify(u.api_grants ?? { allow_create: true, groups: ['process'], allowed_skill_codes: null })));
  grantSkills.value = grantDraft.allowed_skill_codes?.join(',') ?? '';
}
async function saveGrant() {
  if (!grantUser.value) return;
  const skills = grantSkills.value.split(/[,，]/).map(x => x.trim()).filter(Boolean);
  try { await api.patchUser(grantUser.value.id, { api_grants: { ...grantDraft, allowed_skill_codes: skills.length ? skills : null } }); grantUser.value = null; await loadUsers(); toast.ok(t('settings.api_authorization_updated')); }
  catch (e) { toast.error(e); }
}
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
    toast.ok(t('settings.invitation_email_sent'));
    invEmail.value = "";
    await loadUsers();
  } catch (e) { toast.error(e); }
}
async function createDirect() {
  if (!invEmail.value || !invPassword.value) { toast.error(t('settings.to_create_an_account_directly_enter_an_email_and')); return; }
  try {
    await api.createUser(invEmail.value, invPassword.value, invRole.value);
    toast.ok(t('settings.account_created_the_user_must_change_the_password_on'));
    invEmail.value = "";
    invPassword.value = "";
    await loadUsers();
  } catch (e) { toast.error(e); }
}
async function resend(u: UserRow) {
  try {
    await api.invite(u.email, u.role);   // backend reissues the token (60s cooldown)
    toast.ok(t('settings.invitation-resent', { email: u.email }));
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
async function resetPw(u: UserRow) {
  const p1 = window.prompt(t('settings.reset-password-prompt', { email: u.email }));
  if (!p1) return;
  if (p1.length < 8) { toast.error(t('settings.password_must_be_at_least_8_characters')); return; }
  const p2 = window.prompt(t('settings.enter_it_again_to_confirm'));
  if (p1 !== p2) { toast.error(t('settings.the_two_entries_do_not_match_no_changes_made')); return; }
  try {
    await api.resetUserPassword(u.id, p1);
    toast.ok(t('settings.password-reset', { email: u.email }));
  } catch (e) { toast.error(e); }
}

// —— operation log (2026-09-09) ——
const LOG_ACTIONS = computed<Record<string, string>>(() => ({
  auth: t('settings.login_account'), skills: t('settings.skills'), review: t('settings.review'),
  settings: t('settings.settings_keys'), billing: t('settings.billing'), process: t('settings.processing'),
  // R4 (2026-09-22): human task / download / key activity
  files: t('settings.tasks_originals'), artifacts: t('settings.output_files'), cabinet: t('settings.data_cabinet'), me: t('settings.personal_key'),
}));
const logRows = ref<AuditRow[]>([]);
const logTotal = ref(0);
const logPage = ref(1);
const logLimit = 50;
const logQ = ref("");
const logActor = ref(""), logFrom = ref(""), logTo = ref("");
const logActors = ref<string[]>([]);
const logAction = ref("");

async function loadLogs(page = 1) {
  logPage.value = page;
  try {
    logActors.value = (await api.auditActors()).actors;
    const d = await api.auditLogs({ actor: logActor.value, date_from: logFrom.value, date_to: logTo.value, q: logQ.value, action: logAction.value,
                                    page, limit: logLimit });
    logRows.value = d.data;
    logTotal.value = d.total;
  } catch (e) { toast.error(e); }
}
function actionLabel(action: string): string {
  const fam = action.split(".")[0];
  return ACTION_TEXT.value[action] ?? `${LOG_ACTIONS.value[fam] ?? fam} · ${action.split(".")[1] ?? action}`;
}
const ACTION_TEXT = computed<Record<string, string>>(() => ({
  "auth.login_failed": t('settings.login_failed'), "auth.api_grants_updated": t('settings.update_api_authorization'),
  "files.submitted": t('settings.submit_task'), "files.downloaded": t('settings.download_original'), "artifacts.downloaded": t('settings.download_output'),
  "cabinet.exported": t('settings.export_data_cabinet'), "review.fields_patched": t('settings.modify_recognition_fields'),
  "auth.login": t('settings.log_in'), "auth.login_oidc": t('settings.sso_login'), "auth.activated": t('settings.activate_account'),
  "auth.user_created": t('settings.create_user'), "auth.user_updated": t('settings.change_user'), "auth.invite_sent": t('settings.send_invitation'),
  "auth.password_changed": t('settings.change_password_self'), "auth.password_reset": t('settings.reset_password_self_service'),
  "auth.password_reset_admin": t('settings.reset_password_admin'),
  "skills.created": t('settings.create_skill'), "skills.published": t('settings.publish_version'),
  "skills.state_changed": t('settings.enable_disable_skill'), "skills.deleted": t('settings.delete_skill'),
  "skills.restored": t('settings.restore_skill'), "skills.version_deleted": t('settings.delete_version'), "skills.imported": t('settings.import_skill'),
  "review.assign": t('settings.assign_review'), "review.passed": t('settings.approve'), "review.rejected": t('settings.reject'),
  "files.deleted": t('settings.delete_task'),
  "me.agent_key_created": t('settings.create_personal_key'), "me.agent_key_revoked": t('settings.revoke_personal_key'),
  "settings.api_key_created": t('settings.create_app_key'), "settings.api_key_revoked": t('settings.revoke_app_key'),
  "settings.api_key_allocate": t('settings.assign_key_quota'), "settings.api_key_quota_mode": t('settings.switch_key_quota_mode'),
  "settings.byok_set": t('settings.set_model_key'), "settings.byok_removed": t('settings.remove_model_key'),
  "settings.custom_provider_set": t('settings.set_custom_channel'), "settings.custom_provider_removed": t('settings.remove_custom_channel'),
  "settings.oidc_updated": t('settings.update_sso_configuration'), "settings.smtp_updated": t('settings.update_email_configuration'),
  "skills.package_exported": t('settings.export_skill_package'), "skills.package_imported": t('settings.import_skill_package'),
  "skills.references_pinned": t('settings.pin_reference_version'),
}));
function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(locale.value === "zh" ? "zh-CN" : "en-US", { hour12: false });
}
function fmtDetail(detail: Record<string, unknown> | null): string {
  return detail ? JSON.stringify(detail) : "—";
}

// —— billing (§12) ——
const KIND_LABELS = computed<Record<string, string>>(() => ({
  shadow_meter: t('settings.metering'), freeze: t('settings.freeze'), charge: t('settings.actual_deduction'), unfreeze: t('settings.unfreeze'),
  topup: t('settings.top_up'), adjust: t('settings.adjust'), gift: t('settings.grant'), key_transfer: t('settings.key_allocation'),
}));
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
    toast.ok(cfgDraft.mode === "live" ? t('settings.switched_to_live_billing') : t('settings.saved_shadow_billing'));
    await loadBilling();
  } catch (e) { toast.error(e); }
}
async function doTopup() {
  try {
    await api.billingTopup(topupAmount.value!, topupVoucher.value, topupNote.value);
    toast.ok(t('settings.credits-added', { amount: topupAmount.value ?? 0 }));
    topupAmount.value = null; topupVoucher.value = ""; topupNote.value = "";
    await loadBilling();
  } catch (e) { toast.error(e); }
}
async function doAdjust() {
  try {
    await api.billingAdjust(adjAmount.value!, adjBucket.value, adjReason.value);
    toast.ok(t('settings.adjustment_posted'));
    adjAmount.value = null; adjReason.value = "";
    await loadBilling();
  } catch (e) { toast.error(e); }
}
async function doGift() {
  try {
    const r = await api.billingGift(giftAmount.value!, giftCampaign.value, giftReason.value);
    toast.ok(r.status === "approved" ? t('settings.grant_credited') : t('settings.submitted_awaiting_review_by_another_administrator'));
    giftAmount.value = null; giftCampaign.value = ""; giftReason.value = "";
    await loadBilling();
  } catch (e) { toast.error(e); }
}
async function decideGift(g: GiftRequestRow, decision: "approve" | "reject") {
  try {
    await api.billingGiftDecide(g.id, decision);
    toast.ok(decision === "approve" ? t('settings.approved_grant_credited') : t('settings.rejected'));
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
    toast.ok(t('settings.provider-key-saved', { name }));
    await loadProviders();
  } catch (e) { toast.error(e); }
}
async function removeKey(name: string) {
  try {
    await api.deleteByok(name);
    toast.ok(t('settings.provider-key-removed', { name }));
    await loadProviders();
  } catch (e) { toast.error(e); }
}

// —— custom channels ——
const customs = ref<CustomProvider[]>([]);
const draft = reactive({ name: "", model: "", base_url: "", api_key: "",
                         no_key: false, vision: false });
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
      api_key: draft.api_key, no_key: draft.no_key, vision: draft.vision });
    toast.ok(t('settings.provider-added', { name: draft.name.trim() }));
    Object.assign(draft, { name: "", model: "", base_url: "", api_key: "",
                           no_key: false, vision: false });
    await loadCustoms();
  } catch (e) { toast.error(e); }
  finally { savingCustom.value = false; }
}
async function removeCustom(name: string) {
  if (!confirm(t('settings.delete-provider-confirm', { name })))
    return;
  try {
    await api.deleteCustomProvider(name);
    toast.ok(t('settings.provider-deleted', { name }));
    await loadCustoms();
  } catch (e) { toast.error(e); }
}
async function testCustom(name: string) {
  testingName.value = name;
  testOut.value = "";
  try {
    const r = await api.testCustomProvider(name);
    testOut.value = t('settings.provider-tested', { name, model: r.model, reply: JSON.stringify(r.reply), usage: JSON.stringify(r.usage) });
    toast.ok(t('settings.provider-connected', { name }));
  } catch (e) {
    testOut.value = t('settings.provider-failed', { name, error: e instanceof Error ? e.message : String(e) });
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
  if (!oidc.issuer || !oidc.client_id) { toast.error(t('settings.issuer_and_client_id_are_required')); return; }
  try {
    oidcInfo.value = await api.putOidc({ enabled: oidc.enabled, issuer: oidc.issuer,
                                         client_id: oidc.client_id,
                                         client_secret: oidc.client_secret || undefined }) as OidcInfo;
    oidc.client_secret = "";
    toast.ok(t('settings.sso_configuration_saved'));
  } catch (e) { toast.error(e); }
}

// —— API keys ——
interface KeyRow { id: string; name: string; prefix: string; active: boolean;
                   quota_mode: string; allocated_balance: number;
                   allocated_frozen: number; created_at: string | null;
                   key_type: string;
                   owner: { user_id: string; email: string | null } | null;
                   allowed_skill_codes: string[] | null;
                   last_used_at: string | null }
const allocDrafts = reactive<Record<string, number | null>>({});

async function setQuotaMode(k: KeyRow, mode: string) {
  try {
    await api.keyQuotaMode(k.id, mode);
    toast.ok(mode === "allocated"
             ? t('settings.quota-allocated', { name: k.name })
             : t('settings.quota-pooled', { name: k.name }));
    await loadKeys();
  } catch (e) { toast.error(e); }
}
async function allocate(k: KeyRow) {
  const amt = allocDrafts[k.id];
  if (!amt) { toast.error(t('settings.enter_the_transfer_amount_positive_pool_key_negative_reclaim')); return; }
  try {
    await api.keyAllocate(k.id, amt);
    toast.ok(amt > 0 ? t('settings.credits-allocated', { amount: amt, name: k.name }) : t('settings.credits-returned', { amount: -amt }));
    allocDrafts[k.id] = null;
    await loadKeys();
    if (acct.value) await loadBilling();     // pool balance moved too
  } catch (e) { toast.error(e); }
}
const apiKeys = ref<KeyRow[]>([]);
const keyName = ref("");
const keyType = ref("application");
const keySkills = ref("");
const freshKey = ref("");
const copied = ref(false);

async function loadKeys() {
  try { apiKeys.value = await api.listApiKeys(); } catch (e) { toast.error(e); }
}
async function createKey() {
  const codes = keySkills.value.split(/[，,]/).map(s => s.trim()).filter(Boolean);
  try {
    const r = await api.createApiKey(keyName.value, keyType.value,
                                     keyType.value === "agent" && codes.length ? codes : null);
    freshKey.value = r.api_key;
    copied.value = false;
    keyName.value = "";
    keySkills.value = "";
    toast.ok(t('settings.key_created_the_full_key_is_shown_only_once'));
    await loadKeys();
  } catch (e) { toast.error(e); }
}
async function copyKey() {
  copied.value = false;
  try {
    await copyText(freshKey.value);
    copied.value = true;
  } catch { toast.error(t('settings.copy_failed_please_select_the_text_manually')); }
}
async function revokeKey(k: KeyRow) {
  if (!confirm(t('settings.revoke-key-confirm', { name: k.name }))) return;
  try { await api.revokeApiKey(k.id); toast.ok(t('settings.revoked')); await loadKeys(); }
  catch (e) { toast.error(e); }
}

onMounted(() => {
  if (!isAdmin.value) return;
  loadSmtp(); loadUsers(); loadProviders(); loadCustoms(); loadOidc(); loadKeys();
});
watch(tab, (t) => {
  if (!isAdmin.value) return;
  if (t === "users") loadUsers();
  if (t === "logs") loadLogs(1);
  if (t === "billing") loadBilling();
  if (t === "byok") { loadProviders(); loadCustoms(); }
  if (t === "sso") loadOidc();
  if (t === "apikeys") loadKeys();
});
</script>

<style scoped>
.language-panel { margin-bottom: 16px; }
.language-panel select { width: min(100%, 280px); }
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
/* 9.15 WP2: agent key issuance + roster columns */
.wide-in { max-width: 320px; }
.own-cell { max-width: 220px; }
.scope-line { display: block; font-size: 12px; margin-top: 2px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.log-sel { width: 150px; }
.grow { flex: 1; }
.log-count { font-size: 12px; }
.log-hint { margin: -4px 0 0; font-size: 12px; }
.log-table td { vertical-align: top; padding: 6px 10px; }
.log-time { white-space: nowrap; font-size: 12px; }
.log-actor { font-size: 12.5px; }
.log-det code { font-size: 11.5px; font-family: Consolas, monospace;
  word-break: break-all; color: var(--text-dim); }
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
.grant-form { display: flex; flex-direction: column; gap: 12px; min-width: 360px; }
.grant-groups { display: flex; gap: 16px; flex-wrap: wrap; }
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
