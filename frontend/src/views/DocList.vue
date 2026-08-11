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
          <el-tag :type="statusTag(row.status)" size="small">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="chunk_count" label="切片数" width="90" align="center" />
      <el-table-column label="上传时间" width="170">
        <template #default="{ row }">
          {{ formatDate(row) }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100" align="center">
        <template #default="{ row }">
          <!-- I2 RBAC：仅 editor+ 可删除 -->
          <el-popconfirm v-if="canEdit" title="删除后不可恢复，确定删除？" @confirm="handleDelete(row.id)">
            <template #reference>
              <el-button text type="danger" size="small">删除</el-button>
            </template>
          </el-popconfirm>
          <span v-else>—</span>
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
import { ref, computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, type UploadFile, type UploadInstance } from "element-plus";
import { Upload, UploadFilled, Loading, ArrowLeft } from "@element-plus/icons-vue";
import { docApi, type DocItem } from "@/api/docs";
import { kbApi, type KBItem } from "@/api/kbs";

const SUPPORTED_EXTS = [".pdf", ".docx", ".md", ".txt"];

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

const acceptExts = SUPPORTED_EXTS.join(",");

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

function formatDate(row: DocItem): string {
  if (!row.created_at) return "—";
  return new Date(row.created_at).toLocaleString("zh-CN");
}

function onFileChange(file: UploadFile) {
  pendingFile.value = file.raw || null;
}

async function fetchDocs() {
  loading.value = true;
  try {
    docs.value = await docApi.list(kbId);
  } catch (err: any) {
    if (err.response?.status === 403) {
      ElMessage.error("无权访问该知识库");
      router.push("/kbs");
      return;
    }
    ElMessage.error(err.response?.data?.detail || "获取文档列表失败");
  } finally {
    loading.value = false;
  }
}

async function init() {
  try {
    const list = await kbApi.list();
    const kb = list.find((k) => k.id === kbId);
    kbName.value = kb?.name || `知识库 #${kbId}`;
    // I2 RBAC：记录当前用户在该库的角色，用于按钮门控
    myRole.value = kb?.role || "viewer";
  } catch {
    kbName.value = `知识库 #${kbId}`;
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
  if (!SUPPORTED_EXTS.includes(ext)) {
    ElMessage.error(`不支持的文档类型: ${ext || "无扩展名"}（支持: ${SUPPORTED_EXTS.join(", ")}）`);
    return;
  }

  uploading.value = true;
  try {
    await docApi.upload(kbId, file);
    ElMessage.success("上传成功，文档已向量化");
    showUpload.value = false;
    await fetchDocs();
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || "上传失败");
  } finally {
    uploading.value = false;
  }
}

async function handleDelete(docId: number) {
  try {
    await docApi.remove(docId);
    ElMessage.success("已删除");
    await fetchDocs();
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || "删除失败");
  }
}

onMounted(init);
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
  color: #c0c4cc;
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
</style>
