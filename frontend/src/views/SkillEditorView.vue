<template>
  <main class="editor" v-if="pkg">
    <!-- 9.15 WP3 shell: top bar (identity + version menu + actions) above a
         设计/测试 tab pair; the left version rail became a dropdown menu -->
    <div class="main-col">
      <div class="toolbar">
        <router-link to="/skills" class="back" :title="$t('skillEditor.back_to_skill_list')">‹</router-link>
        <div class="ident">
          <h1>{{ pkg.name || pkg.skill_code || $t('skillEditor.new_skill') }}</h1>
          <span class="dim code-line">
            {{ pkg.skill_code || $t('skillEditor.determined_on_save') }}
            <button v-if="pkg.skill_code" class="copy" :title="$t('skillEditor.copy_skill_code')"
                    @click="copyCode">{{ copied ? "✓" : "⧉" }}</button>
          </span>
        </div>

        <!-- version dropdown replaces the old left rail (图02/03) -->
        <details v-if="!isNew" class="ver-menu" @close="">
          <summary class="ver-chip">
            v{{ selectedVersion ?? "—" }}
            <span class="chip" :class="`chip-${selectedStatus}`">{{ verLabel(selectedStatus ?? "") }}</span>
            ▾
          </summary>
          <div class="ver-pop">
            <button class="new-ver" :disabled="saving || fieldTrialStarting || sampleUploading" @click="newVersion">{{ $t('skillEditor.new_version') }}</button>
            <div v-for="v in [...versions].reverse()" :key="v.version" class="ver-card"
                 :class="{ current: v.version === selectedVersion }"
                 @click="selectVersion(v.version)">
              <div class="ver-row">
                <strong>v{{ v.version }}</strong>
                <span class="chip" :class="`chip-${v.status}`">{{ verLabel(v.status) }}</span>
              </div>
              <span class="dim ver-date">{{ shortDate(v.created_at) }}</span>
              <p v-if="v.changelog" class="ver-note-line" :title="v.changelog">{{ v.changelog }}</p>
            </div>
          </div>
        </details>

        <div class="tabs">
          <button :class="{ on: tab === 'design' }" @click="tab = 'design'">{{ $t('skillEditor.design') }}</button>
          <button :class="{ on: tab === 'test' }" @click="tab = 'test'">{{ $t('skillEditor.test') }}</button>
        </div>

        <div class="acts">
          <button v-if="!isNew" @click="downloadFile(api.skillExportUrl(code), `${code}.yaml`)">
            {{ $t('skillEditor.export') }}</button>
          <details class="more-menu">
            <summary>{{ $t('skillEditor.more') }}</summary>
            <div class="more-pop">
              <button @click="apiModal = true">{{ $t('skillEditor.api_access') }}</button>
              <button @click="downloadFile(api.skillExportUrl(code), `${code}.yaml`)">
                {{ $t('skillEditor.export_yaml') }}</button>
              <button v-if="!isNew && versions.length > 1" class="danger"
                      :disabled="selectedStatus === 'published'"
                      :title="selectedStatus === 'published'
                              ? $t('skillEditor.the_current_published_version_cannot_be_deleted_all_submissions')
                              : $t('skillEditor.delete_only_this_version')"
                      @click="removeVersion">{{ $t('skillEditor.delete_current_version') }}</button>
            </div>
          </details>
          <button class="primary" :disabled="saving || fieldTrialStarting || sampleUploading || importing || enriching" @click="save">
            {{ saving ? $t('skillEditor.saving') : (isNew ? $t('skillEditor.create_skill') : saveLabel) }}</button>
          <button v-if="!isNew && selectedStatus === 'draft'" class="confirm" :disabled="saving || fieldTrialStarting || sampleUploading"
                  @click="publishSelected">{{ $t('skillEditor.publish') }}</button>
        </div>
      </div>
      <p v-if="!isNew && selectedStatus === 'draft'" class="ver-note dim">
        {{ $t('skillEditor.currently_editing_v') }}{{ selectedVersion }}{{ $t('skillEditor.draft_save_updates_this_draft_in_place_to_keep') }}</p>
      <p v-else-if="!isNew && selectedStatus" class="ver-note dim">
        {{ $t('skillEditor.currently_viewing_v') }}{{ selectedVersion }}（{{ verLabel(selectedStatus) }}{{ $t('skillEditor.published_versions_cannot_be_modified_save_as_new_draft') }}</p>

      <!-- —— 设计 tab —— -->
      <template v-if="tab === 'design'">
        <div class="design-body" :inert="saving">
          <!-- flow rail: 基础 → (文档分类) → 字段提取 → 文件产出 -->
          <nav class="flow-rail" :aria-label="$t('skillEditor.design_steps')">
            <button v-for="(n, ni) in flowNodes" :key="n.key" class="node"
                    :class="{ on: step === n.key, bad: n.bad }"
                    :disabled="saving || fieldTrialStarting"
                    :aria-current="step === n.key ? 'step' : undefined"
                    @click="goto(n.key)">
              <span class="lbl"><span class="step-number">{{ ni + 1 }}</span>{{ n.label }}</span>
              <span class="sub dim">{{ n.sub }}</span>
            </button>
          </nav>

          <!-- —— 基础 —— -->
          <section v-show="step === 'basic'" class="card-panel block step-panel">
            <h3 class="block-title">{{ $t('skillEditor.basic_info') }}</h3>
            <div class="basic-grid">
              <label>{{ $t('skillEditor.skill_name') }}
                <input ref="skillNameInput" v-model="pkg.name" :placeholder="$t('skillEditor.e_g_xcmg_overseas_invoice')" /></label>
              <label>{{ $t('skillEditor.skill_code_english_cannot_be_changed_after_creation') }}
                <input ref="skillCodeInput" v-model="pkg.skill_code" class="mono" :disabled="!isNew" /></label>
              <label class="span2">{{ $t('skillEditor.description') }}
                <textarea v-model="pkg.description" rows="2"
                          :placeholder="$t('skillEditor.what_documents_does_this_skill_process_and_what_business')"></textarea>
              </label>
              <label v-if="!isNew" class="span2">{{ $t('skillEditor.version_notes_v') }}{{ selectedVersion }} {{ $t('skillEditor.notes') }}
                <input v-model="changelog"
                       :placeholder="$t('skillEditor.what_changed_in_this_version_e_g_added_tax')" />
              </label>
            </div>

            <h4 class="sub-title">{{ $t('skillEditor.processing_mode') }}</h4>
            <div class="cards">
              <button class="mode-card" :class="{ on: pkg.processing_mode !== 'fast' }"
                      @click="setProcessingMode('balanced')">
                <strong>{{ $t('skillEditor.balanced') }}</strong>
                <span class="dim">{{ $t('skillEditor.page_by_page_localization_with_confidence_scoring_supports_review') }}</span></button>
              <button class="mode-card" :class="{ on: pkg.processing_mode === 'fast' }"
                      data-testid="fast-mode-card"
                      @click="setProcessingMode('fast')">
                <strong>{{ $t('skillEditor.fast') }}</strong>
                <span class="dim">{{ $t('skillEditor.for_documents_with_fewer_pages_where_speed_matters_no') }} {{ fastMaxPages }} {{ $t('skillEditor.page') }}</span></button>
            </div>
            <h3>{{ $t('skillEditor.extraction_channel') }}</h3>
            <div class="cards">
              <button class="mode-card" :class="{ on: pkg.extraction_channel !== 'rules_first' }" @click="pkg.extraction_channel = 'model'"><strong>{{ $t('skillEditor.model_extraction') }}</strong><span>{{ $t('skillEditor.extract_all_fields_by_model') }}</span></button>
              <button class="mode-card" :class="{ on: pkg.extraction_channel === 'rules_first' }" @click="pkg.extraction_channel = 'rules_first'"><strong>{{ $t('skillEditor.rules_first') }}</strong><span>{{ $t('skillEditor.extract_by_field_rules_first_fill_missing_fields_by') }}</span></button>
            </div>
            <p v-if="pkg.processing_mode === 'fast'" class="dim adv-note" data-testid="fast-note">
              {{ $t('skillEditor.turbo_mode_results_have_no_localization_or_confidence_scores') }}</p>

            <h4 class="sub-title">{{ $t('skillEditor.manual_review_mode') }}</h4>
            <div class="cards" :class="{ disabled: pkg.processing_mode === 'fast' }">
              <button class="mode-card" :disabled="pkg.processing_mode === 'fast'"
                      :class="{ on: pkg.review_policy.mode === 'never' }"
                      @click="pkg.review_policy.mode = 'never'">
                <strong>{{ $t('skillEditor.no_manual_review_required') }}</strong>
                <span class="dim">{{ $t('skillEditor.results_pass_directly_without_stopping_for_review') }}</span></button>
              <button class="mode-card" :disabled="pkg.processing_mode === 'fast'"
                      :class="{ on: pkg.review_policy.mode === 'auto' }"
                      @click="pkg.review_policy.mode = 'auto'">
                <strong>{{ $t('skillEditor.review_on_low_confidence') }}</strong>
                <span class="dim">{{ $t('skillEditor.fields_below_threshold_enter_review') }}</span></button>
              <button class="mode-card" :disabled="pkg.processing_mode === 'fast'"
                      :class="{ on: pkg.review_policy.mode === 'always' }"
                      @click="pkg.review_policy.mode = 'always'">
                <strong>{{ $t('skillEditor.always_require_review') }}</strong>
                <span class="dim">{{ $t('skillEditor.every_document_enters_review') }}</span></button>
            </div>
            <p v-if="pkg.processing_mode === 'fast'" class="dim">
              {{ $t('skillEditor.turbo_mode_does_not_support_manual_review') }}</p>
            <label v-if="pkg.review_policy.mode === 'auto' && pkg.processing_mode !== 'fast'"
                   class="thr">
              {{ $t('skillEditor.confidence_threshold_below_triggers_manual_review') }}
              <select v-model.number="pkg.review_policy.confidence_threshold">
                <option :value="1">1</option><option :value="2">2</option>
                <option :value="3">3</option>
              </select></label>

            <h4 class="sub-title">{{ $t('skillEditor.skill_mode') }}</h4>
            <div class="cards" :class="{ disabled: pkg.processing_mode === 'fast' }">
              <button class="mode-card" :disabled="pkg.processing_mode === 'fast'"
                      :class="{ on: pkg.skill_mode !== 'advanced' }"
                      @click="setSkillMode('standard')">
                <strong>{{ $t('skillEditor.standard') }}</strong>
                <span class="dim">{{ $t('skillEditor.one_document_one_set_of_fields_suits_most_documents') }}</span></button>
              <button class="mode-card" :disabled="pkg.processing_mode === 'fast'"
                      :class="{ on: pkg.skill_mode === 'advanced' }"
                      data-testid="mode-advanced"
                      @click="setSkillMode('advanced')">
                <strong>{{ $t('skillEditor.advanced') }}</strong>
                <span class="dim">{{ $t('skillEditor.one_file_with_multiple_document_types_classify_first_then') }}</span></button>
            </div>
            <p v-if="pkg.skill_mode === 'advanced'" class="dim adv-note">
              {{ $t('skillEditor.advanced_mode_set_file_composition_categories_and_classification_model') }}</p>

            <details class="adv-settings">
              <summary>{{ $t('skillEditor.advanced_settings_extraction_model_fallback_model_challenger_model_parser') }}</summary>
              <div class="basic-grid">
                <datalist id="dl-providers">
                  <option v-for="p in providerOptions" :key="p.name" :value="p.name"
                          :label="t('skillEditor.provider_option', { name: p.name, model: p.model, active: p.active ? t('skillEditor.platform_default_suffix') : '', custom: p.custom ? t('skillEditor.custom_suffix') : '', vision: p.vision ? t('skillEditor.vision_suffix') : '' })" />
                </datalist>
                <datalist id="dl-parsers">
                  <option v-for="p in parserOptions" :key="p.name" :value="p.name"
                          :label="p.description ? `${p.name} — ${p.description}` : p.name" />
                </datalist>
                <label>{{ $t('skillEditor.extraction_model_empty_platform_default') }}{{ activeProvider ? `：${activeProvider}` : "" }}）
                  <input v-model="pkg.model_binding.extractor" list="dl-providers"
                         :placeholder="$t('skillEditor.select_from_dropdown_or_enter_directly_e_g_qwen')"
                         @focus="comboOpen" @input="comboTyped" @blur="comboClose" />
                  <span v-if="pkg.skill_mode === 'advanced'" class="hint dim">
                    {{ $t('skillEditor.advanced_mode_used_for_categories_with_fields_configured_in') }}</span></label>
                <label>{{ $t('skillEditor.fallback_model') }}
                  <input :value="pkg.model_binding.fallback ?? ''" list="dl-providers"
                         :placeholder="$t('skillEditor.optional_select_from_dropdown_or_enter_directly')"
                         @focus="comboOpen" @blur="comboClose"
                         @input="comboTyped($event); pkg.model_binding.fallback = ($event.target as HTMLInputElement).value || null" /></label>
                <label>{{ $t('skillEditor.challenger_model_inconsistency_flags_manual_review') }}
                  <input :value="pkg.model_binding.challenger ?? ''" list="dl-providers"
                         :placeholder="$t('skillEditor.optional_select_from_dropdown_or_enter_directly')"
                         @focus="comboOpen" @blur="comboClose"
                         @input="comboTyped($event); pkg.model_binding.challenger = ($event.target as HTMLInputElement).value || null" /></label>
                <label>{{ $t('skillEditor.parser_empty_auto_routing') }}
                  <input :value="pkg.parser ?? ''" list="dl-parsers"
                         :placeholder="$t('skillEditor.auto_select_from_dropdown_or_enter_directly')"
                         @focus="comboOpen" @blur="comboClose"
                         @input="comboTyped($event); pkg.parser = ($event.target as HTMLInputElement).value || null" /></label>
              </div>
            </details>
          </section>

          <!-- —— 文档分类（高级模式，图05–11）: 文件构成 + 类别 + 分类模型。
               每个类别怎么提取（字段 / 引用技能 / 仅分类）在「字段提取」里按类别
               配置（2026-09-23 F-02）—— -->
          <section v-show="step === 'classify'" class="card-panel block step-panel">
            <h3 class="block-title">{{ $t('skillEditor.document_classification') }}</h3>
            <p class="dim cat-intro">
              {{ $t('skillEditor.when_a_file_contains_multiple_types_documents_classify_first') }}</p>

            <!-- B-01 (R07 图05): the layout decides whether a file is split at all -->
            <h4 class="sub-title">{{ $t('skillEditor.file_composition') }}</h4>
            <div class="cards layout-cards">
              <button v-for="l in LAYOUTS" :key="l.value" class="mode-card"
                      :class="{ on: docLayout === l.value }"
                      :data-testid="`layout-${l.value}`"
                      @click="pkg.document_layout = l.value">
                <strong>{{ l.label }}</strong>
                <span class="dim">{{ l.desc }}</span></button>
            </div>
            <p v-if="docLayout === 'single'" class="dim adv-note"
               data-testid="layout-single-note">
              {{ $t('skillEditor.the_entire_file_is_classified_as_a_single_document') }}</p>

            <h4 class="sub-title">{{ $t('skillEditor.categories') }}</h4>
            <div class="cat-list">
              <div v-for="(c, ci) in editableCats" :key="c.id" class="cat-card"
                   :class="{ other: c.is_other }">
                <div class="cat-head">
                  <template v-if="c.is_other">
                    <span class="cat-name">{{ $t('skillEditor.other_fallback_category') }}</span>
                  </template>
                  <template v-else>
                    <label class="cat-name-in">
                      {{ $t('skillEditor.category_name_doc_type') }}
                      <input v-model="c.doc_type" maxlength="64"
                             :placeholder="$t('skillEditor.e_g_invoice')" /></label>
                  </template>
                  <button v-if="!c.is_other" class="mini danger"
                          :title="$t('skillEditor.delete_this_category')"
                          @click="removeCategory(ci)">{{ $t('skillEditor.delete') }}</button>
                </div>
                <label class="cat-rec">{{ $t('skillEditor.recognition_description_the_model_uses_this_text_to_determine') }}
                  <textarea v-model="c.recognition_instruction" rows="2"
                            :placeholder="$t('skillEditor.e_g_contains_invoice_number_invoice_date_and_total')"></textarea></label>
                <div class="cat-extract">
                  <span class="dim">{{ $t('skillEditor.extract') }}{{ catSummary(c) }}</span>
                  <button class="mini" :data-testid="`cat-goto-${c.id}`"
                          @click="gotoCategoryFields(c)">{{ $t('skillEditor.configure_fields') }}</button>
                </div>
              </div>
            </div>
            <button class="add-cat" @click="addCategory">{{ $t('skillEditor.add_category') }}</button>

            <!-- F-01: the classification step names its model and what it inherits -->
            <h4 class="sub-title">{{ $t('skillEditor.classification_model') }}</h4>
            <label class="cls-model">
              <input :value="pkg.model_binding.classifier ?? ''" list="dl-providers"
                     :placeholder="$t('skillEditor.empty_same_as_extraction_model_select_from_dropdown_or')"
                     data-testid="classifier-model"
                     @focus="comboOpen" @blur="comboClose"
                     @input="comboTyped($event); pkg.model_binding.classifier = ($event.target as HTMLInputElement).value || null" />
              <span class="hint dim" data-testid="classifier-effective">
                {{ $t('skillEditor.in_use') }}{{ classifierEffective }}</span></label>

            <details class="rules-fold">
              <summary>{{ $t('skillEditor.additional_classification_rules_optional_applies_to_the_entire_classification') }}</summary>
              <textarea v-model="pkg.classification_rules" rows="2"
                        :placeholder="$t('skillEditor.e_g_page_1_is_usually_an_invoice_consecutive')"></textarea>
            </details>
          </section>

          <!-- —— 字段提取 ——
               standard: one field list. advanced (2026-09-23 F-02): one tab per
               category defined in 「文档分类」, each with its own extraction —
               fields of this skill, a referenced skill (read-only, with source,
               jump link and impact), or classification only. -->
          <section v-show="step === 'fields'" class="fields-step"
                   :class="{ 'sample-collapsed': sampleCollapsed, 'show-sample': mobilePane === 'sample' }"
                   data-testid="fields-workbench">
            <nav class="mobile-panes" :aria-label="$t('skillEditor.field_workspace')">
              <button :class="{ on: mobilePane === 'sample' }" @click="mobilePane = 'sample'">{{ $t('skillEditor.sample') }}</button>
              <button :class="{ on: mobilePane === 'fields' }" @click="mobilePane = 'fields'">{{ $t('skillEditor.fields') }} {{ curFields.length }}</button>
            </nav>
            <SamplePanel ref="samplePanel" :skill-code="isNew ? undefined : code"
                         :boxes="fieldTrialBoxes" :active-key="activeFieldName" :stage-pages="fieldTrialPages"
                         :active-page="fieldActivePage" class="sample-col"
                         @select="selectFieldSample" @pick="pickFieldBox"
                         @uploading="sampleUploading = $event"
                         @collapse="sampleCollapsed = $event" />
            <fieldset class="fields-col card-panel" :disabled="saving || fieldTrialStarting">
              <template v-if="activeCat">
                <nav class="cat-tabs" :aria-label="$t('skillEditor.configure_fields_by_category')">
                  <button v-for="c in editableCats" :key="c.id" class="cat-tab"
                          :class="{ on: activeCat.id === c.id }"
                          :data-testid="`cat-tab-${c.id}`"
                          @click="activeCatId = c.id">
                    {{ translateMessage(catName(c)) }} <span class="dim">{{ translateMessage(catBadge(c)) }}</span></button>
                </nav>
                <div class="cat-handler">
                  <span class="dim">{{ $t('skillEditor.extraction_method') }}</span>
                  <button class="hseg" :class="{ on: activeCat.handler === 'inline' }"
                          @click="setHandler(activeCat, 'inline')">{{ $t('skillEditor.configure_fields_within_this_skill') }}</button>
                  <button class="hseg" :class="{ on: activeCat.handler === 'existing_skill' }"
                          @click="setHandler(activeCat, 'existing_skill')">{{ $t('skillEditor.use_an_existing_skill') }}</button>
                  <button class="hseg" :class="{ on: activeCat.handler === 'classify_only' }"
                          @click="setHandler(activeCat, 'classify_only')">{{ $t('skillEditor.classify_only_no_extraction') }}</button>
                </div>
                <!-- F-01: the model / channel that actually runs for this category -->
                <p class="dim eff-line" data-testid="cat-effective">{{ catEffective(activeCat) }}</p>
              </template>

              <template v-if="!activeCat || activeCat.handler === 'inline'">
                <div class="field-heading">
                  <div>
                    <h3>{{ activeCat ? t('skillEditor.category_fields', { name: translateMessage(catName(activeCat)) }) : $t('skillEditor.field_extraction') }} <span class="field-count">{{ curFields.length }}</span></h3>
                    <p class="dim">{{ $t('skillEditor.referring_to_the_sample_on_the_left_define_the') }}</p>
                  </div>
                  <span class="save-state dim">{{ saving ? $t('skillEditor.saving') : dirty ? $t('skillEditor.unsaved_changes') : isNew ? $t('skillEditor.not_yet_created') : $t('skillEditor.saved') }}</span>
                </div>
                <div class="field-actions">
                  <button class="primary" data-testid="field-trial"
                          :disabled="!activeSample || !curFields.length || saving || fieldTrialStarting || sampleUploading || pgPolling || pgCreating || importing || enriching"
                          :title="!activeSample ? $t('skillEditor.upload_or_select_a_sample_first') : $t('skillEditor.save_the_current_definition_first_then_test_extract_the')"
                          @click="runFieldTrial">{{ fieldTrialStarting ? $t('skillEditor.saving_and_preparing_test_extraction') : fieldTrialRunning ? $t('skillEditor.test_extracting') : fieldTrial ? $t('skillEditor.re_run_test_extraction') : $t('skillEditor.test_extract_current_sample') }}</button>
                  <button @click="genPanel = !genPanel">{{ $t('skillEditor.generate_fields') }}</button>
                  <button @click="addInlineField">{{ $t('skillEditor.add') }}</button>
                  <details class="more-draft field-more">
                    <summary class="btn-like" :aria-label="$t('skillEditor.more_field_actions')">•••</summary>
                    <div class="more-pop">
                      <button :disabled="!undoStack.length" @click="undoGenerate">{{ $t('skillEditor.undo_last_generation') }}</button>
                      <label class="file-btn slim">
                        <input type="file" accept=".xlsx,.csv,.tsv" hidden :disabled="importing" @change="tableImport" />
                        <span class="btn-like">{{ importing ? $t('skillEditor.importing') : $t('skillEditor.import_table') }}</span>
                      </label>
                      <button class="mini" :disabled="!curFields.length || enriching"
                              :title="$t('skillEditor.expand_field_descriptions_into_full_extraction_instructions')"
                              @click="enrich">{{ enriching ? $t('skillEditor.completing') : $t('skillEditor.ai_complete_descriptions') }}</button>
                      <button class="danger" :disabled="!curFields.length" @click="clearFields">{{ $t('skillEditor.clear_all_fields') }}</button>
                    </div>
                  </details>
                </div>
                <GenerateFieldsPanel v-if="genPanel || !curFields.length"
                                     :key="`${activeCat?.id ?? 'standard'}:${activeSample?.id ?? ''}`"
                                     :sample-id="activeSample?.id" :sample-name="activeSample?.file_name"
                                     :existing-count="curFields.length"
                                     @apply="applyGenerated" @close="genPanel = false" />
                <div v-if="curFields.length" class="trial-banner" :class="{ stale: fieldTrialStale, failed: fieldTrialError }" role="status">
                  <span v-if="fieldTrialError">{{ fieldTrialError }}</span>
                  <span v-else-if="fieldTrialStarting">{{ $t('skillEditor.saving_current_field_definitions') }}</span>
                  <span v-else-if="fieldTrialRunning">{{ $t('skillEditor.test_extracting_d0784f') }}{{ activeSample?.file_name }}」…</span>
                  <span v-else-if="fieldTrialStale">{{ $t('skillEditor.the_definition_has_changed_the_results_below_may_be') }}</span>
                  <span v-else-if="fieldTrialReady">{{ $t('skillEditor.trial_extraction_complete') }} {{ fieldTrialFilled }}/{{ curFields.length }} {{ $t('skillEditor.fields_have_values') }}<span v-if="pgDuration != null"> · {{ (pgDuration / 1000).toFixed(1) }} {{ $t('skillEditor.seconds') }}</span></span>
                  <span v-else>{{ activeSample ? $t('skillEditor.run_a_trial_extraction_on_the_current_sample_to') : $t('skillEditor.upload_a_sample_to_run_a_trial_extraction_and') }}</span>
                  <select v-if="fieldTrialDocs.length > 1" v-model.number="fieldTrialDocIndex" :aria-label="$t('skillEditor.select_trial_extraction_document')">
                    <option v-for="(doc, di) in fieldTrialDocs" :key="doc.file_id" :value="di">{{ doc.doc_type || $t('skillEditor.document') }} {{ di + 1 }} · {{ doc.page_range }}</option>
                  </select>
                </div>
                <div v-if="curFields.length" class="field-list-head" aria-hidden="true"><span></span><span>{{ $t('skillEditor.fields_descriptions') }}</span><span>{{ $t('skillEditor.type') }}</span><span>{{ $t('skillEditor.trial_extraction_results') }}</span><span>{{ $t('skillEditor.status') }}</span><span></span><span></span></div>
                <div class="field-tree" ref="treeEl">
                  <FieldCard v-for="(f, i) in curFields" :key="`${activeCat?.id ?? 'standard'}:${i}`" :field="f" :index="i"
                             inline :expanded="expandedField === i" :active="activeFieldName === f.name"
                             :result="fieldValue(f.name)" :result-status="fieldResultStatus(f.name)"
                             :drag-from="reorder.from.value ?? -1" :drag-over="reorder.over.value ?? -1"
                             @select="selectInlineField(i)" @update="curFields.splice(i, 1, $event)"
                             @remove="removeInlineField(i)"
                             @grip-down="startFieldDrag" />
                  <button class="add-field" @click="addInlineField">{{ $t('skillEditor.add_field') }}</button>
                </div>
                <p v-if="curFields.length" class="dim drag-tip">{{ $t('skillEditor.drag_to_reorder_fields_click_a_field_to_expand') }}</p>
              </template>

              <!-- existing_skill: R10 reference (图16) + F-02 §3.18 source/jump/impact -->
              <div v-else-if="activeCat.handler === 'existing_skill'" class="cat-ref">
                <div class="ref-row">
                  <select :value="activeCat.skill_ref?.skill_code ?? ''"
                          @change="setRefSkill(activeCat, ($event.target as HTMLSelectElement).value)">
                    <option value="">{{ $t('skillEditor.select_a_skill_to_reuse') }}</option>
                    <option v-for="r in refSkills" :key="r.skill_code"
                            :value="r.skill_code">{{ r.name }}（{{ r.skill_code }}）</option>
                  </select>
                  <select v-if="activeCat.skill_ref?.skill_code"
                          :value="activeCat.skill_ref?.version ?? ''"
                          @change="setRefVersion(activeCat, ($event.target as HTMLSelectElement).value)">
                    <option value="">{{ $t('skillEditor.follow_latest_release') }}{{ refVersionHint(activeCat) }}</option>
                    <option v-for="v in refVersions(activeCat)" :key="v" :value="v">v{{ v }}</option>
                  </select>
                </div>
                <div v-if="activeCat.skill_ref?.skill_code" class="ref-banner">
                  <span class="dim">
                    {{ $t('skillEditor.field_source_references_skill') }}{{ refName(activeCat) }}」（{{ activeCat.skill_ref.skill_code }}{{ $t('skillEditor.read_only_here_to_modify_open_the_original_skill') }}</span>
                  <a class="btn-like" :href="`/#/skills/${activeCat.skill_ref.skill_code}`"
                     target="_blank">{{ $t('skillEditor.open_skill_editor') }}</a>
                </div>
                <p v-if="activeCat.skill_ref?.skill_code" class="dim ref-impact"
                   data-testid="ref-impact">{{ translateMessage(refImpact(activeCat)) }}</p>
                <ul v-if="refFields(activeCat).length" class="ref-fields">
                  <li v-for="f in refFields(activeCat)" :key="f.name">
                    <code>{{ f.name }}</code><span class="dim">{{ f.type }}</span>
                    <span class="ex dim" :title="f.instruction">{{ f.instruction }}</span>
                  </li>
                </ul>
              </div>

              <p v-else-if="activeCat.handler === 'classify_only'" class="dim pad">
                {{ $t('skillEditor.this_category_only_outputs_its_document_type_doc_type') }}</p>

              <label v-if="activeCat && !activeCat.is_other && activeCat.handler !== 'classify_only'"
                     class="cat-rules">{{ $t('skillEditor.category_specific_additional_rules_optional_if_filled_replaces') }}{{ activeCat.handler === 'inline' ? $t('skillEditor.the_general_additional_rules_below') : $t('skillEditor.original_skill_s_additional_rules') }}）
                <textarea v-model="activeCat.additional_rules" v-autosize rows="1"
                          :placeholder="$t('skillEditor.supplementary_notes_that_apply_only_to_this_category')"></textarea></label>

              <details v-if="!activeCat || activeCat.handler === 'inline'" class="rules-fold">
                <summary>{{ $t('skillEditor.more_settings_output_structure_additional_rules') }}</summary>
                <div class="output-shape"><span class="dim">{{ $t('skillEditor.output_structure') }}</span>
                  <div class="seg" role="group" :aria-label="$t('skillEditor.output_structure')">
                    <button :class="{ on: fieldShape !== 'list' }" @click="fieldShape = 'object'">Object</button>
                    <button :class="{ on: fieldShape === 'list' }" @click="fieldShape = 'list'">List</button>
                  </div>
                </div>
                <label class="dim">{{ activeCat ? $t('skillEditor.general_additional_rules_used_when_no_category_specific_rules') : $t('skillEditor.additional_rules') }}</label>
                <textarea v-model="pkg.additional_rules" v-autosize rows="2"
                          :placeholder="$t('skillEditor.e_g_amounts_always_keep_two_decimal_places_dates')"></textarea>
              </details>
              <p v-if="activeCat && pkg.fields.length" class="dim" data-testid="top-fields-note">
                {{ $t('skillEditor.there_are_also') }} {{ pkg.fields.length }} {{ $t('skillEditor.standard_mode_fields_they_take_effect_when_switching_back') }}</p>
            </fieldset>
          </section>

          <!-- —— 文件产出（9.15 WP6，图18-20） —— -->
          <section v-show="step === 'output'" class="card-panel block step-panel"
                   data-testid="output-step">
            <h3 class="block-title">{{ $t('skillEditor.file_output') }}</h3>
            <label class="chk">
              <input type="checkbox" data-testid="out-enabled"
                     :checked="pkg!.output?.enabled" @change="toggleOutput($event)" />
              {{ $t('skillEditor.allow_document_download') }}
            </label>
            <template v-if="pkg!.output?.enabled">
              <div class="out-actions">
                <button class="mode-card" data-testid="out-rename"
                        :class="{ on: pkg!.output?.action === 'rename' }"
                        :disabled="false"
                        @click="setOutputAction('rename')">
                  <b>{{ $t('skillEditor.rename_original_document') }}</b>
                  <span class="dim">{{ $t('skillEditor.produce_1_file_from_the_entire_original_per_naming') }}</span>
                </button>
                <button class="mode-card" data-testid="out-split"
                        :class="{ on: pkg!.output?.action === 'split' }"
                        :disabled="!splitAvailable"
                        :title="splitAvailable ? '' :
                          $t('skillEditor.split_download_is_only_available_in_advanced_mode_and')"
                        @click="setOutputAction('split')">
                  <b>{{ $t('skillEditor.split_into_sub_documents') }}</b>
                  <span class="dim">{{ splitAvailable
                    ? $t('skillEditor.produce_each_sub_document_separately_per_naming_rules') :
                    $t('skillEditor.split_download_is_only_available_when_the_file_contains') }}</span>
                </button>
              </div>
              <template v-if="pkg!.output?.action !== 'off'">
                <label class="lbl">{{ $t('skillEditor.naming_rules') }}</label>
                <input ref="outPatternInput" v-model="pkg!.output!.naming_rule"
                       data-testid="out-pattern" class="txt"
                       :placeholder="pkg!.output?.action === 'split'
                         ? '{doc_index}_{doc_type}_{original_name}{original_ext}'
                         : '{original_name}{original_ext}'"
                       @input="outQueuePreview" />
                <div class="token-row" data-testid="out-tokens">
                  <button v-for="t in outAvailableTokens" :key="t" class="tok"
                          :class="{ on: outTokens.includes(t) }"
                          :title="$t('skillEditor.insert_token', { token: '{' + t + '}' })"
                          @click="insertToken(t)">{{ "{" + t + "}" }}</button>
                </div>
                <div class="out-preview" data-testid="out-preview">
                  <span class="dim">{{ $t('skillEditor.preview') }}</span>
                  <code v-if="outPreview.ok">{{ outPreview.preview }}</code>
                  <span v-else class="err-text">{{ outErrors[0]?.message }}</span>
                  <span v-if="outPreview.appended_ext" class="dim">
                    {{ $t('skillEditor.auto_filled_original_ext') }}</span>
                </div>
                <label class="chk">
                  <input type="checkbox" data-testid="out-searchable"
                         :checked="pkg!.output?.searchable_pdf"
                         @change="pkg!.output!.searchable_pdf = ($event.target as HTMLInputElement).checked" />
                  {{ $t('skillEditor.convert_to_searchable_pdf_electronic_originals_output_as_is') }}
                </label>
              </template>
            </template>
          </section>
        </div>
      </template>

      <!-- —— 测试 tab：试运行 + 金样本回归（Playground 将在极速模式批次替换试运行） —— -->
      <template v-else>
        <div class="test-grid">
          <!-- —— 9.15 WP5 Playground (图21)；走查 #5/#12/#19/#26 修补 —— -->
          <section v-if="!isNew" class="card-panel block playground" data-testid="playground">
            <h3 class="block-title">{{ $t('skillEditor.playground_test_run') }}</h3>
            <p class="dim">{{ $t('skillEditor.run_the_current_definition_on_selected_samples_including_unpublished') }}</p>
            <div class="pg-grid">
              <div class="pg-left">
                <div class="pg-samples">
                  <input v-model="pgSearch" :placeholder="$t('skillEditor.search_samples')" />
                  <label class="file-btn"><input type="file" hidden @change="pgUpload" />
                    <span class="btn-like">{{ $t('skillEditor.upload_sample') }}</span></label>
                  <ul class="pg-list" data-testid="pg-samples">
                    <li v-for="s in pgFilteredSamples" :key="s.id">
                      <label>
                        <input type="checkbox" :value="s.id" v-model="pgSelected" />
                        <span class="pg-name">{{ s.file_name }}</span>
                        <small v-if="pgStatus(s.id)" class="pg-state">{{ pgStatus(s.id) }}</small>
                      </label>
                    </li>
                    <li v-if="!pgFilteredSamples.length" class="dim">{{ $t('skillEditor.no_samples_yet') }}</li>
                  </ul>
                  <button class="primary" data-testid="pg-run"
                          :disabled="!pgSelected.length || pgPolling || pgCreating || fieldTrialStarting" @click="pgRun">
                    {{ $t('skillEditor.run_test') }}{{ pgSelected.length }}）</button>
                </div>
                <!-- #26: switch between the files of one multi-sample run -->
                <div v-if="pgFiles.length > 1" class="pg-files" data-testid="pg-files">
                  <button v-for="f in pgFiles" :key="f.file_id" class="btn-like"
                          :class="{ on: f.file_id === pgActiveFileId }"
                          @click="pgSelectFile(f.file_id)">
                    {{ f.file_name }}</button>
                </div>
              </div>

              <div class="pg-stage-col">
                <!-- #26: page thumbnails + original preview (never for fast mode) -->
                <div class="pg-stage" data-testid="pg-stage">
                  <DocStage v-if="pgCurrent && pgStagePages.length && !pgFast"
                            :src="api.downloadUrl(pgStageFileId!)"
                            :file-name="pgCurrent.file_name" :pages="pgStagePages"
                            :active-box="pgActiveBox" :active-page="pgActivePage"
                            :annotate="false" :boxes="pgStageBoxes"
                            :active-key="pgActiveField ?? undefined"
                            @pick="pgPickField" />
                  <p v-else class="dim pg-stage-hint">
                    {{ !pgCurrent ? $t('skillEditor.after_running_view_the_original_and_field_highlights_here')
                       : (pgFast ? $t('skillEditor.turbo_mode_does_not_provide_location_highlighting')
                                 : $t('skillEditor.loading_original')) }}</p>
                </div>
              </div>

              <div class="pg-result" data-testid="pg-result">
                <div class="pg-top">
                  <strong v-if="pgCurrent">{{ pgCurrent.file_name }}</strong>
                  <span v-if="pgCurrent" class="chip" :class="`chip-${pgCurrent.status}`">
                    {{ stLabel(pgCurrent.status) }}</span>
                  <span v-if="pgRunStatus" class="chip" :class="`chip-${pgRunStatusChip}`"
                        data-testid="pg-run-status">{{ runLabel(pgRunStatus) }}</span>
                  <span v-if="pgDuration" class="dim" data-testid="pg-duration">
                    {{ pgDuration }} ms</span>
                  <button class="btn-like" :disabled="pgPolling || !pgCurrent"
                          data-testid="pg-rerun-file" @click="pgRunCurrent">
                    {{ $t('skillEditor.run_current_file') }}</button>
                  <button class="btn-like" @click="pgHistoryOpen = true; pgLoadHistory()"
                          data-testid="pg-history">{{ $t('skillEditor.run_history') }}</button>
                  <button v-if="pgCurrentRun" class="btn-like"
                          data-testid="pg-detail-btn" @click="pgOpenDetail">{{ $t('skillEditor.details') }}</button>
                </div>
                <p v-if="pgTimeoutNote" class="err-text" data-testid="pg-timeout">
                  {{ pgTimeoutNote }}</p>
                <p v-if="pgFast" class="dim">{{ $t('skillEditor.turbo_mode_does_not_provide_location_highlighting_results_have') }}</p>
                <div v-if="pgFileArtifacts.length" class="pg-arts" data-testid="pg-file-arts">
                  <span v-for="a in pgFileArtifacts" :key="a.artifact_id"
                        class="pg-art" :title="a.error ?? ''">
                    <button v-if="a.status === 'ready'" class="ghost"
                            @click="api.artifactDownload(a.artifact_id, a.name)">
                      {{ $t('skillEditor.output') }} {{ a.name }}</button>
                    <span v-else class="dim">{{ a.name }}{{ $t('skillEditor.failed') }}{{ a.error }}）</span>
                  </span>
                </div>
                <p v-if="!pgVisibleDocs.length" class="dim">{{ $t('skillEditor.view_extraction_results_here_after_running') }}</p>

                <div class="pg-result-head">
                  <strong>{{ $t('skillEditor.extraction_results_output_format_json') }}</strong>
                  <span class="pg-tabs">
                    <button class="btn-like" :disabled="!pgVisibleDocs.length"
                            :class="{ on: pgResultTab === 'all' }"
                            data-testid="pg-tab-all" @click="pgResultTab = 'all'">
                      {{ $t('skillEditor.all_fields') }}</button>
                    <button class="btn-like" :disabled="!pgVisibleDocs.length"
                            :class="{ on: pgResultTab === 'review' }"
                            data-testid="pg-tab-review" @click="pgResultTab = 'review'">
                      {{ $t('skillEditor.pending_review') }}{{ pgReviewCount }}）</button>
                  </span>
                  <button class="btn-like" :disabled="!pgVisibleDocs.length"
                          data-testid="pg-toggle-all"
                          @click="pgExpandAll = !pgExpandAll">
                    {{ pgExpandAll ? $t('skillEditor.collapse_all') : $t('skillEditor.expand_all') }}</button>
                </div>

                <div v-for="doc in pgVisibleDocs" :key="`${doc.file_id}-${doc.doc_index}`"
                     class="pg-doc">
                  <div class="pg-doc-head">
                    <strong>{{ $t('skillEditor.document') }} {{ doc.doc_index }}</strong>
                    <span v-if="doc.doc_type" class="chip">{{ doc.doc_type }}</span>
                    <span class="dim">{{ $t('skillEditor.page_af50a0') }} {{ doc.page_range }} {{ $t('skillEditor.page') }}</span>
                    <span v-if="doc.extraction_status === 'not_requested'" class="dim">
                      {{ $t('skillEditor.classification_only_no_fields_to_validate') }}</span>
                  </div>
                  <table v-if="pgRowsOf(doc).length" class="pg-fields">
                    <tbody>
                      <tr v-for="[k, v] in pgRowsOf(doc)" :key="k"
                          :class="{ on: pgActiveField === String(k) }"
                          @click="pgPickDocField(doc, String(k))">
                        <td class="pg-field-name">{{ k }}
                          <span v-if="doc.field_sources?.[String(k)]" class="dim">{{ translateMessage(sourceLabel(doc.field_sources[String(k)])) }}</span>
                          <span v-if="doc.review_fields.includes(String(k))"
                                class="badge-r">{{ $t('skillEditor.pending_review_6c42f7') }}</span></td>
                        <td>
                          <span v-if="pgIsTable(v)" class="pg-table-grid"
                                :data-testid="`pg-table-${k}`">
                            <span v-for="(row, ri) in pgTableRows(v)" :key="ri"
                                  class="pg-table-row">
                              <span v-for="(cv, ck) in row" :key="ck" class="pg-cell">
                                <b class="dim">{{ ck }}</b>{{ cv === null || cv === "" ? "—" : cv }}
                              </span>
                            </span>
                          </span>
                          <span v-else-if="pgIsList(v)" class="pg-json">{{
                            JSON.stringify(v) }}</span>
                          <span v-else>{{ v === null || v === "" ? "—" : v }}</span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                  <p v-else-if="doc.extraction_status !== 'not_requested' &&
                                pgResultTab === 'all'" class="dim">{{ $t('skillEditor.no_results') }}</p>
                  <p v-else-if="pgResultTab === 'review'" class="dim">{{ $t('skillEditor.no_fields_pending_review') }}</p>
                  <div v-if="doc.artifacts?.length" class="pg-arts" data-testid="pg-arts">
                    <span v-for="a in doc.artifacts" :key="a.artifact_id"
                          class="pg-art" :title="a.error ?? ''">
                      <button v-if="a.status === 'ready'" class="ghost"
                              @click="api.artifactDownload(a.artifact_id, a.name)">
                        ⤓ {{ a.name }}</button>
                      <span v-else class="dim">{{ a.name }}{{ $t('skillEditor.failed') }}{{ a.error }}）</span>
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <AppModal v-if="pgHistoryOpen" wide @close="pgHistoryOpen = false">
              <div class="pg-modal" data-testid="pg-history-modal">
                <h4>{{ $t('skillEditor.run_history') }}</h4>
                <table v-if="pgHistory.length">
                  <thead><tr><th>{{ $t('skillEditor.sample') }}</th><th>{{ $t('skillEditor.version') }}</th><th>{{ $t('skillEditor.status') }}</th><th>{{ $t('skillEditor.duration') }}</th><th>{{ $t('skillEditor.requested_by') }}</th><th>{{ $t('skillEditor.time') }}</th></tr></thead>
                  <tbody>
                    <tr v-for="r in pgHistory" :key="r.run_id" class="pg-hist-row"
                        @click="pgLoadRun(r.run_id)">
                      <td>{{ pgSampleName(r.sample_id) }}</td><td>v{{ r.version }}</td>
                      <td>{{ runLabel(r.status) }}</td>
                      <td>{{ r.duration_ms ?? "—" }}</td>
                      <td>{{ translateMessage(initiatorLabel(r.created_by)) }}</td>
                      <td class="dim">{{ r.created_at.slice(0, 19).replace("T", " ") }}</td>
                    </tr>
                  </tbody>
                </table>
                <p v-else class="dim">{{ $t('skillEditor.no_run_records_yet') }}</p>
                <div class="modal-actions">
                  <button @click="pgHistoryOpen = false">{{ $t('skillEditor.close') }}</button>
                </div>
              </div>
            </AppModal>

            <AppModal v-if="pgDetailOpen" @close="pgDetailOpen = false">
              <div class="pg-modal" data-testid="pg-detail">
                <h4>{{ $t('skillEditor.run_details') }}</h4>
                <p v-if="pgDetailLoading" class="dim">{{ $t('skillEditor.loading_run_details') }}</p>
                <p v-else-if="pgDetailError" class="err-text">{{ pgDetailError }}</p>
                <ul v-else-if="pgDetail" class="pg-detail">
                  <I18nT keypath="skillEditor.skill_version_rich" tag="li" scope="global"><template #version><strong>v{{ pgDetail.version }}</strong></template></I18nT>
                  <li>{{ $t('skillEditor.processing_mode_e8a3c4') }}{{ pgDetail.processing_mode === "fast" ? $t('skillEditor.fast') : $t('skillEditor.balanced') }}</li>
                  <li>{{ $t('skillEditor.status_86344f') }}{{ runLabel(pgDetail.status) }}</li>
                  <li>{{ $t('skillEditor.duration_fe45f1') }}{{ pgDetail.duration_ms ?? "—" }} ms</li>
                  <li>Transaction ID：<code>{{ pgDetail.transaction_id }}</code></li>
                  <li>File ID：<code>{{ pgDetail.file_id }}</code></li>
                </ul>
                <div class="modal-actions">
                  <button @click="pgDetailOpen = false">{{ $t('skillEditor.close') }}</button>
                </div>
              </div>
            </AppModal>
          </section>

          <section class="card-panel block">
            <h3 class="block-title">{{ $t('skillEditor.model_comparison_multi_model_side_by_side_trial_run') }}</h3>
            <p class="dim">{{ $t('skillEditor.trial_run_the_current_definition_on_a_sample_and') }}</p>
            <input v-model="dryProviders" list="dl-providers"
                   :placeholder="$t('skillEditor.model_list_comma_separated_empty_default')" />
            <label class="file-btn"><input type="file" hidden @change="dryRun" :disabled="running" />
              <span class="btn-like">{{ running ? $t('skillEditor.trial_running') : $t('skillEditor.upload_sample_and_trial_run') }}</span></label>
            <div v-if="dryRuns.length" class="runs">
              <div v-for="r in dryRuns" :key="r.provider" class="run">
                <div class="run-head">
                  <strong>{{ r.provider }}</strong>
                  <span v-if="r.ok && r.vision_pages" class="vis">🖼 {{ r.vision_pages }} {{ $t('skillEditor.page_image') }}</span>
                  <span v-if="r.ok" class="dim">
                    {{ r.usage?.prompt_tokens }}+{{ r.usage?.completion_tokens }} tokens</span>
                  <span v-else class="rulefail">{{ r.error }}</span>
                </div>
                <table v-if="r.ok && r.result">
                  <tbody>
                    <tr v-for="(cell, name) in scalarCells(r.result)" :key="name">
                      <td class="dim">{{ name }}</td>
                      <td>{{ cell.$value || "—" }}</td>
                      <td><span class="conf">{{ cell.$confidence === null ? $t('skillEditor.unscored') : `c${cell.$confidence}` }}</span></td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          <section v-if="!isNew" class="card-panel block">
            <h3 class="block-title">{{ $t('skillEditor.golden_sample_regression_release_gate') }}</h3>
            <p class="dim">{{ $t('skillEditor.attach_fixed_samples_with_expected_values_and_run_regression') }}</p>
            <label class="file-btn"><input type="file" hidden @change="pickGolden" />
              <span class="btn-like">{{ goldenFile ? goldenFile.name : $t('skillEditor.select_sample_file') }}</span></label>
            <textarea v-model="goldenExpected" rows="3"
                      :placeholder="$t('skillEditor.expected_values_json_e_g_invoice_no_inv_1')"></textarea>
            <button :disabled="!goldenFile" @click="addGolden">{{ $t('skillEditor.attach_golden_sample') }}</button>
            <button v-if="selectedVersion" @click="goldenCheck" :disabled="checking">
              {{ checking ? $t('skillEditor.running_regression') : t('skillEditor.run_regression_version', { version: selectedVersion ?? "" }) }}</button>
            <div v-if="goldenReport" class="golden-report">
              <template v-if="goldenReport.samples === 0">
                <p class="dim">{{ goldenReport.note }}</p>
              </template>
              <template v-else>
                <I18nT keypath="skillEditor.golden_summary_rich" tag="p" scope="global">
                  <template #samples><strong>{{ goldenReport.samples }}</strong></template>
                  <template #rate><strong :class="{ warn: (goldenReport.avg_match_rate ?? 0) < 1 }">{{ Math.round((goldenReport.avg_match_rate ?? 0) * 100) }}%</strong></template>
                </I18nT>
                <div v-for="(rep, i) in goldenReport.reports" :key="i" class="rep">
                  <template v-if="rep.ok">
                    <span class="dim">{{ $t('skillEditor.sample') }} {{ i + 1 }}{{ $t('skillEditor.matched') }} {{ Math.round((rep.match_rate ?? 0) * 100) }}%</span>
                    <div v-for="(d, fname) in rep.diffs" :key="fname" class="rulefail">
                      {{ fname }}{{ $t('skillEditor.expected') }}{{ d.expected }}{{ $t('skillEditor.got') }}{{ d.got }}」</div>
                  </template>
                  <span v-else class="rulefail">{{ $t('skillEditor.sample') }} {{ i + 1 }}：{{ rep.error }}</span>
                </div>
              </template>
            </div>
          </section>
        </div>
      </template>
    </div>

    <SkillApiModal v-if="apiModal" :skill-code="code" @close="apiModal = false" />
  </main>
  <main v-else class="editor"><Skeleton :rows="8" /></main>
