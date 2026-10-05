<template>
  <div class="qa-logs">
    <div class="qa-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item :to="{ path: '/kbs' }">知识库</el-breadcrumb-item>
        <el-breadcrumb-item>{{ kbName }}</el-breadcrumb-item>
        <el-breadcrumb-item>问答历史</el-breadcrumb-item>
      </el-breadcrumb>
      <el-button :loading="loading" @click="fetchLogs">刷新</el-button>
    </div>

    <el-empty v-if="!loading && logs.length === 0" description="暂无问答记录，去对话页提问试试" />

    <el-table v-else :data="logs" size="small" border>
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="answer-detail">
            <div class="answer-label">回答全文</div>
            <pre class="answer-text">{{ row.answer }}</pre>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="query" label="问题" min-width="200" show-overflow-tooltip />
      <el-table-column prop="hit_count" label="命中文档" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.hit_count > 0 ? 'success' : 'info'">
            {{ row.hit_count }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="时间" width="170">
        <template #default="{ row }">
          {{ formatTime(row.created_at) }}
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute } from "vue-router";
import { ElMessage } from "element-plus";
import { qaLogApi, type QaLogItem } from "@/api/qaLogs";
import { kbApi } from "@/api/kbs";
import { extractErrorMessage } from "@/api/error";

const route = useRoute();
const kbId = Number(route.params.kbId);

const kbName = ref("");
const logs = ref<QaLogItem[]>([]);
const loading = ref(false);

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

async function fetchLogs() {
  loading.value = true;
  try {
    logs.value = await qaLogApi.list(kbId);
  } catch (err: any) {
    ElMessage.error(extractErrorMessage(err, "加载问答历史失败"));
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
  await fetchLogs();
});
</script>

<style scoped>
.qa-logs {
  max-width: 960px;
  margin: 0 auto;
}

.qa-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}

.answer-detail {
  padding: 8px 16px;
  background: var(--app-surface-sunken);
}

.answer-label {
  font-size: 12px;
  color: var(--app-ink-muted);
  margin-bottom: 6px;
}

.answer-text {
  margin: 0;
  font-family: "Noto Serif SC", "Songti SC", "SimSun", serif;
  font-size: var(--fs-table);
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
