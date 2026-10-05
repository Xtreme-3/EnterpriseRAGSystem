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
          <span class="control-label">检索模式</span>
          <el-radio-group v-model="mode" size="small">
            <el-radio-button value="hybrid">混合（向量+关键词）</el-radio-button>
            <el-radio-button value="vector">纯向量</el-radio-button>
          </el-radio-group>
          <el-button type="primary" @click="runInspect" :loading="loading">检索</el-button>
        </div>

        <!-- 高级参数（评审 P0-3）：低频参数折叠，顶部只留 Query / 模式 / 检索 -->
        <el-collapse class="adv-params">
          <el-collapse-item name="adv">
            <template #title>
              <span class="control-label">高级参数</span>
            </template>
            <div class="adv-row">
              <span class="control-label">检索条数</span>
              <el-input-number
                v-model="topK"
                :min="1"
                :max="20"
                :step="1"
                controls-position="right"
              />
            </div>
            <div class="adv-row">
              <span class="control-label">重排</span>
              <el-switch v-model="rerank" size="small" />
            </div>
          </el-collapse-item>
        </el-collapse>
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
      <div class="hits-summary">
        共 {{ hits.length }} 条命中
        <span v-if="usedMode" class="hits-mode">· 检索模式：{{ usedMode }}</span>
        <span v-if="usedRerank" class="hits-mode">· 已重排</span>
      </div>
      <div v-for="(hit, idx) in hits" :key="idx" class="hit-card">
        <div class="hit-rank">
          <span class="rank-num">#{{ idx + 1 }}</span>
        </div>
        <div class="hit-body">
          <div class="hit-meta">
            <span class="hit-filename">{{ hit.filename }}</span>
            <span class="meta-sep">chunk #{{ hit.chunk_index }}</span>
          </div>
          <!-- 评审 P1-8：分数用带色阶的数值 tag（≥90 绿 / 70-90 主色 / <70 橙），
               进度条没有增量信息；省下的空间全部给切片正文 -->
          <div v-if="!usedRerank" class="score-row">
            <span class="score-label">相似度</span>
            <el-tag size="small" :type="scoreTagType(hit.score)" effect="plain">
              {{ scorePct(hit.score) }}%
            </el-tag>
          </div>
          <!-- 重排：检索（重排前）vs 重排 前后对比 -->
          <template v-else>
            <div class="score-row">
              <span class="score-label">检索</span>
              <el-tag size="small" :type="scoreTagType(hit.pre_score)" effect="plain">
                {{ scorePct(hit.pre_score) }}%
              </el-tag>
            </div>
            <div class="score-row">
              <span class="score-label">重排</span>
              <el-tag size="small" :type="scoreTagType(hit.score)" effect="plain">
                {{ scorePct(hit.score) }}%
              </el-tag>
            </div>
          </template>
          <div class="hit-content" :class="{ expanded: expanded.has(idx) }">
            {{ hit.content }}
          </div>
          <el-button
            v-if="hit.content.length > 200"
            link
            type="primary"
            size="small"
            class="hit-expand"
            @click="toggleExpand(idx)"
          >
            {{ expanded.has(idx) ? "收起" : `展开全部 · 共 ${hit.content.length} 字` }}
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
import { extractErrorMessage } from "@/api/error";

const route = useRoute();
const kbId = Number(route.params.kbId);

const kbName = ref("");
const query = ref("");
const topK = ref(5);
const mode = ref("hybrid");
const rerank = ref(false);
const usedMode = ref("");
const usedRerank = ref(false);
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

/** 评审 P1-8：分数 tag 的色阶 —— ≥90% 绿 / 70–90% 主色 / <70% 橙。 */
function scoreTagType(score: number): "success" | "warning" | "primary" {
  const pct = Math.max(0, Math.min(score, 1)) * 100;
  if (pct >= 90) return "success";
  if (pct < 70) return "warning";
  return "primary";
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
    const result = await inspectApi.inspect(kbId, query.value.trim(), topK.value, mode.value, rerank.value);
    hits.value = result.hits;
    usedMode.value = result.mode;
    usedRerank.value = result.rerank;
    ran.value = true;
  } catch (err: any) {
    ElMessage.error(extractErrorMessage(err, "检索失败"));
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
  color: var(--el-text-color-regular);
}

.query-controls {
  display: flex;
  align-items: center;
  gap: 16px;
}

.adv-params {
  --el-collapse-border-color: var(--el-border-color-lighter);
}

.adv-row {
  display: flex;
  align-items: center;
  gap: var(--app-gap-sm);
  padding: 4px 0;
}

.control-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-regular);
  flex-shrink: 0;
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

/* -- 命中列表 -- */
.hits-summary {
  font-size: 13px;
  color: var(--el-text-color-secondary);
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
  border: 1px solid var(--el-border-color-light);
  border-radius: var(--radius-card);
  padding: 16px;
  transition: border-color 0.15s ease;
}

/* 评审 P1-8：零阴影 —— hover 只变边框色 */
.hit-card:hover {
  border-color: var(--el-color-primary-light-5);
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
  color: var(--el-text-color-regular);
}

.hit-filename {
  font-weight: 500;
  color: var(--el-text-color-primary);
}

.meta-sep {
  color: var(--el-text-color-disabled);
}

/* -- 相似度分数：带色阶的数值 tag（评审 P1-8，进度条已移除） -- */
.score-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.score-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}

/* -- 切片内容：主体（评审 P1-8：内容才是质检台的判断对象） -- */
.hit-content {
  font-size: var(--fs-body);
  line-height: 1.7;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 8;
  -webkit-box-orient: vertical;
}

.hit-content.expanded {
  display: block;
  -webkit-line-clamp: unset;
}

.hit-expand {
  align-self: flex-end;
}
</style>
