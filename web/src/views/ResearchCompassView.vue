<template>
  <div
    class="research-view layout-container"
    :class="{ 'copilot-open': copilotOpen && selectedKbId }"
  >
    <PageHeader title="科研罗盘" :loading="loading || databasesLoading" :show-border="true">
      <template #info>
        <span class="header-summary">知识图谱驱动的学术文献分析</span>
      </template>
      <template #actions>
        <a-button
          v-if="userStore.isAdmin"
          class="lucide-icon-btn"
          title="管理论文库"
          aria-label="管理论文库"
          @click="openKnowledgeManagement"
        >
          <template #icon><Database :size="15" /></template>
          管理论文库
        </a-button>
        <a-button
          v-if="userStore.isAdmin"
          class="lucide-icon-btn"
          title="导入公开论文"
          aria-label="导入公开论文"
          :disabled="!selectedKbId"
          @click="externalImportOpen = true"
        >
          <template #icon><BookOpenText :size="15" /></template>
          导入公开论文
        </a-button>
        <a-button
          v-if="userStore.isAdmin"
          class="lucide-icon-btn"
          title="同步引用图谱"
          aria-label="同步引用图谱"
          :loading="graphSyncLoading"
          :disabled="!selectedKbId"
          @click="syncAcademicGraph"
        >
          <template #icon><Network :size="15" /></template>
          同步引用图谱
        </a-button>
        <a-button
          class="lucide-icon-btn"
          title="刷新论文库"
          aria-label="刷新论文库"
          :loading="loading"
          @click="loadPapers"
        >
          <template #icon><RefreshCw :size="15" /></template>
          刷新
        </a-button>
        <a-button
          class="lucide-icon-btn"
          :type="copilotOpen ? 'primary' : 'default'"
          title="AI 研究助手"
          aria-label="AI 研究助手"
          :disabled="!selectedKbId"
          @click="copilotOpen = !copilotOpen"
        >
          <template #icon><Sparkles :size="15" /></template>
          AI 研究助手
        </a-button>
      </template>
    </PageHeader>

    <main class="research-content">
      <a-tabs v-model:active-key="activeMode" class="research-tabs">
        <a-tab-pane key="projects" tab="研究项目" />
        <a-tab-pane key="library" tab="论文库" />
        <a-tab-pane key="search" tab="智能检索" />
        <a-tab-pane key="synthesis" tab="证据综述" />
        <a-tab-pane key="graph" tab="引用图谱" />
        <a-tab-pane key="trends" tab="研究趋势" />
        <a-tab-pane key="opportunities" tab="研究机会" />
        <a-tab-pane key="analysis-evaluations" tab="分析对比" />
        <a-tab-pane v-if="userStore.isAdmin" key="user-studies" tab="用户评测" />
      </a-tabs>

      <ResearchProjectWorkspace
        v-if="activeMode === 'projects'"
        ref="projectWorkspaceRef"
        :kb-id="selectedKbId"
        :database-options="databaseOptions"
        :databases-loading="databasesLoading"
        @change-database="handleDatabaseChange"
        @continue-asset="continueProjectAsset"
        @project-change="handleProjectChange"
      />

      <section v-if="activeMode === 'user-studies'" class="user-studies-workspace">
        <div class="toolbar-field database-field">
          <label for="studies-database">论文知识库</label>
          <a-select
            id="studies-database"
            v-model:value="selectedKbId"
            :options="databaseOptions"
            :loading="databasesLoading"
            show-search
            option-filter-prop="label"
            @change="handleDatabaseChange"
          />
        </div>
        <UserStudyManager v-if="selectedKbId" :kb-id="selectedKbId" />
        <a-empty v-else description="请选择论文知识库后创建用户评测" class="page-empty" />
      </section>

      <section v-if="activeMode === 'analysis-evaluations'" class="user-studies-workspace">
        <div class="toolbar-field database-field">
          <label for="analysis-evaluation-database">论文知识库</label>
          <a-select
            id="analysis-evaluation-database"
            v-model:value="selectedKbId"
            :options="databaseOptions"
            :loading="databasesLoading"
            show-search
            option-filter-prop="label"
            @change="handleDatabaseChange"
          />
        </div>
        <PaperAnalysisEvaluationManager v-if="selectedKbId" :kb-id="selectedKbId" @open-paper="openPaper" />
        <a-empty v-else description="请选择论文知识库后创建分析对比" class="page-empty" />
      </section>

      <ResearchSearchWorkspace
        v-if="activeMode === 'search'"
        ref="searchWorkspaceRef"
        :kb-id="selectedKbId"
        :focus-run-id="projectSearchRunId"
        :database-options="databaseOptions"
        :databases-loading="databasesLoading"
        :max-publication-year="maxPublicationYear"
        @change-database="handleDatabaseChange"
        @open-paper="openPaper"
        @open-evidence="handleSearchEvidenceOpen"
        @start-synthesis="startSynthesisFromSearch"
        @selection-change="handleSearchSelectionChange"
      />

      <section v-else-if="activeMode === 'synthesis'" class="synthesis-workspace">
        <div class="synthesis-grid">
          <section class="synthesis-builder">
            <div class="synthesis-builder-header">
              <div>
                <h2>证据约束研究综述</h2>
                <p>从自然语言问题生成跨论文结构化综述，并强制每个结论绑定真实 chunk 证据。</p>
              </div>
              <a-tag color="green">本地混合检索</a-tag>
            </div>
            <div class="synthesis-options">
              <div class="toolbar-field database-field">
                <label for="synthesis-database">论文知识库</label>
                <a-select
                  id="synthesis-database"
                  v-model:value="selectedKbId"
                  :options="databaseOptions"
                  :loading="databasesLoading"
                  show-search
                  option-filter-prop="label"
                  @change="handleDatabaseChange"
                />
              </div>
              <div class="toolbar-field">
                <label>发表年份</label>
                <div class="year-range">
                  <a-input-number
                    v-model:value="synthesisFilters.yearFrom"
                    :min="1500"
                    :max="maxPublicationYear"
                    placeholder="起始"
                  />
                  <span>至</span>
                  <a-input-number
                    v-model:value="synthesisFilters.yearTo"
                    :min="1500"
                    :max="maxPublicationYear"
                    placeholder="结束"
                  />
                </div>
              </div>
              <div class="toolbar-field compact-option">
                <label for="synthesis-top-k">论文数</label>
                <a-input-number id="synthesis-top-k" v-model:value="synthesisFilters.topK" :min="2" :max="20" />
              </div>
              <div class="toolbar-field compact-option">
                <label for="synthesis-recall-k">候选证据</label>
                <a-input-number
                  id="synthesis-recall-k"
                  v-model:value="synthesisFilters.recallTopK"
                  :min="2"
                  :max="200"
                />
              </div>
            </div>
            <label for="synthesis-query" class="query-label">研究问题</label>
            <a-textarea
              id="synthesis-query"
              v-model:value="synthesisQuery"
              :rows="4"
              :maxlength="4000"
              show-count
              placeholder="例如：当前论文库中大模型辅助科研选题方法的共同证据、矛盾和研究空白是什么？"
              @keydown.ctrl.enter="createSynthesis"
            />
            <div class="synthesis-form-footer">
              <div class="search-pipeline-tags" aria-label="综述生成阶段">
                <a-tag>查询改写</a-tag>
                <a-tag>本地混合检索</a-tag>
                <a-tag>结构化综述</a-tag>
                <a-tag color="green">证据校验</a-tag>
              </div>
              <a-button
                type="primary"
                :loading="synthesisSubmitting"
                :disabled="!selectedKbId || !synthesisQuery.trim()"
                @click="createSynthesis"
              >
                <template #icon><Sparkles :size="15" /></template>
                生成综述
              </a-button>
            </div>
          </section>

          <aside class="synthesis-history">
            <header>
              <div>
                <h3>历史运行</h3>
                <span>{{ synthesisTotal }} 条</span>
              </div>
              <a-button size="small" :loading="synthesisLoading" :disabled="!selectedKbId" @click="loadSyntheses">
                <template #icon><RefreshCw :size="14" /></template>
                刷新
              </a-button>
            </header>
            <a-skeleton v-if="synthesisLoading && !synthesisRuns.length" active :paragraph="{ rows: 4 }" />
            <a-empty v-else-if="!synthesisRuns.length" description="暂无综述运行" />
            <div v-else class="synthesis-history-list">
              <button
                v-for="run in synthesisRuns"
                :key="run.run_id"
                type="button"
                class="synthesis-history-item"
                :class="{ active: selectedSynthesisRunId === run.run_id }"
                @click="selectSynthesisRun(run)"
              >
                <span class="synthesis-history-title">{{ run.query || '未命名研究问题' }}</span>
                <span class="synthesis-history-meta">
                  <span class="synthesis-status" :class="synthesisStatusClass(run.status)">
                    {{ synthesisStatusLabel(run.status) }}
                  </span>
                  {{ formatDateTime(run.created_at) }}
                </span>
              </button>
            </div>
          </aside>
        </div>

        <a-result v-if="synthesisError" status="error" title="研究综述失败" :sub-title="synthesisError">
          <template #extra><a-button @click="loadSyntheses">重新加载</a-button></template>
        </a-result>

        <section v-else-if="selectedSynthesisRun" class="synthesis-result-shell">
          <header class="synthesis-result-header">
            <div>
              <div class="synthesis-title-row">
                <h2>{{ selectedSynthesisRun.query }}</h2>
                <span class="synthesis-status" :class="synthesisStatusClass(selectedSynthesisRun.status)">
                  {{ synthesisStatusLabel(selectedSynthesisRun.status) }}
                </span>
              </div>
              <p>
                {{ synthesisStageLabel(selectedSynthesisRun.stage) }}
                <template v-if="selectedSynthesisRun.task_id"> · 任务 {{ selectedSynthesisRun.task_id }}</template>
              </p>
            </div>
            <div class="synthesis-actions">
              <a-button
                :disabled="!selectedSynthesisRun.run_id"
                :loading="synthesisDetailLoading"
                @click="refreshSelectedSynthesis"
              >
                <template #icon><RefreshCw :size="14" /></template>
                更新
              </a-button>
              <a-popconfirm
                title="取消后将停止本次综述，未验证内容不会保留。"
                ok-text="取消综述"
                cancel-text="继续执行"
                @confirm="cancelSelectedSynthesis"
              >
                <a-button danger :disabled="!canCancelSynthesis" :loading="synthesisCancelling">
                  <template #icon><CircleStop :size="14" /></template>
                  取消
                </a-button>
              </a-popconfirm>
              <a-button
                :disabled="!canRegenerateSynthesis"
                :loading="synthesisRegenerating"
                @click="regenerateSelectedSynthesis"
              >
                <template #icon><RotateCcw :size="14" /></template>
                重新生成
              </a-button>
              <a-button
                :disabled="!canExportSynthesis"
                :loading="synthesisExporting === 'markdown'"
                @click="exportSelectedSynthesis('markdown')"
              >
                <template #icon><FileDown :size="14" /></template>
                Markdown
              </a-button>
              <a-button
                :disabled="!canExportSynthesis"
                :loading="synthesisExporting === 'docx'"
                @click="exportSelectedSynthesis('docx')"
              >
                <template #icon><FileDown :size="14" /></template>
                DOCX
              </a-button>
            </div>
          </header>

          <a-progress
            v-if="isSelectedSynthesisActive"
            :percent="synthesisStagePercent(selectedSynthesisRun.stage)"
            :show-info="false"
            stroke-color="var(--main-color)"
          />
          <a-alert
            v-if="selectedSynthesisRun.status === 'failed'"
            type="error"
            show-icon
            :message="selectedSynthesisRun.error_message || '综述运行失败'"
            :description="selectedSynthesisRun.error_type"
          />
          <a-alert
            v-else-if="selectedSynthesisRun.status === 'cancelled'"
            type="warning"
            show-icon
            :message="selectedSynthesisRun.error_message || '研究综述已取消'"
          />

          <template v-if="selectedSynthesisResult">
            <div class="synthesis-coverage-cards">
              <article>
                <span>引用覆盖率</span>
                <strong>{{ formatPercent(selectedSynthesisCoverage.citation_coverage_ratio) }}</strong>
              </article>
              <article>
                <span>已验证结论</span>
                <strong>{{ selectedSynthesisCoverage.supported_conclusions || 0 }} / {{ selectedSynthesisCoverage.total_conclusions || 0 }}</strong>
              </article>
              <article>
                <span>引用论文</span>
                <strong>{{ selectedSynthesisCoverage.distinct_papers || 0 }} / {{ selectedSynthesisCoverage.retrieved_papers || 0 }}</strong>
              </article>
              <article>
                <span>检索证据</span>
                <strong>{{ selectedSynthesisCoverage.retrieved_chunks || 0 }} chunks</strong>
              </article>
            </div>

            <section class="synthesis-summary-panel">
              <div class="synthesis-section-title">
                <ShieldCheck :size="17" />
                <h3>执行摘要</h3>
              </div>
              <p>{{ selectedSynthesisResult.executive_summary?.text }}</p>
              <div class="search-pipeline-tags">
                <a-tag v-for="claimId in selectedSynthesisResult.executive_summary?.claim_ids || []" :key="claimId">
                  {{ claimId }}
                </a-tag>
              </div>
            </section>
            <a-alert
              v-if="selectedSynthesisResult.validation?.confidence_adjustments?.length"
              type="warning"
              show-icon
              message="置信度已按证据规则校正"
              :description="selectedSynthesisResult.validation.confidence_adjustments.map((item) => `${item.claim_id}：${item.reason}`).join('；')"
            />
            <section v-if="selectedSynthesisResult.methodological_constraints?.length" class="synthesis-section">
              <div class="synthesis-section-title">
                <FileText :size="17" />
                <h3>方法边界</h3>
              </div>
              <ul class="synthesis-constraints">
                <li v-for="constraint in selectedSynthesisResult.methodological_constraints" :key="constraint">
                  {{ constraint }}
                </li>
              </ul>
            </section>

            <section v-if="selectedSynthesisResult.themes?.length" class="synthesis-section">
              <div class="synthesis-section-title">
                <ListChecks :size="17" />
                <h3>主题综述</h3>
              </div>
              <article v-for="theme in selectedSynthesisResult.themes" :key="theme.title" class="synthesis-theme">
                <h4>{{ theme.title }}</h4>
                <p>{{ theme.summary }}</p>
                <div class="search-pipeline-tags">
                  <a-tag v-for="claimId in theme.claim_ids" :key="claimId">{{ claimId }}</a-tag>
                </div>
              </article>
            </section>

            <section class="synthesis-section">
              <div class="synthesis-section-title">
                <Quote :size="17" />
                <h3>核心结论</h3>
              </div>
              <article v-for="claim in selectedSynthesisResult.claims || []" :key="claim.claim_id" class="synthesis-claim">
                <header>
                  <span>{{ claim.claim_id }}</span>
                  <a-tag :color="confidenceColor(claim.confidence)">{{ confidenceLabel(claim.confidence) }}</a-tag>
                </header>
                <p>{{ claim.statement }}</p>
                <div class="synthesis-evidence-chips">
                  <button
                    v-for="citation in claim.evidence"
                    :key="citation.chunk_id"
                    type="button"
                    @click="openSynthesisEvidence(citation)"
                  >
                    {{ getSynthesisEvidenceLabel(citation) }}
                  </button>
                </div>
              </article>
            </section>

            <section
              v-for="section in synthesisInsightSections"
              :key="section.key"
              class="synthesis-section"
            >
              <div class="synthesis-section-title">
                <component :is="section.icon" :size="17" />
                <h3>{{ section.title }}</h3>
              </div>
              <a-empty
                v-if="!(selectedSynthesisResult[section.key] || []).length"
                :description="section.empty"
              />
              <article
                v-for="(item, index) in selectedSynthesisResult[section.key] || []"
                :key="`${section.key}-${index}`"
                class="synthesis-claim"
              >
                <header v-if="item.confidence || item.paper_ids?.length">
                  <span>{{ item.paper_ids?.length || 0 }} 篇论文</span>
                  <a-tag v-if="item.confidence" :color="confidenceColor(item.confidence)">
                    {{ confidenceLabel(item.confidence) }}
                  </a-tag>
                </header>
                <p>{{ item.statement }}</p>
                <p v-if="item.basis" class="synthesis-basis">依据：{{ item.basis }}</p>
                <div class="synthesis-evidence-chips">
                  <button
                    v-for="citation in item.evidence"
                    :key="citation.chunk_id"
                    type="button"
                    @click="openSynthesisEvidence(citation)"
                  >
                    {{ getSynthesisEvidenceLabel(citation) }}
                  </button>
                </div>
              </article>
            </section>

            <section v-if="selectedSynthesisResult.unsupported_claims?.length" class="synthesis-section">
              <div class="synthesis-section-title">
                <History :size="17" />
                <h3>未支持结论</h3>
              </div>
              <article
                v-for="(item, index) in selectedSynthesisResult.unsupported_claims"
                :key="`unsupported-${index}`"
                class="synthesis-unsupported"
              >
                <strong>{{ item.statement }}</strong>
                <span>{{ item.reason }}</span>
              </article>
            </section>
          </template>

          <a-empty v-else-if="isSelectedSynthesisActive" description="综述正在生成，完成后会显示验证结果" />
          <a-empty v-else description="该运行暂无可展示结果" />
        </section>

        <a-empty v-else description="选择历史运行或创建新的证据综述" class="page-empty" />
      </section>

      <section v-else-if="activeMode === 'graph'" class="graph-workspace">
        <div class="graph-toolbar">
          <div class="toolbar-field database-field">
            <label for="graph-database">论文知识库</label>
            <a-select
              id="graph-database"
              v-model:value="selectedKbId"
              :options="databaseOptions"
              :loading="databasesLoading"
              show-search
              option-filter-prop="label"
              @change="handleDatabaseChange"
            />
          </div>
          <div class="graph-counts" v-if="graphData">
            <span>{{ graphData.counts.papers }} 篇论文</span>
            <span>{{ graphData.counts.citations }} 条引用</span>
            <span>{{ graphData.counts.authors }} 位作者</span>
            <span>{{ graphData.counts.topics }} 个主题</span>
          </div>
          <a-button :loading="graphLoading" :disabled="!selectedKbId" @click="loadAcademicGraph">刷新图谱</a-button>
        </div>
        <a-alert
          v-if="graphSyncRun"
          :type="graphSyncRun.status === 'failed' ? 'error' : graphSyncRun.status === 'success' ? 'success' : graphSyncRun.status === 'completed_with_conflicts' ? 'warning' : 'info'"
          show-icon
          :message="graphSyncStatusMessage"
          :description="graphSyncRun.error_message || `已处理 ${graphSyncRun.processed_papers} 篇，发现 ${graphSyncRun.conflict_count} 个元数据冲突`"
        />
        <section v-if="graphSyncRun?.conflict_count" class="graph-conflicts">
          <div class="graph-conflict-heading">
            <h3>待处理元数据冲突</h3>
            <a-button size="small" :loading="graphConflictsLoading" @click="loadGraphConflicts">
              刷新明细
            </a-button>
          </div>
          <a-skeleton v-if="graphConflictsLoading" active :paragraph="{ rows: 3 }" />
          <a-result
            v-else-if="graphConflictsError"
            status="error"
            title="冲突明细加载失败"
            :sub-title="graphConflictsError"
          />
          <a-empty v-else-if="!graphConflicts.length" description="没有可显示的冲突记录" />
          <article
            v-else
            v-for="conflict in graphConflicts"
            :key="conflict.conflict_id"
            class="graph-conflict-item"
          >
            <div class="graph-conflict-heading">
              <strong>{{ conflict.message }}</strong>
              <a-tag color="orange">{{ graphConflictTypeLabel(conflict.conflict_type) }}</a-tag>
            </div>
            <p>字段：{{ conflict.field_name || '身份映射' }}</p>
            <div class="graph-conflict-values">
              <div><span>本地值</span><pre>{{ formatConflictValue(conflict.local_value) }}</pre></div>
              <div><span>远端值</span><pre>{{ formatConflictValue(conflict.remote_value) }}</pre></div>
            </div>
          </article>
          <a-pagination
            v-if="graphConflictsTotal > graphConflictsPageSize"
            v-model:current="graphConflictsPage"
            :page-size="graphConflictsPageSize"
            :total="graphConflictsTotal"
            size="small"
            @change="loadGraphConflicts"
          />
        </section>
        <a-result v-if="graphError" status="error" title="引用图谱加载失败" :sub-title="graphError" />
        <div v-else-if="graphLoading" class="papers-loading"><a-skeleton active :paragraph="{ rows: 10 }" /></div>
        <a-empty v-else-if="!graphData?.nodes?.length" class="page-empty" description="当前知识库还没有学术引用图谱">
          <a-button v-if="userStore.isAdmin" type="primary" :loading="graphSyncLoading" @click="syncAcademicGraph">同步 Semantic Scholar 引用数据</a-button>
        </a-empty>
        <div v-else class="academic-graph-layout">
          <div class="academic-graph-canvas">
            <GraphCanvas
              :graph-data="graphCanvasData"
              label-field="name"
              :node-style-options="academicNodeStyles"
              @node-click="handleAcademicGraphNodeClick"
              @canvas-click="selectedGraphNode = null"
            />
          </div>
          <aside v-if="selectedGraphNode" class="graph-selection-panel">
            <span class="node-type-label">{{ graphNodeTypeLabel(selectedGraphNode.raw_type) }}</span>
            <h3>{{ selectedGraphNode.title || selectedGraphNode.name }}</h3>
            <template v-if="selectedGraphNode.raw_type === 'paper'">
              <p>{{ selectedGraphNode.publication_year || '年份未知' }} · {{ selectedGraphNode.venue || '来源未知' }}</p>
              <p>被引 {{ selectedGraphNode.citation_count || 0 }} 次</p>
              <a-tag v-if="selectedGraphNode.is_library_paper" color="blue">论文库论文</a-tag>
              <a-button
                type="primary"
                block
                :loading="relationsLoading"
                @click="loadGraphRelations(selectedGraphNode.id)"
              >
                发现两跳关联
              </a-button>
              <a-alert
                v-if="relationsError"
                type="error"
                show-icon
                :message="relationsError"
              />
              <div v-if="graphRelations.length" class="graph-relations-list">
                <h4>可解释关联</h4>
                <article v-for="relation in graphRelations" :key="relation.target_graph_paper_id" class="graph-relation-item">
                  <div class="graph-relation-title">
                    <strong>{{ relation.target?.title || '未知论文' }}</strong>
                    <a-tag>{{ relationTypeLabel(relation.relation_type) }}</a-tag>
                  </div>
                  <p>经由：{{ relation.intermediate?.title || '未知论文' }}</p>
                  <div class="graph-relation-evidence">
                    <span v-for="edge in relation.edges" :key="edge.citation_id">
                      {{ edge.direction === 'outgoing' ? '引用' : '被引用' }}
                      <template v-if="edge.is_influential"> · 重要引用</template>
                    </span>
                  </div>
                </article>
              </div>
            </template>
          </aside>
        </div>
        <div class="graph-network-list">
          <article v-for="paper in graphPapers" :key="paper.id" class="graph-paper-node">
            <div class="graph-paper-heading">
              <h3>{{ paper.title }}</h3>
              <a-tag v-if="paper.is_library_paper" color="blue">论文库</a-tag>
            </div>
            <p>{{ paper.publication_year || '年份未知' }} · {{ paper.venue || '来源未知' }}</p>
            <div class="graph-node-meta">
              <span>被引 {{ paper.citation_count || 0 }}</span>
              <span>入边 {{ graphDegree(paper.id).incoming }}</span>
              <span>出边 {{ graphDegree(paper.id).outgoing }}</span>
            </div>
          </article>
        </div>
      </section>

      <section v-else-if="activeMode === 'trends'" class="trends-workspace">
        <div class="trends-toolbar">
          <div class="toolbar-field database-field">
            <label for="trends-database">论文知识库</label>
            <a-select
              id="trends-database"
              v-model:value="selectedKbId"
              :options="databaseOptions"
              :loading="databasesLoading"
              show-search
              option-filter-prop="label"
              @change="handleDatabaseChange"
            />
          </div>
          <div class="toolbar-field">
            <label>发表年份</label>
            <div class="year-range">
              <a-input-number v-model:value="trendFilters.yearFrom" :min="1500" :max="maxPublicationYear" placeholder="起始" />
              <span>至</span>
              <a-input-number v-model:value="trendFilters.yearTo" :min="1500" :max="maxPublicationYear" placeholder="结束" />
            </div>
          </div>
          <a-button type="primary" :loading="trendLoading" :disabled="!selectedKbId" @click="loadTrends">
            <template #icon><RefreshCw :size="15" /></template>
            分析趋势
          </a-button>
        </div>
        <a-result v-if="trendError" status="error" title="研究趋势加载失败" :sub-title="trendError">
          <template #extra><a-button @click="loadTrends">重新加载</a-button></template>
        </a-result>
        <div v-else-if="trendLoading" class="papers-loading"><a-skeleton active :paragraph="{ rows: 8 }" /></div>
        <a-empty v-else-if="!trendData || trendData.scope.total_papers === 0" class="page-empty" description="当前筛选范围没有论文趋势数据" />
        <div v-else class="trends-content">
          <div class="trend-summary-cards">
            <article><span>样本论文</span><strong>{{ trendData.scope.total_papers }}</strong></article>
            <article><span>覆盖年份</span><strong>{{ trendData.publication_trend.length }}</strong></article>
            <article><span>关键词</span><strong>{{ trendData.keyword_totals.length }}</strong></article>
            <article><span>引用网络年份</span><strong>{{ trendData.citation_trend.length }}</strong></article>
          </div>
          <div class="trend-chart-grid">
            <section class="trend-chart-card">
              <header><h3>论文数量与引用趋势</h3><span>按论文发表年份统计</span></header>
              <div ref="publicationTrendChartRef" class="trend-chart"></div>
            </section>
            <section class="trend-chart-card">
              <header><h3>高频研究关键词</h3><span>论文元数据关键词</span></header>
              <div ref="keywordTrendChartRef" class="trend-chart"></div>
            </section>
          </div>
          <section class="keyword-evolution-card">
            <header><h3>关键词演进</h3><span>选择关键词查看跨年份变化</span></header>
            <div class="keyword-chip-row">
              <button
                v-for="keyword in trendData.keyword_trends"
                :key="keyword.keyword"
                type="button"
                :class="{ active: selectedTrendKeyword === keyword.keyword }"
                @click="selectTrendKeyword(keyword.keyword)"
              >
                {{ keyword.keyword }} <span>{{ keyword.total }}</span>
              </button>
            </div>
            <div v-if="selectedTrendKeywordData" class="keyword-evolution-grid">
              <div v-for="item in selectedTrendKeywordData.years" :key="item.year" class="keyword-year-item">
                <span>{{ item.year }}</span><strong>{{ item.count }}</strong>
              </div>
            </div>
            <a-empty v-else description="暂无关键词演进数据" />
          </section>
          <section class="keyword-evolution-card emerging-directions-card">
            <header><h3>新兴研究方向</h3><span>近两年关键词增长</span></header>
            <div v-if="trendData.emerging_directions?.length" class="emerging-direction-list">
              <article v-for="direction in trendData.emerging_directions" :key="direction.keyword">
                <div>
                  <strong>{{ direction.keyword }}</strong>
                  <span>{{ direction.first_year }}–{{ direction.last_year }}</span>
                </div>
                <a-tag color="green">增长 {{ direction.growth }}</a-tag>
              </article>
            </div>
            <a-empty v-else description="暂未识别出新兴方向" />
          </section>
        </div>
      </section>
      <section v-else-if="activeMode === 'opportunities'" class="opportunities-workspace">
        <div class="opportunities-toolbar">
          <div class="toolbar-field database-field">
            <label for="opportunities-database">论文知识库</label>
            <a-select
              id="opportunities-database"
              v-model:value="selectedKbId"
              :options="databaseOptions"
              :loading="databasesLoading"
              show-search
              option-filter-prop="label"
              @change="handleDatabaseChange"
            />
          </div>
          <div class="toolbar-field">
            <label>发表年份</label>
            <div class="year-range">
              <a-input-number v-model:value="opportunityFilters.yearFrom" :min="1500" :max="maxPublicationYear" placeholder="起始" />
              <span>至</span>
              <a-input-number v-model:value="opportunityFilters.yearTo" :min="1500" :max="maxPublicationYear" placeholder="结束" />
            </div>
          </div>
          <a-button type="primary" :loading="opportunityLoading" :disabled="!selectedKbId" @click="loadOpportunities">
            <template #icon><Search :size="15" /></template>
            识别研究机会
          </a-button>
        </div>
        <a-result v-if="opportunityError" status="error" title="研究机会加载失败" :sub-title="opportunityError">
          <template #extra><a-button @click="loadOpportunities">重新加载</a-button></template>
        </a-result>
        <div v-else-if="opportunityLoading" class="papers-loading"><a-skeleton active :paragraph="{ rows: 10 }" /></div>
        <a-empty v-else-if="!opportunityData || opportunityData.scope.total_papers === 0" class="page-empty" description="当前筛选范围没有论文机会数据" />
        <div v-else class="opportunities-content">
          <div class="opportunity-summary-cards">
            <article><span>样本论文</span><strong>{{ opportunityData.scope.total_papers }}</strong></article>
            <article><span>最新年份</span><strong>{{ opportunityData.scope.latest_year || '未知' }}</strong></article>
            <article><span>候选方向</span><strong>{{ opportunityData.opportunities.length }}</strong></article>
            <article><span>证据方法</span><strong>可追溯</strong></article>
          </div>
          <section class="opportunity-methodology">
            <div>
              <strong>证据驱动的机会排序</strong>
              <span>{{ opportunityData.methodology.description }}</span>
              <span v-if="opportunityData.scope.total_papers < 3" class="opportunity-sample-warning">
                当前仅有 {{ opportunityData.scope.total_papers }} 篇论文，候选方向仅作探索排序，建议补充样本后再判断。
              </span>
            </div>
            <a-tag :color="opportunityData.methodology.graph_available ? 'green' : 'orange'">
              {{ opportunityData.methodology.graph_available ? '已纳入引用图谱' : '图谱未接入' }}
            </a-tag>
          </section>
          <a-empty v-if="!opportunityData.opportunities.length" description="当前筛选范围暂未识别出增长方向" />
          <div v-else class="opportunity-list">
            <article v-for="(opportunity, index) in opportunityData.opportunities" :key="opportunity.keyword" class="opportunity-card">
              <header class="opportunity-card-header">
                <div>
                  <span class="opportunity-rank">#{{ index + 1 }}</span>
                  <h3>{{ opportunity.keyword }}</h3>
                </div>
                <div class="opportunity-score">
                  <strong>{{ opportunity.score.toFixed(1) }}</strong>
                  <span>机会分</span>
                </div>
              </header>
              <div class="opportunity-signal-row">
                <a-tag :color="opportunity.confidence === 'high' ? 'green' : opportunity.confidence === 'medium' ? 'blue' : 'orange'">
                  {{ opportunity.confidence === 'high' ? '高置信度' : opportunity.confidence === 'medium' ? '中置信度' : '低置信度' }}
                </a-tag>
                <span>论文 {{ opportunity.signals.paper_count }} 篇</span>
                <span>近两年 {{ opportunity.signals.recent_count }} 篇</span>
                <span>增长 {{ opportunity.signals.growth > 0 ? '+' : '' }}{{ opportunity.signals.growth }}</span>
                <span>平均被引 {{ opportunity.signals.average_citations }}</span>
              </div>
              <a-progress :percent="opportunity.score" :show-info="false" size="small" stroke-color="var(--main-color)" />
              <div class="opportunity-detail-grid">
                <div>
                  <h4>判断依据</h4>
                  <ul>
                    <li v-for="reason in opportunity.reasons" :key="reason">{{ reason }}</li>
                  </ul>
                </div>
                <div class="opportunity-coverage">
                  <h4>证据覆盖</h4>
                  <span>图谱覆盖 {{ Math.round(opportunity.coverage.graph_coverage_ratio * 100) }}%</span>
                  <span>平均连接度 {{ opportunity.coverage.average_citation_degree }}</span>
                  <span>时间范围 {{ opportunity.signals.first_year }}–{{ opportunity.signals.last_year }}</span>
                </div>
              </div>
              <div class="opportunity-evidence">
                <h4>证据论文</h4>
                <div v-for="paper in opportunity.evidence_papers" :key="paper.paper_id" class="opportunity-evidence-item">
                  <div>
                    <strong>{{ paper.title }}</strong>
                    <span>{{ paper.publication_year || '年份未知' }} · 被引 {{ paper.citation_count }}</span>
                  </div>
                  <a-button type="link" size="small" @click="openPaper(paper)">查看论文</a-button>
                </div>
              </div>
              <div class="opportunity-next-actions">
                <span>下一步</span>
                <p v-for="action in opportunity.next_actions" :key="action">{{ action }}</p>
              </div>
            </article>
          </div>
          <p class="opportunity-limitation">{{ opportunityData.methodology.limitation }}</p>
        </div>
      </section>
      <template v-else-if="activeMode === 'library'">
      <section class="research-toolbar" aria-label="论文筛选">
        <div class="toolbar-field database-field">
          <label for="research-database">论文知识库</label>
          <a-select
            id="research-database"
            v-model:value="selectedKbId"
            :options="databaseOptions"
            :loading="databasesLoading"
            placeholder="请选择论文知识库"
            show-search
            option-filter-prop="label"
            @change="handleDatabaseChange"
          />
        </div>
        <div class="toolbar-field search-field">
          <label for="paper-search">搜索论文</label>
          <a-input-search
            id="paper-search"
            v-model:value="filters.query"
            placeholder="标题、作者、DOI 或期刊 / 会议"
            allow-clear
            @search="applyFilters"
          />
        </div>
        <div class="toolbar-field year-field">
          <label>发表年份</label>
          <div class="year-range">
            <a-input-number
              v-model:value="filters.yearFrom"
              :min="1500"
              :max="maxPublicationYear"
              placeholder="起始"
            />
            <span>至</span>
            <a-input-number
              v-model:value="filters.yearTo"
              :min="1500"
              :max="maxPublicationYear"
              placeholder="结束"
            />
          </div>
        </div>
        <div class="toolbar-field sort-field">
          <label>排序</label>
          <a-select
            v-model:value="sortValue"
            :options="sortOptions"
            style="width: 140px"
            @change="applyFilters"
          />
        </div>
        <div class="toolbar-actions">
          <a-button type="primary" :disabled="!selectedKbId" @click="applyFilters">筛选</a-button>
          <a-button :disabled="!hasFilters" @click="resetFilters">重置</a-button>
          <a-button :disabled="!selectedKbId || papers.length === 0" :loading="exportLoading" @click="exportAllBibtex">
            <template #icon><Download :size="15" /></template>
            导出 BibTeX
          </a-button>
        </div>
      </section>

      <a-result
        v-if="databasesError"
        status="error"
        title="知识库加载失败"
        :sub-title="databasesError"
      >
        <template #extra>
          <a-button @click="loadDatabases">重新加载</a-button>
        </template>
      </a-result>

      <a-empty
        v-else-if="!databasesLoading && databases.length === 0"
        class="page-empty"
        description="当前没有可访问的文档知识库"
      >
        <a-button v-if="userStore.isAdmin" type="primary" @click="openKnowledgeManagement">
          创建论文知识库
        </a-button>
      </a-empty>

      <template v-else-if="selectedKbId">
        <section class="list-summary">
          <div>
            <h2>{{ selectedDatabase?.name || '论文库' }}</h2>
            <p>{{ selectedDatabase?.description || '暂无知识库说明' }}</p>
          </div>
          <div class="summary-count">
            <BookOpenText :size="16" />
            <span>{{ total }} 篇论文</span>
          </div>
        </section>

        <div v-if="loading && papers.length === 0" class="papers-loading">
          <a-skeleton active :paragraph="{ rows: 8 }" />
        </div>

        <a-result
          v-else-if="papersError"
          status="error"
          title="论文列表加载失败"
          :sub-title="papersError"
        >
          <template #extra>
            <a-button @click="loadPapers">重新加载</a-button>
          </template>
        </a-result>

        <a-empty
          v-else-if="papers.length === 0"
          class="page-empty"
          :description="hasFilters ? '没有符合筛选条件的论文' : '当前知识库还没有完成入库的学术论文'"
        >
          <template v-if="userStore.isAdmin && !hasFilters">
            <p class="empty-hint">上传论文后请选择 Academic Paper 分块策略并执行入库。</p>
            <a-button type="primary" @click="openSelectedKnowledgeBase">上传并入库论文</a-button>
          </template>
          <a-button v-else-if="hasFilters" @click="resetFilters">清除筛选</a-button>
        </a-empty>

        <div v-else class="paper-list" :class="{ refreshing: loading }">
          <article
            v-for="paper in papers"
            :key="paper.paper_id"
            class="paper-row"
            tabindex="0"
            @click="openPaper(paper)"
            @keydown.enter="openPaper(paper)"
          >
            <div class="paper-icon" aria-hidden="true">
              <FileText :size="20" />
            </div>
            <div class="paper-main">
              <div class="paper-title-row">
                <h3>{{ paper.title }}</h3>
                <span class="metadata-status" :class="statusClass(paper.metadata_status)">
                  {{ statusLabel(paper.metadata_status) }}
                </span>
              </div>
              <p v-if="paper.authors?.length" class="paper-authors">
                {{ formatAuthors(paper.authors) }}
              </p>
              <p v-if="paper.abstract" class="paper-abstract">{{ paper.abstract }}</p>
              <div class="paper-metadata">
                <span v-if="paper.publication_year">
                  <CalendarDays :size="13" />
                  {{ paper.publication_year }}
                </span>
                <span v-if="paper.venue">
                  <Landmark :size="13" />
                  {{ paper.venue }}
                </span>
                <span v-if="paper.doi" :title="paper.doi">
                  <Link :size="13" />
                  {{ paper.doi }}
                </span>
                <span v-if="paper.citation_count !== null">
                  <Quote :size="13" />
                  被引 {{ paper.citation_count }}
                </span>
              </div>
              <div v-if="paper.keywords?.length" class="paper-keywords">
                <a-tag v-for="keyword in paper.keywords.slice(0, 6)" :key="keyword">
                  {{ keyword }}
                </a-tag>
                <span v-if="paper.keywords.length > 6">+{{ paper.keywords.length - 6 }}</span>
              </div>
            </div>
            <ChevronRight class="paper-open-icon" :size="18" aria-label="查看论文详情" />
          </article>
        </div>

        <div v-if="total > pageSize" class="pagination-row">
          <a-pagination
            v-model:current="page"
            v-model:page-size="pageSize"
            :total="total"
            :page-size-options="['20', '50', '100']"
            show-size-changer
            show-quick-jumper
            :show-total="(value) => `共 ${value} 篇`"
            @change="loadPapers"
            @show-size-change="handlePageSizeChange"
          />
        </div>
      </template>
      </template>
    </main>

    <ResearchCopilotPanel
      v-if="selectedKbId"
      v-model:open="copilotOpen"
      :kb-id="selectedKbId"
      :kb-name="selectedDatabase?.name || selectedKbId"
      :project-id="selectedProject?.project_id || ''"
      :project-title="selectedProject?.title || ''"
      :surface="activeMode"
      :selection="copilotSelection"
      @run-complete="handleCopilotRunComplete"
    />

    <a-modal
      v-model:open="externalImportOpen"
      title="导入 Semantic Scholar 公开论文"
      width="760px"
      :footer="null"
      destroy-on-close
    >
      <p class="external-import-help">
        输入论文标题、DOI 或 Semantic Scholar Paper ID。系统只导入 Semantic Scholar 声明的公开 PDF，下载后会自动进入 Academic Paper 分块、解析和 Milvus 索引任务。
      </p>
      <div class="external-import-search-row">
        <a-input
          v-model:value="externalImportQuery"
          placeholder="例如：Attention Is All You Need 或 10.48550/arXiv.1706.03762"
          :maxlength="500"
          @press-enter="searchExternalPaperCatalog"
        />
        <a-button type="primary" :loading="externalSearchLoading" @click="searchExternalPaperCatalog">
          搜索
        </a-button>
      </div>
      <a-alert v-if="externalImportError" type="error" show-icon :message="externalImportError" />
      <a-spin v-if="externalSearchLoading" />
      <a-empty v-else-if="externalSearchPerformed && !externalPapers.length" description="没有找到可导入的论文" />
      <div v-else class="external-paper-results">
        <article v-for="paper in externalPapers" :key="paper.paper_id" class="external-paper-result">
          <div>
            <h3>{{ paper.title || '未命名论文' }}</h3>
            <p>{{ paper.authors?.join('、') || '作者未知' }}<span v-if="paper.publication_year"> · {{ paper.publication_year }}</span></p>
            <p v-if="paper.abstract" class="external-paper-abstract">{{ paper.abstract }}</p>
            <div class="external-paper-meta">
              <a-tag v-if="paper.doi">DOI {{ paper.doi }}</a-tag>
              <a-tag v-if="paper.is_open_access" color="green">公开获取</a-tag>
              <a-tag v-else color="orange">无公开 PDF</a-tag>
              <span v-if="paper.citation_count !== null && paper.citation_count !== undefined">被引 {{ paper.citation_count }}</span>
            </div>
          </div>
          <a-button
            type="primary"
            :disabled="!paper.open_access_url || externalImportingId === paper.paper_id"
            :loading="externalImportingId === paper.paper_id"
            @click="importExternalPaperItem(paper)"
          >
            导入并索引
          </a-button>
        </article>
      </div>
    </a-modal>

    <PaperDetailDrawer
      :open="detailOpen"
      :kb-id="selectedKbId"
      :paper-id="selectedPaperId"
      :can-edit="userStore.isAdmin"
      @close="closePaper"
      @updated="handlePaperUpdated"
    />

    <a-drawer
      :open="evidenceOpen"
      title="证据原文定位"
      :width="520"
      @close="closeEvidence"
    >
      <a-spin v-if="evidenceLoading" />
      <a-result v-else-if="evidenceError" status="error" title="原文定位失败" :sub-title="evidenceError" />
      <div v-else-if="selectedEvidence" class="evidence-detail">
        <div class="evidence-detail-meta">
          <a-tag color="blue">{{ selectedEvidence.section_title || selectedEvidence.section_type || '论文内容' }}</a-tag>
          <span v-if="selectedEvidence.source_page_start">
            PDF 页码 {{ selectedEvidence.source_page_start }}<template v-if="selectedEvidence.source_page_end && selectedEvidence.source_page_end !== selectedEvidence.source_page_start">–{{ selectedEvidence.source_page_end }}</template>
          </span>
          <a-tag v-if="hasPdfRectLocator" color="purple">
            {{ evidencePdfHighlightRects.length }} 处 PDF 文本坐标
          </a-tag>
          <span v-if="selectedEvidence.start_char_pos !== null">
            字符 {{ selectedEvidence.start_char_pos }}–{{ selectedEvidence.end_char_pos }}
          </span>
        </div>
        <div v-if="hasPdfPageLocator" class="evidence-pdf-actions">
          <a-button
            type="primary"
            size="small"
            :loading="evidencePdfLoading"
            @click="openEvidencePdf"
          >
            预览 PDF 原文页码 {{ evidencePdfPage }}
          </a-button>
          <span v-if="hasPdfRectLocator">页码与文本坐标均来自原始 PDF 文本层检索，预览中会叠加真实覆盖层。</span>
          <span v-else>页码来自原始 PDF 文本匹配；未命中稳定文本坐标时不提供覆盖层。</span>
        </div>
        <div v-if="evidencePdfDocument" ref="evidencePdfPreviewRef" class="evidence-pdf-preview">
          <div
            class="evidence-pdf-page"
            :style="{ aspectRatio: evidencePdfAspectRatio || '1 / 1' }"
          >
            <canvas ref="evidencePdfCanvasRef" aria-label="论文 PDF 原文页" />
            <div class="evidence-pdf-highlights" aria-hidden="true">
              <span
                v-for="(rect, index) in evidencePdfHighlightRects"
                :key="`${rect.left}-${rect.top}-${index}`"
                class="evidence-pdf-highlight"
                :style="rect"
              />
            </div>
          </div>
          <a-spin v-if="evidencePdfRendering" class="evidence-pdf-rendering" />
        </div>
        <a-alert v-if="evidencePdfError" type="error" show-icon :message="evidencePdfError" />
        <pre>{{ selectedEvidence.content }}</pre>
        <a-alert
          type="info"
          show-icon
          :message="hasPdfRectLocator ? '已定位到原始 PDF 页码与文本坐标' : (hasPdfPageLocator ? '已定位到原始 PDF 页码' : '当前定位基于解析后的原文字符区间')"
          :description="hasPdfRectLocator ? `已匹配 ${evidencePdfHighlightRects.length} 处 PDF 文本矩形（页 ${evidencePdfPage}），坐标来自 PyMuPDF 文本检索。` : (hasPdfPageLocator ? 'PDF 预览会打开匹配到的起始页；当前证据未命中可检索的稳定文本矩形。' : '如果原始 PDF 没有稳定的页码映射，系统不会伪造页码或覆盖层。')"
        />
        <div class="evidence-download-actions">
          <a-button
            v-if="selectedEvidenceFile?.original_download_path"
            :loading="evidenceDownloadVariant === 'original'"
            @click="downloadEvidenceFile('original')"
          >
            下载原始文件
          </a-button>
          <a-button
            v-if="selectedEvidenceFile?.parsed_download_path"
            :loading="evidenceDownloadVariant === 'parsed'"
            @click="downloadEvidenceFile('parsed')"
          >
            下载解析 Markdown
          </a-button>
        </div>
      </div>
    </a-drawer>
  </div>
