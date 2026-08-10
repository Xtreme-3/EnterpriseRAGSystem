<template>
  <div class="chat-page">
    <!-- 头部 -->
    <div class="chat-header">
      <el-button class="back-btn" @click="goBack">
        <el-icon><ArrowLeft /></el-icon>
        <span>返回</span>
      </el-button>
      <span class="chat-kb-name">{{ kbName }}</span>
      <span class="chat-hint">Enter 发送，Shift+Enter 换行</span>
    </div>

    <!-- 消息区 -->
    <div ref="msgContainer" class="chat-messages">
      <div v-if="messages.length === 0" class="chat-welcome">
        <h3>开始对话</h3>
        <p>基于知识库中的文档内容提问，AI 会检索相关片段并生成答案。</p>
      </div>

      <div
        v-for="(msg, idx) in messages"
        :key="idx"
        class="chat-msg"
        :class="{ 'chat-msg--user': msg.role === 'user', 'chat-msg--assistant': msg.role === 'assistant' }"
      >
        <div class="chat-msg-role">{{ msg.role === "user" ? "你" : "AI" }}</div>
        <div class="chat-msg-content">
          <div class="chat-msg-text">{{ msg.content }}</div>
          <!-- 流式光标 -->
          <span v-if="msg.streaming" class="chat-cursor">|</span>

          <!-- 来源引用 -->
          <div v-if="msg.sources.length > 0 && !msg.streaming" class="chat-sources">
            <el-collapse>
              <el-collapse-item :title="`引用来源（${msg.sources.length} 条）`">
                <div v-for="src in msg.sources" :key="src.chunk_index" class="chat-source-item">
                  <div class="chat-source-header">
                    <el-tag size="small" type="info">{{ src.filename }}</el-tag>
                    <span class="chat-source-score">相似度 {{ (src.score * 100).toFixed(1) }}%</span>
                  </div>
                  <p class="chat-source-content">{{ src.content.slice(0, 200) }}</p>
                </div>
              </el-collapse-item>
            </el-collapse>
          </div>
        </div>
      </div>

      <!-- 错误 -->
      <div v-if="errorMsg" class="chat-error">
        {{ errorMsg }}
        <el-button text size="small" @click="errorMsg = ''">关闭</el-button>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="chat-input-area">
      <el-input
        v-model="input"
        type="textarea"
        :rows="2"
        placeholder="输入问题..."
        :disabled="sending"
        @keydown.enter="onEnter"
      />
      <el-button
        type="primary"
        :loading="sending"
        :disabled="!input.trim()"
        @click="send"
      >
        发送
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { ArrowLeft } from "@element-plus/icons-vue";
import { askStreamRequest, parseSseStream, type SourceRef } from "@/api/chat";
import { kbApi } from "@/api/kbs";

interface Message {
  role: "user" | "assistant";
  content: string;
  sources: SourceRef[];
  streaming: boolean;
}

const route = useRoute();
const router = useRouter();
const kbId = Number(route.params.kbId);

const kbName = ref("加载中...");
const input = ref("");
const sending = ref(false);
const errorMsg = ref("");
const messages = ref<Message[]>([]);
const msgContainer = ref<HTMLElement>();

let abortController: AbortController | null = null;

function goBack() {
  // 站内跳转（KbsList 卡片「对话」/ DocList「开始对话」）→ 返回来源页；
  // 刷新后无历史记录则回文档列表
  if (window.history.length > 1) {
    router.back();
  } else {
    router.push(`/kbs/${kbId}/docs`);
  }
}

function scrollBottom() {
  nextTick(() => {
    if (msgContainer.value) {
      msgContainer.value.scrollTop = msgContainer.value.scrollHeight;
    }
  });
}

function onEnter(e: KeyboardEvent) {
  if (e.shiftKey || e.isComposing) return; // Shift+Enter = 换行，IME 组合中不发送
  e.preventDefault();
  send();
}