</template>

<script setup lang="ts">
import { I18nT, useI18n } from "vue-i18n";
import { translateMessage, type DisplayMessage } from "../i18n";
const { t } = useI18n();

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { onBeforeRouteLeave, useRouter } from "vue-router";
import { api, downloadFile, type Artifact, type CategorySpec,
         type DryRunEntry, type FieldCell,
         type FieldSpec, type SkillPackage, type TxnDocument } from "../api";
import AppModal from "../components/AppModal.vue";
import DocStage from "../components/DocStage.vue";
import FieldCard from "../components/FieldCard.vue";
import GenerateFieldsPanel from "../components/GenerateFieldsPanel.vue";
import SamplePanel from "../components/SamplePanel.vue";
import SkillApiModal from "../components/SkillApiModal.vue";
import Skeleton from "../components/Skeleton.vue";
import { RUN_STATUS_LABELS, STATUS_LABELS, VERSION_LABELS,
         initiatorLabel } from "../labels";
import { vAutosize } from "../autosize";
import { copyText } from "../clipboard";
import { moveItem, useListReorder } from "../reorder";
import { toast } from "../toast";
// R1: where a rules_first field value came from (rule / model / review)
const sourceLabel = (s: string): DisplayMessage => {
  const key = ({ rule: "skillEditor.source_rule", model: "skillEditor.source_model",
    review: "skillEditor.source_review" } as Record<string, string>)[s];
  return key ? { key } : s;
};