</template>

<script setup>
// ResearchCompass 科研罗盘主页
// 本视图是本仓库在开源智能体框架 Yuxi 之上实现的科研业务界面，负责在一个知识库下
// 组织研究项目、论文库、智能检索、证据综述、引用图谱、研究趋势、研究机会、分析对比
// 与用户评测等多张工作台；通用对话页与智能体运行时由 Yuxi 提供，本视图不重复实现。
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { message } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import {
  AlertTriangle,
  BookOpenText,
  CalendarDays,
  ChevronRight,
  CircleStop,
  Database,
  Download,
  FileDown,
  FileText,
  History,
  Landmark,
  Link,
  ListChecks,
  Network,
  Quote,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Sparkles,
  Target
} from 'lucide-vue-next'
import { databaseApi } from '@/apis/knowledge_api'
import { researchApi } from '@/apis/research_api'
import { downloadWorkspaceKnowledgeFile } from '@/apis/workspace_api'
import PaperDetailDrawer from '@/components/research/PaperDetailDrawer.vue'
import ResearchCopilotPanel from '@/components/research/ResearchCopilotPanel.vue'
import ResearchProjectWorkspace from '@/components/research/ResearchProjectWorkspace.vue'
import ResearchSearchWorkspace from '@/components/research/ResearchSearchWorkspace.vue'
import UserStudyManager from '@/components/research/UserStudyManager.vue'
import PaperAnalysisEvaluationManager from '@/components/research/PaperAnalysisEvaluationManager.vue'
import PageHeader from '@/components/shared/PageHeader.vue'
import { useUserStore } from '@/stores/user'

