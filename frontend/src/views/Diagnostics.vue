<template>
  <div class="diagnostics">
    <div class="diag-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item :to="{ path: '/kbs' }">知识库</el-breadcrumb-item>
        <el-breadcrumb-item>{{ kbName }}</el-breadcrumb-item>
        <el-breadcrumb-item>体检</el-breadcrumb-item>
      </el-breadcrumb>

      <div class="diag-toolbar">
        <span class="diag-status-text" v-if="loaded">上次检查：{{ formatTime(lastCheck) }}</span>
        <el-button :loading="loading" @click="fetchDiagnostics">刷新</el-button>
      </div>
    </div>

    <el-empty
      v-if="!loading && !loaded"
      :description="loadError ? `加载失败：${loadError}` : '加载知识库体检报告…'"
    >
      <el-button v-if="loadError" type="primary" @click="fetchDiagnostics">重试</el-button>
    </el-empty>

    <!-- 统计卡片 -->
    <div v-if="loaded" class="stat-grid">
      <StatCard :value="summary.total_documents" label="总文档" />
      <StatCard :value="summary.indexed" label="已索引" tone="good" />
      <StatCard
        :value="summary.failed"
        label="失败"
        :tone="summary.failed > 0 ? 'bad' : 'good'"
      />
      <StatCard :value="summary.processing" label="处理中" />
      <StatCard :value="summary.pending" label="待处理" />
      <StatCard :value="summary.total_chunks" label="总切片" tone="accent" />
    </div>

    <template v-if="loaded">
      <!-- 摄取健康：正常态压成状态条（评审 P0-1：信息密度不该比异常态还低） -->
      <section class="diag-section" v-if="summary.total_documents > 0">
        <h3 class="section-title">摄取健康</h3>
        <div
          class="status-bar"
          :class="failures.length === 0 ? 'status-bar--good' : 'status-bar--danger'"
        >
          <el-icon class="status-icon">
            <CircleCheckFilled v-if="failures.length === 0" />
            <CircleCloseFilled v-else />
          </el-icon>
          <span class="status-main">
            {{
              failures.length === 0
                ? "所有文档均成功索引，无失败记录"
                : `存在摄取失败，见下方失败原因`
            }}
          </span>
          <span class="status-extra">{{ summary.total_documents }} 篇 · {{ summary.total_chunks }} 切片</span>
        </div>
      </section>

      <!-- 失败原因聚合 -->
      <section class="diag-section" v-if="failures.length > 0">
        <h3 class="section-title">失败原因</h3>
        <div v-for="(grp, idx) in failures" :key="idx" class="fail-group">
          <div class="fail-head">
            <span class="fail-error">{{ grp.error }}</span>
            <el-tag size="small" type="danger">{{ grp.count }} 个文档</el-tag>
          </div>
          <div class="fail-docs">
            <el-tag
              v-for="doc in grp.documents"
              :key="doc.id"
              size="small"
              type="info"
              effect="plain"
              class="fail-doc-tag"
            >
              {{ doc.filename }}
            </el-tag>
          </div>
        </div>
      </section>

      <!-- 异常文档清单 -->
      <section class="diag-section" v-if="anomalies.length > 0">
        <h3 class="section-title">异常文档</h3>
        <el-table :data="anomalies" size="small" border>
          <el-table-column prop="filename" label="文件名" min-width="180" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'failed' ? 'danger' : 'warning'">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="chunk_count" label="切片数" width="90" />
          <el-table-column prop="error" label="错误信息" min-width="200" show-overflow-tooltip />
        </el-table>
      </section>

      <!-- 空知识库 -->
      <el-empty v-if="summary.total_documents === 0" description="知识库暂无文档，上传后这里会显示体检报告" />

      <!-- 向量一致性：结论同样压成状态条，异常明细仍用表格 -->
      <section class="diag-section" v-if="summary.total_documents > 0">
        <h3 class="section-title">向量一致性</h3>
        <div
          v-if="!consistency.checked"
          class="status-bar status-bar--warning"
        >
          <el-icon class="status-icon"><WarningFilled /></el-icon>
          <span class="status-main">向量库不可达 · 无法对比，请检查向量库服务</span>
        </div>
        <div v-else-if="consistency.issues.length === 0" class="status-bar status-bar--good">
          <el-icon class="status-icon"><CircleCheckFilled /></el-icon>
          <span class="status-main">向量层与元数据一致</span>
          <span class="status-extra">两侧切片计数吻合</span>
        </div>
        <el-table v-else :data="consistency.issues" size="small" border>
          <el-table-column label="类型" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="kindType(row.kind)">{{ kindLabel(row.kind) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="文档" min-width="180">
            <template #default="{ row }">
              {{ row.filename || `doc_${row.document_id}` }}
            </template>
          </el-table-column>
          <el-table-column prop="meta_count" label="元数据" width="90" />
          <el-table-column prop="vector_count" label="向量库" width="90" />
        </el-table>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute } from "vue-router";
import { ElMessage } from "element-plus";
import { CircleCheckFilled, CircleCloseFilled, WarningFilled } from "@element-plus/icons-vue";
import StatCard from "@/components/StatCard.vue";
import { diagnosticsApi, type Summary, type FailureGroup, type AnomalyDoc, type Consistency } from "@/api/diagnostics";
import { kbApi } from "@/api/kbs";
import { extractErrorMessage } from "@/api/error";

const route = useRoute();
const kbId = Number(route.params.kbId);

const kbName = ref("");
const loading = ref(false);
const loaded = ref(false);
// K8-10：与 DocHealth 同因同解 —— 失败后 loaded 恒为 false，页面永久停在「加载…」。
const loadError = ref("");
const lastCheck = ref(new Date());

const summary = ref<Summary>({
  total_documents: 0, indexed: 0, failed: 0, processing: 0, pending: 0, total_chunks: 0,
});
const failures = ref<FailureGroup[]>([]);
const anomalies = ref<AnomalyDoc[]>([]);
const consistency = ref<Consistency>({ checked: true, issues: [] });

const KIND_LABELS: Record<string, string> = {
  missing_index: "缺索引",
  count_drift: "数量漂移",
  orphan_vector: "孤儿向量",
};

const KIND_TYPES: Record<string, string> = {
  missing_index: "warning",
  count_drift: "warning",
  orphan_vector: "info",
};

function kindLabel(kind: string): string {
  return KIND_LABELS[kind] || kind;
}

function kindType(kind: string): any {
  return KIND_TYPES[kind] || "info";
}

function formatTime(d: Date): string {
  return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

async function fetchDiagnostics() {
  loading.value = true;
  loadError.value = "";
  try {
    const data = await diagnosticsApi.get(kbId);
    summary.value = data.summary;
    failures.value = data.failures;
    anomalies.value = data.anomalies;
    consistency.value = data.consistency;
    loaded.value = true;
    lastCheck.value = new Date();
  } catch (err: any) {
    loadError.value = extractErrorMessage(err, "体检失败");
    ElMessage.error(loadError.value);
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  try {
    const kb = await kbApi.get(kbId);
    kbName.value = kb.name;
  } catch {
    kbName.value = `知识库 #${kbId}`;
  }
  await fetchDiagnostics();
});
</script>

<style scoped>
.diagnostics {
  max-width: 960px;
  margin: 0 auto;
}

.diag-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}

.diag-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.diag-status-text {
  font-size: 12px;
  color: #909399;
}

/* -- 状态条（评审 P0-1）：正常/警告结论压成一行，el-result 只留给空态与错误态 -- */
.status-bar {
  display: flex;
  align-items: center;
  gap: var(--app-gap-sm);
  min-height: 44px;
  padding: 8px 16px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: var(--radius-card);
}

.status-icon {
  font-size: 16px;
  display: inline-flex;
}

.status-bar--good .status-icon {
  color: var(--el-color-success);
}

.status-bar--warning .status-icon {
  color: var(--el-color-warning);
}

.status-bar--danger .status-icon {
  color: var(--el-color-danger);
}

.status-main {
  flex: 1;
  font-size: var(--fs-body);
  color: var(--el-text-color-primary);
}

.status-extra {
  font-size: var(--fs-hint);
  color: var(--el-text-color-secondary);
}

/* -- 统计卡片：卡片本体在共用组件 StatCard.vue（Known Gap #7），本页只保留网格 -- */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}

/* -- 失败原因 -- */
.diag-section {
  margin-top: 24px;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 12px;
}

.fail-group {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 12px;
}

.fail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.fail-error {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-color-danger);
  word-break: break-all;
}

.fail-docs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
</style>
