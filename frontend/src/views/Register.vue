<template>
  <div class="auth-page">
    <div class="auth-card">
      <h1 class="auth-title">EnterpriseRAG</h1>
      <p class="auth-subtitle">创建新账号</p>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="handleRegister">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="请输入用户名" size="large" />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码（6–72 位）"
            size="large"
            show-password
            maxlength="72"
            @keyup.enter="handleRegister"
          />
        </el-form-item>

        <el-form-item label="确认密码" prop="confirmPassword">
          <el-input
            v-model="form.confirmPassword"
            type="password"
            placeholder="请再次输入密码"
            size="large"
            show-password
            @keyup.enter="handleRegister"
          />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" size="large" :loading="loading" class="auth-btn" @click="handleRegister">
            注 册
          </el-button>
        </el-form-item>
      </el-form>

      <p class="auth-switch">
        已有账号？
        <router-link to="/login">去登录</router-link>
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, type FormInstance, type FormRules } from "element-plus";
import { authApi } from "@/api/auth";
import { useAuthStore } from "@/stores/auth";
import { extractErrorMessage } from "@/api/error";

const router = useRouter();
const authStore = useAuthStore();
const formRef = ref<FormInstance>();
const loading = ref(false);

const form = reactive({
  username: "",
  password: "",
  confirmPassword: "",
});

const validateConfirm = (_rule: any, value: string, callback: (err?: Error) => void) => {
  if (value !== form.password) {
    callback(new Error("两次输入的密码不一致"));
  } else {
    callback();
  }
};

const rules: FormRules = {
  username: [
    { required: true, message: "请输入用户名", trigger: "blur" },
    { min: 2, max: 50, message: "用户名长度 2–50 个字符", trigger: "blur" },
  ],
  password: [
    { required: true, message: "请输入密码", trigger: "blur" },
    { min: 6, message: "密码至少 6 位", trigger: "blur" },
  ],
  confirmPassword: [
    { required: true, message: "请确认密码", trigger: "blur" },
    { validator: validateConfirm, trigger: "blur" },
  ],
};

async function handleRegister() {
  const valid = await formRef.value?.validate().catch(() => false);
  if (!valid) return;

  loading.value = true;
  try {
    await authApi.register({
      username: form.username.trim(),
      password: form.password,
    });

    // 注册成功 → 自动登录
    const loginRes = await authApi.login({
      username: form.username.trim(),
      password: form.password,
    });
    authStore.setAuth(loginRes.access_token, form.username.trim());
    await authStore.refreshMe(); // I2 RBAC：同步全局角色
    ElMessage.success("注册成功");
    router.push("/kbs");
  } catch (err: any) {
    const detail = extractErrorMessage(err, "注册失败，请稍后重试");
    ElMessage.error(detail);
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.auth-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.auth-card {
  width: 400px;
  padding: 40px;
  background: var(--app-surface);
  border: 1px solid var(--app-hairline);
  border-radius: var(--radius-container);
}

.auth-title {
  font-size: 28px;
  text-align: center;
  color: var(--el-color-primary);
  margin-bottom: 4px;
}

.auth-subtitle {
  text-align: center;
  color: var(--app-ink-muted);
  font-size: 14px;
  margin-bottom: 32px;
}

.auth-btn {
  width: 100%;
}

.auth-switch {
  text-align: center;
  font-size: 14px;
  color: var(--app-ink-muted);
}

.auth-switch a {
  color: var(--el-color-primary);
  text-decoration: none;
}
</style>
