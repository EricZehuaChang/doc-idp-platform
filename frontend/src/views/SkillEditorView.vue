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
            <button class="new-ver" @click="newVersion">＋ 新建版本</button>
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
          <button class="primary" :disabled="saving" @click="save">
            💾 {{ isNew ? "创建技能" : saveLabel }}</button>
          <button v-if="!isNew && selectedStatus === 'draft'" class="confirm"
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
        <div v-if="isNew && !pkg.fields.length" class="guide card-panel">
          <span class="step"><b>1</b> 自动生成或起草字段</span>
          <span class="arrow">→</span>
          <span class="step"><b>2</b> 逐字段核对修改（点 ✎ 编辑）</span>
          <span class="arrow">→</span>
          <span class="step"><b>3</b> 测试页签试跑，创建并发布</span>
        </div>

        <div class="design-body">
          <!-- flow rail: 基础 → (文档分类) → 字段提取 → 文件产出 -->
          <nav class="flow-rail" aria-label="设计步骤">
            <button v-for="n in flowNodes" :key="n.key" class="node"
                    :class="{ on: step === n.key, bad: n.bad }"
                    @click="goto(n.key)">
              <span class="dot"></span>
              <span class="lbl">{{ n.label }}</span>
              <span class="sub dim">{{ n.sub }}</span>
            </button>
          </nav>

          <!-- —— 基础 —— -->
          <section v-show="step === 'basic'" class="card-panel block step-panel">
            <h3 class="block-title">基本信息</h3>
            <div class="basic-grid">
              <label>技能名称
                <input v-model="pkg.name" placeholder="例如：XCMG 海外发票" /></label>
              <label>skill_code（英文，创建后不可改）
                <input v-model="pkg.skill_code" class="mono" :disabled="!isNew" /></label>
              <label class="span2">描述
                <textarea v-model="pkg.description" rows="2"
                          placeholder="这个技能处理什么文档、服务什么业务（给同事看的说明）"></textarea>
              </label>
              <label v-if="!isNew" class="span2">版本介绍（v{{ selectedVersion }} 的说明）
                <input v-model="changelog"
                       placeholder="这一版改了什么，例如：新增税额字段、放宽发票号正则" />
              </label>
            </div>

            <h4 class="sub-title">处理模式（图02–05）</h4>
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
                      @click="pkg.skill_mode = 'standard'">
                <strong>标准</strong>
                <span class="dim">一份文档、一组字段，适合大多数单据</span></button>
              <button class="mode-card" :disabled="pkg.processing_mode === 'fast'"
                      :class="{ on: pkg.skill_mode === 'advanced' }"
                      @click="pkg.skill_mode = 'advanced'">
                <strong>高级</strong>
                <span class="dim">一份文件多种单据，先分类再提取</span></button>
            </div>
            <p v-if="pkg.skill_mode === 'advanced'" class="dim adv-note">
              「文档分类」步骤（类别、识别说明、引用已有技能）随高级提取批次开放；
              当前保存会保留技能模式标记，发布前需要完成分类配置。</p>

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
                         @focus="comboOpen" @input="comboTyped" @blur="comboClose" /></label>
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

          <!-- —— 文档分类（高级模式，图06–11） —— -->
          <section v-show="step === 'classify'" class="card-panel block step-panel">
            <h3 class="block-title">文档分类</h3>
            <p class="dim cat-intro">
              一份文件包含多种/多份单据时，先分类再提取。每个类别选择提取方式：
              在本技能内配置字段、复用已有技能、或只分类不提取。</p>

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
                <div class="cat-handler">
                  <span class="dim">提取方式</span>
                  <button class="hseg" :class="{ on: c.handler === 'inline' }"
                          @click="setHandler(c, 'inline')">本技能内配置字段</button>
                  <button class="hseg" :class="{ on: c.handler === 'existing_skill' }"
                          @click="setHandler(c, 'existing_skill')">使用已有技能</button>
                  <button class="hseg" :class="{ on: c.handler === 'classify_only' }"
                          @click="setHandler(c, 'classify_only')">仅分类，不提取</button>
                </div>

                <!-- inline: fields edited with the same machinery as the top level -->
                <div v-if="c.handler === 'inline'" class="cat-fields">
                  <div class="seg-row">
                    <span class="dim">输出结构</span>
                    <button class="hseg" :class="{ on: c.output_shape !== 'list' }"
                            @click="c.output_shape = 'object'">Object</button>
                    <button class="hseg" :class="{ on: c.output_shape === 'list' }"
                            @click="c.output_shape = 'list'">List</button>
                  </div>
                  <p v-if="!c.fields.length" class="dim">该类别还没有字段。</p>
                  <div class="field-tree">
                    <FieldCard v-for="(f, fi) in c.fields" :key="f.name || fi"
                               :field="f" :index="fi"
                               :drag-from="reorder.from.value ?? -1"
                               :drag-over="reorder.over.value ?? -1"
                               @edit="openEdit(c.fields, fi)"
                               @remove="c.fields.splice(fi, 1)"
                               @edit-column="(x) => openEdit(f.columns, x, true)"
                               @add-column="openAdd(f.columns, true)"
                               @grip-down="startFieldDrag" />
                    <button class="add-field" @click="openAdd(c.fields)">＋ 添加字段</button>
                  </div>
                </div>

                <!-- existing_skill: R10 reference (图16) -->
                <div v-else-if="c.handler === 'existing_skill'" class="cat-ref">
                  <div class="ref-row">
                    <select :value="c.skill_ref?.skill_code ?? ''"
                            @change="setRefSkill(c, ($event.target as HTMLSelectElement).value)">
                      <option value="">选择要复用的技能…</option>
                      <option v-for="r in refSkills" :key="r.skill_code"
                              :value="r.skill_code">{{ r.name }}（{{ r.skill_code }}）</option>
                    </select>
                    <select v-if="c.skill_ref?.skill_code"
                            :value="c.skill_ref?.version ?? ''"
                            @change="setRefVersion(c, ($event.target as HTMLSelectElement).value)">
                      <option value="">跟随最新发布版{{ refVersionHint(c) }}</option>
                      <option v-for="v in refVersions(c)" :key="v" :value="v">v{{ v }}</option>
                    </select>
                  </div>
                  <div v-if="c.skill_ref?.skill_code" class="ref-banner">
                    <span class="dim">
                      已关联技能：{{ refName(c) }}（{{ c.skill_ref.skill_code }}）。本类别复用已有技能，
                      字段在此只读；如需修改，请打开原技能编辑器。</span>
                    <a class="btn-like" :href="`/#/skills/${c.skill_ref.skill_code}`"
                       target="_blank">打开技能编辑器</a>
                  </div>
                  <ul v-if="refFields(c).length" class="ref-fields">
                    <li v-for="f in refFields(c)" :key="f.name">
                      <code>{{ f.name }}</code><span class="dim">{{ f.type }}</span>
                      <span class="ex dim" :title="f.instruction">{{ f.instruction }}</span>
                    </li>
                  </ul>
                </div>

                <p v-else-if="c.handler === 'classify_only'" class="dim">
                  该类别只输出所属文档类型（doc_type），不抽取字段；审单时可直接通过。</p>

                <label v-if="!c.is_other" class="cat-rules">类别附加规则（可选）
                  <textarea v-model="c.additional_rules" rows="1"
                            placeholder="只作用于该类别的补充说明"></textarea></label>
              </div>
            </div>
            <button class="add-cat" @click="addCategory">＋ 添加类别</button>

            <details class="rules-fold">
              <summary>分类附加规则（可选，作用于整个分类步骤）</summary>
              <textarea v-model="pkg.classification_rules" rows="2"
                        placeholder="如：第 1 页通常是发票；连续的行程单算一份文档"></textarea>
            </details>
          </section>

          <!-- —— 字段提取 —— -->
          <section v-show="step === 'fields'" class="card-panel block step-panel fields-step">
            <SamplePanel :skill-code="code" class="sample-col" />
            <div class="fields-col">
              <div class="block-head">
                <h3 class="block-title">字段配置</h3>
                <div class="seg" role="group" aria-label="输出结构">
                  <button :class="{ on: pkg.output_shape !== 'list' }"
                          title="每个字段一个键（默认）"
                          @click="pkg.output_shape = 'object'">Object</button>
                  <button :class="{ on: pkg.output_shape === 'list' }"
                          title="结果为对象数组，每行一组字段（如逐行明细）"
                          @click="pkg.output_shape = 'list'">List</button>
                </div>
                <button class="mini" :disabled="!undoStack.length"
                        title="撤销上一次自动生成/起草的结果"
                        @click="undoGenerate">↩ 撤销上次生成</button>
                <button class="mini primary" @click="genModal = true">✨ 自动生成字段</button>
                <details class="more-draft">
                  <summary class="mini btn-like">更多起草方式 ▾</summary>
                  <div class="more-pop">
                    <label class="file-btn slim">
                      <input type="file" hidden @change="probe" :disabled="probing" />
                      <span class="btn-like">{{ probing ? "⏳ 分析中…" : "⚡ 样本预标注" }}</span>
                    </label>
                    <button class="mini" :class="{ primary: textPanel }"
                            @click="textPanel = !textPanel">📝 描述生成</button>
                    <label class="file-btn slim">
                      <input type="file" accept=".xlsx,.csv,.tsv" hidden @change="tableImport" />
                      <span class="btn-like">📊 表格导入</span>
                    </label>
                    <button class="mini" :disabled="!pkg.fields.length || enriching"
                            title="把字段说明扩写为完整抽取指令"
                            @click="enrich">{{ enriching ? "⏳ 补全中…" : "✨ AI 补全说明" }}</button>
                  </div>
                </details>
                <button v-if="pkg.fields.length" class="mini danger"
                        @click="clearFields">清空全部字段</button>
              </div>

              <div v-if="textPanel" class="text-panel">
                <textarea v-model="draftText" rows="4"
                          placeholder="用一段话描述要抽取什么。例：从海外发票抽取发票号（去掉空格和连字符）、开票日期（统一 YYYY-MM-DD）、币种（ISO 三位码）、总金额（保留两位小数）…"></textarea>
                <div class="text-panel-act">
                  <span class="dim">生成的字段会预填到下方，可修改后再保存（消耗少量 token）</span>
                  <button class="primary" :disabled="drafting || !draftText.trim()"
                          @click="draftFromText">{{ drafting ? "⏳ 起草中…" : "生成字段草稿" }}</button>
                </div>
              </div>

              <p v-if="!pkg.fields.length" class="dim pad">
                还没有字段。用「✨ 自动生成字段」从样本/描述起草，或「＋ 添加字段」手动创建。
              </p>
              <p v-if="pkg.fields.length > 1" class="dim drag-tip">
                拖动字段左侧 ⠿ 可调整顺序；顺序即抽取结果与导出的字段顺序，保存后生效。
              </p>
              <div class="field-tree" ref="treeEl">
                <FieldCard v-for="(f, i) in pkg.fields" :key="f.name || i" :field="f" :index="i"
                           :drag-from="reorder.from.value ?? -1" :drag-over="reorder.over.value ?? -1"
                           @edit="openEdit(pkg!.fields, i)"
                           @remove="pkg!.fields.splice(i, 1)"
                           @edit-column="(ci) => openEdit(f.columns, ci, true)"
                           @add-column="openAdd(f.columns, true)"
                           @grip-down="startFieldDrag" />
                <button class="add-field" @click="openAdd(pkg!.fields)">＋ 添加字段</button>
              </div>

              <details class="rules-fold">
                <summary>附加规则（可选，自由文本，进提示词）</summary>
                <textarea v-model="pkg.additional_rules" rows="2"
                          placeholder="如：金额一律保留两位小数；日期统一 YYYY-MM-DD"></textarea>
              </details>
            </div>
          </section>

          <!-- —— 文件产出（WP6 上线前保持诚实占位） —— -->
          <section v-show="step === 'output'" class="card-panel block step-panel">
            <h3 class="block-title">文件产出</h3>
            <p class="dim">
              产出文件（按命名规则重命名、按文档拆分、可检索 PDF）随「文件产出」批次开放，
              当前版本一律不改变上传原件。已配置的下载开关不会丢失。</p>
          </section>
        </div>
      </template>

      <!-- —— 测试 tab：试运行 + 金样本回归（Playground 将在极速模式批次替换试运行） —— -->
      <template v-else>
        <div class="test-grid">
          <!-- —— 9.15 WP5 Playground (图21) —— -->
          <section v-if="!isNew" class="card-panel block playground" data-testid="playground">
            <h3 class="block-title">Playground（测试运行）</h3>
            <p class="dim">勾选样本运行当前定义（含未发布草稿）；测试任务不会进入任务列表、统计、
              数据柜、审单队列，也不会触发 webhook。</p>
            <div class="pg-grid">
              <div class="pg-samples">
                <input v-model="pgSearch" placeholder="搜索样本…" />
                <label class="file-btn"><input type="file" hidden @change="pgUpload" />
                  <span class="btn-like">上传样本</span></label>
                <ul class="pg-list">
                  <li v-for="s in pgFilteredSamples" :key="s.id">
                    <label>
                      <input type="checkbox" :value="s.id" v-model="pgSelected" />
                      <span>{{ s.file_name }}</span>
                      <small v-if="pgStatus(s.id)" class="dim">{{ pgStatus(s.id) }}</small>
                    </label>
                  </li>
                  <li v-if="!pgFilteredSamples.length" class="dim">还没有样本</li>
                </ul>
                <button class="primary" data-testid="pg-run" :disabled="!pgSelected.length || pgPolling"
                        @click="pgRun">运行测试（{{ pgSelected.length }}）</button>
              </div>
              <div class="pg-result">
                <div class="pg-top">
                  <strong v-if="pgCurrent">{{ pgCurrent.file_name }}</strong>
                  <span v-if="pgCurrent" class="chip" :class="`chip-${pgCurrent.status}`">{{ pgCurrent.status }}</span>
                  <span v-if="pgDuration" class="dim">{{ pgDuration }} ms</span>
                  <button class="btn-like" @click="pgHistoryOpen = true; pgLoadHistory()" data-testid="pg-history">运行历史</button>
                  <button v-if="pgCurrentRun" class="btn-like" @click="pgDetailOpen = true">详情</button>
                </div>
                <p v-if="pgFast" class="dim">极速模式不提供定位高亮；结果不带置信评分（未评分）。</p>
                <p v-if="!pgDocs.length" class="dim">运行后在这里查看提取结果。</p>
                <div v-for="doc in pgDocs" :key="`${doc.file_id}-${doc.doc_index}`" class="pg-doc">
                  <div class="pg-doc-head">
                    <strong>文档 {{ doc.doc_index }}</strong>
                    <span v-if="doc.doc_type" class="chip">{{ doc.doc_type }}</span>
                    <span class="dim">第 {{ doc.page_range }} 页</span>
                    <span v-if="doc.extraction_status === 'not_requested'" class="dim">
                      仅分类，没有需要校验的字段</span>
                  </div>
                  <table v-if="doc.data && !Array.isArray(doc.data) && Object.keys(doc.data).length">
                    <tbody>
                      <tr v-for="(v, k) in (doc.data as Record<string, unknown>)" :key="k">
                        <td class="dim">{{ k }}
                          <span v-if="doc.review_fields.includes(String(k))" class="badge-r">待复核</span></td>
                        <td>{{ typeof v === "object" ? JSON.stringify(v) : (v || "—") }}</td>
                      </tr>
                    </tbody>
                  </table>
                  <table v-else-if="Array.isArray(doc.data) && doc.data.length">
                    <tbody>
                      <tr v-for="(row, i) in doc.data" :key="i">
                        <td>{{ JSON.stringify(row) }}</td>
                      </tr>
                    </tbody>
                  </table>
                  <p v-else-if="doc.extraction_status !== 'not_requested'" class="dim">（无结果）</p>
                </div>
              </div>
            </div>

            <div v-if="pgHistoryOpen" class="modal-mask" @click.self="pgHistoryOpen = false">
              <div class="modal pg-modal">
                <h4>运行历史</h4>
                <table v-if="pgHistory.length">
                  <thead><tr><th>样本</th><th>版本</th><th>状态</th><th>耗时</th><th>发起人</th><th>时间</th></tr></thead>
                  <tbody>
                    <tr v-for="r in pgHistory" :key="r.run_id" class="pg-hist-row"
                        @click="pgLoadRun(r.run_id)">
                      <td>{{ pgSampleName(r.sample_id) }}</td><td>v{{ r.version }}</td>
                      <td>{{ r.status }}</td>
                      <td>{{ r.duration_ms ?? "—" }}</td><td>{{ r.created_by }}</td>
                      <td class="dim">{{ r.created_at.slice(0, 19).replace("T", " ") }}</td>
                    </tr>
                  </tbody>
                </table>
                <p v-else class="dim">还没有运行记录。</p>
                <button @click="pgHistoryOpen = false">关闭</button>
              </div>
            </div>

            <div v-if="pgDetailOpen && pgDetail" class="modal-mask" @click.self="pgDetailOpen = false">
              <div class="modal pg-modal" data-testid="pg-detail">
                <h4>运行详情（图22）</h4>
                <ul class="pg-detail">
                  <li>技能版本：<strong>v{{ pgDetail.version }}</strong></li>
                  <li>处理模式：{{ pgDetail.processing_mode === "fast" ? "极速" : "均衡" }}</li>
                  <li>状态：{{ pgDetail.status }}</li>
                  <li>耗时：{{ pgDetail.duration_ms ?? "—" }} ms</li>
                  <li>Transaction ID：<code>{{ pgDetail.transaction_id }}</code></li>
                  <li>File ID：<code>{{ pgDetail.file_id }}</code></li>
                </ul>
                <button @click="pgDetailOpen = false">关闭</button>
              </div>
            </div>
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

    <FieldEditModal v-if="editing" :field="editing.spec" :is-new="editing.isNew"
                    :is-column="editing.isColumn"
                    @save="commitEdit" @cancel="editing = null" />
    <SkillApiModal v-if="apiModal" :skill-code="code" @close="apiModal = false" />
    <GenerateFieldsModal v-if="genModal" :skill-code="code"
                         @apply="applyGenerated" @close="genModal = false" />
  </main>
  <main v-else class="editor"><Skeleton :rows="8" /></main>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { onBeforeRouteLeave, useRouter } from "vue-router";