const GraphCanvas = defineAsyncComponent(() => import('@/components/GraphCanvas.vue'))

let echartsPromise
let pdfJsPromise

const loadEcharts = () => {
  if (!echartsPromise) echartsPromise = import('@/utils/echarts')
  return echartsPromise
}

const loadPdfJs = () => {
  if (!pdfJsPromise) {
    pdfJsPromise = Promise.all([
      import('pdfjs-dist'),
      import('pdfjs-dist/build/pdf.worker.min.mjs?url')
    ]).then(([pdfJs, worker]) => {
      pdfJs.GlobalWorkerOptions.workerSrc = worker.default
      return pdfJs
    })
  }
  return pdfJsPromise
}

const router = useRouter()
const userStore = useUserStore()
const databases = ref([])
const databasesLoading = ref(false)
const databasesError = ref('')
const selectedKbId = ref('')
const papers = ref([])
const loading = ref(false)
const papersError = ref('')
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const detailOpen = ref(false)
const selectedPaperId = ref('')
const evidenceOpen = ref(false)
const evidenceLoading = ref(false)
const evidenceError = ref('')
const selectedEvidence = ref(null)
const selectedEvidenceFile = ref(null)
const evidenceDownloadVariant = ref('')
const evidencePdfLoading = ref(false)
const evidencePdfRendering = ref(false)
const evidencePdfError = ref('')
const evidencePdfDocument = shallowRef(null)
const evidencePdfPreviewRef = ref(null)
const evidencePdfCanvasRef = ref(null)
const evidencePdfAspectRatio = ref('')
const exportLoading = ref(false)
const externalImportOpen = ref(false)
const externalImportQuery = ref('')
const externalPapers = ref([])
const externalSearchLoading = ref(false)
const externalSearchPerformed = ref(false)
const externalImportError = ref('')
const externalImportingId = ref('')
const maxPublicationYear = new Date().getFullYear() + 1
const activeMode = ref('projects')
const projectSearchRunId = ref('')
const projectWorkspaceRef = ref(null)
const searchWorkspaceRef = ref(null)
const selectedProject = ref(null)
const selectedSearchRun = ref(null)
const copilotOpen = ref(
  typeof window === 'undefined' || !window.matchMedia('(max-width: 960px)').matches
)
const synthesisQuery = ref('')
const synthesisRuns = ref([])
const synthesisTotal = ref(0)
const synthesisLoading = ref(false)
const synthesisDetailLoading = ref(false)
const synthesisSubmitting = ref(false)
const synthesisRegenerating = ref(false)
const synthesisCancelling = ref(false)
const synthesisExporting = ref('')
const synthesisError = ref('')
const selectedSynthesisRunId = ref('')
const selectedSynthesisRun = ref(null)
const graphLoading = ref(false)
const graphError = ref('')
const graphData = ref(null)
const graphSyncLoading = ref(false)
const graphSyncRun = ref(null)
const graphConflicts = ref([])
const graphConflictsLoading = ref(false)
const graphConflictsError = ref('')
const graphConflictsTotal = ref(0)
const graphConflictsPage = ref(1)
const graphConflictsPageSize = 20
const selectedGraphNode = ref(null)
const graphRelations = ref([])
const relationsLoading = ref(false)
const relationsError = ref('')
const trendData = ref(null)
const trendLoading = ref(false)
const trendError = ref('')
const trendFilters = reactive({ yearFrom: null, yearTo: null })
const selectedTrendKeyword = ref('')
const opportunityData = ref(null)
const opportunityLoading = ref(false)
const opportunityError = ref('')
const opportunityFilters = reactive({ yearFrom: null, yearTo: null })
const synthesisActiveStatuses = new Set(['pending', 'retrieving', 'synthesizing', 'validating'])
const selectedSynthesisResult = computed(() => selectedSynthesisRun.value?.result || null)
const selectedSynthesisCoverage = computed(() => selectedSynthesisResult.value?.coverage || {})
const isSelectedSynthesisActive = computed(() => (
  synthesisActiveStatuses.has(selectedSynthesisRun.value?.status)
))
const canExportSynthesis = computed(() => (
  selectedSynthesisRun.value?.status === 'success'
  && selectedSynthesisResult.value?.validation?.status === 'verified'
))
const canRegenerateSynthesis = computed(() => (
  selectedSynthesisRun.value?.run_id
  && ['success', 'failed', 'cancelled'].includes(selectedSynthesisRun.value.status)
))
const canCancelSynthesis = computed(() => (
  selectedSynthesisRun.value?.run_id
  && synthesisActiveStatuses.has(selectedSynthesisRun.value.status)
))
const synthesisInsightSections = [
  { key: 'contradictions', title: '证据矛盾', empty: '未发现通过验证的跨论文矛盾', icon: AlertTriangle },
  { key: 'limitations', title: '研究局限', empty: '未提取到可验证局限', icon: FileText },
  { key: 'research_gaps', title: '研究空白', empty: '未提取到可验证研究空白', icon: Target }
]
const publicationTrendChartRef = ref(null)
const keywordTrendChartRef = ref(null)
let publicationTrendChart = null
let keywordTrendChart = null
let graphSyncTimer = null
let synthesisPollTimer = null
let synthesisRequestGeneration = 0
let workspaceRequestGeneration = 0
let evidencePdfLoadingTask = null
let evidencePdfRenderTask = null
let evidencePdfRequestId = 0
const synthesisFilters = reactive({
  yearFrom: null,
  yearTo: null,
  topK: 8,
  recallTopK: 50
})