async function send() {
  const query = input.value.trim();
  if (!query || sending.value) return;

  input.value = "";
  errorMsg.value = "";

  // 添加用户消息
  messages.value.push({ role: "user", content: query, sources: [], streaming: false });
  scrollBottom();

  // 添加空的助理消息（流式填充）
  const assistantMsg: Message = { role: "assistant", content: "", sources: [], streaming: true };
  messages.value.push(assistantMsg);
  scrollBottom();

  sending.value = true;
  abortController = new AbortController();

  try {
    const resp = await askStreamRequest(kbId, query, abortController.signal);
    const reader = resp.body!.getReader();

    for await (const event of parseSseStream(reader)) {
      if (event === "done") {
        assistantMsg.streaming = false;
        if (!assistantMsg.content) {
          assistantMsg.content = "（未找到相关信息）";
        }
        break;
      }
      if (event.type === "token") {
        assistantMsg.content += event.content;
        scrollBottom();
      } else if (event.type === "sources") {
        assistantMsg.sources = event.sources;
      }
    }
  } catch (err: any) {
    assistantMsg.streaming = false;
    if (!assistantMsg.content) {
      assistantMsg.content = "（请求失败）";
    }
    errorMsg.value = err.message || "连接失败，请稍后重试";
  } finally {
    sending.value = false;
    scrollBottom();
  }
}

onMounted(async () => {
  try {
    const kb = await kbApi.list().then(list => list.find(k => k.id === kbId));
    kbName.value = kb?.name || `知识库 #${kbId}`;
  } catch {
    kbName.value = `知识库 #${kbId}`;
  }
});

onUnmounted(() => {
  abortController?.abort();
});
</script>

<style scoped>
.chat-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  max-width: 900px;
  margin: 0 auto;
}

.chat-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #e4e7ed;
  flex-shrink: 0;
}

.back-btn {
  height: 32px;
  padding: 0 12px;
  border-radius: var(--radius-button);
}

.chat-kb-name {
  font-size: 16px;
  font-weight: 600;
  font-family: "Noto Serif SC", "Songti SC", "SimSun", serif;
}

.chat-hint {
  font-size: 12px;
  color: #c0c4cc;
  margin-left: auto;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px 0;
}

.chat-welcome {
  text-align: center;
  margin-top: 80px;
  color: #909399;
}

.chat-welcome h3 {
  font-size: 20px;
  margin-bottom: 8px;
}

.chat-msg {
  margin-bottom: 20px;
}

.chat-msg-role {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
  padding: 0 4px;
}

.chat-msg--user .chat-msg-content {
  background: var(--el-color-primary-light-9);
  border-radius: 8px 8px 2px 8px;
}

.chat-msg--assistant .chat-msg-content {
  background: #f5f7fa;
  border-radius: 8px 8px 8px 2px;
}

.chat-msg-content {
  padding: 12px 16px;
  max-width: 85%;
  display: inline-block;
}

.chat-msg--user .chat-msg-content {
  float: right;
}

.chat-msg--assistant .chat-msg-content {
  float: left;
}

.chat-msg::after {
  content: "";
  display: table;
  clear: both;
}

.chat-msg-text {
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.7;
}

.chat-cursor {
  animation: blink 1s infinite;
  color: var(--el-color-primary);
  font-weight: 700;
}

@keyframes blink {
  0%, 50% { opacity: 1; }
  51%, 100% { opacity: 0; }
}

.chat-sources {
  margin-top: 12px;
}

.chat-source-item {
  padding: 8px 0;
  border-bottom: 1px solid #ebeef5;
}

.chat-source-item:last-child {
  border-bottom: none;
}

.chat-source-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.chat-source-score {
  font-size: 12px;
  color: #909399;
}

.chat-source-content {
  font-size: 13px;
  color: #606266;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}

.chat-error {
  text-align: center;
  color: #f56c6c;
  font-size: 13px;
  padding: 8px;
}

.chat-input-area {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  padding-top: 12px;
  border-top: 1px solid #e4e7ed;
  flex-shrink: 0;
}

.chat-input-area .el-textarea {
  flex: 1;
}

.chat-input-area .el-button {
  height: 60px;
}
</style>