import { api, downloadFile, type CategorySpec, type DryRunEntry, type FieldCell,
         type FieldSpec, type SkillPackage, type TxnDocument } from "../api";
import FieldCard from "../components/FieldCard.vue";
import FieldEditModal from "../components/FieldEditModal.vue";
import GenerateFieldsModal from "../components/GenerateFieldsModal.vue";
import SamplePanel from "../components/SamplePanel.vue";
import SkillApiModal from "../components/SkillApiModal.vue";
import Skeleton from "../components/Skeleton.vue";
import { VERSION_LABELS } from "../labels";
import { moveItem, useListReorder } from "../reorder";
import { toast } from "../toast";

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
const genModal = ref(false);
const undoStack = ref<FieldSpec[][]>([]);   // one-shot undo for generation merges

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

async function load(version?: number) {
  if (isNew.value) {
    pkg.value = blankPkg();
    versions.value = [];
    selectedVersion.value = null;
    return;
  }
  try {
    const d = await api.skillDetail(props.code, version);
    pkg.value = { ...blankPkg(), ...(d.latest_package ?? {}),
                  skill_code: d.skill_code, name: d.latest_package?.name || d.name };
    // the detail dump of a v1 row fills schema_version with the Loose default 1;
    // the editor always works on (and saves) a v2 working copy — §5.2, the
    // editor is the in-place migration path
    pkg.value.schema_version = 2;
    versions.value = d.versions;
    selectedVersion.value = d.selected_version ?? null;
    changelog.value = d.versions.find((v) => v.version === d.selected_version)?.changelog ?? "";
  } catch (e) { toast.error(e); }
}
watch(() => props.code, () => load(), { immediate: true });
function selectVersion(v: number) { load(v); }