const filters = reactive({
  query: '',
  yearFrom: null,
  yearTo: null,
  sortBy: 'year',
  sortOrder: 'desc'
})

const sortOptions = [
  { value: 'year-desc', label: '年份最新' },
  { value: 'year-asc', label: '年份最早' },
  { value: 'citation_count-desc', label: '被引最多' },
  { value: 'title-asc', label: '标题 A-Z' },
  { value: 'created_at-desc', label: '最近添加' }
]
const sortValue = computed({
  get: () => `${filters.sortBy}-${filters.sortOrder}`,
  set: (val) => {
    const [by, order] = val.split('-')
    filters.sortBy = by
    filters.sortOrder = order
  }
})
const selectedDatabase = computed(() =>
  databases.value.find((database) => database.kb_id === selectedKbId.value)
)
const copilotSelection = computed(() => {
  if (detailOpen.value && selectedPaperId.value) {
    const paper = papers.value.find((item) => item.paper_id === selectedPaperId.value)
    return {
      type: 'paper',
      id: selectedPaperId.value,
      title: paper?.title || ''
    }
  }
  if (activeMode.value === 'search' && selectedSearchRun.value?.run_id) {
    return {
      type: 'search_run',
      id: selectedSearchRun.value.run_id,
      title: selectedSearchRun.value.query || ''
    }
  }
  if (activeMode.value === 'synthesis' && selectedSynthesisRun.value?.run_id) {
    return {
      type: 'synthesis_run',
      id: selectedSynthesisRun.value.run_id,
      title: selectedSynthesisRun.value.query || ''
    }
  }
  if (activeMode.value === 'graph' && selectedGraphNode.value?.id) {
    return {
      type: 'paper',
      id: selectedGraphNode.value.id,
      title: selectedGraphNode.value.title || selectedGraphNode.value.name || ''
    }
  }
  return null
})
const databaseOptions = computed(() =>
  databases.value.map((database) => ({
    value: database.kb_id,
    label: database.name
  }))
)
const hasFilters = computed(() => Boolean(filters.query || filters.yearFrom || filters.yearTo))
const graphPapers = computed(() => (graphData.value?.nodes || []).filter((node) => node.type === 'paper'))
const graphCanvasData = computed(() => ({
  nodes: (graphData.value?.nodes || []).map((node) => ({
    ...node,
    name: node.title || node.name || node.id,
    raw_type: node.type,
    type: graphNodeTypeLabel(node.type)
  })),
  edges: (graphData.value?.edges || []).map((edge) => ({
    ...edge,
    source_id: edge.source,
    target_id: edge.target
  }))
}))
const academicNodeStyles = {
  style: {
    size: (node) => {
      const original = node.data?.original || {}
      if (original.type !== '论文') return original.type === '作者' ? 22 : 18
      return Math.min(24 + Math.log10(Number(original.citation_count || 0) + 1) * 10, 54)
    }
  }
}
const graphSyncStatusMessage = computed(() => {
  if (!graphSyncRun.value) return ''
  return {
    pending: '学术图谱同步等待执行',
    running: '正在同步 Semantic Scholar 学术图谱',
    success: '学术引用图谱同步完成',
    completed_with_conflicts: '学术引用图谱同步完成，但存在待处理冲突',
    failed: '学术引用图谱同步失败',
    cancelled: '学术引用图谱同步已取消'
  }[graphSyncRun.value.status] || graphSyncRun.value.status
})
const selectedTrendKeywordData = computed(() =>
  (trendData.value?.keyword_trends || []).find((item) => item.keyword === selectedTrendKeyword.value)
)
const evidencePdfPage = computed(() => Number(selectedEvidence.value?.source_page_start || 0))
const hasPdfPageLocator = computed(() => (
  ['pdf_page_range', 'pdf_page_rect'].includes(selectedEvidence.value?.locator_type)
  && Number.isInteger(evidencePdfPage.value)
  && evidencePdfPage.value > 0
  && Boolean(selectedEvidenceFile.value?.original_available)
))

const toPdfHighlightStyle = (rect) => {
  if (
    Number(rect?.page) !== evidencePdfPage.value
    || !['nx0', 'ny0', 'nx1', 'ny1'].every((key) => Number.isFinite(Number(rect?.[key])))
  ) return null

  const x0 = Number(rect.nx0)
  const y0 = Number(rect.ny0)
  const x1 = Number(rect.nx1)
  const y1 = Number(rect.ny1)
  if (x0 < 0 || y0 < 0 || x1 > 1 || y1 > 1 || x1 <= x0 || y1 <= y0) return null

  const rotation = ((Number(rect.rotation) || 0) % 360 + 360) % 360
  const displayRect = {
    0: [x0, y0, x1, y1],
    90: [1 - y1, x0, 1 - y0, x1],
    180: [1 - x1, 1 - y1, 1 - x0, 1 - y0],
    270: [y0, 1 - x1, y1, 1 - x0]
  }[rotation]
  if (!displayRect) return null
  const [left, top, right, bottom] = displayRect
  return {
    left: `${left * 100}%`,
    top: `${top * 100}%`,
    width: `${(right - left) * 100}%`,
    height: `${(bottom - top) * 100}%`
  }
}

const evidencePdfHighlightRects = computed(() => (
  (selectedEvidence.value?.source_rects || [])
    .map(toPdfHighlightStyle)
    .filter(Boolean)
))
const hasPdfRectLocator = computed(() => (
  selectedEvidence.value?.locator_type === 'pdf_page_rect'
  && evidencePdfHighlightRects.value.length > 0
  && hasPdfPageLocator.value
))

