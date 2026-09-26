<template>
  <div class="doc-health">
    <div class="dh-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item :to="{ path: '/kbs' }">知识库</el-breadcrumb-item>
        <el-breadcrumb-item>{{ kbName }}</el-breadcrumb-item>
        <el-breadcrumb-item>文档健康</el-breadcrumb-item>
      </el-breadcrumb>

      <div class="dh-toolbar">
        <span class="dh-hint">基于问答日志统计，问得越多的文档越「热」，从没被问到的即死文档</span>
        <el-button :loading="loading" @click="fetchHealth">刷新</el-button>
      </div>
    </div>

    <el-empty
      v-if="!loading && !loaded"
      :description="loadError ? `加载失败：${loadError}` : '加载文档健康分析…'"
    >
      <el-button v-if="loadError" type="primary" @click="fetchHealth">重试</el-button>
    </el-empty>

    <template v-if="loaded">
      <!-- 统计卡片 -->
      <div class="stat-grid">
        <div class="stat-card">
          <div class="stat-num">{{ summary.total_documents }}</div>
          <div class="stat-label">总文档</div>
        </div>
        <div class="stat-card">
          <div class="stat-num">{{ summary.indexed }}</div>
          <div class="stat-label">已索引</div>
        </div>
        <div class="stat-card stat-good">
          <div class="stat-num">{{ summary.active_count }}</div>
          <div class="stat-label">被问答命中</div>
        </div>
        <div class="stat-card" :class="summary.dead_count > 0 ? 'stat-bad' : 'stat-good'">
          <div class="stat-num">{{ summary.dead_count }}</div>
          <div class="stat-label">死文档</div>
        </div>
        <div class="stat-card">
          <div class="stat-num">{{ summary.total_questions }}</div>
          <div class="stat-label">累计问答</div>
        </div>
        <div class="stat-card stat-accent">
          <div class="stat-num">{{ Math.round(summary.hit_rate * 100) }}%</div>
          <div class="stat-label">命中率</div>
        </div>
      </div>

      <!-- 空知识库 -->
      <el-empty v-if="summary.total_documents === 0" description="知识库暂无文档，上传并提问后这里会分析命中情况" />

      <template v-else>
        <!-- 死文档 -->
        <section class="dh-section">
          <h3 class="section-title">
            死文档
            <el-tag v-if="summary.dead_count > 0" size="small" type="warning" effect="plain">
              {{ summary.dead_count }} 份从未被问答命中
            </el-tag>
            <el-tag v-else size="small" type="success" effect="plain">无</el-tag>
          </h3>
          <p class="section-desc">
            这些文档从没在问答中派上用场。已索引的可能是内容没价值或切分质量差；
            未就绪的（失败/待处理/处理中）则是根本没进向量库，需要重传或修复。
          </p>
          <el-table v-if="dead_docs.length > 0" :data="dead_docs" size="small" border>
            <el-table-column prop="filename" label="文件名" min-width="200" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="chunk_count" label="切片数" width="80" align="center" />
            <el-table-column label="处理建议" min-width="160">
              <template #default="{ row }">
                <span v-if="row.status === 'indexed'" class="suggest-warn">检索不到，考虑重整理或删除</span>
                <span v-else class="suggest-mute">{{ row.error ? '摄取失败' : '尚未就绪' }}，重传或等待处理</span>
              </template>
            </el-table-column>
          </el-table>
          <el-result v-else icon="success" title="没有死文档" sub-title="所有文档都被问答使用过" />
        </section>

        <!-- 命中热力 -->
        <section class="dh-section">
          <h3 class="section-title">命中热力 top {{ hot_docs.length }}</h3>
          <p class="section-desc">按被问答命中次数排序，揭示知识库内容分布是否集中在少数文档。</p>
          <el-table v-if="hot_docs.length > 0" :data="hot_docs" size="small" border>
            <el-table-column type="index" label="#" width="50" align="center" />
            <el-table-column prop="filename" label="文件名" min-width="200" show-overflow-tooltip />
            <el-table-column label="命中次数" width="200">
              <template #default="{ row }">
                <div class="heat-bar-wrap">
                  <div class="heat-bar" :style="{ width: heatWidth(row.hit_count) }"></div>
                  <span class="heat-num">{{ row.hit_count }} 次</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="最近命中" width="180">
              <template #default="{ row }">
                {{ formatTime(row.last_hit_at) }}
              </template>
            </el-table-column>
          </el-table>
          <el-result v-else icon="info" title="暂无命中" sub-title="还没有任何文档被问答命中，去对话页提问试试" />
        </section>
      </template>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute } from "vue-router";