function goto(k: string) {
  step.value = k as typeof step.value;
}

// —— 处理模式 (9.15 WP5, 图02–05): switching to fast keeps the saved review /
// advanced config intact (greyed but not wiped); switching back restores it ——
const fastMaxPages = 5;   // mirror of IDP_FAST_MAX_PAGES default (server enforces)
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
                        fields: { name: string; type: string; instruction: string }[] }[]>([]);
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
  pkg.value!.categories!.push(blankCategory());
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

// —— Playground (9.15 WP5, 图21): sample test runs against the current draft ——
interface PgSample { id: string; file_name: string; skill_code: string | null; created_at: string }
const pgSamples = ref<PgSample[]>([]);
const pgSearch = ref("");
const pgSelected = ref<string[]>([]);
const pgPolling = ref(false);
const pgHistoryOpen = ref(false);
const pgDetailOpen = ref(false);
const pgHistory = ref<{ run_id: string; sample_id: string; version: number;
                        status: string; duration_ms: number | null;
                        created_by: string; created_at: string }[]>([]);
const pgDetail = ref<{ version: number; processing_mode: string; status: string;
                       duration_ms: number | null; transaction_id: string;
                       file_id: string | null } | null>(null);
const pgDocs = ref<TxnDocument[]>([]);
const pgCurrent = ref<{ file_id: string; file_name: string; status: string } | null>(null);
const pgCurrentRun = ref<string | null>(null);
const pgCurrentMode = ref<string>("balanced");
const runBySample = ref<Record<string, { runId: string; txnId: string }>>({});
let pgTimer: ReturnType<typeof setInterval> | null = null;
const pgFast = computed(() => pgCurrentMode.value === "fast");
const pgDuration = computed(() => {
  if (!pgDocs.value.length) return null;
  const totals = pgDocs.value.map((d) => d.metrics?.total_ms).filter(Boolean);
  return totals.length ? Math.max(...(totals as number[])) : null;
});
const pgFilteredSamples = computed(() => pgSamples.value.filter(
  (s) => !pgSearch.value || s.file_name.toLowerCase().includes(pgSearch.value.toLowerCase())));

