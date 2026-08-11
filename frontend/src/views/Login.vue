<template>
  <div class="auth-page">
    <div class="auth-card">
      <h1 class="auth-title">EnterpriseRAG</h1>
      <p class="auth-subtitle">企业知识库问答系统</p>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="handleLogin">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="请输入用户名" size="large" />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            size="large"
            show-password
            maxlength="72"
            @keyup.enter="handleLogin"
          />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" size="large" :loading="loading" class="auth-btn" @click="handleLogin">
            登 录
          </el-button>
        </el-form-item>
      </el-form>

      <p class="auth-switch">
        还没有账号？
        <router-link to="/register">立即注册</router-link>
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

const router = useRouter();
const authStore = useAuthStore();
const formRef = ref<FormInstance>();
const loading = ref(false);

const form = reactive({
  username: "",
  password: "",
});

const rules: FormRules = {
  username: [{ required: true, message: "请输入用户名", trigger: "blur" }],
  password: [{ required: true, message: "请输入密码", trigger: "blur" }],
};

async function handleLogin() {
  const valid = await formRef.value?.validate().catch(() => false);
  if (!valid) return;

  loading.value = true;
  try {
    const res = await authApi.login({
      username: form.username.trim(),
      password: form.password,
    });
    authStore.setAuth(res.access_token, form.username.trim());
    await authStore.refreshMe(); // I2 RBAC：同步全局角色
    ElMessage.success("登录成功");
    router.push("/kbs");
  } catch (err: any) {
    const detail = err.response?.data?.detail || "登录失败，请检查用户名和密码";
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
  background: #fff;
  border: 1px solid #e4e7ed;
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
  color: #909399;
  font-size: 14px;
  margin-bottom: 32px;
}

.auth-btn {
  width: 100%;
}

.auth-switch {
  text-align: center;
  font-size: 14px;
  color: #909399;
}

.auth-switch a {
  color: var(--el-color-primary);
  text-decoration: none;
}
</style>