const props = defineProps<{ code: string }>();
const router = useRouter();
const isNew = computed(() => props.code === "new");

// provider/parser catalogue from the server (never hardcoded)
const providerOptions = ref<{ name: string; model: string; active: boolean;
                              custom: boolean; vision: boolean }[]>([]);
const parserOptions = ref<{ name: string; type: string; description: string | null }[]>([]);
const activeProvider = computed(() => providerOptions.value.find((p) => p.active)?.name ?? "");
api.skillOptions()
  .then((o) => { providerOptions.value = o.providers; parserOptions.value = o.parsers; })
  .catch(() => { /* pickers degrade to free text, which still works */ });

const pkg = ref<SkillPackage | null>(null);
const versions = ref<{ version: number; status: string; changelog: string;
                       created_at?: string }[]>([]);
const selectedVersion = ref<number | null>(null);
const changelog = ref("");
const apiModal = ref(false);
const selectedStatus = computed(() =>
  versions.value.find((v) => v.version === selectedVersion.value)?.status ?? null);

// —— WP3 editor state ——
const tab = ref<"design" | "test">("design");
const step = ref<"basic" | "classify" | "fields" | "output">("basic");
const genPanel = ref(false);
const samplePanel = ref<InstanceType<typeof SamplePanel>>();
const skillNameInput = ref<HTMLInputElement>();
const skillCodeInput = ref<HTMLInputElement>();
const activeSample = ref<{ id: string; file_name: string } | null>(null);
const sampleUploading = ref(false);
const sampleCollapsed = ref(false);
const mobilePane = ref<"sample" | "fields">("fields");
const expandedField = ref<number | null>(null);
const activeFieldName = ref<string | null>(null);
const fieldActivePage = ref(1);
const fieldTrialStarting = ref(false);
const fieldTrial = ref<{ sampleId: string; txnId: string; signature: string } | null>(null);
const fieldTrialDocIndex = ref(0);
const fieldTrialStage = ref<Record<string, unknown>>({});
const fieldTrialPages = ref<{ page_no: number; width: number; height: number }[]>([]);
const generatedExamples = ref<Record<string, Record<string, unknown>>>({});
let skillEpoch = 0;
let loadRequest = 0;
let runRequest = 0;
let preservedCreatedCode: string | null = null;
// one-shot undo for generation merges; `cat` = the category whose list it was
// (null = top-level fields), so switching tabs never restores into the wrong list
const undoStack = ref<{ cat: string | null; fields: FieldSpec[]; exampleKey: string;
                       examples: Record<string, unknown> }[]>([]);