const sampleStatus = ref<Record<string, string>>({});
function pgStatus(sampleId: string): string {
  return sampleStatus.value[sampleId] ?? "";
}

watch([tab, props.code], async ([t]) => {
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
  if (!pgSelected.value.length) return;
  try {
    const r = await api.studioCreateRun({ skill_code: props.code ?? "",
                                         sample_ids: pgSelected.value });
    for (const run of r.runs) {
      runBySample.value[run.sample_id] = { runId: run.run_id, txnId: r.transaction_id };
      sampleStatus.value[run.sample_id] = "运行中…";
    }
    pgPollTransaction(r.transaction_id);
  } catch (err) {
    toast.error(`运行失败：${err instanceof Error ? err.message : err}`);
  }
}

function pgPollTransaction(txnId: string) {
  pgPolling.value = true;
  if (pgTimer) clearInterval(pgTimer);
  pgTimer = setInterval(async () => {
    try {
      const st = await api.txnStatus(txnId);
      const root = st.files[0];
      if (root) {
        pgCurrent.value = { file_id: root.file_id, file_name: root.file_name,
                            status: root.status };
        const active = root.children?.length ? root.children[0] : root;
        if (active) {
          pgCurrent.value = { file_id: active.file_id, file_name: root.file_name,
                              status: active.status };
        }
      }
      const terminal = st.files.every((f) =>
        ["completed", "passed", "error", "rejected"].includes(f.status));
      if (terminal) {
        if (pgTimer) clearInterval(pgTimer);
        pgTimer = null;
        pgPolling.value = false;
        for (const [sid, r] of Object.entries(runBySample.value)) {
          if (r.txnId === txnId) {
            sampleStatus.value[sid] = st.files.every((f) => f.status === "error")
              ? "失败" : "完成";
          }
        }
        await pgLoadDocuments(txnId);
        pgLoadHistory();
      }
    } catch {
      if (pgTimer) clearInterval(pgTimer);
      pgTimer = null;
      pgPolling.value = false;
    }
  }, 2000);
}

async function pgLoadDocuments(txnId: string) {
  try {
    const d = await api.txnDocuments(txnId);
    pgDocs.value = d.files.flatMap((f) => f.documents);
    pgCurrentMode.value =
      pgDocs.value.some((x) => x.metrics?.parse_route === "vision") ? "fast" : pgCurrentMode.value;
  } catch { /* keep previous view */ }
}

async function pgLoadHistory() {
  try {
    pgHistory.value = (await api.studioRuns({ skill_code: props.code })).runs;
  } catch { /* history is best-effort */ }
}

function pgSampleName(id: string): string {
  return pgSamples.value.find((s) => s.id === id)?.file_name ?? id;
}

async function pgLoadRun(runId: string) {
  pgHistoryOpen.value = false;
  try {
    const d = await api.studioRun(runId);
    pgDetail.value = d;
    pgCurrentRun.value = runId;
    pgCurrentMode.value = d.processing_mode;
    pgDetailOpen.value = true;
    await pgLoadDocuments(d.transaction_id);
    const st = await api.txnStatus(d.transaction_id);
    const root = st.files[0];
    if (root) {
      const active = root.children?.length ? root.children[0] : root;
      pgCurrent.value = { file_id: active?.file_id ?? root.file_id,
                          file_name: root.file_name, status: active?.status ?? root.status };
    }
  } catch (err) {
    toast.error(`载入运行失败：${err instanceof Error ? err.message : err}`);
  }
}

// —— flow rail: nodes with live subtitles; bad dots from light client checks ——
const flowNodes = computed(() => {
  const p = pkg.value!;
  const badFields = p.fields.some((f) => !f.name.trim());
  const badCats = p.skill_mode === "advanced"
    && !(p.categories ?? []).some((c) => c.is_other);
  return [
    { key: "basic" as const, label: "基础",
      sub: p.review_policy.mode === "never" ? "无需复核" : "复核模式已配置",
      bad: false },
    ...(p.skill_mode === "advanced"
      ? [{ key: "classify" as const, label: "文档分类",
           sub: `${(p.categories ?? []).length} 个类别`, bad: badCats }]
      : []),
    { key: "fields" as const, label: "字段提取",
      sub: `${p.fields.length} 个字段${p.output_shape === "list" ? " · List" : ""}`,
      bad: badFields },
    { key: "output" as const, label: "文件产出", sub: "未开启下载", bad: false },
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
onBeforeUnmount(() => window.removeEventListener("beforeunload", beforeUnload));
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
                (f, t) => { if (pkg.value) moveItem(pkg.value.fields, f, t); });
}

// —— field modal editing ——
const editing = ref<{ list: FieldSpec[]; index: number; spec: FieldSpec;
                      isNew: boolean; isColumn: boolean } | null>(null);
function openEdit(list: FieldSpec[], index: number, isColumn = false) {
  editing.value = { list, index, spec: list[index], isNew: false, isColumn };
}
function openAdd(list: FieldSpec[], isColumn = false) {
  editing.value = { list, index: -1, spec: blankField(), isNew: true, isColumn };
}
function commitEdit(spec: FieldSpec) {
  if (!editing.value) return;
  const { list, index, isNew: adding } = editing.value;
  if (adding) list.push(spec);
  else list.splice(index, 1, spec);
  editing.value = null;
}

// —— generation (R05/图13): snapshot → apply suggested fields → undoable ——
function applyGenerated(fields: FieldSpec[], replaceAll: boolean) {
  if (!pkg.value) return;
  undoStack.value.push(JSON.parse(JSON.stringify(pkg.value.fields)));
  if (replaceAll) {
    pkg.value.fields = fields.map((f) => ({ ...blankField(), ...f }));
    return;
  }
  const existing = new Set(pkg.value.fields.map((f) => f.name));
  const fresh = fields.filter((f) => !existing.has(f.name));
  const skipped = fields.length - fresh.length;
  pkg.value.fields.push(...fresh.map((f) => ({ ...blankField(), ...f })));
  if (skipped) toast.ok(`跳过 ${skipped} 个同名字段（未覆盖你的修改）`);
}
function undoGenerate() {
  if (!pkg.value || !undoStack.value.length) return;
  pkg.value.fields = undoStack.value.pop()!;
  toast.ok("已撤销上一次生成");
}
function clearFields() {
  if (!pkg.value || !pkg.value.fields.length) return;
  if (!confirm(`清空全部 ${pkg.value.fields.length} 个字段？可用「撤销上次生成」恢复。`)) return;
  undoStack.value.push(JSON.parse(JSON.stringify(pkg.value.fields)));
  pkg.value.fields = [];
}

/** Save = update the draft on screen (P04). Outbound payloads are always v2. */
const saveLabel = computed(() =>
  selectedStatus.value && selectedStatus.value !== "draft" ? "另存为新草稿" : "保存");
const saving = ref(false);
function outbound(): SkillPackage {
  return { ...pkg.value!, schema_version: 2 };
}

function validPkg(): boolean {
  if (!pkg.value) return false;
  if (!pkg.value.skill_code) { toast.error("请填写 skill_code"); return false; }
  if (!pkg.value.fields.length && pkg.value.skill_mode !== "advanced") {
    toast.error("至少定义一个字段"); return false;
  }
  return true;
}

async function save() {
  if (!validPkg() || !pkg.value) return;
  saving.value = true;
  try {
    if (isNew.value) {
      await api.skillCreate(outbound(), changelog.value);
      toast.ok("技能已创建（v1 草稿）");
      router.push(`/skills/${pkg.value.skill_code}`);
    } else if (selectedStatus.value === "draft" && selectedVersion.value) {
      await api.skillSaveDraft(props.code, selectedVersion.value, outbound(), changelog.value);
      toast.ok(`v${selectedVersion.value} 草稿已保存（未新增版本）`);
      await load(selectedVersion.value);
    } else {
      const r = await api.skillNewDraft(props.code, outbound(), changelog.value);
      toast.ok(`已以 v${selectedVersion.value} 为底新建 v${r.version} 草稿`);
      await load(r.version);
    }
    markClean();
  } catch (e) { toast.error(e); }
  finally { saving.value = false; }
}

async function newVersion() {
  if (!validPkg() || !pkg.value) return;
  try {
    const r = await api.skillNewDraft(props.code, outbound(), changelog.value);
    toast.ok(`已新建 v${r.version} 草稿`);
    await load(r.version);
    markClean();
  } catch (e) { toast.error(e); }
}
async function publishSelected() {
  if (!selectedVersion.value) return;
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
    await navigator.clipboard.writeText(pkg.value?.skill_code ?? "");
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch { /* clipboard unavailable */ }
}

// —— draft channels ——
const probing = ref(false);
function mergeDraft(fields: FieldSpec[], docType?: string): number {
  if (!pkg.value) return 0;
  undoStack.value.push(JSON.parse(JSON.stringify(pkg.value.fields)));
  const existing = new Set(pkg.value.fields.map((f) => f.name));
  const fresh = fields.filter((f) => !existing.has(f.name));
  pkg.value.fields.push(...fresh);
  if (docType && !pkg.value.doc_type_hint) pkg.value.doc_type_hint = docType;
  return fresh.length;
}
async function probe(ev: Event) {
  const file = (ev.target as HTMLInputElement).files?.[0];
  if (!file || !pkg.value) return;
  probing.value = true;
  try {
    const r = await api.skillProbe(file);
    toast.ok(`预标注完成：新增 ${mergeDraft(r.fields, r.doc_type)} 个字段草稿（${r.provider_used}），请核对后保存`);
  } catch (e) { toast.error(e); }
  finally { probing.value = false; (ev.target as HTMLInputElement).value = ""; }
}
const textPanel = ref(false);
const draftText = ref("");
const drafting = ref(false);
async function draftFromText() {
  if (!draftText.value.trim()) return;
  drafting.value = true;
  try {
    const r = await api.skillDraftFromText(draftText.value);
    toast.ok(`已从描述起草 ${mergeDraft(r.fields, r.doc_type)} 个字段（${r.provider_used}），请核对后保存`);
    textPanel.value = false;
    draftText.value = "";
  } catch (e) { toast.error(e); }
  finally { drafting.value = false; }
}
async function tableImport(ev: Event) {
  const file = (ev.target as HTMLInputElement).files?.[0];
  if (!file) return;
  try {
    const r = await api.skillDraftFromTable(file);
    toast.ok(`表格导入完成：新增 ${mergeDraft(r.fields)} 个字段。可用「✨ AI 补全说明」扩写规则`);
  } catch (e) { toast.error(e); }
  finally { (ev.target as HTMLInputElement).value = ""; }
}
const enriching = ref(false);
async function enrich() {
  if (!pkg.value?.fields.length) return;
  enriching.value = true;
  try {
    const r = await api.skillEnrich(pkg.value.fields, pkg.value.doc_type_hint);
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
    apply(pkg.value.fields, r.fields);
    toast.ok(`已补全 ${n} 处字段说明（${r.provider_used}），请核对后保存`);
  } catch (e) { toast.error(e); }
  finally { enriching.value = false; }
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
.main-col { width: 100%; max-width: 1200px; padding: 16px 20px 40px;
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
.guide { display: flex; gap: 14px; align-items: center; justify-content: center;
  padding: 14px; border-style: dashed; flex-wrap: wrap; font-size: 13px;
  color: var(--text-dim); }
.guide b { display: inline-flex; width: 20px; height: 20px; border-radius: 50%;
  background: var(--accent); color: var(--accent-text); align-items: center;
  justify-content: center; margin-right: 6px; }
.arrow { color: var(--accent); }

/* flow rail + step panels */
.design-body { display: grid; grid-template-columns: 190px minmax(0, 1fr);
  gap: 16px; align-items: start; }
.flow-rail { position: sticky; top: 66px; display: flex; flex-direction: column;
  gap: 6px; }
.node { display: flex; flex-direction: column; align-items: flex-start;
  gap: 1px; border: 1px solid var(--border); border-radius: 10px;
  background: var(--bg-panel); padding: 9px 12px; cursor: pointer; text-align: left; }
.node:hover { border-color: var(--accent); }
.node.on { border-color: var(--accent); background: var(--bg-raised); }
.node .dot { width: 8px; height: 8px; border-radius: 50%;
  background: var(--border); position: absolute; }
.node .lbl { font-weight: 700; font-size: 13.5px; display: flex; gap: 6px;
  align-items: center; }
.node.on .lbl { color: var(--accent); }
.node .sub { font-size: 11.5px; }
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
.fields-step { display: flex; gap: 14px; align-items: flex-start; }
.sample-col { flex: 0 0 320px; }
.fields-col { flex: 1; min-width: 0; }
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
.more-menu .more-pop button:hover { background: var(--bg-hover, rgba(0,0,0,.05)); }
.more-menu .more-pop button.danger { color: var(--red); }
.mini { padding: 2px 10px; font-size: 12px; }
.file-btn .btn-like:hover { border-color: var(--accent); }
.text-panel { border: 1px solid var(--border); border-radius: 8px; padding: 12px;
  display: flex; flex-direction: column; gap: 8px; background: var(--bg-raised);
  margin-bottom: 10px; }
.text-panel-act { display: flex; gap: 12px; align-items: center;
  justify-content: space-between; font-size: 12px; }
.pad { padding: 4px 0; margin: 0; }
.drag-tip { margin: 0 0 8px; font-size: 12px; }
.field-tree { display: flex; flex-direction: column; gap: 10px; }
.add-field { border-style: dashed; padding: 8px; }
.rules-fold { margin-top: 12px; border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 12px; }
.rules-fold summary { cursor: pointer; font-size: 13px; color: var(--text-dim); }
.rules-fold textarea { width: 100%; margin-top: 8px; }

/* test tab */
.test-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
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
.playground .pg-grid { display: grid; grid-template-columns: 240px 1fr; gap: 14px; }
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
.pg-modal { max-width: 720px; width: 92%; }
.pg-hist-row { cursor: pointer; }
.pg-detail { list-style: none; margin: 0 0 10px; padding: 0; }
.pg-detail li { padding: 4px 0; border-bottom: 1px solid var(--border); }
.pg-detail code { font-size: 11px; }

@media (max-width: 1100px) {
  .design-body { grid-template-columns: 1fr; }
  .flow-rail { position: static; flex-direction: row; flex-wrap: wrap; }
  .fields-step { flex-direction: column; }
  .sample-col { flex: 1 1 auto; max-width: none; width: 100%; }
  .test-grid { grid-template-columns: 1fr; }
  .cards { grid-template-columns: 1fr; }
}
</style>
