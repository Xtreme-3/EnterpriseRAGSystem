<template>
  <div class="kbs-page">
    <div class="kbs-toolbar">
      <h2>知识库</h2>
      <el-button type="primary" @click="showCreate = true">新建知识库</el-button>
    </div>

    <!-- 空状态 -->
    <div v-if="!loading && kbs.length === 0" class="kbs-empty">
      <el-empty description="暂无知识库，点击上方按钮创建" />
    </div>

    <!-- 卡片网格 -->
    <div v-else class="kbs-grid">
      <div v-for="kb in kbs" :key="kb.id" class="kb-card">
        <div class="kb-card-body">
          <h3 class="kb-name">{{ kb.name }}</h3>
          <p class="kb-desc">{{ kb.description || '暂无描述' }}</p>
          <div class="kb-meta">
            <span>{{ kb.document_count }} 个文档</span>
            <span>{{ formatDate(kb.created_at) }}</span>
          </div>
        </div>
        <div class="kb-card-actions">
          <div class="kb-action-group">
            <el-button text type="primary" @click="goDocs(kb.id)">文档</el-button>
            <el-button text type="success" @click="goChat(kb.id)">对话</el-button>
            <el-button text type="warning" @click="goInspect(kb.id)">质检</el-button>
            <el-button text @click="goDiagnostics(kb.id)">体检</el-button>
            <el-button text type="success" @click="goDocHealth(kb.id)">健康</el-button>
            <el-button text type="info" @click="goQaLogs(kb.id)">历史</el-button>
          </div>
          <el-popconfirm title="删除后不可恢复，确定删除？" @confirm="handleDelete(kb.id)">
            <template #reference>
              <el-button text type="danger">删除</el-button>
            </template>
          </el-popconfirm>
        </div>
      </div>
    </div>

    <!-- 加载 -->
    <div v-if="loading" class="kbs-loading">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
    </div>

    <!-- 创建对话框：知识库信息 + 可选上传 -->
    <el-dialog v-model="showCreate" title="新建知识库" width="520px" :close-on-click-modal="false" @closed="resetForm">
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="我的知识库" maxlength="100" show-word-limit />
        </el-form-item>
        <el-form-item label="描述（可选）" prop="description">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="2"
            placeholder="简要描述知识库的内容"
            maxlength="2000"
            show-word-limit
          />
        </el-form-item>

        <!-- 可选：直接上传文档 -->
        <el-divider content-position="left">
          <span style="font-size:13px;color:#909399">添加文档（可选，支持批量）</span>
        </el-divider>

        <el-upload
          ref="uploadRef"
          :auto-upload="false"
          multiple
          :on-change="onFileChange"
          :on-remove="onFileRemove"
          :accept="acceptExts"
          drag
          class="kb-upload"
        >
          <el-icon class="el-icon--upload" :size="28"><UploadFilled /></el-icon>
          <div class="el-upload__text">拖拽文件到此处，或 <em>点击选择</em></div>
          <template #tip>
            <div class="el-upload__tip">PDF / DOCX / MD / TXT，单文件最大 20MB</div>
          </template>
        </el-upload>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="handleCreate">
          {{ pendingFiles.length > 0 ? `创建并上传 ${pendingFiles.length} 个文件` : '创建知识库并添加文档' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, type FormInstance, type FormRules, type UploadFile, type UploadInstance } from "element-plus";
import { Loading, UploadFilled } from "@element-plus/icons-vue";
import { kbApi, type KBItem } from "@/api/kbs";
import { docApi } from "@/api/docs";

const SUPPORTED_EXTS = [".pdf", ".docx", ".md", ".txt"];

const router = useRouter();

const kbs = ref<KBItem[]>([]);
const loading = ref(true);
const showCreate = ref(false);
const creating = ref(false);
const formRef = ref<FormInstance>();
const uploadRef = ref<UploadInstance>();

// 用 on-change 直接捕获 File，避免通过 uploadRef 内部结构取文件
const pendingFiles = ref<File[]>([]);

const acceptExts = SUPPORTED_EXTS.join(",");

const form = reactive({
  name: "",
  description: "",
});

const rules: FormRules = {
  name: [{ required: true, message: "请输入知识库名称", trigger: "blur" }],
};

function onFileChange(file: UploadFile) {
  if (file.raw) pendingFiles.value.push(file.raw);
}

function onFileRemove(file: UploadFile) {
  pendingFiles.value = pendingFiles.value.filter(f => f !== file.raw);
}

function resetForm() {
  form.name = "";
  form.description = "";
  pendingFiles.value = [];
  uploadRef.value?.clearFiles();
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("zh-CN");
}

async function fetchKbs() {
  loading.value = true;
  try {
    kbs.value = await kbApi.list();
  } catch (err: any) {
    const detail = err.response?.data?.detail || "获取知识库列表失败";
    ElMessage.error(detail);
  } finally {
    loading.value = false;
  }
}

function goDocs(kbId: number) {
  router.push(`/kbs/${kbId}/docs`);
}

function goChat(kbId: number) {
  router.push(`/kbs/${kbId}/chat`);
}

function goInspect(kbId: number) {
  router.push(`/kbs/${kbId}/inspect`);
}

function goDiagnostics(kbId: number) {
  router.push(`/kbs/${kbId}/diagnostics`);
}

function goDocHealth(kbId: number) {
  router.push(`/kbs/${kbId}/doc-health`);
}

function goQaLogs(kbId: number) {
  router.push(`/kbs/${kbId}/qa-logs`);
}

function validateFileExt(file: File): boolean {
  const ext = "." + file.name.split(".").pop()?.toLowerCase();
  return SUPPORTED_EXTS.includes(ext);
}

async function handleCreate() {
  const valid = await formRef.value?.validate().catch(() => false);
  if (!valid) return;

  creating.value = true;
  try {
    const kb = await kbApi.create({ name: form.name, description: form.description });

    // 扩展名校验
    const invalidFiles = pendingFiles.value.filter(f => !validateFileExt(f));
    if (invalidFiles.length > 0) {
      ElMessage.warning(`已跳过 ${invalidFiles.length} 个不支持的文件类型`);
    }
    const validFiles = pendingFiles.value.filter(f => validateFileExt(f));

    // 逐个上传
    let uploaded = 0;
    for (const f of validFiles) {
      try {
        await docApi.upload(kb.id, f);
        uploaded++;
      } catch (e: any) {
        ElMessage.error(`"${f.name}" 上传失败: ${e.response?.data?.detail || e.message}`);
      }
    }

    showCreate.value = false;
    resetForm();

    if (uploaded > 0) {
      ElMessage.success(`知识库"${kb.name}"创建成功，已上传 ${uploaded} 份文档`);
    } else {
      ElMessage.success(`知识库"${kb.name}"创建成功，现在可以上传文档`);
    }

    // 直接进入文档管理页
    router.push(`/kbs/${kb.id}/docs`);
  } catch (err: any) {
    const detail = err.response?.data?.detail || "创建失败";
    ElMessage.error(detail);
  } finally {
    creating.value = false;
  }
}

async function handleDelete(id: number) {
  try {
    await kbApi.remove(id);
    ElMessage.success("已删除");
    await fetchKbs();
  } catch (err: any) {
    const detail = err.response?.data?.detail || "删除失败";
    ElMessage.error(detail);
  }
}

onMounted(fetchKbs);
</script>

<style scoped>
.kbs-page {
  max-width: 1200px;
  margin: 0 auto;
}

.kbs-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}

.kbs-toolbar h2 {
  font-size: 22px;
  font-weight: 600;
}

.kbs-empty {
  margin-top: 80px;
}

.kbs-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.kb-card {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: var(--radius-card);
  display: flex;
  flex-direction: column;
  transition: border-color 0.2s;
}

.kb-card:hover {
  border-color: var(--el-color-primary-light-5);
}

.kb-card-body {
  padding: 20px 20px 12px;
  flex: 1;
}

.kb-name {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  font-family: "Noto Serif SC", "Songti SC", "SimSun", serif;
  margin-bottom: 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-desc {
  font-size: 13px;
  color: #909399;
  min-height: 36px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.kb-meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #c0c4cc;
  margin-top: 8px;
}

.kb-card-actions {
  padding: 8px 16px;
  border-top: 1px solid #f2f2f2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.kb-action-group {
  display: flex;
  flex-wrap: wrap;
  gap: 0;
}

.kbs-loading {
  display: flex;
  justify-content: center;
  margin-top: 80px;
}

.kb-upload .el-upload-dragger {
  padding: 24px;
}
</style>