function blankPkg(): SkillPackage {
  return { skill_code: "", name: "", description: "", kind: "extract", doc_type_hint: "",
           system_prompt: "", fields: [], few_shot: [], validators: [],
           review_policy: { mode: "auto", confidence_threshold: 2 },
           model_binding: { extractor: "", fallback: null, challenger: null },
           parser: null, additional_rules: "",
           schema_version: 2, skill_mode: "standard", output_shape: "object" };
}
function blankField(name = ""): FieldSpec {
  return { name, type: "string", instruction: "", mode: "verbatim", required: false,
           anchor_hints: [], enum_values: [], columns: [] };
}
const verLabel = (s: string) => VERSION_LABELS()[s] ?? s;
const shortDate = (v?: string) =>
  v ? new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
const splitCsv = (v: string) => v.split(/[,，]/).map((s) => s.trim()).filter(Boolean);

/** #14 (走查): the route view is reused between /skills/:code values, so every
 *  piece of per-skill state must be reset on switch — otherwise the previous
 *  skill's version note (实测: written into the NEW skill's v1) and Playground
 *  results leak across skills. */
/** #21: fields held by the categories of an advanced skill. */
function catFieldCount(p: SkillPackage): number {
  return (p.categories ?? []).reduce((n, c) => n + (c.fields?.length ?? 0), 0);
}

function resetPerSkillState() {
  skillEpoch++;
  loadRequest++;
  runRequest++;
  activeSample.value = null;
  sampleUploading.value = false;
  sampleCollapsed.value = false;
  mobilePane.value = "fields";
  expandedField.value = null;
  activeFieldName.value = null;
  fieldTrial.value = null;
  fieldTrialStarting.value = false;
  fieldTrialStage.value = {};
  fieldTrialPages.value = [];
  generatedExamples.value = {};
  genPanel.value = false;
  changelog.value = "";
  step.value = "basic";
  activeCatId.value = null;
  sampleStatus.value = {};
  runBySample.value = {};
  pgSamples.value = [];
  pgDocs.value = [];
  pgFiles.value = [];
  pgHistory.value = [];
  pgDetail.value = null;
  pgDetailOpen.value = false;
  pgDetailLoading.value = false;
  pgDetailError.value = "";
  pgCurrent.value = null;
  pgCurrentRun.value = null;
  pgCurrentTxnId.value = null;
  pgRunStatus.value = "";
  pgRunDuration.value = null;
  pgTimeoutNote.value = "";
  pgSelected.value = [];
  pgResultTab.value = "all";
  pgActiveField.value = null;
  pgActiveBox.value = null;
  pgStagePages.value = [];
  pgStageResult.value = {};
  dryRuns.value = [];
  undoStack.value = [];
  if (pgTimer) { clearInterval(pgTimer); pgTimer = null; }
  pgPolling.value = false;
  pgCreating.value = false;
  importing.value = false;
  enriching.value = false;
  saving.value = false;
  preservedCreatedCode = null;
  savedSnapshot.value = "";
}

async function load(version?: number) {
  const skillCode = props.code;
  const request = ++loadRequest;
  if (isNew.value) {
    pkg.value = blankPkg();
    versions.value = [];
    selectedVersion.value = null;
    return true;
  }
  try {
    const d = await api.skillDetail(skillCode, version);
    if (props.code !== skillCode || request !== loadRequest) return false;
    pkg.value = { ...blankPkg(), ...(d.latest_package ?? {}),
                  skill_code: d.skill_code, name: d.latest_package?.name || d.name };
    // the detail dump of a v1 row fills schema_version with the Loose default 1;
    // the editor always works on (and saves) a v2 working copy — §5.2, the
    // editor is the in-place migration path
    pkg.value.schema_version = 2;
    versions.value = d.versions;
    selectedVersion.value = d.selected_version ?? null;
    changelog.value = d.versions.find((v) => v.version === d.selected_version)?.changelog ?? "";
    return true;
  } catch (e) { if (request === loadRequest) toast.error(e); return false; }
}
watch(() => props.code, (code, prev) => {
  // Creating a draft changes the URL, but keeps this editing session and its
  // adopted sample alive so the requested trial can continue on the same file.
  if (prev === "new" && code === preservedCreatedCode) {
    preservedCreatedCode = null;
    return;
  }
  // #14: reset only on a real switch — the immediate (first) call runs during
  // setup, before the Playground refs below are initialised.
  if (prev !== undefined) resetPerSkillState();
  load();
}, { immediate: true });
function selectVersion(v: number) {
  if (saving.value || fieldTrialStarting.value) return;
  fieldTrial.value = null;
  void load(v);
}

function goto(k: string) {
  step.value = k as typeof step.value;
}

// —— 处理模式 (9.15 WP5, 图02–05): switching to fast keeps the saved review /
// advanced config intact (greyed but not wiped); switching back restores it ——
const fastMaxPages = 5;   // mirror of IDP_FAST_MAX_PAGES default (server enforces)
// —— 9.15 WP6: output step (图18-20) ——
const splitAvailable = computed(() => {
  const p = pkg.value;
  return !!p && p.skill_mode === "advanced"
    && (p.document_layout ?? "single") !== "single";
});

function setOutputAction(a: "rename" | "split") {
  const p = pkg.value;
  if (!p?.output) return;
  if (!p.output.enabled) p.output.enabled = true;   // clicking an action enables
  p.output.action = a;
  if (!p.output.naming_rule) {
    p.output.naming_rule = a === "split"
      ? "{doc_index}_{doc_type}_{original_name}{original_ext}"
      : "{original_name}{original_ext}";
  }
}

function toggleOutput(e: Event) {
  const p = pkg.value;
  if (!p?.output) return;
  const on = (e.target as HTMLInputElement).checked;
  p.output.enabled = on;
  if (on && p.output.action === "off") setOutputAction("rename");
}

const outPatternInput = ref<HTMLInputElement>();
const outPreview = ref<{ ok: boolean; preview: string; appended_ext: boolean }>(
  { ok: true, preview: "", appended_ext: false });
const outErrors = ref<{ message: string }[]>([]);
const outTokens = ref<string[]>([]);          // variables the rule uses
const outAvailableTokens = ref<string[]>([]); // D4: every usable variable
let outPreviewTimer: ReturnType<typeof setTimeout> | null = null;

function outSampleData(): Record<string, unknown> {
  // prefer the latest Playground result (§WP6), else blank placeholders
  const d = pgDocs.value?.[0]?.data;
  if (Array.isArray(d)) return {};
  if (d && typeof d === "object") return d as Record<string, unknown>;
  return {};
}

function outQueuePreview() {
  if (outPreviewTimer) clearTimeout(outPreviewTimer);
  outPreviewTimer = setTimeout(outRunPreview, 300);
}

