<script setup lang="ts">
/**
 * 统计卡（DESIGN.md §七 / design-manifest P2）：大数字 + 标签，数据看板的语言。
 *
 * Diagnostics / DocHealth（以及将来的数据看板）共用这一份，杜绝逐页复制漂移
 * —— design-manifest 原话："同一份数据看板语言，比多写一遍样式重要"。
 * tone 只影响数字颜色（DESIGN.md §七：语义色只允许出现在统计卡数字上）：
 * good = success / bad = danger / accent = 品牌墨绿。
 */
withDefaults(
  defineProps<{
    label: string;
    value: string | number;
    tone?: "good" | "bad" | "accent";
  }>(),
  { tone: undefined }
);
</script>

<template>
  <div class="stat-card" :class="tone ? `stat-card--${tone}` : undefined">
    <div class="stat-num">{{ value }}</div>
    <div class="stat-label">{{ label }}</div>
  </div>
</template>

<style scoped>
.stat-card {
  background: var(--app-surface);
  border: 1px solid var(--app-hairline);
  border-radius: var(--radius-card);
  padding: 16px;
  text-align: center;
}

.stat-num {
  font-size: 26px;
  font-weight: 700;
  color: var(--app-ink);
  line-height: 1.2;
}

.stat-label {
  font-size: var(--fs-hint);
  color: var(--app-ink-muted);
  margin-top: 4px;
}

.stat-card--good .stat-num {
  color: var(--el-color-success);
}

.stat-card--bad .stat-num {
  color: var(--el-color-danger);
}

.stat-card--accent .stat-num {
  color: var(--el-color-primary);
}
</style>
