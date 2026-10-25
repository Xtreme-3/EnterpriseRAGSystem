<template>
  <div class="audit-logs">
    <div class="audit-header">
      <h2 class="audit-title">审计日志</h2>
      <el-button :loading="loadState === 'loading'" @click="fetchLogs">刷新</el-button>
    </div>

    <div class="audit-filters">
      <el-select
        v-model="actionFilter"
        placeholder="全部操作"
        clearable
        style="width: 200px"
        @change="handleFilterChange"
      >
        <el-option label="全部操作" value="" />
        <el-option v-for="(meta, action) in ACTION_META" :key="action" :label="meta.label" :value="action" />
      </el-select>
    </div>

    <!-- K8 三态：加载中 / 成功 / 失败（失败必须可重试，不能永久停在加载） -->
    <el-empty v-if="loadState === 'loading'" description="加载中…" />
    <div v-else-if="loadState === 'failed'" class="audit-failed">
      <span>审计日志加载失败</span>
      <el-button text type="primary" @click="fetchLogs">重试</el-button>
    </div>
    <el-empty v-else-if="items.length === 0" description="暂无审计记录" />
    <template v-else>
      <el-table :data="items" size="small" border>
        <el-table-column label="时间" width="170">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140">
          <template #default="{ row }">
            <el-tag size="small" :type="tagType(row.action)">
              {{ actionLabel(row.action) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="actor_name" label="操作人" width="140" show-overflow-tooltip />
        <el-table-column prop="target" label="对象" min-width="220" show-overflow-tooltip />
        <el-table-column prop="detail" label="详情" min-width="160" show-overflow-tooltip />
      </el-table>
      <div class="audit-pagination">
        <el-pagination
          v-model:current-page="page"
          layout="total, prev, pager, next"
          :total="total"
          :page-size="PAGE_SIZE"
          @current-change="fetchLogs"
        />
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { auditApi, type AuditLogItem } from "@/api/audit";
import { extractErrorMessage } from "@/api/error";

// action → 中文标签 + 语义色。与后端 app/core/audit.py 的 ACTIONS 常量是两处维护：
// 后端新增 action 忘改这里时降级显示原始 action 名（不报错）。
// 语义色一屏三种封顶：danger=删除/登录失败，success=创建/上传/注册/加成员，info=其余。
const ACTION_META: Record<string, { label: string; tag: "success" | "danger" | "info" }> = {
  "auth.login": { label: "登录", tag: "info" },
  "auth.login_failed": { label: "登录失败", tag: "danger" },
  "auth.register": { label: "注册", tag: "success" },
  "kb.create": { label: "创建知识库", tag: "success" },
  "kb.delete": { label: "删除知识库", tag: "danger" },
  "document.upload": { label: "上传文档", tag: "success" },
  "document.delete": { label: "删除文档", tag: "danger" },
  "document.retry": { label: "重试摄取", tag: "info" },
  "member.add": { label: "添加成员", tag: "success" },
  "member.role_change": { label: "变更成员角色", tag: "info" },
  "member.remove": { label: "移除成员", tag: "danger" },
};

const PAGE_SIZE = 50;

const items = ref<AuditLogItem[]>([]);
const total = ref(0);
const page = ref(1);
const actionFilter = ref("");
// K8：页面加载态用三态，不要只用一个 loaded boolean（失败时永久停在加载）
const loadState = ref<"loading" | "ready" | "failed">("loading");

function actionLabel(action: string): string {
  return ACTION_META[action]?.label ?? action;
}

function tagType(action: string): "success" | "danger" | "info" {
  return ACTION_META[action]?.tag ?? "info";
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

async function fetchLogs() {
  loadState.value = "loading";
  try {
    const res = await auditApi.list({
      action: actionFilter.value || undefined,
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
    });
    items.value = res.items;
    total.value = res.total;
    loadState.value = "ready";
  } catch (err: any) {
    ElMessage.error(extractErrorMessage(err, "加载审计日志失败"));
    loadState.value = "failed";
  }
}

function handleFilterChange() {
  page.value = 1;
  fetchLogs();
}

onMounted(fetchLogs);
</script>

<style scoped>
.audit-logs {
  max-width: 1200px;
  margin: 0 auto;
}

.audit-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--app-gap);
}

.audit-title {
  font-size: var(--fs-page);
  font-weight: 600;
  color: var(--app-ink);
  margin: 0;
  font-family: "Noto Serif SC", "Songti SC", "SimSun", serif;
}

.audit-filters {
  margin-bottom: var(--app-gap);
}

.audit-failed {
  display: flex;
  align-items: center;
  gap: var(--app-gap-sm);
  color: var(--app-ink-secondary);
  font-size: var(--fs-body);
}

.audit-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--app-gap);
}
</style>