const graphConflictTypeLabel = (type) => ({
  paper_not_found: '远端论文不存在',
  paper_match_not_found: '未找到严格匹配',
  paper_match_ambiguous: '匹配结果不唯一',
  paper_title_conflict: '标题不一致',
  paper_year_conflict: '年份不一致',
  graph_identity_conflict: '图谱身份冲突'
})[type] || type
const relationTypeLabel = (type) => ({
  citation_chain: '引用链',
  shared_reference: '共同参考文献',
  shared_citation: '共同被引',
  reverse_citation_chain: '反向引用链'
})[type] || type
const formatConflictValue = (value) => value === null ? '无' : JSON.stringify(value, null, 2)
const synthesisStatusLabel = (status) => ({
  pending: '等待执行',
  retrieving: '正在检索',
  synthesizing: '正在综述',
  validating: '正在校验',
  success: '验证完成',
  failed: '运行失败',
  cancelled: '已取消'
})[status] || status || '未知状态'
const synthesisStageLabel = (stage) => ({
  pending: '等待执行',
  retrieving: '本地混合检索',
  synthesizing: '结构化跨论文综述',
  validating: '证据身份与引用覆盖校验',
  completed: '证据验证完成',
  failed: '运行失败',
  cancelled: '任务已取消'
})[stage] || stage || '状态未知'
const synthesisStatusClass = (status) => `synthesis-status-${status || 'unknown'}`
const synthesisStagePercent = (stage) => ({
  pending: 5,
  retrieving: 25,
  synthesizing: 60,
  validating: 85,
  completed: 100,
  failed: 100,
  cancelled: 100
})[stage] || 0
const confidenceColor = (confidence) => ({
  high: 'green',
  medium: 'blue',
  low: 'orange'
})[confidence] || 'default'
const confidenceLabel = (confidence) => ({
  high: '高置信度',
  medium: '中置信度',
  low: '低置信度'
})[confidence] || '未知置信度'
const formatPercent = (value) => `${(Number(value || 0) * 100).toFixed(1)}%`
const formatDateTime = (value) => {
  if (!value) return '时间未知'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString()
}
const getSynthesisEvidenceLabel = (citation) => (
  `${citation.paper_title || citation.paper_id} · ${citation.section_title || citation.section_type || '论文内容'}`
)

const statusLabels = {
  extracted: '已提取',
  verified: '已校正',
  pending_reindex: '同步中',
  sync_failed: '同步失败'
}

const statusLabel = (status) => statusLabels[status] || status || '未知状态'
const statusClass = (status) => `status-${status || 'unknown'}`
const formatAuthors = (authors) => {
  if (authors.length <= 6) return authors.join('、')
  return `${authors.slice(0, 6).join('、')} 等 ${authors.length} 位作者`
}

const loadDatabases = async () => {
  databasesLoading.value = true
  databasesError.value = ''
  try {
    const result = await databaseApi.getAccessibleDatabases()
    databases.value = (result.databases || []).filter((database) => database.supports_documents)
    if (!selectedKbId.value || !databases.value.some((item) => item.kb_id === selectedKbId.value)) {
      selectedKbId.value = databases.value[0]?.kb_id || ''
    }
  } catch (error) {
    databasesError.value = error.message || '无法加载可访问知识库'
  } finally {
    databasesLoading.value = false
  }
}

const loadPapers = async () => {
  if (!selectedKbId.value) {
    papers.value = []
    total.value = 0
    return
  }
  if (filters.yearFrom && filters.yearTo && filters.yearFrom > filters.yearTo) {
    papersError.value = '起始年份不能大于结束年份'
    return
  }

  const kbId = selectedKbId.value
  const requestGeneration = workspaceRequestGeneration
  loading.value = true
  papersError.value = ''
  try {
    const result = await researchApi.listPapers(kbId, {
      query: filters.query.trim(),
      year_from: filters.yearFrom,
      year_to: filters.yearTo,
      sort_by: filters.sortBy,
      sort_order: filters.sortOrder,
      page: page.value,
      page_size: pageSize.value
    })
    if (requestGeneration !== workspaceRequestGeneration || kbId !== selectedKbId.value) return
    papers.value = result.items || []
    total.value = result.total || 0
    if (page.value > 1 && papers.value.length === 0 && total.value > 0) {
      page.value = Math.ceil(total.value / pageSize.value)
      await loadPapers()
    }
  } catch (error) {
    if (requestGeneration !== workspaceRequestGeneration) return
    papersError.value = error.message || '无法加载论文列表'
  } finally {
    if (requestGeneration === workspaceRequestGeneration) loading.value = false
  }
}

const renderTrendCharts = async (requestGeneration = workspaceRequestGeneration) => {
  await nextTick()
  if (requestGeneration !== workspaceRequestGeneration || !trendData.value) return
  const echarts = await loadEcharts()
  if (requestGeneration !== workspaceRequestGeneration) return
  if (publicationTrendChart) publicationTrendChart.dispose()
  if (keywordTrendChart) keywordTrendChart.dispose()
  if (publicationTrendChartRef.value) {
    publicationTrendChart = echarts.init(publicationTrendChartRef.value)
    const rows = trendData.value.publication_trend || []
    publicationTrendChart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['论文数', '引用数'] },
      grid: { left: 42, right: 18, top: 42, bottom: 28 },
      xAxis: { type: 'category', data: rows.map((item) => item.year) },
      yAxis: { type: 'value', minInterval: 1 },
      series: [
        { name: '论文数', type: 'line', smooth: true, data: rows.map((item) => item.papers) },
        { name: '引用数', type: 'line', smooth: true, data: rows.map((item) => item.citations) }
      ]
    })
  }
  if (keywordTrendChartRef.value) {
    keywordTrendChart = echarts.init(keywordTrendChartRef.value)
    const rows = trendData.value.keyword_totals || []
    keywordTrendChart.setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 100, right: 18, top: 18, bottom: 28 },
      xAxis: { type: 'value', minInterval: 1 },
      yAxis: { type: 'category', data: rows.slice().reverse().map((item) => item.keyword) },
      series: [{ type: 'bar', data: rows.slice().reverse().map((item) => item.total), barMaxWidth: 22 }]
    })
  }
}

const loadTrends = async () => {
  if (!selectedKbId.value) return
  if (trendFilters.yearFrom && trendFilters.yearTo && trendFilters.yearFrom > trendFilters.yearTo) {
    trendError.value = '起始年份不能大于结束年份'
    return
  }
  const kbId = selectedKbId.value
  const requestGeneration = workspaceRequestGeneration
  trendLoading.value = true
  trendError.value = ''
  try {
    const result = await researchApi.getAcademicTrends(kbId, {
      year_from: trendFilters.yearFrom,
      year_to: trendFilters.yearTo,
      top_keywords: 20,
      limit: 50000
    })
    if (requestGeneration !== workspaceRequestGeneration || kbId !== selectedKbId.value) return
    trendData.value = result
    selectedTrendKeyword.value = trendData.value.keyword_trends?.[0]?.keyword || ''
    await renderTrendCharts(requestGeneration)
  } catch (error) {
    if (requestGeneration !== workspaceRequestGeneration) return
    trendError.value = error.message || '研究趋势加载失败'
  } finally {
    if (requestGeneration === workspaceRequestGeneration) trendLoading.value = false
  }
}

const loadOpportunities = async () => {
  if (!selectedKbId.value) return
  if (
    opportunityFilters.yearFrom
    && opportunityFilters.yearTo
    && opportunityFilters.yearFrom > opportunityFilters.yearTo
  ) {
    opportunityError.value = '起始年份不能大于结束年份'
    return
  }
  const kbId = selectedKbId.value
  const requestGeneration = workspaceRequestGeneration
  opportunityLoading.value = true
  opportunityError.value = ''
  try {
    const result = await researchApi.getAcademicOpportunities(kbId, {
      year_from: opportunityFilters.yearFrom,
      year_to: opportunityFilters.yearTo,
      limit: 12
    })
    if (requestGeneration !== workspaceRequestGeneration || kbId !== selectedKbId.value) return
    opportunityData.value = result
  } catch (error) {
    if (requestGeneration !== workspaceRequestGeneration) return
    opportunityError.value = error.message || '研究机会加载失败'
  } finally {
    if (requestGeneration === workspaceRequestGeneration) opportunityLoading.value = false
  }
}

const selectTrendKeyword = (keyword) => {
  selectedTrendKeyword.value = keyword
}

const clearSynthesisPolling = () => {
  clearTimeout(synthesisPollTimer)
  synthesisPollTimer = null
}

const upsertSynthesisRun = (run) => {
  if (!run?.run_id) return
  const index = synthesisRuns.value.findIndex((item) => item.run_id === run.run_id)
  if (index >= 0) {
    synthesisRuns.value[index] = { ...synthesisRuns.value[index], ...run }
    return
  }
  synthesisRuns.value.unshift(run)
  synthesisTotal.value += 1
}

const scheduleSynthesisPoll = (runId) => {
  clearSynthesisPolling()
  synthesisPollTimer = setTimeout(() => pollSynthesisRun(runId), 2500)
}

const applySynthesisRun = (run) => {
  upsertSynthesisRun(run)
  if (selectedSynthesisRunId.value === run.run_id) selectedSynthesisRun.value = run
}

const loadSynthesisRun = async (runId, { poll = true } = {}) => {
  if (!runId) return null
  const requestGeneration = synthesisRequestGeneration
  synthesisDetailLoading.value = true
  try {
    const run = await researchApi.getResearchSynthesisRun(runId)
    if (requestGeneration !== synthesisRequestGeneration) return null
    applySynthesisRun(run)
    if (poll && synthesisActiveStatuses.has(run.status)) {
      scheduleSynthesisPoll(run.run_id)
    } else if (!synthesisActiveStatuses.has(run.status)) {
      clearSynthesisPolling()
    }
    return run
  } catch (error) {
    if (requestGeneration !== synthesisRequestGeneration) return null
    synthesisError.value = error.message || '研究综述详情加载失败'
    return null
  } finally {
    if (requestGeneration === synthesisRequestGeneration) synthesisDetailLoading.value = false
  }
}

const pollSynthesisRun = async (runId) => {
  const run = await loadSynthesisRun(runId, { poll: false })
  if (!run) return
  if (synthesisActiveStatuses.has(run.status)) {
    scheduleSynthesisPoll(run.run_id)
    return
  }
  await loadSyntheses({ preserveSelection: true })
  if (run.status === 'success') message.success('证据约束研究综述已完成验证')
}

const loadSyntheses = async ({ preserveSelection = true } = {}) => {
  if (!selectedKbId.value) {
    synthesisRuns.value = []
    synthesisTotal.value = 0
    selectedSynthesisRun.value = null
    selectedSynthesisRunId.value = ''
    return
  }
  const requestGeneration = synthesisRequestGeneration
  synthesisLoading.value = true
  synthesisError.value = ''
  try {
    const result = await researchApi.listResearchSyntheses(selectedKbId.value, { offset: 0, limit: 20 })
    if (requestGeneration !== synthesisRequestGeneration) return
    synthesisRuns.value = result.items || []
    synthesisTotal.value = result.total || 0
    const selected = preserveSelection
      ? synthesisRuns.value.find((item) => item.run_id === selectedSynthesisRunId.value)
      : null
    const nextRun = selected || synthesisRuns.value[0] || null
    selectedSynthesisRun.value = nextRun
    selectedSynthesisRunId.value = nextRun?.run_id || ''
    if (nextRun && synthesisActiveStatuses.has(nextRun.status)) scheduleSynthesisPoll(nextRun.run_id)
  } catch (error) {
    if (requestGeneration !== synthesisRequestGeneration) return
    synthesisError.value = error.message || '研究综述历史加载失败'
  } finally {
    if (requestGeneration === synthesisRequestGeneration) synthesisLoading.value = false
  }
}

const selectSynthesisRun = async (run) => {
  if (!run?.run_id) return
  selectedSynthesisRunId.value = run.run_id
  selectedSynthesisRun.value = run
  synthesisError.value = ''
  await loadSynthesisRun(run.run_id)
}

const createSynthesis = async () => {
  if (!selectedKbId.value || !synthesisQuery.value.trim()) return
  if (synthesisFilters.yearFrom && synthesisFilters.yearTo && synthesisFilters.yearFrom > synthesisFilters.yearTo) {
    synthesisError.value = '起始年份不能大于结束年份'
    return
  }
  if (synthesisFilters.recallTopK < synthesisFilters.topK) {
    synthesisError.value = '候选证据数不能小于论文数'
    return
  }
  const kbId = selectedKbId.value
  const requestGeneration = synthesisRequestGeneration
  const query = synthesisQuery.value.trim()
  synthesisSubmitting.value = true
  synthesisError.value = ''
  try {
    const created = await researchApi.createResearchSynthesis(kbId, {
      query,
      top_k: synthesisFilters.topK,
      recall_top_k: synthesisFilters.recallTopK,
      year_from: synthesisFilters.yearFrom,
      year_to: synthesisFilters.yearTo
    })
    if (requestGeneration !== synthesisRequestGeneration || kbId !== selectedKbId.value) return
    const run = {
      run_id: created.run_id,
      task_id: created.task_id,
      query,
      status: created.status || 'pending',
      stage: 'pending'
    }
    selectedSynthesisRunId.value = run.run_id
    selectedSynthesisRun.value = run
    upsertSynthesisRun(run)
    message.success('已提交证据约束研究综述任务')
    scheduleSynthesisPoll(run.run_id)
  } catch (error) {
    if (requestGeneration !== synthesisRequestGeneration) return
    synthesisError.value = error.message || '研究综述任务提交失败'
  } finally {
    if (requestGeneration === synthesisRequestGeneration) synthesisSubmitting.value = false
  }
}

const refreshSelectedSynthesis = async () => {
  if (!selectedSynthesisRunId.value) return
  synthesisError.value = ''
  await loadSynthesisRun(selectedSynthesisRunId.value)
}

const cancelSelectedSynthesis = async () => {
  const runId = selectedSynthesisRun.value?.run_id
  if (!runId || !canCancelSynthesis.value || synthesisCancelling.value) return
  const requestGeneration = synthesisRequestGeneration
  synthesisCancelling.value = true
  synthesisError.value = ''
  try {
    const cancelled = await researchApi.cancelResearchSynthesis(runId)
    if (requestGeneration !== synthesisRequestGeneration) return
    selectedSynthesisRun.value = cancelled
    upsertSynthesisRun(cancelled)
    message.success('研究综述已取消')
  } catch (error) {
    if (requestGeneration !== synthesisRequestGeneration) return
    synthesisError.value = error.message || '研究综述取消失败'
  } finally {
    if (requestGeneration === synthesisRequestGeneration) synthesisCancelling.value = false
  }
}