import { ElMessage } from "element-plus";
import { docHealthApi, type DocHealthSummary, type DeadDoc, type HotDoc } from "@/api/docHealth";
import { kbApi } from "@/api/kbs";
import { extractErrorMessage } from "@/api/error";

const route = useRoute();
const kbId = Number(route.params.kbId);

const kbName = ref("");
const loading = ref(false);
const loaded = ref(false);
// K8-10：加载失败时 loaded 永远为 false，页面会**永久停在**「加载…」占位 ——
// 用户以为在等，其实请求早就失败了（toast 3 秒就消失，页面本体毫无变化）。
// 用独立的错误态把「加载中」和「加载失败」分开。
const loadError = ref("");

const summary = ref<DocHealthSummary>({
  total_documents: 0, indexed: 0, active_count: 0, dead_count: 0, total_questions: 0, hit_rate: 0,
});
const dead_docs = ref<DeadDoc[]>([]);
const hot_docs = ref<HotDoc[]>([]);

function statusTag(status: string): "success" | "danger" | "warning" | "info" {
  const map: Record<string, "success" | "danger" | "warning" | "info"> = {
    indexed: "success",
    failed: "danger",
    processing: "warning",
    pending: "info",
  };
  return map[status] || "info";
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    indexed: "已索引",
    failed: "失败",
    processing: "处理中",
    pending: "待处理",
  };
  return map[status] || status;
}

function heatWidth(hitCount: number): string {
  const max = Math.max(...hot_docs.value.map((h) => h.hit_count), 1);
  return `${Math.round((hitCount / max) * 100)}%`;
}

function formatTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("zh-CN");
}

async function fetchHealth() {
  loading.value = true;
  loadError.value = "";
  try {
    const data = await docHealthApi.get(kbId);
    summary.value = data.summary;
    dead_docs.value = data.dead_docs;
    hot_docs.value = data.hot_docs;
    loaded.value = true;
  } catch (err: any) {
    loadError.value = extractErrorMessage(err, "获取文档健康分析失败");
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
  await fetchHealth();
});
</script>

<style scoped>
.doc-health {
  max-width: 960px;
  margin: 0 auto;
}

.dh-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}

.dh-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.dh-hint {
  font-size: 12px;
  color: #909399;
  max-width: 360px;
  text-align: right;
}

/* -- 统计卡片（复用体检页风格） -- */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}

.stat-card {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 16px;
  text-align: center;
}

.stat-num {
  font-size: 26px;
  font-weight: 700;
  color: #303133;
  line-height: 1.2;
}

.stat-label {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}

.stat-good .stat-num {
  color: var(--el-color-success);
}

.stat-bad .stat-num {
  color: var(--el-color-danger);
}

.stat-accent .stat-num {
  color: var(--el-color-primary);
}

.dh-section {
  margin-top: 24px;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.section-desc {
  font-size: 12px;
  color: #909399;
  margin: 0 0 12px;
}

.suggest-warn {
  color: var(--el-color-warning);
  font-size: 13px;
}

.suggest-mute {
  color: #909399;
  font-size: 13px;
}

.heat-bar-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 20px;
}

.heat-bar {
  height: 10px;
  min-width: 2px;
  background: var(--el-color-primary);
  border-radius: 5px;
  opacity: 0.85;
}

.heat-num {
  font-size: 12px;
  color: #606266;
  white-space: nowrap;
}
</style>
