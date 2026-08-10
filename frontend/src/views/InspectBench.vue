<template>
  <div class="inspect-bench">
    <div class="inspect-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item :to="{ path: '/kbs' }">知识库</el-breadcrumb-item>
        <el-breadcrumb-item>{{ kbName }}</el-breadcrumb-item>
        <el-breadcrumb-item>检索质检</el-breadcrumb-item>
      </el-breadcrumb>

      <div class="query-area">
        <el-input
          v-model="query"
          placeholder="输入测试 query，看看检索层实际命中了哪些切片…"
          size="large"
          clearable
          @keyup.enter="runInspect"
        >
          <template #prepend>
            <span class="prepend-label">Query</span>
          </template>
        </el-input>

        <div class="query-controls">
          <span class="control-label">top_k</span>
          <el-slider
            v-model="topK"
            :min="1"
            :max="20"
            :step="1"
            show-input
            size="small"
            style="width: 180px"
          />
          <el-button type="primary" @click="runInspect" :loading="loading">检索</el-button>
        </div>
      </div>
    </div>

    <el-divider />

    <!-- 空态 -->
    <el-empty v-if="!ran && hits.length === 0" description="输入 query 后点击「检索」查看命中切片" />

    <!-- 无命中 -->
    <el-result v-else-if="ran && hits.length === 0" icon="info" title="无命中" sub-title="该 query 在知识库中没有匹配的切片">
      <template #extra>
        <span class="hint">试试更泛化的关键词，或检查知识库是否有内容</span>
      </template>
    </el-result>

    <!-- 命中列表 -->
    <div v-if="hits.length > 0" class="hits-list">
      <div class="hits-summary">共 {{ hits.length }} 条命中</div>
      <div v-for="(hit, idx) in hits" :key="idx" class="hit-card">
        <div class="hit-rank">
          <span class="rank-num">#{{ idx + 1 }}</span>
        </div>
        <div class="hit-body">
          <div class="hit-meta">
            <el-tag size="small" type="info">{{ hit.filename }}</el-tag>
            <span class="meta-sep">chunk #{{ hit.chunk_index }}</span>
          </div>
          <div class="score-row">
            <span class="score-label">相似度</span>
            <div class="score-bar-track">
              <div class="score-bar-fill" :style="{ width: scorePct(hit.score) + '%' }"></div>
            </div>
            <span class="score-value">{{ scorePct(hit.score) }}%</span>
          </div>
          <div class="hit-content" :class="{ expanded: expanded.has(idx) }">
            {{ hit.content }}
          </div>
          <el-button
            v-if="hit.content.length > 200"
            link
            type="primary"
            size="small"
            @click="toggleExpand(idx)"
          >
            {{ expanded.has(idx) ? '收起' : '展开全部' }}
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute } from "vue-router";
import { ElMessage } from "element-plus";
import { inspectApi, type InspectHit } from "@/api/inspect";
import { kbApi } from "@/api/kbs";

const route = useRoute();
const kbId = Number(route.params.kbId);

const kbName = ref("");
const query = ref("");
const topK = ref(5);
const hits = ref<InspectHit[]>([]);
const ran = ref(false);
const loading = ref(false);
const expanded = ref(new Set<number>());

onMounted(async () => {
  try {
    const kb = await kbApi.get(kbId);
    kbName.value = kb.name;
  } catch {
    kbName.value = `知识库 #${kbId}`;
  }
});

/** 余弦相似度转百分比，钳制到 [0, 100]（真实 embedding 可能返回负分或超 1）。 */
function scorePct(score: number): string {
  const clamped = Math.max(0, Math.min(score, 1));
  return (clamped * 100).toFixed(1);
}

function toggleExpand(idx: number) {
  if (expanded.value.has(idx)) {
    expanded.value.delete(idx);
  } else {
    expanded.value.add(idx);
  }
}

async function runInspect() {
  if (!query.value.trim()) {
    ElMessage.warning("请输入 query");
    return;
  }
  loading.value = true;
  try {
    const result = await inspectApi.inspect(kbId, query.value.trim(), topK.value);
    hits.value = result.hits;
    ran.value = true;
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || "检索失败");
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.inspect-bench {
  max-width: 960px;
  margin: 0 auto;
}

.inspect-header {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.query-area {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.prepend-label {
  font-weight: 600;
  font-size: 13px;
  color: #606266;
}

.query-controls {
  display: flex;
  align-items: center;
  gap: 16px;
}

.control-label {
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  flex-shrink: 0;
}

.hint {
  color: #909399;
  font-size: 13px;
}

/* -- 命中列表 -- */
.hits-summary {
  font-size: 13px;
  color: #909399;
  margin-bottom: 12px;
}

.hits-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.hit-card {
  display: flex;
  gap: 12px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 16px;
  transition: box-shadow 0.15s;
}

.hit-card:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.hit-rank {
  flex-shrink: 0;
  width: 36px;
  display: flex;
  align-items: flex-start;
}

.rank-num {
  font-size: 13px;
  font-weight: 700;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  padding: 2px 8px;
  border-radius: 4px;
}

.hit-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.hit-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #606266;
}

.meta-sep {
  color: #c0c4cc;
}

/* -- 相似度分数条 -- */
.score-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.score-label {
  font-size: 12px;
  color: #909399;
  flex-shrink: 0;
}

.score-bar-track {
  flex: 1;
  height: 6px;
  background: #f0f0f0;
  border-radius: 3px;
  overflow: hidden;
}

.score-bar-fill {
  height: 100%;
  background: var(--el-color-primary);
  border-radius: 3px;
  transition: width 0.3s ease;
}

.score-value {
  font-size: 12px;
  font-weight: 600;
  color: #303133;
  min-width: 48px;
  text-align: right;
}

/* -- 切片内容 -- */
.hit-content {
  font-size: 13px;
  line-height: 1.7;
  color: #404040;
  white-space: pre-wrap;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
}

.hit-content.expanded {
  display: block;
  -webkit-line-clamp: unset;
}
</style>