const regenerateSelectedSynthesis = async () => {
  if (!selectedSynthesisRun.value?.run_id || synthesisRegenerating.value) return
  const kbId = selectedKbId.value
  const requestGeneration = synthesisRequestGeneration
  const selectedRun = selectedSynthesisRun.value
  synthesisRegenerating.value = true
  synthesisError.value = ''
  try {
    const created = await researchApi.regenerateResearchSynthesis(selectedRun.run_id)
    if (requestGeneration !== synthesisRequestGeneration || kbId !== selectedKbId.value) return
    const run = {
      run_id: created.run_id,
      task_id: created.task_id,
      query: selectedRun.query,
      status: created.status || 'pending',
      stage: 'pending'
    }
    selectedSynthesisRunId.value = run.run_id
    selectedSynthesisRun.value = run
    upsertSynthesisRun(run)
    message.success('已提交重新生成任务')
    scheduleSynthesisPoll(run.run_id)
  } catch (error) {
    if (requestGeneration !== synthesisRequestGeneration) return
    synthesisError.value = error.message || '重新生成研究综述失败'
  } finally {
    if (requestGeneration === synthesisRequestGeneration) synthesisRegenerating.value = false
  }
}

const exportSelectedSynthesis = async (format) => {
  const runId = selectedSynthesisRun.value?.run_id
  if (!runId || !canExportSynthesis.value || synthesisExporting.value) return
  synthesisExporting.value = format
  try {
    const response = await researchApi.exportResearchSynthesis(runId, format)
    const blob = await response.blob()
    const header = response.headers.get('content-disposition') || ''
    const filename = header.match(/filename="?([^";]+)"?/i)?.[1] || `research_synthesis_${runId}.${format === 'docx' ? 'docx' : 'md'}`
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    synthesisError.value = error.message || '研究综述导出失败'
  } finally {
    synthesisExporting.value = ''
  }
}

const openSynthesisEvidence = (citation) => {
  if (!citation?.paper_id || !citation?.chunk_id) {
    synthesisError.value = '该引用缺少论文或证据 chunk 标识'
    return
  }
  openEvidence({ paper_id: citation.paper_id }, { chunk_id: citation.chunk_id })
}

const openEvidence = async (paper, evidence) => {
  closeEvidence()
  const requestId = evidencePdfRequestId
  const kbId = selectedKbId.value
  evidenceOpen.value = true
  evidenceLoading.value = true
  evidenceError.value = ''
  selectedEvidence.value = null
  selectedEvidenceFile.value = null
  try {
    const result = await researchApi.getPaperEvidence(kbId, paper.paper_id, evidence.chunk_id)
    if (requestId !== evidencePdfRequestId || kbId !== selectedKbId.value) return
    selectedEvidence.value = result.evidence
    selectedEvidenceFile.value = result.file
  } catch (error) {
    if (requestId !== evidencePdfRequestId) return
    evidenceError.value = error.message || '证据原文定位失败'
  } finally {
    if (requestId === evidencePdfRequestId) evidenceLoading.value = false
  }
}

const disposeEvidencePdf = () => {
  evidencePdfRenderTask?.cancel()
  evidencePdfRenderTask = null
  evidencePdfLoadingTask?.destroy()
  evidencePdfLoadingTask = null
  const pdfDocument = evidencePdfDocument.value
  evidencePdfDocument.value = null
  if (pdfDocument) void pdfDocument.destroy()
  evidencePdfAspectRatio.value = ''
  evidencePdfRendering.value = false
}

const renderEvidencePdfPage = async () => {
  const pdfDocument = evidencePdfDocument.value
  const preview = evidencePdfPreviewRef.value
  const canvas = evidencePdfCanvasRef.value
  if (!pdfDocument || !preview || !canvas) return
  if (evidencePdfPage.value > pdfDocument.numPages) {
    throw new Error(`定位页码 ${evidencePdfPage.value} 超出 PDF 总页数 ${pdfDocument.numPages}`)
  }

  evidencePdfRendering.value = true
  const page = await pdfDocument.getPage(evidencePdfPage.value)
  const baseViewport = page.getViewport({ scale: 1 })
  const scale = Math.max(preview.clientWidth / baseViewport.width, 0.1)
  const viewport = page.getViewport({ scale })
  const outputScale = Math.min(window.devicePixelRatio || 1, 2)
  const context = canvas.getContext('2d', { alpha: false })
  if (!context) throw new Error('浏览器无法创建 PDF Canvas 渲染上下文')

  evidencePdfAspectRatio.value = `${viewport.width} / ${viewport.height}`
  canvas.width = Math.floor(viewport.width * outputScale)
  canvas.height = Math.floor(viewport.height * outputScale)
  canvas.style.width = `${viewport.width}px`
  canvas.style.height = `${viewport.height}px`
  evidencePdfRenderTask = page.render({
    canvasContext: context,
    transform: outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0],
    viewport
  })
  try {
    await evidencePdfRenderTask.promise
  } finally {
    evidencePdfRenderTask = null
    evidencePdfRendering.value = false
  }
}

const openEvidencePdf = async () => {
  if (!hasPdfPageLocator.value || evidencePdfLoading.value || evidencePdfDocument.value) return
  const requestId = ++evidencePdfRequestId
  evidencePdfLoading.value = true
  evidencePdfError.value = ''
  try {
    const response = await downloadWorkspaceKnowledgeFile(
      selectedKbId.value,
      selectedEvidenceFile.value.file_id,
      'original'
    )
    const contentType = response.headers.get('content-type') || ''
    if (!contentType.includes('application/pdf')) {
      throw new Error('原始文件不是 PDF，无法按页码预览')
    }
    const { getDocument } = await loadPdfJs()
    evidencePdfLoadingTask = getDocument({ data: new Uint8Array(await response.arrayBuffer()) })
    const pdfDocument = await evidencePdfLoadingTask.promise
    if (requestId !== evidencePdfRequestId || !evidenceOpen.value) {
      await pdfDocument.destroy()
      return
    }
    evidencePdfLoadingTask = null
    evidencePdfDocument.value = pdfDocument
    await nextTick()
    await renderEvidencePdfPage()
  } catch (error) {
    if (requestId === evidencePdfRequestId) {
      const errorMessage = error.message || 'PDF 原文加载失败'
      disposeEvidencePdf()
      evidencePdfError.value = errorMessage
      message.error(errorMessage)
    }
  } finally {
    if (requestId === evidencePdfRequestId) evidencePdfLoading.value = false
  }
}

const closeEvidence = () => {
  evidencePdfRequestId += 1
  evidenceOpen.value = false
  disposeEvidencePdf()
  evidencePdfLoading.value = false
  evidencePdfError.value = ''
  evidenceDownloadVariant.value = ''
}

const searchExternalPaperCatalog = async () => {
  const query = externalImportQuery.value.trim()
  if (!selectedKbId.value || !query) {
    externalImportError.value = '请输入论文标题、DOI 或 Semantic Scholar Paper ID'
    return
  }
  const kbId = selectedKbId.value
  const requestGeneration = workspaceRequestGeneration
  externalSearchLoading.value = true
  externalSearchPerformed.value = true
  externalImportError.value = ''
  try {
    const result = await researchApi.searchExternalPapers(kbId, query, 10)
    if (requestGeneration !== workspaceRequestGeneration || kbId !== selectedKbId.value) return
    externalPapers.value = result.items || []
  } catch (error) {
    if (requestGeneration !== workspaceRequestGeneration) return
    externalPapers.value = []
    externalImportError.value = error.message || '外部论文搜索失败'
  } finally {
    if (requestGeneration === workspaceRequestGeneration) externalSearchLoading.value = false
  }
}

const importExternalPaperItem = async (paper) => {
  if (!paper?.paper_id || externalImportingId.value) return
  const kbId = selectedKbId.value
  const requestGeneration = workspaceRequestGeneration
  externalImportingId.value = paper.paper_id
  externalImportError.value = ''
  try {
    const result = await researchApi.importExternalPaper(kbId, paper.paper_id)
    if (requestGeneration !== workspaceRequestGeneration || kbId !== selectedKbId.value) return
    message.success(`已提交“${result.title}”导入任务`)
    externalImportOpen.value = false
    externalPapers.value = []
    externalImportQuery.value = ''
    externalSearchPerformed.value = false
    await loadPapers()
  } catch (error) {
    if (requestGeneration !== workspaceRequestGeneration) return
    externalImportError.value = error.message || '外部论文导入失败'
  } finally {
    if (requestGeneration === workspaceRequestGeneration) externalImportingId.value = ''
  }
}

const downloadEvidenceFile = async (variant) => {
  const file = selectedEvidenceFile.value
  if (!file?.file_id || evidenceDownloadVariant.value) return
  const requestId = evidencePdfRequestId
  const kbId = selectedKbId.value
  evidenceDownloadVariant.value = variant
  try {
    const response = await downloadWorkspaceKnowledgeFile(kbId, file.file_id, variant)
    if (requestId !== evidencePdfRequestId || kbId !== selectedKbId.value) return
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    const originalName = file.filename || `paper-${file.file_id}`
    anchor.download = variant === 'parsed'
      ? `${originalName.replace(/\.[^.]+$/, '')}.md`
      : originalName
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    if (requestId !== evidencePdfRequestId) return
    message.error(error.message || '文件下载失败')
  } finally {
    if (requestId === evidencePdfRequestId) evidenceDownloadVariant.value = ''
  }
}

const loadAcademicGraph = async () => {
  if (!selectedKbId.value) return
  const kbId = selectedKbId.value
  const requestGeneration = workspaceRequestGeneration
  graphLoading.value = true
  graphError.value = ''
  try {
    const result = await researchApi.getAcademicGraph(kbId, { depth: 2, limit: 300 })
    if (requestGeneration !== workspaceRequestGeneration || kbId !== selectedKbId.value) return
    graphData.value = result
  } catch (error) {
    if (requestGeneration !== workspaceRequestGeneration) return
    graphError.value = error.message || '引用图谱加载失败'
  } finally {
    if (requestGeneration === workspaceRequestGeneration) graphLoading.value = false
  }
}

const pollGraphSync = async (runId, requestGeneration) => {
  clearTimeout(graphSyncTimer)
  graphSyncTimer = null
  if (requestGeneration !== workspaceRequestGeneration) return
  try {
    const result = await researchApi.getAcademicGraphSyncRun(runId)
    if (requestGeneration !== workspaceRequestGeneration) return
    graphSyncRun.value = result
    if (['pending', 'running'].includes(result.status)) {
      graphSyncTimer = setTimeout(() => pollGraphSync(runId, requestGeneration), 2000)
    } else {
      graphSyncLoading.value = false
      if (['success', 'completed_with_conflicts'].includes(result.status)) await loadAcademicGraph()
      if (requestGeneration === workspaceRequestGeneration && result.conflict_count) await loadGraphConflicts()
    }
  } catch (error) {
    if (requestGeneration !== workspaceRequestGeneration) return
    graphSyncLoading.value = false
    graphError.value = error.message || '同步状态查询失败'
  }
}

const loadGraphConflicts = async () => {
  const runId = graphSyncRun.value?.run_id
  if (!runId) return
  const requestGeneration = workspaceRequestGeneration
  graphConflictsLoading.value = true
  graphConflictsError.value = ''
  try {
    const result = await researchApi.listAcademicGraphConflicts(runId, {
      resolution_status: 'unresolved',
      offset: (graphConflictsPage.value - 1) * graphConflictsPageSize,
      limit: graphConflictsPageSize
    })
    if (requestGeneration !== workspaceRequestGeneration || runId !== graphSyncRun.value?.run_id) return
    graphConflicts.value = result.items || []
    graphConflictsTotal.value = result.total || 0
  } catch (error) {
    if (requestGeneration !== workspaceRequestGeneration) return
    graphConflictsError.value = error.message || '元数据冲突明细加载失败'
  } finally {
    if (requestGeneration === workspaceRequestGeneration) graphConflictsLoading.value = false
  }
}

const syncAcademicGraph = async () => {
  if (!selectedKbId.value) return
  const kbId = selectedKbId.value
  const requestGeneration = workspaceRequestGeneration
  graphSyncLoading.value = true
  graphError.value = ''
  try {
    const result = await researchApi.syncAcademicGraph(kbId, {
      paper_ids: [],
      citation_limit: 100,
      reference_limit: 100
    })
    if (requestGeneration !== workspaceRequestGeneration || kbId !== selectedKbId.value) return
    graphSyncRun.value = { run_id: result.run_id, status: result.status, processed_papers: 0, conflict_count: 0 }
    await pollGraphSync(result.run_id, requestGeneration)
  } catch (error) {
    if (requestGeneration !== workspaceRequestGeneration) return
    graphSyncLoading.value = false
    graphError.value = error.message || '学术图谱同步启动失败'
  }
}

const graphDegree = (paperId) => {
  const citations = (graphData.value?.edges || []).filter((edge) => edge.type === 'CITES')
  return {
    incoming: citations.filter((edge) => edge.target === paperId).length,
    outgoing: citations.filter((edge) => edge.source === paperId).length
  }
}

const graphNodeTypeLabel = (type) => ({ paper: '论文', author: '作者', topic: '主题' })[type] || type
const handleAcademicGraphNodeClick = (node) => {
  selectedGraphNode.value = node?.data?.original || null
  graphRelations.value = []
  relationsError.value = ''
}

const loadGraphRelations = async (graphPaperId) => {
  if (!selectedKbId.value || !graphPaperId) return
  const kbId = selectedKbId.value
  const requestGeneration = workspaceRequestGeneration
  relationsLoading.value = true
  relationsError.value = ''
  try {
    const result = await researchApi.getAcademicGraphRelations(kbId, graphPaperId, { limit: 20 })
    if (requestGeneration !== workspaceRequestGeneration || kbId !== selectedKbId.value) return
    graphRelations.value = result.relations || []
    if (!graphRelations.value.length) relationsError.value = '当前节点没有可解释的两跳关联'
  } catch (error) {
    if (requestGeneration !== workspaceRequestGeneration) return
    relationsError.value = error.message || '关联发现失败'
  } finally {
    if (requestGeneration === workspaceRequestGeneration) relationsLoading.value = false
  }
}

