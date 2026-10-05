<script setup lang="ts">
/**
 * 会话侧边栏内容（J2）：桌面放 <aside>，窄屏（<1280px）放 el-drawer
 * （Known Gap #9 响应式）。纯展示 + 事件上抛 —— 会话数据与操作逻辑都在 Chat.vue，
 * 两处宿主共用同一份标记，不复制。
 */
import { Plus, Delete } from "@element-plus/icons-vue";
import type { Conversation } from "@/api/chat";

defineProps<{
  conversations: Conversation[];
  activeConvId: number | null;
}>();

defineEmits<{
  (e: "new"): void;
  (e: "select", conv: Conversation): void;
  (e: "remove", conv: Conversation): void;
}>();
</script>

<template>
  <div class="conv-panel">
    <div class="conv-header">
      <span class="conv-title">会话</span>
      <el-button size="small" type="primary" text @click="$emit('new')">
        <el-icon><Plus /></el-icon>
        <span>新对话</span>
      </el-button>
    </div>
    <div class="conv-list">
      <div v-if="conversations.length === 0" class="conv-empty">
        暂无会话<br />发送第一条消息自动创建
      </div>
      <div
        v-for="conv in conversations"
        :key="conv.id"
        class="conv-item"
        :class="{ 'conv-item--active': conv.id === activeConvId }"
        @click="$emit('select', conv)"
      >
        <span class="conv-item-title" :title="conv.title">{{ conv.title }}</span>
        <el-popconfirm title="删除该会话？" width="180" @confirm="$emit('remove', conv)">
          <template #reference>
            <el-icon class="conv-item-del" @click.stop><Delete /></el-icon>
          </template>
        </el-popconfirm>
      </div>
    </div>
  </div>
</template>

<style scoped>
.conv-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 4px 12px;
  border-bottom: 1px solid var(--app-hairline);
  flex-shrink: 0;
}

.conv-title {
  font-size: var(--fs-card);
  font-weight: 600;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding-top: var(--app-gap-sm);
}

.conv-empty {
  color: var(--app-ink-disabled);
  font-size: var(--fs-hint);
  text-align: center;
  line-height: 1.8;
  padding: 24px 0;
}

.conv-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  margin-bottom: 4px;
  border-radius: 6px;
  cursor: pointer;
  color: var(--app-ink-secondary);
  font-size: 13px;
  transition: background 0.15s ease;
}

.conv-item:hover {
  background: var(--app-canvas);
}

.conv-item--active {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

.conv-item-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.conv-item-del {
  margin-left: var(--app-gap-sm);
  color: var(--app-ink-disabled);
  flex-shrink: 0;
}

.conv-item-del:hover {
  color: var(--el-color-danger);
}
</style>
