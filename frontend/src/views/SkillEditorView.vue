<template>
  <main class="editor" v-if="pkg">
    <!-- 9.15 WP3 shell: top bar (identity + version menu + actions) above a
         设计/测试 tab pair; the left version rail became a dropdown menu -->
    <div class="main-col">
      <div class="toolbar">
        <router-link to="/skills" class="back" title="返回技能列表">‹</router-link>
        <div class="ident">
          <h1>{{ pkg.name || pkg.skill_code || "新技能" }}</h1>
          <span class="dim code-line">
            {{ pkg.skill_code || "（保存时确定）" }}
            <button v-if="pkg.skill_code" class="copy" title="复制技能代码"
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
            <button class="new-ver" :disabled="saving || fieldTrialStarting || sampleUploading" @click="newVersion">＋ 新建版本</button>
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
          <button :class="{ on: tab === 'design' }" @click="tab = 'design'">设计</button>
          <button :class="{ on: tab === 'test' }" @click="tab = 'test'">测试</button>
        </div>

        <div class="acts">
          <button v-if="!isNew" @click="downloadFile(api.skillExportUrl(code), `${code}.yaml`)">
            导出</button>
          <details class="more-menu">
            <summary>更多</summary>
            <div class="more-pop">
              <button @click="apiModal = true">🔌 API 接入</button>
              <button @click="downloadFile(api.skillExportUrl(code), `${code}.yaml`)">
                导出 YAML</button>
              <button v-if="!isNew && versions.length > 1" class="danger"
                      :disabled="selectedStatus === 'published'"
                      :title="selectedStatus === 'published'
                              ? '当前发布版本不可删除——提交都按它跑'
                              : '只删除当前这一个版本'"
                      @click="removeVersion">删除当前版本</button>
            </div>
          </details>
          <button class="primary" :disabled="saving || fieldTrialStarting || sampleUploading || importing || enriching" @click="save">
            {{ saving ? "保存中…" : (isNew ? "创建技能" : saveLabel) }}</button>
          <button v-if="!isNew && selectedStatus === 'draft'" class="confirm" :disabled="saving || fieldTrialStarting || sampleUploading"
                  @click="publishSelected">🚀 发布</button>
        </div>
      </div>
      <p v-if="!isNew && selectedStatus === 'draft'" class="ver-note dim">
        当前编辑 v{{ selectedVersion }}（草稿）——「保存」原地更新本草稿；
        要留存快照请用版本菜单「＋ 新建版本」。</p>
      <p v-else-if="!isNew && selectedStatus" class="ver-note dim">
        当前查看 v{{ selectedVersion }}（{{ verLabel(selectedStatus) }}）——已发布版本不可改，
        「另存为新草稿」将以此为底新建草稿版本。</p>

      <!-- —— 设计 tab —— -->
      <template v-if="tab === 'design'">
        <div class="design-body" :inert="saving">
          <!-- flow rail: 基础 → (文档分类) → 字段提取 → 文件产出 -->
          <nav class="flow-rail" aria-label="设计步骤">
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
            <h3 class="block-title">基本信息</h3>
            <div class="basic-grid">
              <label>技能名称
                <input ref="skillNameInput" v-model="pkg.name" placeholder="例如：XCMG 海外发票" /></label>
              <label>skill_code（英文，创建后不可改）
                <input ref="skillCodeInput" v-model="pkg.skill_code" class="mono" :disabled="!isNew" /></label>
              <label class="span2">描述
                <textarea v-model="pkg.description" rows="2"
                          placeholder="这个技能处理什么文档、服务什么业务（给同事看的说明）"></textarea>
              </label>
              <label v-if="!isNew" class="span2">版本介绍（v{{ selectedVersion }} 的说明）
                <input v-model="changelog"
                       placeholder="这一版改了什么，例如：新增税额字段、放宽发票号正则" />
              </label>
            </div>

            <h4 class="sub-title">处理模式</h4>
            <div class="cards">
              <button class="mode-card" :class="{ on: pkg.processing_mode !== 'fast' }"
                      @click="setProcessingMode('balanced')">
                <strong>均衡</strong>
                <span class="dim">逐页定位与置信评分，支持复核与挑战者模型</span></button>
              <button class="mode-card" :class="{ on: pkg.processing_mode === 'fast' }"
                      data-testid="fast-mode-card"
                      @click="setProcessingMode('fast')">
                <strong>极速</strong>
                <span class="dim">适用于页数较少、追求速度的文档，不支持复核；
                  单页上限 {{ fastMaxPages }} 页</span></button>
            </div>
            <h3>提取通道</h3>
            <div class="cards">
              <button class="mode-card" :class="{ on: pkg.extraction_channel !== 'rules_first' }" @click="pkg.extraction_channel = 'model'"><strong>模型提取</strong><span>由模型提取全部字段</span></button>
              <button class="mode-card" :class="{ on: pkg.extraction_channel === 'rules_first' }" @click="pkg.extraction_channel = 'rules_first'"><strong>规则优先</strong><span>先按字段规则提取，缺失字段由模型补充</span></button>
            </div>
            <p v-if="pkg.processing_mode === 'fast'" class="dim adv-note" data-testid="fast-note">
              极速模式：结果不带定位与置信评分（显示「未评分」），不进入人工复核；
              校验器照常运行。已保存的复核与高级配置会保留，切回「均衡」后恢复。</p>

            <h4 class="sub-title">人工复核模式</h4>
            <div class="cards" :class="{ disabled: pkg.processing_mode === 'fast' }">
              <button class="mode-card" :disabled="pkg.processing_mode === 'fast'"
                      :class="{ on: pkg.review_policy.mode === 'never' }"
                      @click="pkg.review_policy.mode = 'never'">
                <strong>无需人工复核</strong>
                <span class="dim">识别结果直接通过，不停留审单</span></button>
              <button class="mode-card" :disabled="pkg.processing_mode === 'fast'"
                      :class="{ on: pkg.review_policy.mode === 'auto' }"
                      @click="pkg.review_policy.mode = 'auto'">
                <strong>低置信度时复核</strong>
                <span class="dim">低于阈值的字段进入审单</span></button>
              <button class="mode-card" :disabled="pkg.processing_mode === 'fast'"
                      :class="{ on: pkg.review_policy.mode === 'always' }"
                      @click="pkg.review_policy.mode = 'always'">
                <strong>始终需要复核</strong>
                <span class="dim">每份文档都进审单</span></button>
            </div>
            <p v-if="pkg.processing_mode === 'fast'" class="dim">
              极速模式不支持人工复核。</p>
            <label v-if="pkg.review_policy.mode === 'auto' && pkg.processing_mode !== 'fast'"
                   class="thr">
              置信阈值（低于则人审）
              <select v-model.number="pkg.review_policy.confidence_threshold">
                <option :value="1">1</option><option :value="2">2</option>
                <option :value="3">3</option>
              </select></label>

            <h4 class="sub-title">技能模式</h4>
            <div class="cards" :class="{ disabled: pkg.processing_mode === 'fast' }">
              <button class="mode-card" :disabled="pkg.processing_mode === 'fast'"
                      :class="{ on: pkg.skill_mode !== 'advanced' }"
                      @click="setSkillMode('standard')">
                <strong>标准</strong>
                <span class="dim">一份文档、一组字段，适合大多数单据</span></button>
              <button class="mode-card" :disabled="pkg.processing_mode === 'fast'"
                      :class="{ on: pkg.skill_mode === 'advanced' }"
                      data-testid="mode-advanced"
                      @click="setSkillMode('advanced')">
                <strong>高级</strong>
                <span class="dim">一份文件多种单据，先分类再提取</span></button>
            </div>
            <p v-if="pkg.skill_mode === 'advanced'" class="dim adv-note">
              高级模式：在「文档分类」设置文件构成、类别与分类模型；在「字段提取」按类别配置字段。</p>

            <details class="adv-settings">
              <summary>高级设置（抽取模型、备用模型、挑战者模型、解析器）</summary>
              <div class="basic-grid">
                <datalist id="dl-providers">
                  <option v-for="p in providerOptions" :key="p.name" :value="p.name"
                          :label="`${p.name} · ${p.model}${p.active ? '（平台默认）' : ''}${p.custom ? '（自定义）' : ''}${p.vision ? '・支持图像' : ''}`" />
                </datalist>
                <datalist id="dl-parsers">
                  <option v-for="p in parserOptions" :key="p.name" :value="p.name"
                          :label="p.description ? `${p.name} — ${p.description}` : p.name" />
                </datalist>
                <label>抽取模型（空=平台默认{{ activeProvider ? `：${activeProvider}` : "" }}）
                  <input v-model="pkg.model_binding.extractor" list="dl-providers"
                         placeholder="下拉选择或直接输入，如 qwen"
                         @focus="comboOpen" @input="comboTyped" @blur="comboClose" />
                  <span v-if="pkg.skill_mode === 'advanced'" class="hint dim">
                    高级模式：「本技能内配置字段」的类别用此模型；分类未单独设置时也用它；
                    引用的已有技能用它自己的模型。</span></label>
                <label>备用模型（fallback）
                  <input :value="pkg.model_binding.fallback ?? ''" list="dl-providers"
                         placeholder="可空；下拉选择或直接输入"
                         @focus="comboOpen" @blur="comboClose"
                         @input="comboTyped($event); pkg.model_binding.fallback = ($event.target as HTMLInputElement).value || null" /></label>
                <label>挑战者模型（不一致标人审）
                  <input :value="pkg.model_binding.challenger ?? ''" list="dl-providers"
                         placeholder="可空；下拉选择或直接输入"
                         @focus="comboOpen" @blur="comboClose"
                         @input="comboTyped($event); pkg.model_binding.challenger = ($event.target as HTMLInputElement).value || null" /></label>
                <label>解析器（空=自动路由）
                  <input :value="pkg.parser ?? ''" list="dl-parsers"
                         placeholder="自动；下拉选择或直接输入"
                         @focus="comboOpen" @blur="comboClose"
                         @input="comboTyped($event); pkg.parser = ($event.target as HTMLInputElement).value || null" /></label>
              </div>
            </details>
          </section>

          <!-- —— 文档分类（高级模式，图05–11）: 文件构成 + 类别 + 分类模型。
               每个类别怎么提取（字段 / 引用技能 / 仅分类）在「字段提取」里按类别
               配置（2026-09-23 F-02）—— -->
          <section v-show="step === 'classify'" class="card-panel block step-panel">
            <h3 class="block-title">文档分类</h3>
            <p class="dim cat-intro">
              一份文件包含多种/多份单据时，先分类再提取。这里设置文件怎么拆、有哪些类别、
              怎么识别；每个类别提取哪些字段，在下一步「字段提取」里按类别配置。</p>

            <!-- B-01 (R07 图05): the layout decides whether a file is split at all -->
            <h4 class="sub-title">文件构成</h4>
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
              整个文件按一份文档判断类别，不会拆分。文件里有多份单据时，请选「多种单据混合」。</p>

            <h4 class="sub-title">类别</h4>
            <div class="cat-list">
              <div v-for="(c, ci) in editableCats" :key="c.id" class="cat-card"
                   :class="{ other: c.is_other }">
                <div class="cat-head">
                  <template v-if="c.is_other">
                    <span class="cat-name">Other（兜底类别）</span>
                  </template>
                  <template v-else>
                    <label class="cat-name-in">
                      类别名（doc_type）
                      <input v-model="c.doc_type" maxlength="64"
                             placeholder="例如：发票" /></label>
                  </template>
                  <button v-if="!c.is_other" class="mini danger"
                          title="删除该类别"
                          @click="removeCategory(ci)">删除</button>
                </div>
                <label class="cat-rec">识别说明（模型按这段话判断页面归属）
                  <textarea v-model="c.recognition_instruction" rows="2"
                            placeholder="例如：有「发票号码」「开票日期」和价税合计"></textarea></label>
                <div class="cat-extract">
                  <span class="dim">提取：{{ catSummary(c) }}</span>
                  <button class="mini" :data-testid="`cat-goto-${c.id}`"
                          @click="gotoCategoryFields(c)">配置字段 →</button>
                </div>
              </div>
            </div>
            <button class="add-cat" @click="addCategory">＋ 添加类别</button>

            <!-- F-01: the classification step names its model and what it inherits -->
            <h4 class="sub-title">分类模型</h4>
            <label class="cls-model">
              <input :value="pkg.model_binding.classifier ?? ''" list="dl-providers"
                     placeholder="空 = 与抽取模型相同；下拉选择或直接输入"
                     data-testid="classifier-model"
                     @focus="comboOpen" @blur="comboClose"
                     @input="comboTyped($event); pkg.model_binding.classifier = ($event.target as HTMLInputElement).value || null" />
              <span class="hint dim" data-testid="classifier-effective">
                实际使用：{{ classifierEffective }}</span></label>

            <details class="rules-fold">
              <summary>分类附加规则（可选，作用于整个分类步骤）</summary>
              <textarea v-model="pkg.classification_rules" rows="2"
                        placeholder="如：第 1 页通常是发票；连续的行程单算一份文档"></textarea>
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
            <nav class="mobile-panes" aria-label="字段工作区">
              <button :class="{ on: mobilePane === 'sample' }" @click="mobilePane = 'sample'">样本</button>
              <button :class="{ on: mobilePane === 'fields' }" @click="mobilePane = 'fields'">字段 · {{ curFields.length }}</button>
            </nav>
            <SamplePanel ref="samplePanel" :skill-code="isNew ? undefined : code"
                         :boxes="fieldTrialBoxes" :active-key="activeFieldName" :stage-pages="fieldTrialPages"
                         :active-page="fieldActivePage" class="sample-col"
                         @select="selectFieldSample" @pick="pickFieldBox"
                         @uploading="sampleUploading = $event"
                         @collapse="sampleCollapsed = $event" />
            <fieldset class="fields-col card-panel" :disabled="saving || fieldTrialStarting">
              <template v-if="activeCat">
                <nav class="cat-tabs" aria-label="按类别配置字段">
                  <button v-for="c in editableCats" :key="c.id" class="cat-tab"
                          :class="{ on: activeCat.id === c.id }"
                          :data-testid="`cat-tab-${c.id}`"
                          @click="activeCatId = c.id">
                    {{ catName(c) }} <span class="dim">{{ catBadge(c) }}</span></button>
                </nav>
                <div class="cat-handler">
                  <span class="dim">提取方式</span>
                  <button class="hseg" :class="{ on: activeCat.handler === 'inline' }"
                          @click="setHandler(activeCat, 'inline')">本技能内配置字段</button>
                  <button class="hseg" :class="{ on: activeCat.handler === 'existing_skill' }"
                          @click="setHandler(activeCat, 'existing_skill')">使用已有技能</button>
                  <button class="hseg" :class="{ on: activeCat.handler === 'classify_only' }"
                          @click="setHandler(activeCat, 'classify_only')">仅分类，不提取</button>
                </div>
                <!-- F-01: the model / channel that actually runs for this category -->
                <p class="dim eff-line" data-testid="cat-effective">{{ catEffective(activeCat) }}</p>
              </template>

              <template v-if="!activeCat || activeCat.handler === 'inline'">
                <div class="field-heading">
                  <div>
                    <h3>{{ activeCat ? `「${catName(activeCat)}」的字段` : "字段提取" }} <span class="field-count">{{ curFields.length }}</span></h3>
                    <p class="dim">对照左侧样本，定义需要提取的信息。</p>
                  </div>
                  <span class="save-state dim">{{ saving ? "保存中…" : dirty ? "有未保存修改" : isNew ? "尚未创建" : "已保存" }}</span>
                </div>
                <div class="field-actions">
                  <button class="primary" data-testid="field-trial"
                          :disabled="!activeSample || !curFields.length || saving || fieldTrialStarting || sampleUploading || pgPolling || pgCreating || importing || enriching"
                          :title="!activeSample ? '先上传或选择一份样本' : '先保存当前定义，再试抽当前样本'"
                          @click="runFieldTrial">{{ fieldTrialStarting ? "保存并准备试抽…" : fieldTrialRunning ? "试抽中…" : fieldTrial ? "重新试抽" : "试抽当前样本" }}</button>
                  <button @click="genPanel = !genPanel">生成字段</button>
                  <button @click="addInlineField">＋ 添加</button>
                  <details class="more-draft field-more">
                    <summary class="btn-like" aria-label="更多字段操作">•••</summary>
                    <div class="more-pop">
                      <button :disabled="!undoStack.length" @click="undoGenerate">撤销上次生成</button>
                      <label class="file-btn slim">
                        <input type="file" accept=".xlsx,.csv,.tsv" hidden :disabled="importing" @change="tableImport" />
                        <span class="btn-like">{{ importing ? '导入中…' : '表格导入' }}</span>
                      </label>
                      <button class="mini" :disabled="!curFields.length || enriching"
                              title="把字段说明扩写为完整抽取指令"
                              @click="enrich">{{ enriching ? "⏳ 补全中…" : "✨ AI 补全说明" }}</button>
                      <button class="danger" :disabled="!curFields.length" @click="clearFields">清空全部字段</button>
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
                  <span v-else-if="fieldTrialStarting">正在保存当前字段定义…</span>
                  <span v-else-if="fieldTrialRunning">正在试抽「{{ activeSample?.file_name }}」…</span>
                  <span v-else-if="fieldTrialStale">定义已修改，下面的结果可能已过期。请重新试抽。</span>
                  <span v-else-if="fieldTrialReady">试抽完成 · {{ fieldTrialFilled }}/{{ curFields.length }} 个字段有值<span v-if="pgDuration != null"> · {{ (pgDuration / 1000).toFixed(1) }} 秒</span></span>
                  <span v-else>{{ activeSample ? '试抽当前样本，核对每个字段的值与位置。' : '上传一份样本后，即可在这里试抽核对。' }}</span>
                  <select v-if="fieldTrialDocs.length > 1" v-model.number="fieldTrialDocIndex" aria-label="选择试抽文档">
                    <option v-for="(doc, di) in fieldTrialDocs" :key="doc.file_id" :value="di">{{ doc.doc_type || '文档' }} {{ di + 1 }} · {{ doc.page_range }}</option>
                  </select>
                </div>
                <div v-if="curFields.length" class="field-list-head" aria-hidden="true"><span></span><span>字段与说明</span><span>类型</span><span>试抽结果</span><span>状态</span><span></span><span></span></div>
                <div class="field-tree" ref="treeEl">
                  <FieldCard v-for="(f, i) in curFields" :key="`${activeCat?.id ?? 'standard'}:${i}`" :field="f" :index="i"
                             inline :expanded="expandedField === i" :active="activeFieldName === f.name"
                             :result="fieldValue(f.name)" :result-status="fieldResultStatus(f.name)"
                             :drag-from="reorder.from.value ?? -1" :drag-over="reorder.over.value ?? -1"
                             @select="selectInlineField(i)" @update="curFields.splice(i, 1, $event)"
                             @remove="removeInlineField(i)"
                             @grip-down="startFieldDrag" />
                  <button class="add-field" @click="addInlineField">＋ 添加字段</button>
                </div>
                <p v-if="curFields.length" class="dim drag-tip">拖动 ⠿ 调整字段顺序；点字段展开编辑，与样本对照。</p>
              </template>

              <!-- existing_skill: R10 reference (图16) + F-02 §3.18 source/jump/impact -->
              <div v-else-if="activeCat.handler === 'existing_skill'" class="cat-ref">
                <div class="ref-row">
                  <select :value="activeCat.skill_ref?.skill_code ?? ''"
                          @change="setRefSkill(activeCat, ($event.target as HTMLSelectElement).value)">
                    <option value="">选择要复用的技能…</option>
                    <option v-for="r in refSkills" :key="r.skill_code"
                            :value="r.skill_code">{{ r.name }}（{{ r.skill_code }}）</option>
                  </select>
                  <select v-if="activeCat.skill_ref?.skill_code"
                          :value="activeCat.skill_ref?.version ?? ''"
                          @change="setRefVersion(activeCat, ($event.target as HTMLSelectElement).value)">
                    <option value="">跟随最新发布版{{ refVersionHint(activeCat) }}</option>
                    <option v-for="v in refVersions(activeCat)" :key="v" :value="v">v{{ v }}</option>
                  </select>
                </div>
                <div v-if="activeCat.skill_ref?.skill_code" class="ref-banner">
                  <span class="dim">
                    字段来源：引用技能「{{ refName(activeCat) }}」（{{ activeCat.skill_ref.skill_code }}），
                    在此只读；如需修改，请打开原技能编辑器。</span>
                  <a class="btn-like" :href="`/#/skills/${activeCat.skill_ref.skill_code}`"
                     target="_blank">打开技能编辑器</a>
                </div>
                <p v-if="activeCat.skill_ref?.skill_code" class="dim ref-impact"
                   data-testid="ref-impact">{{ refImpact(activeCat) }}</p>
                <ul v-if="refFields(activeCat).length" class="ref-fields">
                  <li v-for="f in refFields(activeCat)" :key="f.name">
                    <code>{{ f.name }}</code><span class="dim">{{ f.type }}</span>
                    <span class="ex dim" :title="f.instruction">{{ f.instruction }}</span>
                  </li>
                </ul>
              </div>

              <p v-else-if="activeCat.handler === 'classify_only'" class="dim pad">
                该类别只输出所属文档类型（doc_type），不抽取字段；审单时可直接通过。</p>

              <label v-if="activeCat && !activeCat.is_other && activeCat.handler !== 'classify_only'"
                     class="cat-rules">类别附加规则（可选，填写后替代{{ activeCat.handler === 'inline' ? "下方通用附加规则" : "原技能的附加规则" }}）
                <textarea v-model="activeCat.additional_rules" rows="1"
                          placeholder="只作用于该类别的补充说明"></textarea></label>

              <details v-if="!activeCat || activeCat.handler === 'inline'" class="rules-fold">
                <summary>更多设置 · 输出结构与附加规则</summary>
                <div class="output-shape"><span class="dim">输出结构</span>
                  <div class="seg" role="group" aria-label="输出结构">
                    <button :class="{ on: fieldShape !== 'list' }" @click="fieldShape = 'object'">Object</button>
                    <button :class="{ on: fieldShape === 'list' }" @click="fieldShape = 'list'">List</button>
                  </div>
                </div>
                <label class="dim">{{ activeCat ? '通用附加规则（未填写类别附加规则时使用）' : '附加规则' }}</label>
                <textarea v-model="pkg.additional_rules" rows="2"
                          placeholder="如：金额一律保留两位小数；日期统一 YYYY-MM-DD"></textarea>
              </details>
              <p v-if="activeCat && pkg.fields.length" class="dim" data-testid="top-fields-note">
                另有 {{ pkg.fields.length }} 个标准模式字段：切回「标准」技能模式或使用极速模式时生效，高级模式下不使用。</p>
            </fieldset>
          </section>

          <!-- —— 文件产出（9.15 WP6，图18-20） —— -->
          <section v-show="step === 'output'" class="card-panel block step-panel"
                   data-testid="output-step">
            <h3 class="block-title">文件产出</h3>
            <label class="chk">
              <input type="checkbox" data-testid="out-enabled"
                     :checked="pkg!.output?.enabled" @change="toggleOutput($event)" />
              允许下载文档
            </label>
            <template v-if="pkg!.output?.enabled">
              <div class="out-actions">
                <button class="mode-card" data-testid="out-rename"
                        :class="{ on: pkg!.output?.action === 'rename' }"
                        :disabled="false"
                        @click="setOutputAction('rename')">
                  <b>重命名原始文档</b>
                  <span class="dim">整个原件按命名规则产出 1 个文件</span>
                </button>
                <button class="mode-card" data-testid="out-split"
                        :class="{ on: pkg!.output?.action === 'split' }"
                        :disabled="!splitAvailable"
                        :title="splitAvailable ? '' :
                          '拆分下载仅在高级模式且文件包含多份文档时可用'"
                        @click="setOutputAction('split')">
                  <b>拆分为子文档</b>
                  <span class="dim">{{ splitAvailable
                    ? "每份子文档按命名规则单独产出" :
                    "拆分下载仅在文件包含多份文档时可用" }}</span>
                </button>
              </div>
              <template v-if="pkg!.output?.action !== 'off'">
                <label class="lbl">命名规则</label>
                <input ref="outPatternInput" v-model="pkg!.output!.naming_rule"
                       data-testid="out-pattern" class="txt"
                       :placeholder="pkg!.output?.action === 'split'
                         ? '{doc_index}_{doc_type}_{original_name}{original_ext}'
                         : '{original_name}{original_ext}'"
                       @input="outQueuePreview" />
                <div class="token-row" data-testid="out-tokens">
                  <button v-for="t in outAvailableTokens" :key="t" class="tok"
                          :class="{ on: outTokens.includes(t) }"
                          :title="'插入 {' + t + '}'"
                          @click="insertToken(t)">{{ "{" + t + "}" }}</button>
                </div>
                <div class="out-preview" data-testid="out-preview">
                  <span class="dim">预览：</span>
                  <code v-if="outPreview.ok">{{ outPreview.preview }}</code>
                  <span v-else class="err-text">{{ outErrors[0]?.message }}</span>
                  <span v-if="outPreview.appended_ext" class="dim">
                    （已自动补 {original_ext}）</span>
                </div>
                <label class="chk">
                  <input type="checkbox" data-testid="out-searchable"
                         :checked="pkg!.output?.searchable_pdf"
                         @change="pkg!.output!.searchable_pdf = ($event.target as HTMLInputElement).checked" />
                  转换为可检索 PDF（电子原件原样输出；扫描件/图片叠加隐藏文字层）
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
            <h3 class="block-title">Playground（测试运行）</h3>
            <p class="dim">勾选样本运行当前定义（含未发布草稿）；测试任务不会进入任务列表、统计、
              数据柜、审单队列，也不会触发 webhook。</p>
            <div class="pg-grid">
              <div class="pg-left">
                <div class="pg-samples">
                  <input v-model="pgSearch" placeholder="搜索样本…" />
                  <label class="file-btn"><input type="file" hidden @change="pgUpload" />
                    <span class="btn-like">上传样本</span></label>
                  <ul class="pg-list" data-testid="pg-samples">
                    <li v-for="s in pgFilteredSamples" :key="s.id">
                      <label>
                        <input type="checkbox" :value="s.id" v-model="pgSelected" />
                        <span class="pg-name">{{ s.file_name }}</span>
                        <small v-if="pgStatus(s.id)" class="pg-state">{{ pgStatus(s.id) }}</small>
                      </label>
                    </li>
                    <li v-if="!pgFilteredSamples.length" class="dim">还没有样本</li>
                  </ul>
                  <button class="primary" data-testid="pg-run"
                          :disabled="!pgSelected.length || pgPolling || pgCreating || fieldTrialStarting" @click="pgRun">
                    运行测试（{{ pgSelected.length }}）</button>
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
                    {{ !pgCurrent ? "运行后在这里查看原件与字段高亮"
                       : (pgFast ? "极速模式不提供定位高亮"
                                 : "原件加载中…") }}</p>
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
                    运行（当前文件）</button>
                  <button class="btn-like" @click="pgHistoryOpen = true; pgLoadHistory()"
                          data-testid="pg-history">运行历史</button>
                  <button v-if="pgCurrentRun" class="btn-like"
                          data-testid="pg-detail-btn" @click="pgOpenDetail">详情</button>
                </div>
                <p v-if="pgTimeoutNote" class="err-text" data-testid="pg-timeout">
                  {{ pgTimeoutNote }}</p>
                <p v-if="pgFast" class="dim">极速模式不提供定位高亮；结果不带置信评分（未评分）。</p>
                <div v-if="pgFileArtifacts.length" class="pg-arts" data-testid="pg-file-arts">
                  <span v-for="a in pgFileArtifacts" :key="a.artifact_id"
                        class="pg-art" :title="a.error ?? ''">
                    <button v-if="a.status === 'ready'" class="ghost"
                            @click="api.artifactDownload(a.artifact_id, a.name)">
                      产出 ⤓ {{ a.name }}</button>
                    <span v-else class="dim">{{ a.name }}（失败：{{ a.error }}）</span>
                  </span>
                </div>
                <p v-if="!pgVisibleDocs.length" class="dim">运行后在这里查看提取结果。</p>

                <div class="pg-result-head">
                  <strong>提取结果 · 输出格式：json</strong>
                  <span class="pg-tabs">
                    <button class="btn-like" :disabled="!pgVisibleDocs.length"
                            :class="{ on: pgResultTab === 'all' }"
                            data-testid="pg-tab-all" @click="pgResultTab = 'all'">
                      全部字段</button>
                    <button class="btn-like" :disabled="!pgVisibleDocs.length"
                            :class="{ on: pgResultTab === 'review' }"
                            data-testid="pg-tab-review" @click="pgResultTab = 'review'">
                      待复核（{{ pgReviewCount }}）</button>
                  </span>
                  <button class="btn-like" :disabled="!pgVisibleDocs.length"
                          data-testid="pg-toggle-all"
                          @click="pgExpandAll = !pgExpandAll">
                    {{ pgExpandAll ? "全部收起" : "全部展开" }}</button>
                </div>

                <div v-for="doc in pgVisibleDocs" :key="`${doc.file_id}-${doc.doc_index}`"
                     class="pg-doc">
                  <div class="pg-doc-head">
                    <strong>文档 {{ doc.doc_index }}</strong>
                    <span v-if="doc.doc_type" class="chip">{{ doc.doc_type }}</span>
                    <span class="dim">第 {{ doc.page_range }} 页</span>
                    <span v-if="doc.extraction_status === 'not_requested'" class="dim">
                      仅分类，没有需要校验的字段</span>
                  </div>
                  <table v-if="pgRowsOf(doc).length" class="pg-fields">
                    <tbody>
                      <tr v-for="[k, v] in pgRowsOf(doc)" :key="k"
                          :class="{ on: pgActiveField === String(k) }"
                          @click="pgPickDocField(doc, String(k))">
                        <td class="pg-field-name">{{ k }}
                          <span v-if="doc.field_sources?.[String(k)]" class="dim">{{ sourceLabel(doc.field_sources[String(k)]) }}</span>
                          <span v-if="doc.review_fields.includes(String(k))"
                                class="badge-r">待复核</span></td>
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
                                pgResultTab === 'all'" class="dim">（无结果）</p>
                  <p v-else-if="pgResultTab === 'review'" class="dim">没有待复核字段</p>
                  <div v-if="doc.artifacts?.length" class="pg-arts" data-testid="pg-arts">
                    <span v-for="a in doc.artifacts" :key="a.artifact_id"
                          class="pg-art" :title="a.error ?? ''">
                      <button v-if="a.status === 'ready'" class="ghost"
                              @click="api.artifactDownload(a.artifact_id, a.name)">
                        ⤓ {{ a.name }}</button>
                      <span v-else class="dim">{{ a.name }}（失败：{{ a.error }}）</span>
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <AppModal v-if="pgHistoryOpen" wide @close="pgHistoryOpen = false">
              <div class="pg-modal" data-testid="pg-history-modal">
                <h4>运行历史</h4>
                <table v-if="pgHistory.length">
                  <thead><tr><th>样本</th><th>版本</th><th>状态</th><th>耗时</th><th>发起人</th><th>时间</th></tr></thead>
                  <tbody>
                    <tr v-for="r in pgHistory" :key="r.run_id" class="pg-hist-row"
                        @click="pgLoadRun(r.run_id)">
                      <td>{{ pgSampleName(r.sample_id) }}</td><td>v{{ r.version }}</td>
                      <td>{{ runLabel(r.status) }}</td>
                      <td>{{ r.duration_ms ?? "—" }}</td>
                      <td>{{ initiatorLabel(r.created_by) }}</td>
                      <td class="dim">{{ r.created_at.slice(0, 19).replace("T", " ") }}</td>
                    </tr>
                  </tbody>
                </table>
                <p v-else class="dim">还没有运行记录。</p>
                <div class="modal-actions">
                  <button @click="pgHistoryOpen = false">关闭</button>
                </div>
              </div>
            </AppModal>

            <AppModal v-if="pgDetailOpen" @close="pgDetailOpen = false">
              <div class="pg-modal" data-testid="pg-detail">
                <h4>运行详情</h4>
                <p v-if="pgDetailLoading" class="dim">正在加载运行详情…</p>
                <p v-else-if="pgDetailError" class="err-text">{{ pgDetailError }}</p>
                <ul v-else-if="pgDetail" class="pg-detail">
                  <li>技能版本：<strong>v{{ pgDetail.version }}</strong></li>
                  <li>处理模式：{{ pgDetail.processing_mode === "fast" ? "极速" : "均衡" }}</li>
                  <li>状态：{{ runLabel(pgDetail.status) }}</li>
                  <li>耗时：{{ pgDetail.duration_ms ?? "—" }} ms</li>
                  <li>Transaction ID：<code>{{ pgDetail.transaction_id }}</code></li>
                  <li>File ID：<code>{{ pgDetail.file_id }}</code></li>
                </ul>
                <div class="modal-actions">
                  <button @click="pgDetailOpen = false">关闭</button>
                </div>
              </div>
            </AppModal>
          </section>

          <section class="card-panel block">
            <h3 class="block-title">模型对比（多模型并排试运行）</h3>
            <p class="dim">当前定义在一份样本上试跑，可多模型并排对比。</p>
            <input v-model="dryProviders" list="dl-providers"
                   placeholder="模型列表，逗号分隔；空=默认" />
            <label class="file-btn"><input type="file" hidden @change="dryRun" :disabled="running" />
              <span class="btn-like">{{ running ? "⏳ 试跑中…" : "上传样本试跑" }}</span></label>
            <div v-if="dryRuns.length" class="runs">
              <div v-for="r in dryRuns" :key="r.provider" class="run">
                <div class="run-head">
                  <strong>{{ r.provider }}</strong>
                  <span v-if="r.ok && r.vision_pages" class="vis">🖼 {{ r.vision_pages }} 页图</span>
                  <span v-if="r.ok" class="dim">
                    {{ r.usage?.prompt_tokens }}+{{ r.usage?.completion_tokens }} tokens</span>
                  <span v-else class="rulefail">{{ r.error }}</span>
                </div>
                <table v-if="r.ok && r.result">
                  <tbody>
                    <tr v-for="(cell, name) in scalarCells(r.result)" :key="name">
                      <td class="dim">{{ name }}</td>
                      <td>{{ cell.$value || "—" }}</td>
                      <td><span class="conf">{{ cell.$confidence === null ? "未评分" : `c${cell.$confidence}` }}</span></td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          <section v-if="!isNew" class="card-panel block">
            <h3 class="block-title">金样本回归（发布门禁）</h3>
            <p class="dim">挂固定样本＋期望值，发布前跑回归防退化。</p>
            <label class="file-btn"><input type="file" hidden @change="pickGolden" />
              <span class="btn-like">{{ goldenFile ? goldenFile.name : "选择样本文件" }}</span></label>
            <textarea v-model="goldenExpected" rows="3"
                      placeholder='期望值 JSON，如 {"invoice_no": "INV-1"}'></textarea>
            <button :disabled="!goldenFile" @click="addGolden">挂载金样本</button>
            <button v-if="selectedVersion" @click="goldenCheck" :disabled="checking">
              {{ checking ? "⏳ 回归中…" : `对 v${selectedVersion} 跑回归` }}</button>
            <div v-if="goldenReport" class="golden-report">
              <template v-if="goldenReport.samples === 0">
                <p class="dim">{{ goldenReport.note }}</p>
              </template>
              <template v-else>
                <p><strong>{{ goldenReport.samples }}</strong> 个样本，平均匹配率
                  <strong :class="{ warn: (goldenReport.avg_match_rate ?? 0) < 1 }">
                    {{ Math.round((goldenReport.avg_match_rate ?? 0) * 100) }}%</strong></p>
                <div v-for="(rep, i) in goldenReport.reports" :key="i" class="rep">
                  <template v-if="rep.ok">
                    <span class="dim">样本 {{ i + 1 }}：匹配 {{ Math.round((rep.match_rate ?? 0) * 100) }}%</span>
                    <div v-for="(d, fname) in rep.diffs" :key="fname" class="rulefail">
                      {{ fname }}: 期望「{{ d.expected }}」→ 实得「{{ d.got }}」</div>
                  </template>
                  <span v-else class="rulefail">样本 {{ i + 1 }}：{{ rep.error }}</span>
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
import { copyText } from "../clipboard";
import { moveItem, useListReorder } from "../reorder";
import { toast } from "../toast";
// R1: where a rules_first field value came from (rule / model / review)
const sourceLabel = (s: string) => ({ rule: "规则", model: "模型", review: "待复核" }[s] ?? s);

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
const verLabel = (s: string) => VERSION_LABELS[s] ?? s;
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
      sample: { original_name: outSampleName(), original_ext: ".pdf",
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
function outSampleName(): string {
  const s = pgSamples.value.find((x) => x.id === pgSelected.value[0]);
  return s?.file_name ?? "示例扫描件.pdf";
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
      toast.error("请先切换到标准提取并配置字段（fast_mode_requires_standard_fields）");
      return;
    }
    if (p.skill_mode === "advanced" && topFields) {
      toast.ok("极速模式将按标准字段提取，高级分类配置已保留但不生效");
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
  return m ? `（当前 v${m.published_version}）` : "";
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
const LAYOUTS = [
  { value: "single", label: "单份文档", desc: "一个文件就是一份文档，只判断类别，不拆分" },
  { value: "mixed", label: "多种单据混合", desc: "一个文件含多份、不同类型的单据，逐页分类并拆分" },
  { value: "same_type_independent", label: "同类单据 · 每页一份",
    desc: "每页都是一份独立单据，类别相同" },
  { value: "same_type_continuous", label: "同类单据 · 可跨页",
    desc: "同一类单据，一份可能连续占多页" },
] as const;
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
const catName = (c: CategorySpec) =>
  c.is_other ? "Other（兜底）" : (c.doc_type || "未命名类别");
function catBadge(c: CategorySpec): string {
  if (c.handler === "existing_skill") return "引用";
  if (c.handler === "classify_only") return "仅分类";
  return `${c.fields.length} 字段`;
}
function catSummary(c: CategorySpec): string {
  if (c.handler === "existing_skill")
    return c.skill_ref?.skill_code ? `引用技能「${refName(c)}」` : "引用已有技能（未选择）";
  if (c.handler === "classify_only") return "仅分类，不提取";
  return `本技能内配置 ${c.fields.length} 个字段`;
}
function gotoCategoryFields(c: CategorySpec) {
  activeCatId.value = c.id;
  step.value = "fields";
}

// —— F-01: which model / channel actually runs, spelled out in the editor ——
const channelLabel = (c?: string | null) => (c === "rules_first" ? "规则优先" : "模型提取");
function modelLabel(name?: string | null): string {
  return name || `平台默认${activeProvider.value ? `（${activeProvider.value}）` : ""}`;
}
const classifierEffective = computed(() => {
  const mb = pkg.value!.model_binding;
  return mb.classifier || `与抽取模型相同 · ${modelLabel(mb.extractor)}`;
});
/** Mirrors app/skillengine/effective.category_subpackage. */
function catEffective(c: CategorySpec): string {
  const p = pkg.value!;
  if (c.handler === "classify_only") return "生效配置：只分类，不调用抽取模型。";
  if (c.handler === "existing_skill") {
    const m = refMeta(c);
    if (!m) return "选择要复用的技能后，这里显示它实际使用的模型与提取通道。";
    return `生效配置：沿用「${m.name}」自己的抽取模型 ${modelLabel(m.extractor)}、`
      + `提取通道「${channelLabel(m.extraction_channel)}」；复核策略与解析器沿用本技能。`;
  }
  return `生效配置：本技能的抽取模型 ${modelLabel(p.model_binding.extractor)}、`
    + `提取通道「${channelLabel(p.extraction_channel)}」（在「基础」中设置）。`;
}
/** F-02 §3.18: what changing the referenced skill does to this one (R10
 *  pinning: a publish freezes the referenced version; drafts follow latest). */
function refImpact(c: CategorySpec): string {
  const v = c.skill_ref?.version;
  const pin = v != null
    ? `固定使用 v${v}：原技能之后发布的新版本不影响本技能。`
    : "跟随最新发布版：本技能发布时锁定原技能当时的最新发布版；原技能再发布新版本后，"
      + "本技能需重新发布才会使用，测试页试跑始终用原技能最新发布版。";
  return `${pin}原技能可能被多个技能共享，在原技能里修改并发布会按上述规则影响所有引用它的技能。`;
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

function stLabel(status: string): string { return STATUS_LABELS[status] ?? status; }
function runLabel(status: string): string { return RUN_STATUS_LABELS[status] ?? status; }
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

const sampleStatus = ref<Record<string, string>>({});
function pgStatus(sampleId: string): string {
  return sampleStatus.value[sampleId] ?? "";
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
    toast.ok("样本已上传");
  } catch (err) {
    toast.error(`样本上传失败：${err instanceof Error ? err.message : err}`);
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
      sampleStatus.value[run.sample_id] = "运行中…";
    }
    pgPollTransaction(r.transaction_id);
    return r.transaction_id;
  } catch (err) {
    if (request === runRequest) toast.error(`运行失败：${err instanceof Error ? err.message : err}`);
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
        "运行超时（超过 10 分钟仍未结束），已停止轮询。请到「运行历史」查看，或稍后重试。";
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
      pgTimeoutNote.value = `测试状态或结果加载失败：${err instanceof Error ? err.message : err}。请在运行历史中重试查看。`;
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
    sampleStatus.value[sid] =
      f.status === "error" || f.status === "rejected" ? "失败"
        : f.status === "pending_verification"
          ? (reviews ? `完成（待复核 ${reviews}）` : "完成（待复核）")
          : f.status === "split" ? (reviews ? `完成（待复核 ${reviews}）` : "完成")
            : ["completed", "passed"].includes(f.status) ? "完成" : "运行中…";
  }
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
    pgTimeoutNote.value = `提取结果加载失败：${err instanceof Error ? err.message : err}`;
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
    if (pgCurrentRun.value === runId) pgDetailError.value = `详情加载失败：${err instanceof Error ? err.message : err}`;
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
  if (!sid) { toast.error("找不到该文件对应的样本，请直接勾选样本运行"); return; }
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
    toast.error(`载入运行失败：${err instanceof Error ? err.message : err}`);
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
  if (pgRunStatus.value === "failed") return "试抽失败，请检查字段配置或稍后重试。";
  if (fieldTrialDoc.value?.error) return fieldTrialDoc.value.error;
  if (["completed", "needs_review"].includes(pgRunStatus.value) && !fieldTrialDocs.value.length)
    return activeCat.value ? "当前样本未识别到这个类别，请切换类别或样本。" : "本次试抽没有返回字段结果。";
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
    return fieldTrialBoxes.value.some((b) => b.key === name) ? "已定位" : "已提取";
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
      const sub = m === "never" ? "无需复核"
        : m === "always" ? "全部复核"
          : `${th ?? 2} 分以下复核（默认）`;
      return { key: "basic" as const, label: "基础", sub, bad: false };
    })(),
    ...(p.skill_mode === "advanced"
      ? [{ key: "classify" as const, label: "文档分类",
           sub: `${(p.categories ?? []).length} 个类别 · ${
             LAYOUTS.find((l) => l.value === (p.document_layout ?? "single"))?.label ?? ""}`,
           bad: badCats }]
      : []),
    { key: "fields" as const, label: "字段提取",
      // #21: an advanced skill keeps its fields inside the categories, so the
      // subtitle must count those (it used to read "0 个字段")
      sub: p.skill_mode === "advanced"
        ? `${(p.categories ?? []).length} 个类别 / ${catFieldCount(p)} 个字段`
        : `${p.fields.length} 个字段${p.output_shape === "list" ? " · List" : ""}`,
      bad: badFields },
    (() => {   // 9.15 WP6 (图18-20): subtitle counts enabled output items
      const o = p.output;
      if (!o?.enabled || o.action === "off") {
        return { key: "output" as const, label: "文件产出",
                 sub: "未开启下载", bad: false };
      }
      const n = 1 + (o.searchable_pdf ? 1 : 0);   // an action + maybe searchable
      return { key: "output" as const, label: "文件产出",
               sub: `已开启 ${n} 项`, bad: false };
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
  return window.confirm("有未保存的修改，离开将丢失。确定离开？");
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
  if (skipped) toast.ok(`跳过 ${skipped} 个同名字段（未覆盖你的修改）`);
}
function undoGenerate() {
  const last = undoStack.value.pop();
  if (!pkg.value || !last) return;
  if (last.cat) {
    const cat = pkg.value.categories?.find((c) => c.id === last.cat);
    if (!cat) { toast.error("该类别已删除，无法撤销"); return; }
    cat.fields = last.fields;
    activeCatId.value = cat.id;
  } else {
    pkg.value.fields = last.fields;
  }
  generatedExamples.value[last.exampleKey] = last.examples;
  expandedField.value = null;
  activeFieldName.value = null;
  toast.ok("已撤销上一次生成");
}
function clearFields() {
  if (!pkg.value || !curFields.value.length) return;
  if (!confirm(`清空全部 ${curFields.value.length} 个字段？可用「撤销上次生成」恢复。`)) return;
  pushUndo();
  setCurFields([]);
  generatedExamples.value[exampleScope.value] = {};
  expandedField.value = null;
}

/** Save = update the draft on screen (P04). Outbound payloads are always v2. */
const saveLabel = computed(() =>
  selectedStatus.value && selectedStatus.value !== "draft" ? "另存为新草稿" : "保存");
const saving = ref(false);
function outbound(): SkillPackage {
  return JSON.parse(JSON.stringify({ ...pkg.value!, schema_version: 2 }));
}

function validPkg(): boolean {
  if (!pkg.value) return false;
  if (!pkg.value.name.trim() || !pkg.value.skill_code.trim()) {
    const missingName = !pkg.value.name.trim();
    toast.error(missingName ? "请先填写技能名称，再保存或试抽" : "请先填写 skill_code，再保存或试抽");
    tab.value = "design";
    step.value = "basic";
    void nextTick(() => (missingName ? skillNameInput.value : skillCodeInput.value)?.focus());
    return false;
  }
  if (!pkg.value.fields.length && pkg.value.skill_mode !== "advanced") {
    toast.error("至少定义一个字段"); return false;
  }
  const checkFields = (fields: FieldSpec[]): boolean => {
    const names = new Set<string>();
    for (const field of fields) {
      if (!field.name.trim()) { toast.error("请填写所有字段名称"); step.value = "fields"; return false; }
      if (names.has(field.name.trim())) { toast.error(`字段名称重复：${field.name}`); step.value = "fields"; return false; }
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
      catch (e) { adopted = false; toast.error(`技能已创建，样本关联失败：${e instanceof Error ? e.message : e}`); }
      if (epoch !== skillEpoch) return false;
      preservedCreatedCode = payload.skill_code;
      markClean();
      await router.replace(`/skills/${payload.skill_code}`);
      await nextTick();
      if (epoch !== skillEpoch || props.code !== payload.skill_code) return false;
      if (!await load(1)) return false;
      toast.ok("技能已创建（v1 草稿）");
      markClean();
      return adopted;
    } else if (selectedStatus.value === "draft" && selectedVersion.value) {
      const version = selectedVersion.value;
      const r = await api.skillSaveDraft(skillCode, version, payload, changelog.value);
      if (epoch !== skillEpoch) return false;
      toast.ok(`v${selectedVersion.value} 草稿已保存（未新增版本）`);
      for (const w of r.warnings ?? []) toast.ok(w);
      if (!await load(version)) return false;
    } else {
      const r = await api.skillNewDraft(skillCode, payload, changelog.value);
      if (epoch !== skillEpoch) return false;
      toast.ok(`已以 v${selectedVersion.value} 为底新建 v${r.version} 草稿`);
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
    toast.ok(`已新建 v${r.version} 草稿`);
    await load(r.version);
    markClean();
  } catch (e) { toast.error(e); }
}
async function publishSelected() {
  if (sampleUploading.value || saving.value || fieldTrialStarting.value || !selectedVersion.value) return;
  try {
    await api.skillPublish(props.code, selectedVersion.value);
    toast.ok(`v${selectedVersion.value} 已发布（旧版本自动归档）`);
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
  const ok = confirm(`确定删除 v${selectedVersion.value}（${verLabel(selectedStatus.value ?? "")}）？\n`
    + "该版本的定义将从版本历史中移除，技能与其它版本不受影响。");
  if (!ok) return;
  try {
    await api.skillDeleteVersion(props.code, selectedVersion.value);
    toast.ok(`v${selectedVersion.value} 已删除`);
    await load();
  } catch (e) { toast.error(e); }
}

const copied = ref(false);
async function copyCode() {
  try {
    await copyText(pkg.value?.skill_code ?? "");
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch { toast.error("复制失败，请手动选择技能代码"); }
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
    toast.ok(`表格导入完成：新增 ${mergeDraft(r.fields)} 个字段。可用「✨ AI 补全说明」扩写规则`);
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
    toast.ok(`已补全 ${n} 处字段说明（${r.provider_used}），请核对后保存`);
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
  catch { toast.error("期望值不是合法 JSON"); return; }
  try {
    await api.goldenAdd(props.code, goldenFile.value, expected);
    toast.ok("金样本已挂载");
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