const exportAllBibtex = async () => {
  if (!selectedKbId.value || papers.value.length === 0) return
  const kbId = selectedKbId.value
  const requestGeneration = workspaceRequestGeneration
  exportLoading.value = true
  try {
    const blob = await researchApi.exportPapersBibtex(kbId)
    if (requestGeneration !== workspaceRequestGeneration || kbId !== selectedKbId.value) return
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `papers_${kbId}.bib`
    a.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    if (requestGeneration !== workspaceRequestGeneration) return
    message.error(error.message || 'BibTeX 导出失败')
  } finally {
    if (requestGeneration === workspaceRequestGeneration) exportLoading.value = false
  }
}

const handleDatabaseChange = (kbId) => {
  if (typeof kbId === 'string') selectedKbId.value = kbId
  selectedProject.value = null
  selectedSearchRun.value = null
  workspaceRequestGeneration += 1
  page.value = 1
  synthesisRequestGeneration += 1
  clearTimeout(graphSyncTimer)
  graphSyncTimer = null
  clearSynthesisPolling()
  closePaper()
  closeEvidence()
  publicationTrendChart?.dispose()
  keywordTrendChart?.dispose()
  publicationTrendChart = null
  keywordTrendChart = null
  loading.value = false
  graphLoading.value = false
  graphSyncLoading.value = false
  graphConflictsLoading.value = false
  relationsLoading.value = false
  trendLoading.value = false
  opportunityLoading.value = false
  exportLoading.value = false
  externalSearchLoading.value = false
  externalImportingId.value = ''
  externalImportOpen.value = false
  externalPapers.value = []
  externalSearchPerformed.value = false
  externalImportError.value = ''
  loadPapers()
  graphData.value = null
  graphError.value = ''
  selectedGraphNode.value = null
  graphRelations.value = []
  relationsError.value = ''
  graphSyncRun.value = null
  graphConflicts.value = []
  graphConflictsTotal.value = 0
  graphConflictsPage.value = 1
  graphConflictsError.value = ''
  trendData.value = null
  selectedTrendKeyword.value = ''
  trendError.value = ''
  opportunityData.value = null
  opportunityError.value = ''
  synthesisRuns.value = []
  synthesisTotal.value = 0
  synthesisLoading.value = false
  synthesisDetailLoading.value = false
  synthesisSubmitting.value = false
  synthesisRegenerating.value = false
  synthesisError.value = ''
  selectedSynthesisRunId.value = ''
  selectedSynthesisRun.value = null
  if (activeMode.value === 'graph') loadAcademicGraph()
  if (activeMode.value === 'trends') loadTrends()
  if (activeMode.value === 'opportunities') loadOpportunities()
  if (activeMode.value === 'synthesis') loadSyntheses({ preserveSelection: false })
}

const handleSearchEvidenceOpen = ({ paper, evidence }) => {
  openEvidence(paper, evidence)
}

const handleProjectChange = (project) => {
  selectedProject.value = project || null
}

const handleSearchSelectionChange = (run) => {
  selectedSearchRun.value = run || null
}

const handleCopilotRunComplete = async () => {
  if (activeMode.value === 'projects') {
    await projectWorkspaceRef.value?.refresh?.()
    return
  }
  if (activeMode.value === 'search') {
    await searchWorkspaceRef.value?.refresh?.()
    return
  }
  if (activeMode.value === 'synthesis') {
    await loadSyntheses({ preserveSelection: true })
    return
  }
  if (activeMode.value === 'library') {
    await loadPapers()
  }
}

const startSynthesisFromSearch = ({ query, yearFrom, yearTo, topK, recallTopK }) => {
  synthesisQuery.value = query || ''
  Object.assign(synthesisFilters, { yearFrom, yearTo, topK, recallTopK })
  activeMode.value = 'synthesis'
}

const continueProjectAsset = async (asset) => {
  if (!asset?.available) return
  if (asset.asset_type === 'paper') {
    openPaper({ paper_id: asset.reference_id })
    return
  }
  if (asset.asset_type === 'search_run') {
    projectSearchRunId.value = asset.reference_id
    activeMode.value = 'search'
    return
  }
  if (asset.asset_type === 'synthesis_run') {
    activeMode.value = 'synthesis'
    selectedSynthesisRunId.value = asset.reference_id
    await nextTick()
    await loadSynthesisRun(asset.reference_id)
    return
  }
  if (asset.asset_type === 'analysis_run') {
    const paperId = asset.metadata?.paper_id
    if (!paperId) {
      message.error('该分析记录缺少论文标识')
      return
    }
    await router.push({
      name: 'ResearchPaperAnalysis',
      params: { kbId: selectedKbId.value, paperId }
    })
    return
  }
  if (asset.asset_type === 'evaluation_experiment') {
    await router.push({
      path: `/extensions/knowledgebase/${encodeURIComponent(selectedKbId.value)}`,
      query: { tab: 'experiments', experiment: asset.reference_id }
    })
  }
}

const applyFilters = () => {
  page.value = 1
  loadPapers()
}

const resetFilters = () => {
  Object.assign(filters, { query: '', yearFrom: null, yearTo: null, sortBy: 'year', sortOrder: 'desc' })
  page.value = 1
  loadPapers()
}

const handlePageSizeChange = () => {
  page.value = 1
  loadPapers()
}

const openPaper = (paper) => {
  selectedPaperId.value = paper.paper_id
  detailOpen.value = true
}

const closePaper = () => {
  detailOpen.value = false
  selectedPaperId.value = ''
}

const handlePaperUpdated = (result) => {
  const index = papers.value.findIndex((paper) => paper.paper_id === result.paper.paper_id)
  if (index >= 0) papers.value[index] = result.paper
}

const openKnowledgeManagement = () => router.push({ path: '/extensions', query: { tab: 'knowledge' } })
const openSelectedKnowledgeBase = () => {
  if (!selectedKbId.value) return
  router.push(`/extensions/knowledgebase/${encodeURIComponent(selectedKbId.value)}`)
}

onMounted(async () => {
  await loadDatabases()
  await loadPapers()
})

watch(activeMode, (mode) => {
  if (mode === 'graph' && !graphData.value) loadAcademicGraph()
  if (mode === 'trends' && !trendData.value) loadTrends()
  if (mode === 'opportunities' && !opportunityData.value) loadOpportunities()
  if (mode === 'synthesis' && !synthesisRuns.value.length) loadSyntheses()
})

onBeforeUnmount(() => {
  evidencePdfRequestId += 1
  workspaceRequestGeneration += 1
  synthesisRequestGeneration += 1
  clearTimeout(graphSyncTimer)
  graphSyncTimer = null
  clearSynthesisPolling()
  publicationTrendChart?.dispose()
  keywordTrendChart?.dispose()
  disposeEvidencePdf()
})
</script>

<style scoped lang="less">
.research-view {
  --research-copilot-width: 420px;

  position: relative;
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: var(--gray-0);
  transition: padding-right 0.18s ease;
}

.header-summary {
  color: var(--color-text-tertiary);
  font-size: 13px;
}

.research-content {
  height: calc(100% - 57px);
  padding: 20px var(--page-padding) 32px;
  overflow-y: auto;
  overflow-x: hidden;
}

.research-tabs {
  margin-bottom: 14px;
}

