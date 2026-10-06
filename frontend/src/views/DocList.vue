<template>
  <div class="docs-page">
    <!-- 头部面包屑 -->
    <div class="docs-header">
      <div class="docs-breadcrumb">
        <el-button class="back-btn" @click="goBack">
          <el-icon><ArrowLeft /></el-icon>
          <span>返回</span>
        </el-button>
        <span class="breadcrumb-sep">/</span>
        <span class="breadcrumb-current">{{ kbName }}</span>
        <span class="breadcrumb-sep">/</span>
        <span>文档管理</span>
      </div>
      <div class="docs-toolbar-right">
        <el-button @click="goChat">💬 开始对话</el-button>
        <!-- I2 RBAC：仅 editor+ 可上传 -->
        <el-button v-if="canEdit" type="primary" @click="handleUploadClick">
          <el-icon><Upload /></el-icon>
          上传文档
        </el-button>
        <!-- I2 RBAC：只读提示 -->
        <el-tag v-else type="info" effect="plain" size="small">只读 · 无上传权限</el-tag>
      </div>
    </div>

    <!-- 加载 -->
    <div v-if="loading" class="docs-loading">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
    </div>

    <!-- 空状态 -->
    <div v-else-if="docs.length === 0" class="docs-empty">
      <el-empty description="暂无文档，点击右上角上传" />
    </div>

    <!-- 文档表格 -->
    <el-table v-else :data="docs" stripe style="width: 100%">
      <el-table-column prop="filename" label="文件名" min-width="200" />
      <el-table-column prop="file_type" label="类型" width="80" align="center">
        <template #default="{ row }">
          <el-tag size="small" type="info">{{ row.file_type.toUpperCase() }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="110" align="center">
        <template #default="{ row }">
          <!-- 失败原因藏在 tooltip 里：列表里塞长文案会把行高撑爆 -->
          <el-tooltip
            v-if="row.status === 'failed' && row.error"
            :content="row.error"
            placement="top"
          >
            <el-tag :type="statusTag(row.status)" size="small">
              {{ statusLabel(row.status) }}
            </el-tag>
          </el-tooltip>
          <el-tag v-else :type="statusTag(row.status)" size="small">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <!-- K4：异步摄取的进度。终态文档不占位（显示 —），避免满屏空格子 -->
      <el-table-column label="进度" width="190">
        <template #default="{ row }">
          <div v-if="isPending(row)" class="progress-cell">
            <el-progress
              v-if="row.job && row.job.total_units > 0"
              :percentage="progressPercent(row)"
              :stroke-width="6"
              :show-text="false"
            />
            <span class="progress-text">{{ progressLabel(row) }}</span>
          </div>
          <span v-else class="progress-idle">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="chunk_count" label="切片数" width="90" align="center" />
      <el-table-column label="上传时间" width="170">
        <template #default="{ row }">
          {{ formatDate(row) }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" align="center">
        <template #default="{ row }">
          <!-- K4：failed 可一键重试，不必删掉重传（原始文件已留存在服务端） -->
          <el-button
            v-if="canEdit && row.status === 'failed'"
            text
            type="primary"
            size="small"
            @click="handleRetry(row)"
          >
            重试
          </el-button>
          <!-- I2 RBAC：仅 editor+ 可删除 -->
          <el-popconfirm v-if="canEdit" title="删除后不可恢复，确定删除？" @confirm="handleDelete(row.id)">
            <template #reference>
              <el-button text type="danger" size="small">删除</el-button>
            </template>
          </el-popconfirm>
          <span v-if="!canEdit">—</span>
        </template>
      </el-table-column>
    </el-table>

    <!-- 上传对话框 -->
    <el-dialog v-model="showUpload" title="上传文档" width="440px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="支持 PDF / DOCX / MD / TXT，最大 20 MB"
        style="margin-bottom: 16px"
      />
      <el-upload
        ref="uploadRef"
        :auto-upload="false"
        :limit="1"
        :on-change="onFileChange"
        :on-exceed="() => ElMessage.warning('一次只能上传一个文件')"
        :accept="acceptExts"
        drag
      >
        <el-icon class="el-icon--upload" :size="40"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽文件到此处，或 <em>点击选择</em></div>
      </el-upload>
      <template #footer>
        <el-button @click="showUpload = false">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!pendingFile" @click="handleUpload">
          开始上传
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, type UploadFile, type UploadInstance } from "element-plus";
import { Upload, UploadFilled, Loading, ArrowLeft } from "@element-plus/icons-vue";
import { docApi, type DocItem } from "@/api/docs";
import { kbApi, type KBItem } from "@/api/kbs";
import { configApi } from "@/api/chat";
import { extractErrorMessage } from "@/api/error";

// K10：受支持扩展名由后端注册表下发（/api/config/upload），拉取失败退回默认
const DEFAULT_EXTS = [".pdf", ".docx", ".md", ".txt"];
const supportedExts = ref<string[]>([...DEFAULT_EXTS]);

const route = useRoute();
const router = useRouter();
const kbId = Number(route.params.kbId);

if (isNaN(kbId)) {
  router.replace("/kbs");
  // 组件继续渲染但 kbId 无效 — 由 router guard 最终处理
}

const kbName = ref("加载中...");
const docs = ref<DocItem[]>([]);
const loading = ref(true);
const showUpload = ref(false);
const uploading = ref(false);
const uploadRef = ref<UploadInstance>();
const pendingFile = ref<File | null>(null);

// I2 RBAC：当前用户对此库的角色（owner | editor | viewer；admin 后端返回 owner）
const myRole = ref<string>("viewer");
const canEdit = computed(() => myRole.value === "owner" || myRole.value === "editor");

const acceptExts = computed(() => supportedExts.value.join(","));

function goBack() {
  router.push("/kbs");
}

function goChat() {
  router.push(`/kbs/${kbId}/chat`);
}

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

// ---- K4：异步摄取进度 ----

/** 终态：到这两个状态后进度不再变化，轮询可以停。 */
const TERMINAL_STATUSES = ["indexed", "failed"];
const STAGE_LABELS: Record<string, string> = {
  pending: "排队中",
  parsing: "解析中",
  chunking: "切片中",
  embedding: "向量化",
  indexing: "写入索引",
};

function isPending(row: DocItem): boolean {
  return !TERMINAL_STATUSES.includes(row.status);
}

function progressPercent(row: DocItem): number {
  const job = row.job;
  if (!job || !job.total_units) return 0;
  return Math.min(100, Math.round((job.done_units / job.total_units) * 100));
}

function progressLabel(row: DocItem): string {
  const job = row.job;
  // job 为 null 只出现在"文档状态还没被后台任务改过"的极窄窗口（或老数据），
  // 此时给一个中性文案，不要显示空白。
  if (!job) return "处理中";
  const label = STAGE_LABELS[job.stage] || job.stage;
  // 只有向量化阶段有可数的单位（切片条数），其余阶段给的是阶段名本身。
  if (job.stage === "embedding" && job.total_units > 0) {
    return `${label} ${job.done_units}/${job.total_units}`;
  }
  return label;
}

/** 列表里是否存在未收敛的文档 —— 决定要不要继续轮询。 */
const hasPending = computed(() => docs.value.some(isPending));

let pollTimer: ReturnType<typeof setInterval> | null = null;

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

/**
 * 按需轮询：只在列表里还有未收敛文档时开，全部到终态后自动停。
 *
 * 为什么不用 WebSocket / SSE：状态是"低频、可丢、可重取"的，轮询实现成本最低；
 * 而且这里 2 秒一轮、只在有任务时开，对后端压力可忽略。
 */
function syncPolling() {
  if (hasPending.value) {
    if (pollTimer === null) {
      pollTimer = setInterval(() => {
        // silent：不要每 2 秒把整页打回加载态（loading 会盖掉表格）
        void fetchDocs(true);
      }, 2000);
    }
  } else {
    stopPolling();
  }
}

function formatDate(row: DocItem): string {
  if (!row.created_at) return "—";
  return new Date(row.created_at).toLocaleString("zh-CN");
}

function onFileChange(file: UploadFile) {
  pendingFile.value = file.raw || null;
}

/**
 * @param silent 轮询触发的刷新：不要打 loading（会把表格整片盖掉，每 2 秒闪一次），
 *               也不要弹错误 toast（一次网络抖动不该连弹提示，下次轮询会自愈）。
 */
async function fetchDocs(silent = false) {
  if (!silent) loading.value = true;
  try {
    docs.value = await docApi.list(kbId);
    syncPolling();
  } catch (err: any) {
    if (err.response?.status === 403) {
      ElMessage.error("无权访问该知识库");
      stopPolling();
      router.push("/kbs");
      return;
    }
    if (!silent) ElMessage.error(extractErrorMessage(err, "获取文档列表失败"));
    else stopPolling();
  } finally {
    if (!silent) loading.value = false;
  }
}

async function init() {
  // K10：拉取后端下发的受支持扩展名（失败保持默认，不阻塞页面）
  try {
    const cfg = await configApi.uploadConfig();
    if (cfg.supported_exts?.length) supportedExts.value = cfg.supported_exts;
  } catch {
    // 保持 DEFAULT_EXTS
  }
  try {
    const list = await kbApi.list();
    const kb = list.find((k) => k.id === kbId);
    kbName.value = kb?.name || `知识库 #${kbId}`;
    // I2 RBAC：记录当前用户在该库的角色，用于按钮门控
    myRole.value = kb?.role || "viewer";
  } catch {
    // K8-11：原先这里只改 kbName，**完全不提示**。角色取不到 → myRole 停在初始值
    // （永久 viewer）→ 上传/删除按钮凭空消失，用户只会觉得"页面坏了"，
    // 而且刷新多少次都一样。至少要说清"现在是只读态、以及为什么"。
    kbName.value = `知识库 #${kbId}`;
    myRole.value = "viewer";
    ElMessage.warning("未能获取你的库内角色，已按「只读」展示；如需上传文档请刷新页面重试");
  }
  await fetchDocs();
}

function handleUploadClick() {
  pendingFile.value = null;
  uploadRef.value?.clearFiles();
  showUpload.value = true;
}

async function handleUpload() {
  if (!pendingFile.value) {
    ElMessage.warning("请选择文件");
    return;
  }
  const file = pendingFile.value;
  // 取最后一个点之后的扩展名；无扩展名或多个点（如 .tar.gz）以后端校验为准
  const nameParts = file.name.split(".");
  const ext = nameParts.length > 1 ? "." + nameParts.pop()!.toLowerCase() : "";
  if (!supportedExts.value.includes(ext)) {
    ElMessage.error(`不支持的文档类型: ${ext || "无扩展名"}（支持: ${supportedExts.value.join(", ")}）`);
    return;
  }

  uploading.value = true;
  try {
    await docApi.upload(kbId, file);
    // K4：上传接口现在 202 立即返回，向量化在后台跑 —— 说"已向量化"是不准确的
    ElMessage.success("上传成功，正在后台解析");
    showUpload.value = false;
    await fetchDocs();
  } catch (err: any) {
    ElMessage.error(extractErrorMessage(err, "上传失败"));
  } finally {
    uploading.value = false;
  }
}

/** K4：重试一份 failed 的文档。原始文件留在服务端，不必删掉重传。 */
async function handleRetry(row: DocItem) {
  try {
    await docApi.retry(row.id);
    ElMessage.success("已重新提交解析");
    await fetchDocs();
  } catch (err: any) {
    // 409 覆盖两种可预期情况：文档现在不是 failed（可能刚被别人重试成功）、
    // 以及原始文件已不可用。后端把具体原因写在 detail 里，直接透传即可。
    ElMessage.error(extractErrorMessage(err, "重试失败"));
    await fetchDocs(true);
  }
}

async function handleDelete(docId: number) {
  try {
    await docApi.remove(docId);
    ElMessage.success("已删除");
    await fetchDocs();
  } catch (err: any) {
    ElMessage.error(extractErrorMessage(err, "删除失败"));
  }
}

onMounted(init);
// K4：离开页面必须清掉轮询，否则定时器会在已卸载的组件上一直打接口
onUnmounted(stopPolling);
</script>

<style scoped>
.docs-page {
  max-width: 1100px;
  margin: 0 auto;
}

.docs-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}

.back-btn {
  height: 32px;
  padding: 0 12px;
  border-radius: var(--radius-button);
}

.docs-breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
}

.docs-toolbar-right {
  display: flex;
  gap: 8px;
}

.breadcrumb-sep {
  color: var(--app-ink-disabled);
}

.breadcrumb-current {
  font-size: 18px;
  font-weight: 600;
  font-family: "Noto Serif SC", "Songti SC", "SimSun", serif;
}

.docs-loading {
  display: flex;
  justify-content: center;
  margin-top: 80px;
}

.docs-empty {
  margin-top: 60px;
}

/* K4：进度单元格 —— 进度条在文字上方，整体靠左不抢视觉重量 */
.progress-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.progress-text {
  font-size: 12px;
  color: var(--app-ink-secondary);
  line-height: 1.2;
}

.progress-idle {
  color: var(--app-ink-disabled);
}
</style>