async function outRunPreview() {
  const p = pkg.value;
  if (!p?.output) return;
  const rule = p.output.naming_rule ||
    (p.output.action === "split"
      ? "{doc_index}_{doc_type}_{original_name}{original_ext}"
      : "{original_name}{original_ext}");
  const raw = (outPatternInput.value?.value ?? rule);
  const pattern = /\{original_ext\}/.test(raw) || /\{data\./.test(raw)
    ? raw : raw + "{original_ext}";
  try {
    // #13/D4: the server composes the available-variable list and resolves the
    // sample values (its latest Playground run, else a ‹field› placeholder),
    // so the frontend never invents either.
    const r = await api.namingPreview({
      pattern, action: p.output.action ?? "rename",
      searchable_pdf: p.output.searchable_pdf,
      fields: outScalarFieldNames(),
      sample_id: pgSelected.value[0],
      sample: { original_name: translateMessage(outSampleName()), original_ext: ".pdf",
                doc_type: p.skill_mode === "advanced"
                  ? (p.categories?.[0]?.doc_type ?? "") : "",
                doc_index: p.output.action === "split" ? 2 : undefined,
                data: outSampleData() } });
    outPreview.value = { ok: r.ok, preview: r.preview,
                         appended_ext: r.appended_ext };
    outErrors.value = r.errors;
    outTokens.value = r.tokens;
    outAvailableTokens.value = r.available_tokens ?? r.tokens;
  } catch { /* preview failures never block editing */ }
}

/** #13/D4: every scalar field the naming rule may reference — top-level plus
 *  (advanced) each category's, since a category field can be referenced too. */
function outScalarFieldNames(): string[] {
  const p = pkg.value;
  if (!p) return [];
  const names: string[] = [];
  const push = (f: { name?: string; type?: string }) => {
    if (f?.name && f.type !== "table" && !names.includes(f.name)) {
      names.push(f.name);
    }
  };
  (p.fields ?? []).forEach(push);
  (p.categories ?? []).forEach((c) => (c.fields ?? []).forEach(push));
  return names;
}

/** the sample name shown in the preview: the selected sample when there is one */
function outSampleName(): DisplayMessage {
  const s = pgSamples.value.find((x) => x.id === pgSelected.value[0]);
  return s?.file_name ?? { key: "skillEditor.sample_scan_filename" };
}

function insertToken(t: string) {
  const el = outPatternInput.value;
  const p = pkg.value;
  if (!p?.output) return;
  const tag = `{${t}}`;
  if (!el) { p.output.naming_rule += tag; return; }
  const start = el.selectionStart ?? el.value.length;
  const end = el.selectionEnd ?? start;
  el.value = el.value.slice(0, start) + tag + el.value.slice(end);
  p.output.naming_rule = el.value;
  el.focus();
  el.setSelectionRange(start + tag.length, start + tag.length);
  outQueuePreview();
}

watch(() => pkg.value?.output?.naming_rule, outQueuePreview);
watch(() => pkg.value?.output?.searchable_pdf, outQueuePreview);
watch(() => pkg.value?.output?.action, outQueuePreview);
// entering the step (or loading a package) seeds tokens + preview once
watch(() => [step.value, pkg.value?.skill_code], ([st]) => {
  if (st === "output") outQueuePreview();
});

function setProcessingMode(m: "balanced" | "fast") {
  const p = pkg.value;
  if (!p || m === p.processing_mode) return;
  if (m === "fast") {
    const topFields = (p.fields ?? []).length > 0;
    if (p.skill_mode === "advanced" && !topFields) {
      toast.error(t('skillEditor.please_switch_to_standard_extraction_and_configure_fields_first'));
      return;
    }
    if (p.skill_mode === "advanced" && topFields) {
      toast.ok(t('skillEditor.fast_mode_extracts_by_standard_fields_advanced_classification_settings'));
    }
  }
  p.processing_mode = m;
}

// —— 文档分类 (R08, 图06–11): categories live on the package; Other is a fixed
// sentinel at the bottom (doc_type stays "Other") ——
const refSkills = ref<{ skill_code: string; name: string; published_version: number;
                        fields: { name: string; type: string; instruction: string }[];
                        extractor?: string; extraction_channel?: string }[]>([]);
watch([tab, pkg], async ([t]) => {
  if (t !== "design" || refSkills.value.length) return;
  try {
    refSkills.value = (await api.referenceSkills(props.code)).skills;
  } catch { /* picker degrades to empty; existing refs still render */ }
}, { immediate: true });

function blankCategory(): CategorySpec {
  return { id: `cat_${Math.random().toString(36).slice(2, 8)}`, doc_type: "",
           recognition_instruction: "", is_other: false, handler: "inline",
           fields: [], validators: [], additional_rules: "",
           output_shape: "object", skill_ref: null };
}
const editableCats = computed(() => {
  const cats = pkg.value?.categories ?? (pkg.value!.categories = []);
  if (!cats.some((c) => c.is_other)) cats.push({ ...blankCategory(), id: "Other",
    doc_type: "Other", is_other: true, handler: "classify_only" });
  return cats;
});
function addCategory() {
  // Other stays the bottom sentinel: new categories go in above it
  const cats = pkg.value!.categories!;
  const other = cats.findIndex((c) => c.is_other);
  cats.splice(other < 0 ? cats.length : other, 0, blankCategory());
}
function removeCategory(ci: number) {
  const cats = pkg.value!.categories!;
  if (cats[ci]?.is_other) return;
  cats.splice(ci, 1);
}
function setHandler(c: CategorySpec, h: CategorySpec["handler"]) {
  c.handler = h;
  if (h === "existing_skill" && !c.skill_ref) c.skill_ref = { skill_code: "", version: null };
}
function setRefSkill(c: CategorySpec, code: string) {
  c.skill_ref = code ? { skill_code: code, version: null } : null;
}
function setRefVersion(c: CategorySpec, v: string) {
  if (!c.skill_ref) return;
  const n = Number(v);
  c.skill_ref.version = v === "" || Number.isNaN(n) ? null : n;
}
function refMeta(c: CategorySpec) {
  return refSkills.value.find((r) => r.skill_code === c.skill_ref?.skill_code);
}
function refName(c: CategorySpec) {
  return refMeta(c)?.name ?? c.skill_ref?.skill_code;
}
function refVersionHint(c: CategorySpec) {
  const m = refMeta(c);
  return m ? t('skillEditor.current_version', { version: m.published_version }) : "";
}
function refVersions(c: CategorySpec): number[] {
  const m = refMeta(c);
  return m ? [m.published_version] : [];
}
function refFields(c: CategorySpec) {
  return refMeta(c)?.fields ?? [];
}

/** R07 图05 / B-01 (2026-09-23): 文件构成 — how the classifier cuts a file into
 *  documents (app/extraction/classifier.py). The backend default is single,
 *  which never splits; the editor used to offer no way to change it. */
const LAYOUTS = computed(() => [
  { value: "single", label: t('skillEditor.layout_single'), desc: t('skillEditor.layout_single_description') },
  { value: "mixed", label: t('skillEditor.layout_mixed'), desc: t('skillEditor.layout_mixed_description') },
  { value: "same_type_independent", label: t('skillEditor.layout_same_type_independent'),
    desc: t('skillEditor.layout_same_type_independent_description') },
  { value: "same_type_continuous", label: t('skillEditor.layout_same_type_continuous'),
    desc: t('skillEditor.layout_same_type_continuous_description') },
] as const);
const docLayout = computed(() => pkg.value?.document_layout ?? "single");
/** The 高级 card promises "一份文件多种单据": entering advanced mode from the
 *  default layout starts at 多种单据混合 so that promise holds (B-01). */
function setSkillMode(m: "standard" | "advanced") {
  const p = pkg.value;
  if (!p) return;
  if (m === "advanced" && p.skill_mode !== "advanced"
      && (p.document_layout ?? "single") === "single") p.document_layout = "mixed";
  p.skill_mode = m;
}

// —— 字段提取 by category (F-02): the tab being edited. Fast mode executes as
// standard, so it keeps editing the top-level list like a standard skill ——
const activeCatId = ref<string | null>(null);
const activeCat = computed<CategorySpec | null>(() => {
  const p = pkg.value;
  if (!p || p.skill_mode !== "advanced" || p.processing_mode === "fast") return null;
  const cats = editableCats.value;
  return cats.find((c) => c.id === activeCatId.value) ?? cats[0] ?? null;
});
/** The list the field tools edit: the active inline category's, else the
 *  skill's top-level fields. */
const curFields = computed<FieldSpec[]>(() =>
  activeCat.value?.handler === "inline" ? activeCat.value.fields : pkg.value?.fields ?? []);
function setCurFields(v: FieldSpec[]) {
  if (activeCat.value?.handler === "inline") activeCat.value.fields = v;
  else pkg.value!.fields = v;
}
const fieldShape = computed<"object" | "list">({
  get: () => (activeCat.value ?? pkg.value!).output_shape ?? "object",
  set: (v) => { (activeCat.value ?? pkg.value!).output_shape = v; },
});
const catName = (c: CategorySpec): DisplayMessage =>
  c.is_other ? { key: "skillEditor.other_category" }
    : (c.doc_type || { key: "skillEditor.unnamed_category" });
function catBadge(c: CategorySpec): DisplayMessage {
  if (c.handler === "existing_skill") return { key: "skillEditor.reference_badge" };
  if (c.handler === "classify_only") return { key: "skillEditor.classification_only_badge" };
  return { key: "skillEditor.fields_badge", params: { count: c.fields.length } };
}
function catSummary(c: CategorySpec): string {
  if (c.handler === "existing_skill")
    return c.skill_ref?.skill_code ? t('skillEditor.reference_skill', { name: refName(c) ?? "" }) : t('skillEditor.reference_skill_unselected');
  if (c.handler === "classify_only") return t('skillEditor.classification_without_extraction');
  return t('skillEditor.inline_fields', { count: c.fields.length });
}
function gotoCategoryFields(c: CategorySpec) {
  activeCatId.value = c.id;
  step.value = "fields";
}

// —— F-01: which model / channel actually runs, spelled out in the editor ——
const channelLabel = (c?: string | null): DisplayMessage => ({
  key: c === "rules_first" ? "skillEditor.channel_rules_first" : "skillEditor.channel_model",
});
function modelLabel(name?: string | null): string {
  return name || t('skillEditor.platform_default_model', { model: activeProvider.value ? ` (${activeProvider.value})` : "" });
}
const classifierEffective = computed(() => {
  const mb = pkg.value!.model_binding;
  return mb.classifier || t('skillEditor.same_as_extraction_model', { model: modelLabel(mb.extractor) });
});
/** Mirrors app/skillengine/effective.category_subpackage. */
function catEffective(c: CategorySpec): string {
  const p = pkg.value!;
  if (c.handler === "classify_only") return t('skillEditor.effective_classify_only');
  if (c.handler === "existing_skill") {
    const m = refMeta(c);
    if (!m) return t('skillEditor.select_reference_for_configuration');
    return t('skillEditor.effective_reference_configuration', { name: m.name, model: modelLabel(m.extractor), channel: translateMessage(channelLabel(m.extraction_channel)) });
  }
  return t('skillEditor.effective_inline_configuration', { model: modelLabel(p.model_binding.extractor), channel: translateMessage(channelLabel(p.extraction_channel)) });
}
/** F-02 §3.18: what changing the referenced skill does to this one (R10
 *  pinning: a publish freezes the referenced version; drafts follow latest). */
function refImpact(c: CategorySpec): DisplayMessage {
  const version = c.skill_ref?.version;
  return version != null ? { key: "skillEditor.pinned_reference_impact", params: { version } }
    : { key: "skillEditor.latest_reference_impact" };
}

// —— Playground (9.15 WP5, 图21): sample test runs against the current draft ——
interface PgSample { id: string; file_name: string; skill_code: string | null; created_at: string }
const pgSamples = ref<PgSample[]>([]);
const pgSearch = ref("");
const pgSelected = ref<string[]>([]);
watch(() => pgSelected.value[0], outQueuePreview);
const pgPolling = ref(false);
const pgCreating = ref(false);
const pgHistoryOpen = ref(false);
const pgDetailOpen = ref(false);
const pgDetailLoading = ref(false);
const pgDetailError = ref("");
const pgHistory = ref<{ run_id: string; sample_id: string; version: number;
                        status: string; duration_ms: number | null;
                        created_by: string; created_at: string }[]>([]);
const pgDetail = ref<{ version: number; processing_mode: string; status: string;
                       duration_ms: number | null; transaction_id: string;
                       file_id: string | null } | null>(null);
const pgDocs = ref<TxnDocument[]>([]);
const pgFiles = ref<{ file_id: string; file_name: string; status: string;
                      artifacts: Artifact[];
                      documents: TxnDocument[] }[]>([]);
const pgCurrent = ref<{ file_id: string; file_name: string; status: string } | null>(null);
const pgCurrentRun = ref<string | null>(null);
const pgCurrentMode = ref<string>("balanced");
const pgRunStatus = ref<string>("");          // D2 vocabulary, from the server
const pgRunDuration = ref<number | null>(null);
const pgTimeoutNote = ref<string>("");
const pgCurrentTxnId = ref<string | null>(null);
const pgResultTab = ref<"all" | "review">("all");
const pgExpandAll = ref(true);
const pgActiveField = ref<string | null>(null);
const pgActiveBox = ref<number[] | null>(null);
const pgActivePage = ref<number>(1);
const pgStagePages = ref<{ page_no: number; width: number; height: number }[]>([]);
const pgStageResult = ref<Record<string, unknown>>({});
const runBySample = ref<Record<string, { runId: string; txnId: string; fileId?: string }>>({});
let pgTimer: ReturnType<typeof setInterval> | null = null;
let pgStartedAt = 0;
const PG_POLL_LIMIT_MS = 10 * 60 * 1000;      // #5: poll cap, never forever
const pgFast = computed(() => pgCurrentMode.value === "fast");
const pgActiveFileId = computed(() => pgCurrent.value?.file_id ?? null);
const pgStageFileId = computed(() => pgActiveFileId.value);
// D3 (#8): the run's own file-level artifacts (rename output lives here)
const pgFileArtifacts = computed(() =>
  pgFiles.value.find((f) => f.file_id === pgActiveFileId.value)?.artifacts ?? []);
// #5: results belong to the SELECTED file (multi-sample runs share one txn)
const pgVisibleDocs = computed(() => {
  const fid = pgActiveFileId.value;
  // Advanced-mode documents have CHILD file IDs. Their root file's grouping
  // is the contract that associates all split documents with this sample.
  return fid ? pgFiles.value.find((f) => f.file_id === fid)?.documents ?? [] : pgDocs.value;
});
const pgReviewCount = computed(() =>
  pgVisibleDocs.value.reduce((n, d) => n + (d.review_fields?.length ?? 0), 0));
// duration comes from the server-derived run record so the top bar, the
// history list and the detail dialog can never disagree (#6)
const pgDuration = computed(() => pgRunDuration.value);
const pgRunStatusChip = computed(() =>
  pgRunStatus.value === "failed" ? "error"
    : pgRunStatus.value === "completed" ? "completed"
      : pgRunStatus.value === "needs_review" ? "pending_verification" : "processing");
const pgStageBoxes = computed(() => {
  const out: { key: string; label: string; page: number; bbox: number[] }[] = [];
  for (const [k, v] of Object.entries(pgStageResult.value ?? {})) {
    const cell = v as Record<string, unknown>;
    const bbox = cell?.$bbox as number[] | undefined;
    const page = cell?.$pages as string | undefined;
    if (bbox?.length === 4 && page) {
      out.push({ key: k, label: k, page: Number(page), bbox });
    }
  }
  return out;
});

function stLabel(status: string): string { return STATUS_LABELS()[status] ?? status; }
function runLabel(status: string): string { return RUN_STATUS_LABELS()[status] ?? status; }
function pgIsTable(v: unknown): boolean {
  return Array.isArray(v) && v.length > 0 && typeof v[0] === "object"
    && !Array.isArray(v[0]);
}
function pgTableRows(v: unknown): Record<string, unknown>[] {
  return Array.isArray(v) ? (v as Record<string, unknown>[]) : [];
}
function pgIsList(v: unknown): boolean { return Array.isArray(v); }
function pgRowsOf(doc: TxnDocument): [string, unknown][] {
  const data = doc.data;
  if (!data || Array.isArray(data)) return [];
  const rows = Object.entries(data as Record<string, unknown>);
  if (pgResultTab.value === "review") {
    return rows.filter(([k]) => doc.review_fields.includes(String(k)));
  }
  if (!pgExpandAll.value) return rows.slice(0, 1);
  return rows;
}
const pgFilteredSamples = computed(() => pgSamples.value.filter(
  (s) => !pgSearch.value || s.file_name.toLowerCase().includes(pgSearch.value.toLowerCase())));

const sampleStatus = ref<Record<string, DisplayMessage>>({});
function pgStatus(sampleId: string): string {
  return translateMessage(sampleStatus.value[sampleId] ?? "");
}

watch([tab, () => props.code], async ([t]) => {
  if (t !== "test" || pgSamples.value.length) return;
  try {
    pgSamples.value = (await api.studioSamples(props.code)).samples;
  } catch { /* samples panel degrades to empty */ }
}, { immediate: true });

async function pgUpload(e: Event) {
  const input = e.target as HTMLInputElement;
  if (!input.files?.length) return;
  try {
    await api.studioUploadSample(input.files[0], props.code);
    pgSamples.value = (await api.studioSamples(props.code)).samples;
    toast.ok(t('skillEditor.sample_uploaded'));
  } catch (err) {
    toast.error(t('skillEditor.sample_upload_error', { error: String(err instanceof Error ? err.message : err) }));
  }
  input.value = "";
}

async function pgRun() {
  await startPlaygroundRun([...pgSelected.value]);
}

async function startPlaygroundRun(sampleIds: string[], version?: number): Promise<string | null> {
  if (!sampleIds.length || pgPolling.value || pgCreating.value) return null;
  const skillCode = props.code;
  const request = ++runRequest;
  pgCreating.value = true;
  try {
    const r = await api.studioCreateRun({ skill_code: skillCode,
                                         ...(version != null ? { version } : {}),
                                         sample_ids: sampleIds });
    if (props.code !== skillCode || request !== runRequest) return null;
    pgCurrentRun.value = r.runs[0]?.run_id ?? null;
    pgCurrentTxnId.value = r.transaction_id;
    pgCurrent.value = null;
    pgDocs.value = [];
    pgFiles.value = [];
    pgDetail.value = null;
    pgDetailOpen.value = false;
    pgDetailLoading.value = false;
    pgDetailError.value = "";
    pgRunStatus.value = "queued";
    pgCurrentMode.value = pkg.value?.processing_mode ?? "balanced";
    pgRunDuration.value = null;
    pgStagePages.value = [];
    pgStageResult.value = {};
    for (const run of r.runs) {
      runBySample.value[run.sample_id] = { runId: run.run_id, txnId: r.transaction_id,
                                         fileId: run.file_id ?? undefined };
      sampleStatus.value[run.sample_id] = { key: "skillEditor.sample_running" };
    }
    pgPollTransaction(r.transaction_id);
    return r.transaction_id;
  } catch (err) {
    if (request === runRequest) toast.error(t('skillEditor.run_error', { error: String(err instanceof Error ? err.message : err) }));
    return null;
  } finally {
    if (request === runRequest) pgCreating.value = false;
  }
}

function pgPollTransaction(txnId: string) {
  pgPolling.value = true;
  pgTimeoutNote.value = "";
  pgStartedAt = Date.now();
  if (pgTimer) clearInterval(pgTimer);
  let inFlight = false;
  pgTimer = setInterval(async () => {
    if (inFlight) return;
    // #5: a test run never reaches the review queue, so `pending_verification`
    // IS a finished state here; and the poll stops after 10 minutes instead of
    // spinning forever with the results pane empty.
    if (Date.now() - pgStartedAt > PG_POLL_LIMIT_MS) {
      pgStopPolling();
      pgTimeoutNote.value =
        t('skillEditor.run_timed_out_still_not_finished_after_10_minutes');
      return;
    }
    inFlight = true;
    try {
      const st = await api.txnStatus(txnId);
      if (pgCurrentTxnId.value !== txnId) return;
      pgFiles.value = st.files.map((f) => ({
        file_id: f.file_id, file_name: f.file_name, status: f.status,
        artifacts: [], documents: [] }));
      const root = st.files[0];
      if (root) {
        // stage data (coordinates + page dims) for the selected/在跑 file
        const target = pgCurrent.value?.file_id
          && st.files.some((f) => f.file_id === pgCurrent.value!.file_id)
          ? pgCurrent.value.file_id : root.file_id;
        pgCurrent.value = { file_id: target,
                            file_name: (st.files.find((f) => f.file_id === target)
                                        ?? root).file_name,
                            status: (st.files.find((f) => f.file_id === target)
                                     ?? root).status };
      }
      pgUpdateSampleStatuses(st.files, txnId);
      const terminal = st.files.every((f) =>
        ["completed", "passed", "pending_verification", "error", "rejected", "split"]
          .includes(f.status));
      if (terminal) {
        pgStopPolling();
        await pgLoadDocuments(txnId);
        if (pgCurrentTxnId.value !== txnId) return;
        await pgLoadRunState(txnId);
        if (pgCurrentTxnId.value !== txnId) return;
        pgLoadHistory();
      }
    } catch (err) {
      if (pgCurrentTxnId.value !== txnId) return;
      pgStopPolling();
      pgTimeoutNote.value = t('skillEditor.test_state_error', { error: String(err instanceof Error ? err.message : err) });
    } finally {
      inFlight = false;
    }
  }, 2000);
}

function pgStopPolling() {
  if (pgTimer) clearInterval(pgTimer);
  pgTimer = null;
  pgPolling.value = false;      // #5: the run button is usable again
}

/** #5: per-sample state from that sample's OWN file, not the whole batch. */
function pgUpdateSampleStatuses(files: { file_id: string; status: string }[],
                                txnId: string) {
  for (const [sid, r] of Object.entries(runBySample.value)) {
    if (r.txnId !== txnId) continue;
    const fileId = r.fileId
      ?? files.find((f) => f.file_id !== pgCurrent.value?.file_id)?.file_id;
    const f = files.find((x) => x.file_id === fileId) ?? files[0];
    if (!f) continue;
    r.fileId = f.file_id;
    const reviews = pgDocsForFile(f.file_id).reduce(
      (n, d) => n + (d.review_fields?.length ?? 0), 0);
    sampleStatus.value[sid] = sampleStatusMessage(f.status, reviews);
  }
}

function sampleStatusMessage(status: string, reviews: number): DisplayMessage {
  if (["error", "rejected"].includes(status)) return { key: "skillEditor.sample_failed" };
  if (status === "pending_verification" || status === "split" && reviews > 0)
    return reviews ? { key: "skillEditor.sample_review_count", params: { count: reviews } }
      : { key: "skillEditor.sample_needs_review" };
  if (["split", "completed", "passed"].includes(status)) return { key: "skillEditor.sample_completed" };
  return { key: "skillEditor.sample_running" };
}

function pgDocsForFile(fileId: string): TxnDocument[] {
  return pgFiles.value.find((f) => f.file_id === fileId)?.documents ?? [];
}

function pgSelectFile(fileId: string) {
  const f = pgFiles.value.find((x) => x.file_id === fileId);
  if (!f) return;
  pgCurrent.value = { file_id: f.file_id, file_name: f.file_name,
                      status: f.status };
  pgActiveField.value = null;
  pgActiveBox.value = null;
  const run = Object.values(runBySample.value).find((r) => r.fileId === fileId);
  if (run) pgCurrentRun.value = run.runId;
  if (pgCurrentTxnId.value) void pgLoadRunState(pgCurrentTxnId.value);
  void pgLoadStage(pgCurrentRun.value ? pgCurrentTxnId.value : null, fileId);
}

async function pgLoadDocuments(txnId: string) {
  try {
    const d = await api.txnDocuments(txnId);
    if (pgCurrentTxnId.value !== txnId) return;
    pgFiles.value = d.files.map((f) => ({
      file_id: f.file_id, file_name: f.file_name, status: f.status,
      artifacts: f.artifacts ?? [], documents: f.documents }));
    pgDocs.value = d.files.flatMap((f) => f.documents);
    if (!pgCurrent.value || !d.files.some((f) => f.file_id === pgCurrent.value!.file_id)) {
      const first = d.files[0];
      if (first) {
        pgCurrent.value = { file_id: first.file_id, file_name: first.file_name,
                            status: first.status };
      }
    }
    const active = pgCurrent.value?.file_id;
    for (const [sid, r] of Object.entries(runBySample.value)) {
      if (r.txnId !== txnId || r.fileId) continue;
      const f = d.files.find((x) => !Object.values(runBySample.value)
        .some((o) => o !== r && o.fileId === x.file_id));
      if (f) r.fileId = f.file_id;
    }
    pgUpdateSampleStatuses(d.files, txnId);
    pgCurrentMode.value =
      pgDocs.value.some((x) => x.metrics?.parse_route === "vision") ? "fast" : pgCurrentMode.value;
    if (pgCurrent.value && !pgFast.value) await pgLoadStage(txnId, active);
  } catch (err) {
    if (pgCurrentTxnId.value !== txnId) return;
    pgTimeoutNote.value = t('skillEditor.result_load_error', { error: String(err instanceof Error ? err.message : err) });
    throw err;
  }
}

/** #26: highlight needs the FULL result (with $bbox) and page dimensions —
    /review carries both; the documents view stays a clean-value contract. */
async function pgLoadStage(txnId: string | null, fileId: string | undefined) {
  if (!fileId || pgFast.value) { pgStagePages.value = []; return; }
  try {
    const d = await api.detail(fileId);
    if (pgCurrentTxnId.value !== txnId || pgCurrent.value?.file_id !== fileId) return;
    pgStagePages.value = d.pages ?? [];
    pgStageResult.value = (d.result ?? {}) as Record<string, unknown>;
  } catch {
    if (pgCurrentTxnId.value === txnId && pgCurrent.value?.file_id === fileId) pgStagePages.value = [];
  }
}

async function pgLoadRunState(txnId: string) {
  const activeFile = pgCurrent.value?.file_id;
  const entries = Object.values(runBySample.value)
    .filter((r) => r.txnId === txnId);
  const entry = entries.find((r) => r.fileId === activeFile) ?? entries[0];
  const runId = entry?.runId ?? pgCurrentRun.value;
  if (!runId) return;
  try {
    const d = await api.studioRun(runId);
    if (pgCurrentTxnId.value !== txnId || pgCurrent.value?.file_id !== activeFile) return;
    pgCurrentRun.value = runId;
    pgDetail.value = d;
    pgRunStatus.value = d.status;
    pgRunDuration.value = d.duration_ms;
    pgCurrentMode.value = d.processing_mode;
  } catch { /* the run record is best-effort */ }
}

async function pgOpenDetail() {
  const runId = pgCurrentRun.value;
  if (!runId || pgDetailLoading.value) return;
  pgDetailOpen.value = true;
  pgDetailLoading.value = true;
  pgDetailError.value = "";
  try {
    const detail = await api.studioRun(runId);
    if (pgCurrentRun.value === runId) pgDetail.value = detail;
  } catch (err) {
    if (pgCurrentRun.value === runId) pgDetailError.value = t('skillEditor.detail_load_error', { error: String(err instanceof Error ? err.message : err) });
  } finally {
    if (pgCurrentRun.value === runId) pgDetailLoading.value = false;
  }
}

function pgPickField(key: string) {
  pgActiveField.value = key;
  const cell = pgStageResult.value?.[key] as Record<string, unknown> | undefined;
  pgActiveBox.value = (cell?.$bbox as number[]) ?? null;
  pgActivePage.value = Number(cell?.$pages ?? 1) || 1;
}

function pgPickDocField(doc: TxnDocument, key: string) {
  pgActiveField.value = key;
  const cell = pgStageResult.value?.[key] as Record<string, unknown> | undefined;
  pgActiveBox.value = (cell?.$bbox as number[]) ?? null;
  const page = Number(cell?.$pages ?? 1) || 1;
  // 高级模式：子文档的页码映射回原件（source_pages）
  const src = doc.source_pages ?? [];
  pgActivePage.value = src.length >= page ? src[page - 1] : page;
  if (!cell) pgActiveBox.value = null;
}

/** #26: 「运行」— re-run just the file on screen. */
async function pgRunCurrent() {
  const fileId = pgCurrent.value?.file_id;
  const sid = Object.entries(runBySample.value)
    .find(([, r]) => r.fileId === fileId)?.[0];
  if (!sid) { toast.error(t('skillEditor.no_sample_found_for_this_file_please_select_a')); return; }
  pgSelected.value = [sid];
  await pgRun();
}

async function pgLoadHistory() {
  const skillCode = props.code;
  try {
    const history = await api.studioRuns({ skill_code: skillCode });
    if (props.code === skillCode) pgHistory.value = history.runs;
  } catch { /* history is best-effort */ }
}

function pgSampleName(id: string): string {
  return pgSamples.value.find((s) => s.id === id)?.file_name ?? id;
}

async function pgLoadRun(runId: string) {
  pgHistoryOpen.value = false;
  pgStopPolling();
  pgCurrentTxnId.value = null;
  pgCurrentRun.value = runId;
  pgDetailLoading.value = false;
  try {
    const d = await api.studioRun(runId);
    if (pgCurrentRun.value !== runId) return;
    pgDetail.value = d;
    pgCurrentTxnId.value = d.transaction_id;
    pgRunStatus.value = d.status;          // same source as the history row (#6)
    pgRunDuration.value = d.duration_ms;
    pgCurrentMode.value = d.processing_mode;
    pgDetailOpen.value = true;
    await pgLoadDocuments(d.transaction_id);
    if (pgCurrentRun.value !== runId) return;
    if (d.file_id) {
      const f = pgFiles.value.find((x) => x.file_id === d.file_id);
      if (f && pgCurrent.value?.file_id !== f.file_id) pgSelectFile(f.file_id);
    }
  } catch (err) {
    toast.error(t('skillEditor.run_load_error', { error: String(err instanceof Error ? err.message : err) }));
  }
}

// The field workbench uses the same run/polling pipeline as the test tab. Its
// small context binds values to the selected sample and the saved definition.
const fieldTrialMatches = computed(() => !!fieldTrial.value
  && fieldTrial.value.sampleId === activeSample.value?.id
  && fieldTrial.value.txnId === pgCurrentTxnId.value);
const fieldTrialRunning = computed(() => fieldTrialMatches.value && pgPolling.value);
const fieldTrialStale = computed(() => fieldTrialMatches.value
  && fieldTrial.value!.signature !== JSON.stringify(pkg.value));
const fieldTrialDocs = computed(() => {
  if (!fieldTrialMatches.value) return [];
  const cat = activeCat.value;
  return cat ? pgVisibleDocs.value.filter((d) => d.category_id === cat.id
    || (!d.category_id && d.doc_type === cat.doc_type)) : pgVisibleDocs.value;
});
const fieldTrialDoc = computed(() => fieldTrialDocs.value[fieldTrialDocIndex.value]
  ?? fieldTrialDocs.value[0]);
const fieldTrialError = computed(() => {
  if (!fieldTrialMatches.value || fieldTrialRunning.value) return "";
  if (pgTimeoutNote.value) return pgTimeoutNote.value;
  if (pgRunStatus.value === "failed") return t('skillEditor.test_extraction_failed_please_check_the_field_configuration_or');
  if (fieldTrialDoc.value?.error) return fieldTrialDoc.value.error;
  if (["completed", "needs_review"].includes(pgRunStatus.value) && !fieldTrialDocs.value.length)
    return activeCat.value ? t('skillEditor.this_category_was_not_recognized_in_the_current_sample') : t('skillEditor.this_test_extraction_returned_no_field_results');
  return "";
});
const fieldTrialReady = computed(() => fieldTrialMatches.value && !fieldTrialRunning.value
  && !fieldTrialError.value && !!fieldTrialDoc.value);
const exampleScope = computed(() => `${activeCat.value?.id ?? 'standard'}:${activeSample.value?.id ?? ''}`);

function trialValue(name: string): unknown {
  const data = fieldTrialDoc.value?.data;
  if (Array.isArray(data)) return data.map((row) => (row as Record<string, unknown> | null)?.[name]);
  return data?.[name];
}
function fieldValue(name: string): unknown {
  return fieldTrialReady.value ? trialValue(name) : generatedExamples.value[exampleScope.value]?.[name];
}
function hasFieldValue(value: unknown): boolean {
  if (value == null || value === "") return false;
  if (Array.isArray(value)) return value.some(hasFieldValue);
  if (typeof value === "object") return Object.values(value).some(hasFieldValue);
  return true; // 0 and false are valid extracted values.
}
const fieldTrialFilled = computed(() => curFields.value.filter((f) => hasFieldValue(trialValue(f.name))).length);
function fieldResultStatus(name: string): string {
  if (fieldTrialRunning.value || fieldTrialStarting.value) return "running";
  if (fieldTrialReady.value) {
    if (fieldTrialStale.value) return "stale";
    if (!hasFieldValue(trialValue(name))) return "missing";
    if (fieldTrialDoc.value?.review_fields?.includes(name)
        || fieldTrialDoc.value?.field_sources?.[name] === "review") return "review";
    return fieldTrialBoxes.value.some((b) => b.key === name) ? "success" : "extracted";
  }
  return Object.prototype.hasOwnProperty.call(generatedExamples.value[exampleScope.value] ?? {}, name)
    ? "generated" : "idle";
}
const fieldStageResult = computed(() => fieldTrialDoc.value?.file_id === pgCurrent.value?.file_id
  ? pgStageResult.value : fieldTrialStage.value);
const fieldTrialBoxes = computed(() => {
  if (!fieldTrialReady.value || fieldTrialStale.value || pgFast.value) return [];
  const sourcePages = fieldTrialDoc.value?.source_pages ?? [];
  return Object.entries(fieldStageResult.value).flatMap(([key, value]) => {
    const cell = value as Record<string, unknown> | null;
    const bbox = cell?.$bbox;
    const page = Number(cell?.$pages);
    if (!Array.isArray(bbox) || bbox.length !== 4 || !bbox.every(Number.isFinite)
        || !Number.isFinite(page) || page < 1) return [];
    return [{ key, label: key, page: sourcePages[page - 1] ?? page, bbox: bbox as number[] }];
  });
});
watch(() => [fieldTrial.value?.txnId, fieldTrialDoc.value?.file_id] as const, async ([txnId, fileId]) => {
  fieldTrialStage.value = {};
  fieldTrialPages.value = [];
  if (!fileId || !txnId || !fieldTrialMatches.value || pgFast.value) return;
  try {
    // Root page dimensions stay aligned with the original sample. Split child
    // results map their local page numbers through source_pages above.
    const d = await api.detail(fileId);
    if (fieldTrial.value?.txnId !== txnId || fieldTrialDoc.value?.file_id !== fileId) return;
    fieldTrialStage.value = (d.result ?? {}) as Record<string, unknown>;
    fieldTrialPages.value = fileId === pgCurrent.value?.file_id ? d.pages ?? [] : pgStagePages.value;
  } catch { /* values remain useful when no coordinates are available */ }
});
watch(pgStagePages, (pages) => {
  if (fieldTrialMatches.value && fieldTrialDoc.value?.file_id !== pgCurrent.value?.file_id)
    fieldTrialPages.value = pages;
});
watch(() => activeCat.value?.id, () => {
  expandedField.value = null;
  activeFieldName.value = null;
  fieldTrialDocIndex.value = 0;
  genPanel.value = false;
});

function selectFieldSample(sample: { id: string; file_name: string } | null) {
  if (sample?.id !== activeSample.value?.id) {
    if (fieldTrialMatches.value) {
      pgStopPolling();
      pgCurrentTxnId.value = null;
    }
    if (fieldTrialStarting.value) { runRequest++; pgCreating.value = false; }
    fieldTrial.value = null;
    fieldTrialStage.value = {};
    fieldTrialPages.value = [];
    fieldTrialDocIndex.value = 0;
    activeFieldName.value = null;
    fieldActivePage.value = 1;
  }
  activeSample.value = sample;
}
function selectInlineField(index: number) {
  expandedField.value = expandedField.value === index ? null : index;
  activeFieldName.value = curFields.value[index]?.name ?? null;
  const box = fieldTrialBoxes.value.find((b) => b.key === activeFieldName.value);
  if (box) fieldActivePage.value = box.page;
}
function pickFieldBox(key: string) {
  const index = curFields.value.findIndex((f) => f.name === key);
  if (index < 0) return;
  expandedField.value = index;
  activeFieldName.value = key;
  mobilePane.value = "fields";
}
function addInlineField() {
  curFields.value.push(blankField());
  expandedField.value = curFields.value.length - 1;
  activeFieldName.value = null;
  mobilePane.value = "fields";
}
function removeInlineField(index: number) {
  curFields.value.splice(index, 1);
  expandedField.value = null;
  activeFieldName.value = null;
}
async function runFieldTrial() {
  if (!activeSample.value || !curFields.value.length || saving.value
      || fieldTrialStarting.value || sampleUploading.value || pgPolling.value || pgCreating.value) return;
  const epoch = skillEpoch;
  const sampleId = activeSample.value.id;
  fieldTrialStarting.value = true;
  try {
    if (!await save() || epoch !== skillEpoch || activeSample.value?.id !== sampleId) return;
    if (selectedVersion.value == null) return;
    const signature = JSON.stringify(pkg.value);
    const txnId = await startPlaygroundRun([sampleId], selectedVersion.value);
    if (!txnId || epoch !== skillEpoch || activeSample.value?.id !== sampleId) return;
    fieldTrial.value = { sampleId, txnId, signature };
    fieldTrialDocIndex.value = 0;
    fieldTrialStage.value = {};
    activeFieldName.value = null;
  } finally {
    if (epoch === skillEpoch) fieldTrialStarting.value = false;
  }
}

// —— flow rail: nodes with live subtitles; bad dots from light client checks ——
const flowNodes = computed(() => {
  const p = pkg.value!;
  const badFields = p.fields.some((f) => !f.name.trim());
  const badCats = p.skill_mode === "advanced"
    && !(p.categories ?? []).some((c) => c.is_other);
  return [
    (() => {   // #27: say what is actually configured, not "已配置"
      const m = p.review_policy.mode;
      const th = p.review_policy.confidence_threshold;
      const sub = m === "never" ? t('skillEditor.no_review')
        : m === "always" ? t('skillEditor.review_all')
          : t('skillEditor.review_below_threshold', { threshold: th ?? 2 });
      return { key: "basic" as const, label: t('skillEditor.basics'), sub, bad: false };
    })(),
    ...(p.skill_mode === "advanced"
      ? [{ key: "classify" as const, label: t('skillEditor.document_classification'),
           sub: t('skillEditor.category_layout_summary', { count: (p.categories ?? []).length, layout: LAYOUTS.value.find((l) => l.value === (p.document_layout ?? "single"))?.label ?? "" }),
           bad: badCats }]
      : []),
    { key: "fields" as const, label: t('skillEditor.field_extraction'),
      // #21: an advanced skill keeps its fields inside the categories, so the
      // subtitle must count those (it used to read "0 个字段")
      sub: p.skill_mode === "advanced"
        ? t('skillEditor.category_field_summary', { categories: (p.categories ?? []).length, fields: catFieldCount(p) })
        : t('skillEditor.field_count_summary', { count: p.fields.length, shape: p.output_shape === "list" ? " · List" : "" }),
      bad: badFields },
    (() => {   // 9.15 WP6 (图18-20): subtitle counts enabled output items
      const o = p.output;
      if (!o?.enabled || o.action === "off") {
        return { key: "output" as const, label: t('skillEditor.output_files'),
                 sub: t('skillEditor.download_disabled'), bad: false };
      }
      const n = 1 + (o.searchable_pdf ? 1 : 0);   // an action + maybe searchable
      return { key: "output" as const, label: t('skillEditor.output_files'),
               sub: t('skillEditor.enabled_output_count', { count: n }), bad: false };
    })(),
  ];
});

// —— dirty tracking: unsaved edits ask before leaving (R? UX guard § WP3) ——
const savedSnapshot = ref("");
function snapshotOf(): string {
  if (!pkg.value) return "";
  return JSON.stringify({ p: pkg.value, c: changelog.value });
}
watch(pkg, (v) => { if (v && !savedSnapshot.value) savedSnapshot.value = snapshotOf(); },
      { deep: false, immediate: true });
watch([pkg, changelog], () => { markDirty(); }, { deep: true });
const dirty = ref(false);
function markDirty() {
  if (!pkg.value) return;
  dirty.value = savedSnapshot.value !== snapshotOf();
}
function markClean() { savedSnapshot.value = snapshotOf(); dirty.value = false; }

function beforeUnload(ev: BeforeUnloadEvent) {
  if (dirty.value) { ev.preventDefault(); ev.returnValue = ""; }
}
onMounted(() => window.addEventListener("beforeunload", beforeUnload));
onBeforeUnmount(() => {
  skillEpoch++;
  loadRequest++;
  runRequest++;
  window.removeEventListener("beforeunload", beforeUnload);
  pgStopPolling();
  pgCurrentTxnId.value = null;
  pgCurrentRun.value = null;
});
onBeforeRouteLeave(() => {
  if (!dirty.value) return true;
  // synchronous confirm is the only browser-native option here
  return window.confirm(t('skillEditor.leave_unsaved_confirmation'));
});

// —— field order (P07) ——
const treeEl = ref<HTMLElement>();
const reorder = useListReorder();
function startFieldDrag(i: number, ev: PointerEvent) {
  reorder.start(i, ev, treeEl.value,
                (f, t) => {
                  if (pkg.value) moveItem(curFields.value, f, t);
                  expandedField.value = null;
                });
}

// —— generation (R05/图13): snapshot → apply suggested fields → undoable ——
function pushUndo() {
  undoStack.value.push({ cat: activeCat.value?.id ?? null,
                         fields: JSON.parse(JSON.stringify(curFields.value)),
                         exampleKey: exampleScope.value,
                         examples: { ...generatedExamples.value[exampleScope.value] } });
}
function applyGenerated(fields: FieldSpec[], replaceAll: boolean, examples: Record<string, unknown> = {}) {
  if (!pkg.value) return;
  pushUndo();
  genPanel.value = false;
  expandedField.value = null;
  activeFieldName.value = null;
  if (replaceAll) {
    setCurFields(fields.map((f) => ({ ...blankField(), ...f })));
    generatedExamples.value[exampleScope.value] = Object.fromEntries(
      fields.filter((f) => Object.prototype.hasOwnProperty.call(examples, f.name))
        .map((f) => [f.name, examples[f.name]]));
    return;
  }
  const existing = new Set(curFields.value.map((f) => f.name));
  const fresh = fields.filter((f) => !existing.has(f.name));
  const skipped = fields.length - fresh.length;
  curFields.value.push(...fresh.map((f) => ({ ...blankField(), ...f })));
  generatedExamples.value[exampleScope.value] = {
    ...generatedExamples.value[exampleScope.value],
    ...Object.fromEntries(fresh.filter((f) => Object.prototype.hasOwnProperty.call(examples, f.name))
      .map((f) => [f.name, examples[f.name]])),
  };
  if (skipped) toast.ok(t('skillEditor.skipped_duplicate_fields', { count: skipped }));
}
function undoGenerate() {
  const last = undoStack.value.pop();
  if (!pkg.value || !last) return;
  if (last.cat) {
    const cat = pkg.value.categories?.find((c) => c.id === last.cat);
    if (!cat) { toast.error(t('skillEditor.this_category_has_been_deleted_and_cannot_be_undone')); return; }
    cat.fields = last.fields;
    activeCatId.value = cat.id;
  } else {
    pkg.value.fields = last.fields;
  }
  generatedExamples.value[last.exampleKey] = last.examples;
  expandedField.value = null;
  activeFieldName.value = null;
  toast.ok(t('skillEditor.last_generation_undone'));
}
function clearFields() {
  if (!pkg.value || !curFields.value.length) return;
  if (!confirm(t('skillEditor.clear_fields_confirmation', { count: curFields.value.length }))) return;
  pushUndo();
  setCurFields([]);
  generatedExamples.value[exampleScope.value] = {};
  expandedField.value = null;
}

/** Save = update the draft on screen (P04). Outbound payloads are always v2. */
const saveLabel = computed(() =>
  selectedStatus.value && selectedStatus.value !== "draft" ? t('skillEditor.save_as_new_draft') : t('skillEditor.save'));
const saving = ref(false);
function outbound(): SkillPackage {
  return JSON.parse(JSON.stringify({ ...pkg.value!, schema_version: 2 }));
}

function validPkg(): boolean {
  if (!pkg.value) return false;
  if (!pkg.value.name.trim() || !pkg.value.skill_code.trim()) {
    const missingName = !pkg.value.name.trim();
    toast.error(missingName ? t('skillEditor.please_enter_a_skill_name_before_saving_or_testing') : t('skillEditor.please_enter_skill_code_before_saving_or_testing'));
    tab.value = "design";
    step.value = "basic";
    void nextTick(() => (missingName ? skillNameInput.value : skillCodeInput.value)?.focus());
    return false;
  }
  if (!pkg.value.fields.length && pkg.value.skill_mode !== "advanced") {
    toast.error(t('skillEditor.define_at_least_one_field')); return false;
  }
  const checkFields = (fields: FieldSpec[]): boolean => {
    const names = new Set<string>();
    for (const field of fields) {
      if (!field.name.trim()) { toast.error(t('skillEditor.please_fill_in_all_field_names')); step.value = "fields"; return false; }
      if (names.has(field.name.trim())) { toast.error(t('skillEditor.duplicate_field_name', { name: field.name })); step.value = "fields"; return false; }
      names.add(field.name.trim());
      if (field.type === "table" && !checkFields(field.columns ?? [])) return false;
    }
    return true;
  };
  return checkFields(pkg.value.fields) && (pkg.value.categories ?? [])
    .every((cat) => cat.handler !== "inline" || checkFields(cat.fields));
}

async function save(): Promise<boolean> {
  if (saving.value || sampleUploading.value || importing.value || enriching.value || !validPkg() || !pkg.value) return false;
  const epoch = skillEpoch;
  const payload = outbound();
  const skillCode = props.code;
  saving.value = true;
  try {
    if (isNew.value) {
      await api.skillCreate(payload, changelog.value);
      if (epoch !== skillEpoch) return false;
      let adopted = true;
      try { await samplePanel.value?.adoptSamples(payload.skill_code); }
      catch (e) { adopted = false; toast.error(t('skillEditor.sample_association_error', { error: String(e instanceof Error ? e.message : e) })); }
      if (epoch !== skillEpoch) return false;
      preservedCreatedCode = payload.skill_code;
      markClean();
      await router.replace(`/skills/${payload.skill_code}`);
      await nextTick();
      if (epoch !== skillEpoch || props.code !== payload.skill_code) return false;
      if (!await load(1)) return false;
      toast.ok(t('skillEditor.skill_created_v1_draft'));
      markClean();
      return adopted;
    } else if (selectedStatus.value === "draft" && selectedVersion.value) {
      const version = selectedVersion.value;
      const r = await api.skillSaveDraft(skillCode, version, payload, changelog.value);
      if (epoch !== skillEpoch) return false;
      toast.ok(t('skillEditor.draft_saved', { version: selectedVersion.value ?? "" }));
      for (const w of r.warnings ?? []) toast.ok(w);
      if (!await load(version)) return false;
    } else {
      const r = await api.skillNewDraft(skillCode, payload, changelog.value);
      if (epoch !== skillEpoch) return false;
      toast.ok(t('skillEditor.draft_forked', { base: selectedVersion.value ?? "", version: r.version }));
      if (!await load(r.version)) return false;
    }
    if (epoch !== skillEpoch) return false;
    markClean();
    return true;
  } catch (e) { if (epoch === skillEpoch) toast.error(e); return false; }
  finally { if (epoch === skillEpoch) saving.value = false; }
}

async function newVersion() {
  if (sampleUploading.value || saving.value || fieldTrialStarting.value || !validPkg() || !pkg.value) return;
  try {
    const r = await api.skillNewDraft(props.code, outbound(), changelog.value);
    toast.ok(t('skillEditor.draft_created', { version: r.version }));
    await load(r.version);
    markClean();
  } catch (e) { toast.error(e); }
}
async function publishSelected() {
  if (sampleUploading.value || saving.value || fieldTrialStarting.value || !selectedVersion.value) return;
  try {
    await api.skillPublish(props.code, selectedVersion.value);
    toast.ok(t('skillEditor.version_published', { version: selectedVersion.value ?? "" }));
    await load(selectedVersion.value);
    markClean();
  } catch (e) { toast.error(e); }
}

// —— combobox behaviour (2026-09-04) ——
function comboOpen(ev: FocusEvent) {
  const el = ev.target as HTMLInputElement;
  el.dataset.prev = el.value;
  el.dataset.typed = "";
  el.value = "";
}
function comboTyped(ev: Event) {
  (ev.target as HTMLInputElement).dataset.typed = "1";
}
function comboClose(ev: Event) {
  const el = ev.target as HTMLInputElement;
  if (!el.dataset.typed) el.value = el.dataset.prev ?? "";
  delete el.dataset.prev;
  delete el.dataset.typed;
}

async function removeVersion() {
  if (!selectedVersion.value) return;
  const ok = confirm(t('skillEditor.delete_version_confirmation', { version: selectedVersion.value, status: verLabel(selectedStatus.value ?? "") }));
  if (!ok) return;
  try {
    await api.skillDeleteVersion(props.code, selectedVersion.value);
    toast.ok(t('skillEditor.version_deleted', { version: selectedVersion.value ?? "" }));
    await load();
  } catch (e) { toast.error(e); }
}

const copied = ref(false);
async function copyCode() {
  try {
    await copyText(pkg.value?.skill_code ?? "");
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch { toast.error(t('skillEditor.copy_failed_please_select_the_skill_code_manually')); }
}

// —— draft channels ——
function mergeDraft(fields: FieldSpec[], docType?: string): number {
  if (!pkg.value) return 0;
  pushUndo();
  const existing = new Set(curFields.value.map((f) => f.name));
  const fresh = fields.filter((f) => !existing.has(f.name));
  curFields.value.push(...fresh);
  // a category already names its document type; only a standard skill takes the hint
  if (docType && !activeCat.value && !pkg.value.doc_type_hint) pkg.value.doc_type_hint = docType;
  return fresh.length;
}
const importing = ref(false);
async function tableImport(ev: Event) {
  const file = (ev.target as HTMLInputElement).files?.[0];
  if (!file || importing.value) return;
  const epoch = skillEpoch;
  const targetFields = curFields.value;
  importing.value = true;
  try {
    const r = await api.skillDraftFromTable(file);
    if (epoch !== skillEpoch || targetFields !== curFields.value) return;
    toast.ok(t('skillEditor.table_import_complete', { count: mergeDraft(r.fields) }));
  } catch (e) { if (epoch === skillEpoch) toast.error(e); }
  finally {
    if (epoch === skillEpoch) importing.value = false;
    (ev.target as HTMLInputElement).value = "";
  }
}
const enriching = ref(false);
async function enrich() {
  if (!pkg.value || !curFields.value.length) return;
  const epoch = skillEpoch;
  const targetFields = curFields.value;
  enriching.value = true;
  try {
    const r = await api.skillEnrich(curFields.value,
                                    activeCat.value?.doc_type || pkg.value.doc_type_hint);
    if (epoch !== skillEpoch || targetFields !== curFields.value) return;
    let n = 0;
    const apply = (specs: FieldSpec[], patches: { name: string; instruction: string;
                                                  columns?: { name: string; instruction: string }[] }[]) => {
      for (const p of patches) {
        const f = specs.find((x) => x.name === p.name);
        if (!f) continue;
        if (p.instruction && p.instruction !== f.instruction) { f.instruction = p.instruction; n++; }
        if (p.columns?.length && f.columns.length)
          apply(f.columns, p.columns.map((c) => ({ ...c, columns: undefined })));
      }
    };
    apply(curFields.value, r.fields);
    toast.ok(t('skillEditor.descriptions_enriched', { count: n, provider: r.provider_used }));
  } catch (e) { if (epoch === skillEpoch) toast.error(e); }
  finally { if (epoch === skillEpoch) enriching.value = false; }
}

// —— studio: dry-run + golden ——
const running = ref(false);
const dryProviders = ref("");
const dryRuns = ref<DryRunEntry[]>([]);
async function dryRun(ev: Event) {
  const file = (ev.target as HTMLInputElement).files?.[0];
  if (!file || !pkg.value) return;
  running.value = true;
  try {
    const r = await api.skillDryRun(file, pkg.value, splitCsv(dryProviders.value));
    dryRuns.value = r.runs;
  } catch (e) { toast.error(e); }
  finally { running.value = false; (ev.target as HTMLInputElement).value = ""; }
}
function scalarCells(result: Record<string, FieldCell>): Record<string, FieldCell> {
  const out: Record<string, FieldCell> = {};
  for (const [k, v] of Object.entries(result))
    if (!Array.isArray(v)) out[k] = v;
  return out;
}
const goldenFile = ref<File | null>(null);
const goldenExpected = ref("");
const checking = ref(false);
const goldenReport = ref<Awaited<ReturnType<typeof api.goldenCheck>> | null>(null);
function pickGolden(ev: Event) {
  goldenFile.value = (ev.target as HTMLInputElement).files?.[0] ?? null;
}
async function addGolden() {
  if (!goldenFile.value) return;
  let expected: Record<string, string> = {};
  try { expected = goldenExpected.value ? JSON.parse(goldenExpected.value) : {}; }
  catch { toast.error(t('skillEditor.expected_value_is_not_valid_json')); return; }
  try {
    await api.goldenAdd(props.code, goldenFile.value, expected);
    toast.ok(t('skillEditor.golden_sample_attached'));
    goldenFile.value = null;
    goldenExpected.value = "";
  } catch (e) { toast.error(e); }
}
async function goldenCheck() {
  if (!selectedVersion.value) return;
  checking.value = true;
  goldenReport.value = null;
  try { goldenReport.value = await api.goldenCheck(props.code, selectedVersion.value); }
  catch (e) { toast.error(e); }
  finally { checking.value = false; }
}
</script>

<style scoped>
.editor { min-height: calc(100vh - 52px); display: flex; justify-content: center; }
.main-col { width: 100%; max-width: 1560px; padding: 16px 24px 40px;
  display: flex; flex-direction: column; gap: 14px; min-width: 0; }

/* top bar */
.toolbar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.back { font-size: 22px; line-height: 1; padding: 2px 8px; color: var(--text-dim); }
.back:hover { color: var(--accent); }
.ident { flex: 1; min-width: 220px; }
.ident h1 { margin: 0; font-size: 20px; }
.code-line { font-family: Consolas, monospace; font-size: 12px;
  display: inline-flex; gap: 6px; align-items: center; }
.copy { border: 0; background: none; cursor: pointer; color: var(--text-dim); }
.copy:hover { color: var(--accent); }
.acts { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }

/* version dropdown */
.ver-menu { position: relative; }
.ver-menu summary { list-style: none; cursor: pointer; }
.ver-chip { display: inline-flex; gap: 8px; align-items: center;
  border: 1px solid var(--border); border-radius: 8px; padding: 5px 12px;
  background: var(--bg-raised); font-weight: 700; }
.ver-pop { position: absolute; z-index: 60; top: calc(100% + 6px); left: 0;
  width: 280px; max-height: 420px; overflow: auto; background: var(--bg-panel);
  border: 1px solid var(--border); border-radius: 10px; padding: 10px;
  display: flex; flex-direction: column; gap: 8px; box-shadow: 0 8px 24px rgba(0,0,0,.12); }
.new-ver { border-style: dashed; }
.ver-card { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px;
  cursor: pointer; background: var(--bg); }
.ver-card:hover { border-color: var(--accent); }
.ver-card.current { border-color: var(--accent); background: var(--bg-raised); }
.ver-row { display: flex; justify-content: space-between; align-items: center; }
.ver-date { font-size: 11px; }
.ver-note-line { margin: 4px 0 0; font-size: 11px; color: var(--text-dim);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* design/test tabs */
.tabs { display: inline-flex; border: 1px solid var(--border); border-radius: 8px;
  overflow: hidden; }
.tabs button { border: 0; background: var(--bg-raised); padding: 6px 18px;
  font-size: 14px; cursor: pointer; }
.tabs button.on { background: var(--accent); color: var(--accent-text);
  font-weight: 700; }

.ver-note { margin: -6px 0 0; font-size: 12px; }
/* flow rail + step panels */
.design-body { display: grid; grid-template-columns: minmax(0, 1fr);
  gap: 16px; align-items: start; }
.flow-rail { display: flex; gap: 8px; border-bottom: 1px solid var(--border);
  padding-bottom: 12px; }
.node { display: flex; flex-direction: column; align-items: flex-start;
  gap: 5px; border: 1px solid transparent; border-radius: 9px; flex: 1;
  background: transparent; padding: 10px 14px; cursor: pointer; text-align: left; }
.node:hover { border-color: var(--accent); }
.node.on { border-color: var(--accent); background: var(--bg-raised); }
.step-number { width: 22px; height: 22px; border-radius: 50%; flex: 0 0 auto;
  background: var(--bg-raised); border: 1px solid var(--border); display: inline-flex;
  align-items: center; justify-content: center; font-size: 11px; color: var(--text-dim); }
.node .lbl { font-weight: 700; font-size: 13.5px; display: flex; gap: 6px;
  align-items: center; }
.node.on .step-number { background: var(--accent); color: var(--accent-text); border-color: var(--accent); }
.node.on .lbl { color: var(--accent); }
.node .sub { font-size: 11.5px; padding-left: 28px; }
.node.bad .lbl::after { content: "●"; color: var(--red); font-size: 10px; }
.step-panel { padding: 14px 16px; }
.block-title { margin: 0 0 10px; font-size: 14px; color: var(--accent); }
.block-head { display: flex; gap: 8px; align-items: center; margin-bottom: 10px;
  flex-wrap: wrap; }
.block-head .block-title { margin: 0; flex: 0 1 auto; }
.sub-title { margin: 16px 0 6px; font-size: 13px; color: var(--text-dim); }
.basic-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.basic-grid label { display: flex; flex-direction: column; gap: 4px; font-size: 13px;
  color: var(--text-dim); }
.span2 { grid-column: span 2; }
.mono { font-family: Consolas, monospace; }
.cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.cards + .cards, .thr { margin-top: 8px; }
.mode-card { border: 1px solid var(--border); border-radius: 10px;
  background: var(--bg); padding: 10px 12px; cursor: pointer; text-align: left;
  display: flex; flex-direction: column; gap: 3px; }
.mode-card:hover { border-color: var(--accent); }
.mode-card.on { border-color: var(--accent); background: var(--bg-raised);
  box-shadow: inset 0 0 0 1px var(--accent); }
.mode-card strong { font-size: 13.5px; }
.mode-card .dim { font-size: 12px; }
.thr { display: flex; flex-direction: column; gap: 4px; font-size: 13px;
  color: var(--text-dim); max-width: 220px; margin-top: 10px; }
.adv-note { font-size: 12px; margin: 8px 0 0; }
.adv-settings { margin-top: 14px; border: 1px solid var(--border);
  border-radius: 10px; padding: 10px 12px; }
.adv-settings summary { cursor: pointer; font-size: 13px; color: var(--text-dim); }
.adv-settings .basic-grid { margin-top: 10px; }
.thr select, .adv-settings select { max-width: 100%; }

/* fields step */
.fields-step { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr);
  gap: 18px; align-items: start; min-width: 0; }
.fields-step.sample-collapsed { grid-template-columns: 48px minmax(0, 1fr); }
.fields-step > .sample-col { min-width: 0; width: 100%; max-width: none;
  position: sticky; top: 70px; height: min(740px, calc(100vh - 280px)); min-height: 460px; }
.fields-col { min-width: 0; margin: 0; padding: 18px; display: flex;
  flex-direction: column; gap: 12px; border: 1px solid var(--border); }
.field-heading { display: flex; gap: 10px; align-items: start; justify-content: space-between; }
.field-heading h3 { font-size: 18px; margin: 0; display: flex; align-items: center; gap: 8px; }
.field-heading p { margin: 6px 0 0; font-size: 12px; }
.field-count { font-size: 11px; color: var(--text-dim); font-weight: 500;
  padding: 2px 7px; border: 1px solid var(--border); border-radius: 20px; }
.save-state { font-size: 11px; white-space: nowrap; padding-top: 5px; }
.field-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.field-actions button { font-size: 12px; }
.field-more { margin-left: auto; }
.trial-banner { padding: 10px 12px; font-size: 12px; line-height: 1.6;
  background: var(--bg-raised); border: 1px solid var(--border); border-radius: 8px;
  color: var(--text-dim); display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.trial-banner.stale { border-color: var(--accent); color: var(--accent); }
.trial-banner.failed { border-color: var(--red); color: var(--red); }
.trial-banner select { max-width: 100%; font-size: 12px; }
.field-list-head { display: grid;
  grid-template-columns: 16px minmax(0, 1.35fr) 48px minmax(0, 1fr) 48px 24px 10px;
  padding: 0 12px; gap: 8px; color: var(--text-dim); font-size: 11px; }
.output-shape { display: flex; align-items: center; gap: 12px; margin: 14px 0; font-size: 12px; }
.mobile-panes { display: none; }
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: 8px;
  overflow: hidden; }
.seg button { border: 0; background: var(--bg-raised); padding: 4px 12px;
  font-size: 12.5px; cursor: pointer; }
.seg button.on { background: var(--accent); color: var(--accent-text);
  font-weight: 700; }
.more-draft { position: relative; }
.more-draft summary { list-style: none; cursor: pointer; }
.btn-like { border: 1px solid var(--border); background: var(--bg-raised);
  border-radius: 6px; padding: 3px 10px; cursor: pointer; display: inline-block;
  font-size: 12px; }
.more-draft .more-pop, .more-menu .more-pop {
  position: absolute; z-index: 60; top: calc(100% + 4px); right: 0;
  background: var(--bg-panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 8px; display: flex; flex-direction: column; gap: 6px; min-width: 170px;
  box-shadow: 0 8px 24px rgba(0,0,0,.12); }
.more-menu { position: relative; }
.more-menu summary { list-style: none; cursor: pointer;
  border: 1px solid var(--border); border-radius: 6px; padding: 5px 12px;
  background: var(--bg-raised); font-size: 13px; }
.more-menu .more-pop button { border: 0; background: none; text-align: left;
  padding: 6px 8px; cursor: pointer; border-radius: 6px; font-size: 13px; }
.more-menu .more-pop button:hover { background: var(--bg-hover); }
.more-menu .more-pop button.danger { color: var(--red); }
.mini { padding: 2px 10px; font-size: 12px; }
.file-btn .btn-like:hover { border-color: var(--accent); }
.pad { padding: 4px 0; margin: 0; }
.drag-tip { margin: 0 0 8px; font-size: 12px; }
.field-tree { display: flex; flex-direction: column; gap: 0; }
.add-field { border-style: dashed; padding: 10px; margin-top: 10px; }
.rules-fold { margin-top: 12px; border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 12px; }
.rules-fold summary { cursor: pointer; font-size: 13px; color: var(--text-dim); }
.rules-fold textarea { width: 100%; margin-top: 8px; }

/* test tab */
/* #12: the Playground owns the whole tab; 模型对比/金样本回归 stack below it */
.test-grid { display: grid; grid-template-columns: 1fr; gap: 16px;
  align-items: start; }
.block { padding: 14px 16px; display: flex; flex-direction: column; gap: 8px; }
.runs { display: flex; flex-direction: column; gap: 10px; }
.run { border: 1px solid var(--border); border-radius: 8px; padding: 8px; }
.run-head { display: flex; gap: 10px; align-items: baseline; margin-bottom: 6px; }
.run table { font-size: 12px; border-collapse: collapse; width: 100%; }
.run td { padding: 3px 6px; border-bottom: 1px solid var(--border); }
.conf { font-size: 11px; color: var(--text-dim); }
.vis { font-size: 11px; color: var(--blue); }
.rulefail { color: var(--red); font-size: 12px; }
.golden-report { border: 1px solid var(--border); border-radius: 8px; padding: 8px;
  font-size: 13px; }
.warn { color: var(--red); }
.rep { margin-top: 6px; }
.dim { color: var(--text-dim); }

/* classify step */
.cat-intro { font-size: 12.5px; margin: 0 0 10px; }
.cat-list { display: flex; flex-direction: column; gap: 12px; }
.cat-card { border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 12px; display: flex; flex-direction: column; gap: 8px;
  background: var(--bg); }
.cat-card.other { background: var(--bg-raised); border-style: dashed; }
.cat-head { display: flex; gap: 10px; align-items: end; }
.cat-name { font-weight: 700; }
.cat-name-in { display: flex; flex-direction: column; gap: 3px;
  font-size: 12px; color: var(--text-dim); flex: 1; max-width: 260px; }
.cat-head .mini { margin-left: auto; }
.cat-rec { display: flex; flex-direction: column; gap: 3px; font-size: 12px;
  color: var(--text-dim); }
.cat-handler { display: flex; gap: 6px; align-items: center; flex-wrap: wrap;
  font-size: 12px; }
.hseg { border: 1px solid var(--border); background: var(--bg-raised);
  border-radius: 999px; padding: 3px 12px; font-size: 12px; cursor: pointer; }
.hseg.on { background: var(--accent); color: var(--accent-text);
  border-color: var(--accent); font-weight: 700; }
.seg-row { display: flex; gap: 6px; align-items: center; font-size: 12px;
  margin-bottom: 6px; }
.cat-fields { display: flex; flex-direction: column; gap: 4px; }
.cat-ref { display: flex; flex-direction: column; gap: 8px; }
.ref-row { display: flex; gap: 8px; }
.ref-row select { flex: 1; max-width: 280px; }
.ref-banner { border: 1px solid var(--border); border-left: 3px solid var(--accent);
  border-radius: 8px; background: var(--bg-raised); padding: 8px 10px;
  font-size: 12.5px; display: flex; gap: 10px; align-items: center; }
.ref-banner a { text-decoration: none; white-space: nowrap; }
.ref-fields { list-style: none; margin: 0; padding: 0; border: 1px solid var(--border);
  border-radius: 8px; max-height: 170px; overflow: auto; }
.ref-fields li { display: flex; gap: 8px; padding: 5px 10px; font-size: 12.5px;
  border-bottom: 1px solid var(--border); align-items: baseline; }
.ref-fields li:last-child { border-bottom: 0; }
.ref-fields .ex { overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  flex: 1; }
.cat-rules { display: flex; flex-direction: column; gap: 3px; font-size: 12px;
  color: var(--text-dim); }
.add-cat { border-style: dashed; }

.cards.disabled { opacity: .55; pointer-events: none; }
/* 2026-09-23 B-01 / F-01 / F-02: layout cards, per-category tabs, effective config */
.layout-cards { grid-template-columns: repeat(4, 1fr); }
.cat-extract { display: flex; gap: 10px; align-items: center; font-size: 12.5px; }
.cat-extract .mini { margin-left: auto; }
.cls-model { display: flex; flex-direction: column; gap: 4px; max-width: 420px; }
.cls-model .hint { font-size: 12px; }
.cat-tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.cat-tab { font-size: 12.5px; padding: 4px 12px; border-radius: 16px; }
.cat-tab.on { border-color: var(--accent); background: var(--bg-raised); font-weight: 600; }
.cat-tab .dim { font-weight: 400; margin-left: 2px; }
.eff-line { font-size: 12px; margin: 6px 0 10px; }
.ref-impact { font-size: 12px; margin: 0; }
/* #26: samples | original preview | results */
.playground .pg-grid { display: grid;
  grid-template-columns: 250px minmax(320px, 1fr) minmax(360px, 1.2fr);
  gap: 14px; align-items: start; }
.pg-left { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.pg-files { display: flex; flex-wrap: wrap; gap: 6px; }
.pg-files .btn-like.on { border-color: var(--accent); color: var(--accent); }
.pg-stage-col { min-width: 0; }
.pg-stage { height: 460px; border: 1px solid var(--border); border-radius: 8px;
  overflow: hidden; }
.pg-stage-hint { padding: 14px; }
.pg-result-head { display: flex; align-items: center; gap: 10px;
  flex-wrap: wrap; }
.pg-tabs { display: inline-flex; gap: 6px; }
.pg-tabs .btn-like.on { border-color: var(--accent); color: var(--accent); }
.pg-list .pg-name { flex: 1; min-width: 0; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.pg-list .pg-state { color: var(--text-dim); white-space: nowrap; }
.pg-list label { min-width: 0; }
/* #12: field names keep a readable column; table fields render as a grid */
.pg-fields { width: 100%; border-collapse: collapse; font-size: 13px; }
.pg-fields td { padding: 5px 8px; border-bottom: 1px solid var(--border);
  vertical-align: top; }
.pg-fields tr.on { background: rgba(240, 180, 41, 0.08); }
.pg-fields tr { cursor: pointer; }
.pg-field-name { min-width: 150px; max-width: 220px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }
.pg-table-grid { display: flex; flex-direction: column; gap: 4px; }
.pg-table-row { display: flex; flex-wrap: wrap; gap: 10px;
  border-bottom: 1px dashed var(--border); padding-bottom: 3px; }
.pg-cell { display: inline-flex; gap: 4px; align-items: baseline; }
.pg-json { font-family: "JetBrains Mono", monospace; font-size: 12px;
  word-break: break-all; }
.err-text { color: var(--red); }
.pg-samples { display: flex; flex-direction: column; gap: 8px; }
.pg-list { list-style: none; margin: 0; padding: 0; max-height: 260px; overflow: auto;
  border: 1px solid var(--border); border-radius: 8px; }
.pg-list li label { display: flex; gap: 6px; padding: 6px 10px; align-items: center;
  cursor: pointer; border-bottom: 1px solid var(--border); font-size: 13px; }
.pg-list li:last-child label { border-bottom: 0; }
.pg-list li.dim { padding: 8px 10px; }
.pg-result { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.pg-top { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.pg-doc { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.pg-doc-head { display: flex; gap: 8px; align-items: center; margin-bottom: 6px;
  flex-wrap: wrap; }
.pg-doc table { width: 100%; font-size: 12.5px; border-collapse: collapse; }
.pg-doc td { padding: 4px 6px; border-bottom: 1px solid var(--border);
  word-break: break-all; }
.pg-doc tr:last-child td { border-bottom: 0; }
.pg-modal { width: 100%; }
.pg-modal table { width: 100%; border-collapse: collapse; font-size: 13px; }
.pg-modal thead th { text-align: left; padding: 8px 10px; color: var(--text-dim);
  font-size: 12px; border-bottom: 1px solid var(--border); white-space: nowrap; }
.pg-modal tbody td { padding: 8px 10px; border-bottom: 1px solid var(--border);
  white-space: nowrap; }
.pg-modal tbody tr:hover { background: rgba(240, 180, 41, 0.06); }
.pg-hist-row { cursor: pointer; }
.pg-detail { list-style: none; margin: 0 0 10px; padding: 0; }
.pg-detail li { padding: 4px 0; border-bottom: 1px solid var(--border); }
.pg-detail code { font-size: 11px; }

/* —— 9.15 WP6 output step —— */
.out-actions { display: flex; gap: 10px; margin-top: 10px; }
.out-actions .mode-card { flex: 1; min-width: 0; }
.out-actions .mode-card:disabled { opacity: .5; cursor: not-allowed; }
.token-row { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.token-row .tok { font-size: 12px; padding: 2px 8px; border: 1px solid var(--border);
  border-radius: 10px; background: transparent; cursor: pointer; }
.token-row .tok:hover { border-color: var(--accent); }
.out-preview { margin: 6px 0 10px; font-size: 13px; }
.out-preview code { background: var(--bg-soft); color: var(--text);
  padding: 2px 6px; border-radius: 4px; }
.chk { display: flex; align-items: center; gap: 8px; margin: 8px 0; font-size: 14px; }
.pg-arts { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.pg-art .ghost { font-size: 12px; padding: 2px 8px; }
.pg-result { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.pg-top { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.pg-doc { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.pg-doc-head { display: flex; gap: 8px; align-items: center; margin-bottom: 6px;
  flex-wrap: wrap; }
.pg-doc table { width: 100%; font-size: 12.5px; border-collapse: collapse; }
.pg-doc td { padding: 4px 6px; border-bottom: 1px solid var(--border);
  word-break: break-all; }
.pg-doc tr:last-child td { border-bottom: 0; }
.pg-modal { width: 100%; }
.pg-modal table { width: 100%; border-collapse: collapse; font-size: 13px; }
.pg-modal thead th { text-align: left; padding: 8px 10px; color: var(--text-dim);
  font-size: 12px; border-bottom: 1px solid var(--border); white-space: nowrap; }
.pg-modal tbody td { padding: 8px 10px; border-bottom: 1px solid var(--border);
  white-space: nowrap; }
.pg-modal tbody tr:hover { background: rgba(240, 180, 41, 0.06); }
.pg-hist-row { cursor: pointer; }
.pg-detail { list-style: none; margin: 0 0 10px; padding: 0; }
.pg-detail li { padding: 4px 0; border-bottom: 1px solid var(--border); }
.pg-detail code { font-size: 11px; }

/* —— 9.15 WP6 output step —— */
.out-actions { display: flex; gap: 10px; margin-top: 10px; }
.out-actions .mode-card { flex: 1; min-width: 0; }
.out-actions .mode-card:disabled { opacity: .5; cursor: not-allowed; }
.token-row { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.token-row .tok { font-size: 12px; padding: 2px 8px; border: 1px solid var(--border);
  border-radius: 10px; background: transparent; cursor: pointer; }
.token-row .tok:hover { border-color: var(--accent); }
.out-preview { margin: 6px 0 10px; font-size: 13px; }
.out-preview code { background: var(--bg-soft); color: var(--text);
  padding: 2px 6px; border-radius: 4px; }
.chk { display: flex; align-items: center; gap: 8px; margin: 8px 0; font-size: 14px; }
.pg-arts { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.pg-art .ghost { font-size: 12px; padding: 2px 8px; }
.art-dropdown { position: relative; }
.art-dropdown summary { cursor: pointer; list-style: none; padding: 6px 10px;
  border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.art-dropdown ul { position: absolute; z-index: 30; top: 100%; left: 0;
  min-width: 280px; margin: 4px 0 0; padding: 6px; list-style: none;
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 8px; box-shadow: 0 8px 24px rgb(0 0 0 / 12%); }
.art-dropdown li { display: flex; align-items: center; gap: 8px; padding: 4px 6px;
  font-size: 13px; }
.art-dropdown .art-name { flex: 1; min-width: 0; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }

@media (max-width: 1100px) {
  .test-grid { grid-template-columns: 1fr; }
  .cards { grid-template-columns: 1fr; }
}
@media (max-width: 959px) {
  .fields-step, .fields-step.sample-collapsed { grid-template-columns: minmax(0, 1fr); gap: 12px; }
  .mobile-panes { display: flex; padding: 3px; border: 1px solid var(--border); border-radius: 8px; }
  .mobile-panes button { flex: 1; border: none; background: transparent; }
  .mobile-panes .on { background: var(--bg-raised); color: var(--accent); }
  .fields-step > .sample-col { display: none; position: static; height: 65vh; min-height: 400px; }
  .fields-step.show-sample > .sample-col { display: flex; }
  .fields-step.show-sample > .fields-col { display: none; }
  .fields-col { padding: 14px; }
}
@media (max-width: 580px) {
  .main-col { padding: 12px 12px 28px; }
  .ident { min-width: 160px; }
  .ident h1 { font-size: 18px; }
  .toolbar { gap: 9px; }
  .acts { width: 100%; }
  .acts > .primary { margin-left: auto; }
  .flow-rail { gap: 4px; }
  .node { padding: 8px 6px; min-width: 0; }
  .node .lbl { font-size: 12px; gap: 4px; }
  .node .sub { display: none; }
  .step-number { width: 18px; height: 18px; font-size: 10px; }
  .basic-grid { grid-template-columns: minmax(0, 1fr); }
  .span2 { grid-column: auto; }
  .field-heading h3 { font-size: 16px; }
  .field-actions { gap: 6px; }
  .field-actions button { padding: 6px 9px; }
  .field-list-head { display: none; }
}
</style>
