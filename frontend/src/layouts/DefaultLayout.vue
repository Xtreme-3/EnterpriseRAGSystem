<template>
  <div class="layout">
    <header class="layout-header">
      <div class="header-left">
        <span class="header-logo">EnterpriseRAG</span>
      </div>
      <div class="header-right">
        <span class="header-user">{{ authStore.username }}</span>
        <el-button text @click="handleLogout">退出</el-button>
      </div>
    </header>
    <main class="layout-main">
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { useAuthStore } from "@/stores/auth";

const router = useRouter();
const authStore = useAuthStore();

function handleLogout() {
  authStore.logout();
  ElMessage.success("已退出登录");
  router.push("/login");
}
</script>

<style scoped>
.layout {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.layout-header {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  flex-shrink: 0;
}

.header-logo {
  font-size: 18px;
  font-weight: 700;
  color: var(--el-color-primary);
  font-family: "Noto Serif SC", "Songti SC", "SimSun", serif;
  letter-spacing: 0.5px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-user {
  color: #606266;
  font-size: 14px;
}

.layout-main {
  flex: 1;
  overflow: auto;
  padding: 24px;
}
</style>
