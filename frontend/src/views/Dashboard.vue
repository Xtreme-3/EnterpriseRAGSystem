<template>
  <div class="dashboard">
    <div class="dash-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item :to="{ path: '/kbs' }">知识库</el-breadcrumb-item>
        <el-breadcrumb-item>{{ kbName }}</el-breadcrumb-item>
        <el-breadcrumb-item>数据看板</el-breadcrumb-item>
      </el-breadcrumb>
      <el-button :loading="loading" @click="fetchDashboard">刷新</el-button>
    </div>

    <el-empty
      v-if="!loading && !d"
      :description="loadError ? `加载失败：${loadError}` : '加载看板数据…'"
    >
      <el-button v-if="loadError" type="primary" @click="fetchDashboard">重试</el-button>
    </el-empty>

    <template v-if="d">
      <!-- 概览统计卡（复用 StatCard，Known Gap #7 的成果） -->
      <div class="stat-grid">
        <StatCard :value="d.total_questions" label="累计问答" />
        <StatCard :value="d.cache_hits" label="缓存命中" tone="accent" />
        <StatCard :value="`${Math.round(d.cache_hit_rate * 100)}%`" label="缓存命中率" />
        <StatCard :value="d.feedback_total" label="反馈数" />
        <StatCard
          :value="`${Math.round(d.feedback_up_rate * 100)}%`"
          label="有用率"
          :tone="d.feedback_total > 0 ? 'good' : undefined"
        />
        <StatCard
          :value="d.dead_documents"
          label="死文档"
          :tone="d.dead_documents > 0 ? 'bad' : 'good'"
        />
      </div>

      <!-- 常问问题 TOP -->
      <section class="dash-section">
        <h3 class="section-title">常问问题 TOP {{ d.top_questions.length }}</h3>
        <el-table v-if="d.top_questions.length > 0" :data="d.top_questions" size="small" border>
          <el-table-column type="index" label="#" width="50" align="center" />
          <el-table-column prop="query" label="问题" min-width="240" show-overflow-tooltip />
          <el-table-column prop="count" label="提问次数" width="110" align="center" />
          <el-table-column label="缓存命中" width="110" align="center">
            <template #default="{ row }">
              {{ row.cache_hits > 0 ? `${row.cache_hits} 次` : "—" }}
            </template>
          </el-table-column>
        </el-table>
        <p v-else class="dash-empty">还没有问答记录，去对话页提问后这里会出现常问问题。</p>
      </section>

      <!-- 「没用」原因分布 -->
      <section class="dash-section">
        <h3 class="section-title">「没用」反馈原因分布</h3>
        <el-table v-if="d.reason_breakdown.length > 0" :data="d.reason_breakdown" size="small" border>
          <el-table-column prop="reason" label="原因" min-width="160" />
          <el-table-column prop="count" label="次数" width="110" align="center" />
        </el-table>
        <p v-else class="dash-empty">暂无「没用」反馈。</p>
      </section>

      <!-- 死文档 -->
      <section class="dash-section">
        <h3 class="section-title">死文档</h3>
        <template v-if="d.dead_documents > 0">
          <p class="dash-note">
            {{ d.dead_documents }} 个文档从未被问答命中（完整清单见「索引体检」）：
          </p>
          <div class="dash-dead-chips">
            <el-tag
              v-for="doc in d.dead_doc_list"
              :key="doc.filename"
              size="small"
              type="info"
              effect="plain"
            >
              {{ doc.filename }}
            </el-tag>
          </div>
        </template>
        <p v-else class="dash-empty">所有文档都至少被命中过一次。</p>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute } from "vue-router";
import StatCard from "@/components/StatCard.vue";
import { dashboardApi, type Dashboard } from "@/api/dashboard";
import { kbApi } from "@/api/kbs";
import { extractErrorMessage } from "@/api/error";

const route = useRoute();
const kbId = Number(route.params.kbId);

const kbName = ref("");
const loading = ref(false);
const loadError = ref("");
// 三态：null = 未加载（配合 loadError 区分首载/失败），拿到数据即为加载完成
// ——模板里 v-if="d" 同时充当 vue-tsc 的类型收窄
const d = ref<Dashboard | null>(null);

async function fetchDashboard() {
  loading.value = true;
  loadError.value = "";
  try {
    d.value = await dashboardApi.get(kbId);
  } catch (err: any) {
    loadError.value = extractErrorMessage(err, "看板加载失败");
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
  await fetchDashboard();
});
</script>

<style scoped>
.dashboard {
  max-width: 960px;
  margin: 0 auto;
}

.dash-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}

/* -- 概览统计卡：卡片本体在共用组件 StatCard.vue，本页只保留网格 -- */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: var(--app-gap-sm);
  margin-bottom: 24px;
}

.dash-section {
  margin-top: 24px;
}

.section-title {
  font-size: var(--fs-card);
  font-weight: 600;
  color: var(--app-ink);
  margin-bottom: 12px;
}

.dash-empty {
  font-size: var(--fs-hint);
  color: var(--app-ink-muted);
}

.dash-note {
  font-size: var(--fs-body);
  color: var(--app-ink-secondary);
  margin-bottom: var(--app-gap-sm);
}

.dash-dead-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--app-gap-sm);
}
</style>
