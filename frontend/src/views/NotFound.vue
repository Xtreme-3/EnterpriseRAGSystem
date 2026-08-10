<template>
  <div class="not-found">
    <h1>404</h1>
    <p>页面不存在</p>
    <el-button type="primary" @click="$router.push('/kbs')">返回知识库</el-button>
  </div>
</template>

<style scoped>
.not-found {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
}
.not-found h1 {
  font-size: 64px;
  color: #c0c4cc;
  margin: 0;
}
.not-found p {
  font-size: 18px;
  color: #909399;
  margin-bottom: 8px;
}
</style>