.trends-workspace,
.opportunities-workspace {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.user-studies-workspace {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.synthesis-workspace {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.synthesis-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(280px, 0.6fr);
  gap: 14px;
}

.synthesis-builder,
.synthesis-history,
.synthesis-result-shell {
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.research-view.copilot-open {
  padding-right: var(--research-copilot-width);
}

.research-view.copilot-open :deep(.page-header) {
  gap: 8px;
  padding-inline: 16px;
}

.research-view.copilot-open .header-summary {
  display: none;
}

.research-view.copilot-open :deep(.page-header-right .lucide-icon-btn) {
  width: 34px;
  min-width: 34px;
  padding-inline: 0;
  font-size: 0;
}

.research-view.copilot-open :deep(.page-header-right .lucide-icon-btn .ant-btn-icon) {
  margin-inline-end: 0;
}

.research-view.copilot-open :deep(.project-detail-header) {
  flex-direction: column;
}

.research-view.copilot-open :deep(.project-actions) {
  width: 100%;
  max-width: none;
  justify-content: flex-start;
}

.research-view.copilot-open :deep(.project-overview) {
  grid-template-columns: minmax(0, 1fr);
}

.research-view.copilot-open :deep(.project-overview > div) {
  border-right: 0;
  border-bottom: 1px solid var(--gray-150);
}

.research-view.copilot-open :deep(.project-overview > div:last-child) {
  border-bottom: 0;
}

.research-view.copilot-open :deep(.description-block) {
  grid-column: auto;
  border-top: 0;
}

.synthesis-builder {
  padding: 18px;
}

.synthesis-builder-header,
.synthesis-form-footer,
.synthesis-result-header,
.synthesis-title-row,
.synthesis-actions,
.synthesis-section-title {
  display: flex;
  align-items: center;
}

.synthesis-builder-header {
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.synthesis-builder-header h2,
.synthesis-builder-header p,
.synthesis-history h3,
.synthesis-result-header h2,
.synthesis-result-header p,
.synthesis-section-title h3,
.synthesis-theme h4,
.synthesis-theme p,
.synthesis-claim p {
  margin: 0;
}

.synthesis-builder-header h2,
.synthesis-result-header h2 {
  color: var(--color-text);
  font-size: 17px;
}

.synthesis-builder-header p,
.synthesis-result-header p {
  margin-top: 5px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.synthesis-options {
  display: grid;
  grid-template-columns: minmax(220px, 1.2fr) minmax(250px, 1fr) 110px 110px;
  gap: 12px;
  align-items: end;
  margin-bottom: 16px;
}

.synthesis-builder label {
  display: block;
  margin-bottom: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 500;
}

.synthesis-form-footer {
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
}

.synthesis-history {
  min-height: 260px;
  padding: 14px;
}

.synthesis-history header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.synthesis-history header span {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.synthesis-history-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 360px;
  overflow-y: auto;
}

.synthesis-history-item {
  width: 100%;
  padding: 10px;
  border: 1px solid var(--gray-150);
  border-radius: 7px;
  color: inherit;
  background: var(--gray-10);
  cursor: pointer;
  text-align: left;
}

.synthesis-history-item:hover,
.synthesis-history-item.active {
  border-color: var(--main-300);
  background: var(--main-30);
}

.synthesis-history-title {
  display: -webkit-box;
  overflow: hidden;
  color: var(--color-text);
  font-size: 12px;
  line-height: 1.45;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.synthesis-history-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 7px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.synthesis-status {
  display: inline-flex;
  align-items: center;
  padding: 2px 7px;
  border-radius: 999px;
  color: var(--gray-600);
  background: var(--gray-100);
  font-size: 11px;
  font-weight: 600;
}

.synthesis-status-success {
  color: var(--color-success-700);
  background: var(--color-success-50);
}

.synthesis-status-failed {
  color: var(--color-error-700);
  background: var(--color-error-50);
}

.synthesis-status-cancelled {
  color: var(--color-warning-700);
  background: var(--color-warning-50);
}

.synthesis-status-retrieving,
.synthesis-status-synthesizing,
.synthesis-status-validating,
.synthesis-status-pending {
  color: var(--color-info-700);
  background: var(--color-info-50);
}

.synthesis-result-shell {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px;
}

.synthesis-result-header {
  justify-content: space-between;
  gap: 16px;
}

.synthesis-title-row {
  align-items: flex-start;
  gap: 8px;
}

.synthesis-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.synthesis-coverage-cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.synthesis-coverage-cards article {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);
}

.synthesis-coverage-cards span {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.synthesis-coverage-cards strong {
  color: var(--color-text);
  font-size: 20px;
}

.synthesis-summary-panel,
.synthesis-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);
}

.synthesis-summary-panel p {
  margin: 0;
  color: var(--color-text);
  font-size: 14px;
  line-height: 1.7;
}

.synthesis-constraints {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding-left: 18px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.synthesis-section-title {
  gap: 7px;
  color: var(--main-color);
}

.synthesis-section-title h3 {
  color: var(--color-text);
  font-size: 15px;
}

.synthesis-theme,
.synthesis-claim,
.synthesis-unsupported {
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 7px;
  background: var(--gray-0);
}

.synthesis-theme h4 {
  color: var(--color-text);
  font-size: 14px;
}

.synthesis-theme p,
.synthesis-claim p {
  margin-top: 6px;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.synthesis-claim header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.synthesis-claim header span {
  color: var(--main-color);
  font-size: 12px;
  font-weight: 650;
}

.synthesis-basis {
  color: var(--color-text-tertiary) !important;
  font-size: 12px !important;
}

.synthesis-evidence-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.synthesis-evidence-chips button {
  max-width: 100%;
  padding: 4px 8px;
  overflow: hidden;
  border: 1px solid var(--main-200);
  border-radius: 999px;
  color: var(--main-color);
  background: var(--main-30);
  cursor: pointer;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.synthesis-evidence-chips button:hover {
  border-color: var(--main-500);
  background: var(--main-50);
}

.synthesis-unsupported {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.synthesis-unsupported strong {
  color: var(--color-text);
  font-size: 13px;
}

.synthesis-unsupported span {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.trends-toolbar {
  display: flex;
  align-items: flex-end;
  gap: 14px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);
}

.trends-toolbar .database-field {
  min-width: 260px;
  margin-right: auto;
}

.trends-content {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.trend-summary-cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.trend-summary-cards article:nth-child(4) strong {
  color: var(--main-color);
}

.trend-summary-cards article {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);
}

.trend-summary-cards span {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.trend-summary-cards strong {
  color: var(--color-text);
  font-size: 22px;
}

.trend-chart-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
  gap: 14px;
}

.trend-chart-card,
.keyword-evolution-card {
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.trend-chart-card header,
.keyword-evolution-card header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.trend-chart-card h3,
.keyword-evolution-card h3 {
  margin: 0;
  color: var(--color-text);
  font-size: 15px;
}

.trend-chart-card header span,
.keyword-evolution-card header span {
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.trend-chart {
  width: 100%;
  height: 300px;
  margin-top: 8px;
}

.keyword-evolution-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.keyword-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.keyword-chip-row button {
  padding: 5px 9px;
  border: 1px solid var(--gray-150);
  border-radius: 999px;
  color: var(--color-text-secondary);
  background: var(--gray-10);
  cursor: pointer;
  font-size: 12px;
}

.keyword-chip-row button.active,
.keyword-chip-row button:hover {
  border-color: var(--main-300);
  color: var(--main-color);
  background: var(--main-30);
}

.keyword-chip-row span {
  margin-left: 4px;
  color: var(--color-text-tertiary);
}

.keyword-evolution-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
  gap: 8px;
}

.keyword-year-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--gray-25);
}

.keyword-year-item span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.keyword-year-item strong {
  color: var(--main-color);
}

.emerging-direction-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 8px;
}

.emerging-direction-list article {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px;
  border-radius: 6px;
  background: var(--gray-25);
}

.emerging-direction-list article div {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.emerging-direction-list strong {
  color: var(--color-text);
  font-size: 13px;
}

.emerging-direction-list span {
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.opportunities-toolbar {
  display: flex;
  align-items: flex-end;
  gap: 14px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);
}

.opportunities-toolbar .database-field {
  min-width: 260px;
  margin-right: auto;
}

.opportunities-content {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.opportunity-summary-cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.opportunity-summary-cards article {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);
}

.opportunity-summary-cards span,
.opportunity-score span {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.opportunity-summary-cards strong {
  color: var(--color-text);
  font-size: 22px;
}

.opportunity-summary-cards article:last-child strong {
  color: var(--color-success-700);
  font-size: 17px;
}

.opportunity-methodology {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 14px;
  border-left: 3px solid var(--main-500);
  background: var(--main-30);
}

.opportunity-methodology div {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.opportunity-methodology strong {
  color: var(--color-text);
  font-size: 13px;
}

.opportunity-methodology span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.opportunity-methodology .opportunity-sample-warning {
  color: var(--color-warning-700);
}

.opportunity-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(430px, 1fr));
  gap: 14px;
}

.opportunity-card {
  min-width: 0;
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.opportunity-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;
}

.opportunity-card-header > div:first-child {
  display: flex;
  align-items: baseline;
  min-width: 0;
  gap: 8px;
}

.opportunity-card h3 {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  color: var(--color-text);
  font-size: 17px;
}

.opportunity-rank {
  flex: none;
  color: var(--main-color);
  font-size: 12px;
  font-weight: 600;
}

.opportunity-score {
  display: flex;
  flex: none;
  flex-direction: column;
  align-items: flex-end;
}

.opportunity-score strong {
  color: var(--main-color);
  font-size: 24px;
  line-height: 1;
}

.opportunity-signal-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px 12px;
  margin-bottom: 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.opportunity-detail-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(150px, 0.7fr);
  gap: 18px;
  margin-top: 12px;
}

.opportunity-card h4 {
  margin: 0 0 7px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.opportunity-card ul {
  margin: 0;
  padding-left: 18px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.7;
}

.opportunity-coverage {
  display: flex;
  flex-direction: column;
  gap: 5px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.opportunity-evidence {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--gray-150);
}

.opportunity-evidence-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 0;
}

.opportunity-evidence-item > div {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 2px;
}

.opportunity-evidence-item strong {
  overflow: hidden;
  color: var(--color-text);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.opportunity-evidence-item span {
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.opportunity-next-actions {
  margin-top: 10px;
  padding: 10px 12px;
  background: var(--gray-25);
}

.opportunity-next-actions span {
  color: var(--main-color);
  font-size: 11px;
  font-weight: 600;
}

.opportunity-next-actions p {
  margin: 4px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.opportunity-limitation {
  margin: 0;
  color: var(--color-text-tertiary);
  font-size: 11px;
  text-align: right;
}

.query-label {
  margin-top: 2px;
}

.search-pipeline-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.evidence-detail {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.evidence-detail-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.evidence-pdf-actions {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 10px;
  border: 1px solid var(--main-100);
  border-radius: 7px;
  background: var(--main-25);
}

.evidence-pdf-actions span {
  color: var(--color-text-tertiary);
  font-size: 11px;
  line-height: 1.45;
}

  .evidence-pdf-preview {
    position: relative;
    max-height: 520px;
    overflow: hidden;
    border: 1px solid var(--gray-150);
    border-radius: 7px;
    background: var(--gray-25);
    overflow-y: auto;
  }
  
  .evidence-pdf-page {
    position: relative;
    width: 100%;
    min-height: 160px;
    background: var(--gray-0);
  }

  .evidence-pdf-page canvas {
    display: block;
    width: 100%;
    height: 100%;
  }

  .evidence-pdf-highlights {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }

  .evidence-pdf-highlight {
    position: absolute;
    border: 2px solid var(--color-warning-700);
    border-radius: 2px;
    background: color-mix(in srgb, var(--color-warning-100) 48%, transparent);
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--color-warning-900) 20%, transparent);
  }

  .evidence-pdf-rendering {
    position: absolute;
    top: 12px;
    right: 12px;
  }

.evidence-detail pre {
  max-height: 460px;
  padding: 12px;
  overflow: auto;
  margin: 0;
  border-radius: 7px;
  background: var(--gray-25);
  color: var(--color-text);
  font-size: 13px;
  line-height: 1.65;
  white-space: pre-wrap;
}

.graph-workspace {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.graph-toolbar,
.graph-counts,
.graph-paper-heading,
.graph-node-meta {
  display: flex;
  align-items: center;
}

.graph-toolbar {
  gap: 14px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);
}

.graph-toolbar .database-field {
  min-width: 260px;
  margin-right: auto;
}

.graph-counts {
  flex-wrap: wrap;
  gap: 6px 14px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.graph-conflicts {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);
}

.graph-conflict-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.graph-conflict-heading h3 {
  margin: 0;
  color: var(--color-text);
  font-size: 14px;
}

.graph-conflict-item {
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 7px;
  background: var(--gray-0);
}

.graph-conflict-item p {
  margin: 7px 0;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.graph-conflict-values {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.graph-conflict-values span {
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.graph-conflict-values pre {
  max-height: 160px;
  margin: 4px 0 0;
  padding: 8px;
  overflow: auto;
  border-radius: 5px;
  background: var(--gray-50);
  color: var(--color-text-secondary);
  font-size: 11px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}

.academic-graph-layout {
  position: relative;
  height: 600px;
  min-height: 420px;
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.academic-graph-canvas {
  width: 100%;
  height: 100%;
}

.graph-selection-panel {
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 20;
  width: min(320px, calc(100% - 28px));
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--color-trans-light);
  box-shadow: 0 8px 24px var(--shadow-2);
  backdrop-filter: blur(12px);

  h3 {
    margin: 6px 0;
    color: var(--color-text);
    font-size: 15px;
    line-height: 1.45;
  }

  p {
    margin: 4px 0;
    color: var(--color-text-secondary);
    font-size: 12px;
  }
}

.graph-selection-panel > .ant-btn {
  margin-top: 12px;
}

.graph-relations-list {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--gray-150);

  h4 {
    margin: 0 0 8px;
    color: var(--color-text);
    font-size: 13px;
  }
}

.graph-relation-item {
  padding: 9px 0;
  border-bottom: 1px solid var(--gray-100);

  &:last-child {
    border-bottom: 0;
  }

  p {
    margin: 4px 0;
  }
}

.graph-relation-title {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;

  strong {
    color: var(--color-text);
    font-size: 12px;
    line-height: 1.4;
  }
}

.graph-relation-evidence {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.external-import-help {
  margin: 0 0 14px;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.external-import-search-row {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}

.external-paper-results {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 520px;
  overflow-y: auto;
}

.external-paper-result {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);

  h3 {
    margin: 0;
    color: var(--color-text);
    font-size: 14px;
    line-height: 1.45;
  }

  p {
    margin: 5px 0 0;
    color: var(--color-text-secondary);
    font-size: 12px;
  }
}

.external-paper-abstract {
  display: -webkit-box;
  max-width: 560px;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  line-height: 1.5;
}

.external-paper-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px 8px;
  margin-top: 8px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.node-type-label {
  color: var(--main-color);
  font-size: 11px;
  font-weight: 600;
}

.graph-network-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px;
}

.graph-paper-node {
  padding: 13px;
  border: 1px solid var(--gray-150);
  border-radius: 7px;
  background: var(--gray-0);

  h3,
  p {
    margin: 0;
  }

  h3 {
    color: var(--color-text);
    font-size: 13px;
    line-height: 1.45;
  }

  p {
    margin-top: 5px;
    color: var(--color-text-secondary);
    font-size: 12px;
  }
}

.graph-paper-heading {
  justify-content: space-between;
  gap: 8px;
}

.graph-node-meta {
  gap: 12px;
  margin-top: 8px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.research-toolbar {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(260px, 1.6fr) minmax(260px, 1fr) auto;
  gap: 14px;
  align-items: end;
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);
}

.toolbar-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;

  label {
    color: var(--color-text-secondary);
    font-size: 12px;
    font-weight: 500;
  }
}

.year-range,
.toolbar-actions,
.list-summary,
.summary-count,
.paper-title-row,
.paper-metadata span,
.pagination-row {
  display: flex;
  align-items: center;
}

.year-range {
  gap: 7px;

  :deep(.ant-input-number) {
    width: 110px;
  }

  span {
    color: var(--color-text-tertiary);
    font-size: 12px;
  }
}

.toolbar-actions {
  gap: 8px;
}

.list-summary {
  justify-content: space-between;
  gap: 20px;
  margin: 24px 0 12px;

  h2 {
    margin: 0;
    color: var(--color-text);
    font-size: 18px;
    font-weight: 600;
  }

  p {
    max-width: 720px;
    margin: 4px 0 0;
    color: var(--color-text-secondary);
    font-size: 13px;
    line-height: 1.5;
  }
}

.summary-count {
  flex: none;
  gap: 7px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.papers-loading,
.page-empty {
  padding: 48px 20px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}

.empty-hint {
  margin: 8px 0 12px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.paper-list {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  overflow: hidden;

  &.refreshing {
    opacity: 0.68;
    pointer-events: none;
  }
}

.paper-row {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) 24px;
  gap: 14px;
  align-items: start;
  padding: 16px 18px;
  background: var(--gray-0);
  cursor: pointer;
  outline: none;

  & + & {
    border-top: 1px solid var(--gray-100);
  }

  &:hover,
  &:focus-visible {
    background: var(--gray-10);
  }

  &:focus-visible {
    box-shadow: inset 0 0 0 2px var(--main-200);
  }
}

.paper-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 7px;
  color: var(--main-color);
  background: var(--main-30);
}

.paper-main {
  min-width: 0;
}

.paper-title-row {
  justify-content: space-between;
  gap: 12px;

  h3 {
    min-width: 0;
    margin: 0;
    color: var(--color-text);
    font-size: 15px;
    line-height: 1.45;
    font-weight: 600;
  }
}

.metadata-status {
  flex: none;
  padding: 2px 8px;
  border-radius: 999px;
  color: var(--gray-600);
  background: var(--gray-100);
  font-size: 11px;
  font-weight: 500;

  &.status-verified {
    color: var(--color-success-700);
    background: var(--color-success-50);
  }

  &.status-pending_reindex {
    color: var(--color-info-700);
    background: var(--color-info-50);
  }

  &.status-sync_failed {
    color: var(--color-error-700);
    background: var(--color-error-50);
  }
}

.paper-authors,
.paper-abstract {
  margin: 5px 0 0;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.paper-abstract {
  display: -webkit-box;
  overflow: hidden;
  line-height: 1.55;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.paper-metadata {
  display: flex;
  flex-wrap: wrap;
  gap: 7px 16px;
  margin-top: 10px;

  span {
    gap: 5px;
    max-width: 360px;
    overflow: hidden;
    color: var(--color-text-tertiary);
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.paper-keywords {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 10px;

  span {
    color: var(--color-text-tertiary);
    font-size: 11px;
  }
}

.paper-open-icon {
  align-self: center;
  color: var(--gray-400);
}

.pagination-row {
  justify-content: flex-end;
  margin-top: 18px;
}

@media (max-width: 1200px) {
  .research-view :deep(.page-header) {
    gap: 6px;
    padding-inline: 12px;
  }

  .research-view .header-summary {
    display: none;
  }

  .research-view :deep(.page-header-right) {
    gap: 4px;
    min-width: 0;
  }

  .research-view :deep(.page-header-right .lucide-icon-btn) {
    width: 32px;
    min-width: 32px;
    height: 32px;
    padding: 0;
    font-size: 0;
  }

  .research-view :deep(.page-header-right .lucide-icon-btn .ant-btn-icon) {
    margin-inline-end: 0;
  }
}

@media (max-width: 1100px) {
  .research-view {
    --research-copilot-width: 380px;
  }

  .synthesis-grid {
    grid-template-columns: 1fr;
  }

  .synthesis-options {
    grid-template-columns: 1fr 1fr;
  }

  .synthesis-coverage-cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .trend-chart-grid {
    grid-template-columns: 1fr;
  }

  .trend-summary-cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .opportunity-summary-cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .research-toolbar {
    grid-template-columns: 1fr 1.4fr;
  }

  .toolbar-actions {
    justify-content: flex-end;
  }
}

@media (max-width: 960px) {
  .research-view.copilot-open {
    padding-right: 0;
  }
}

@media (max-width: 700px) {
  .research-view :deep(.page-header) {
    gap: 8px;
  }

  .research-view :deep(.page-header-right) {
    gap: 4px;
    min-width: 0;
  }

  .research-view :deep(.page-header-right .lucide-icon-btn) {
    width: 32px;
    min-width: 32px;
    height: 32px;
    padding: 0;
    font-size: 0;
  }

  .research-view :deep(.page-header-right .lucide-icon-btn .ant-btn-icon) {
    margin-inline-end: 0;
  }

  .synthesis-options,
  .synthesis-coverage-cards {
    grid-template-columns: 1fr;
  }

  .synthesis-builder-header,
  .synthesis-form-footer,
  .synthesis-result-header,
  .synthesis-title-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .synthesis-actions {
    justify-content: stretch;
    width: 100%;
  }

  .synthesis-actions :deep(.ant-btn) {
    flex: 1;
    min-width: 120px;
  }

  .trends-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .trends-toolbar .database-field {
    min-width: 0;
  }

  .opportunities-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .opportunities-toolbar .database-field {
    min-width: 0;
  }

  .opportunity-list,
  .opportunity-detail-grid {
    grid-template-columns: 1fr;
  }

  .opportunity-methodology {
    align-items: flex-start;
    flex-direction: column;
  }

  .graph-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .graph-toolbar .database-field {
    min-width: 0;
  }

  .academic-graph-layout {
    height: 480px;
  }
  .header-summary {
    display: none;
  }

  .research-toolbar {
    grid-template-columns: 1fr;
  }

  .year-range :deep(.ant-input-number) {
    width: calc(50% - 14px);
  }

  .toolbar-actions {
    justify-content: stretch;

    :deep(.ant-btn) {
      flex: 1;
    }
  }

  .paper-row {
    grid-template-columns: 32px minmax(0, 1fr);
    padding: 14px;
  }

  .paper-icon {
    width: 32px;
    height: 32px;
  }

  .paper-open-icon {
    display: none;
  }

  .paper-title-row,
  .list-summary {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
