<template>
  <div class="chat-page">
    <!-- J2 会话侧边栏：桌面常驻；窄屏（<1280px）隐藏，改用下方 el-drawer -->
    <aside class="chat-sidebar">
      <ChatConversations
        :conversations="conversations"
        :active-conv-id="activeConvId"
        @new="onNewConv"
        @select="onSelectConv"
        @remove="removeConversation"
      />
    </aside>

    <!-- 主聊天区 -->
    <div class="chat-main">
      <!-- 头部 -->
      <div class="chat-header">
        <el-button class="back-btn" @click="goBack">
          <el-icon><ArrowLeft /></el-icon>
          <span>返回</span>
        </el-button>
        <el-button class="chat-conv-toggle" @click="drawerVisible = true">
          <el-icon><Menu /></el-icon>
          <span>会话</span>
        </el-button>
        <span class="chat-kb-name">{{ kbName }}</span>
        <!-- 评审 P0-4：检索参数是调参入口，不与「这是哪个知识库」同级 —— 收进参数 popover -->
        <el-popover placement="bottom-end" :width="280" trigger="click" class="chat-params-popover">
          <template #reference>
            <el-button text class="chat-params-btn" aria-label="检索参数">
              <el-icon><Setting /></el-icon>
              <span>参数</span>
            </el-button>
          </template>
          <div class="chat-params">
            <div class="chat-param-row">
              <span class="chat-param-label">检索模式</span>
              <el-radio-group v-model="mode" size="small">
                <el-radio-button value="hybrid">混合</el-radio-button>
                <el-radio-button value="vector">纯向量</el-radio-button>
              </el-radio-group>
            </div>
            <div v-if="modelChoices.length > 1" class="chat-param-row">
              <span class="chat-param-label">模型</span>
              <el-select v-model="model" size="small" class="chat-model" aria-label="模型">
                <el-option v-for="m in modelChoices" :key="m" :label="m" :value="m" />
              </el-select>
            </div>
            <div class="chat-param-row">
              <span class="chat-param-label">检索条数</span>
              <el-input-number
                v-model="topK"
                size="small"
                :min="1"
                :max="10"
                controls-position="right"
                class="chat-topk"
                aria-label="检索条数"
              />
            </div>
            <div class="chat-param-row">
              <span class="chat-param-label">重排</span>
              <!-- 评审 P0-4：服务端未启用重排时不渲染开关，只留一行说明 ——
                   主界面不摆永远不可用的控件 -->
              <el-switch v-if="rerankAvailable" v-model="rerank" size="small" />
              <span v-else class="chat-param-muted">重排未启用（服务端 RERANK=false）</span>
            </div>
          </div>
        </el-popover>
      </div>

      <!-- 消息区 -->
      <div ref="msgContainer" class="chat-messages">
        <div v-if="messages.length === 0" class="chat-welcome">
          <h3>开始对话</h3>
          <p>基于知识库中的文档内容提问，答案带引用来源。</p>
          <!-- 评审 P2-9：空态的价值在引导 —— 左对齐 + 可点击的示例问题，不做居中 hero -->
          <div class="chat-welcome-examples">
            <button
              v-for="q in exampleQuestions"
              :key="q"
              type="button"
              class="chat-reason-chip"
              @click="input = q"
            >
              {{ q }}
            </button>
          </div>
        </div>

        <div
          v-for="(msg, idx) in messages"
          :key="idx"
          class="chat-msg"
          :class="{ 'chat-msg--user': msg.role === 'user', 'chat-msg--assistant': msg.role === 'assistant' }"
        >
          <div class="chat-msg-role">
            {{ msg.role === "user" ? "你" : "AI" }}
            <el-tag v-if="msg.cacheHit" size="small" type="info" class="chat-cache-tag">缓存</el-tag>
          </div>
          <div class="chat-msg-content">
            <div v-if="msg.content" class="chat-msg-text">{{ msg.content }}</div>
            <!-- K1 真流式：首个 token 到达前的阶段提示（改写 → 检索 → 重排 → 生成） -->
            <div v-else-if="msg.streaming" class="chat-msg-stage">
              {{ msg.stageText || "正在处理…" }}
            </div>
            <div v-if="msg.failed" class="chat-msg-failed">
              {{ msg.failReason || "生成失败，请重试" }}
            </div>
            <!-- 流式光标 -->
            <span v-if="msg.streaming && msg.content" class="chat-cursor">|</span>

            <!-- 来源引用：K1 起 sources 事件先于答案到达，立即渲染，不必等流结束。
                 引用芯片（design-manifest P0-a）：溯源是产品最大卖点，不藏进折叠面板 ——
                 一排「文件名 · 相似度%」芯片，点击展开对应切片原文 -->
            <div v-if="msg.sources.length > 0" class="chat-sources">
              <div class="chat-source-chips">
                <button
                  v-for="(src, si) in msg.sources"
                  :key="`chip-${si}-${src.chunk_index}`"
                  type="button"
                  class="chat-source-chip"
                  :class="{ 'chat-source-chip--active': expandedSource === `${idx}-${si}` }"
                  @click="toggleSource(idx, si)"
                >
                  {{ src.filename }} · {{ (src.score * 100).toFixed(0) }}%
                </button>
              </div>
              <div
                v-for="(src, si) in msg.sources"
                v-show="expandedSource === `${idx}-${si}`"
                :key="`detail-${si}`"
                class="chat-source-detail"
              >
                <p class="chat-source-content">{{ src.content }}</p>
              </div>
            </div>

            <!-- K5 反馈：只对已出完整内容的 AI 回答显示（消息 id 由 done 事件/会话详情带回） -->
            <div
              v-if="msg.role === 'assistant' && !msg.streaming && msg.content && msg.id"
              class="chat-feedback"
            >
              <el-button
                text
                size="small"
                class="chat-feedback-btn"
                :class="{ 'chat-feedback-btn--active': msg.feedback === 'up' }"
                @click="rate(msg, 'up')"
              >有用</el-button>
              <el-button
                text
                size="small"
                class="chat-feedback-btn"
                :class="{ 'chat-feedback-btn--active': msg.feedback === 'down' }"
                @click="rate(msg, 'down')"
              >没用</el-button>
            </div>
            <div v-if="msg.feedback === 'down' && msg.id" class="chat-feedback-reasons">
              <span>原因：</span>
              <span
                v-for="r in feedbackReasons"
                :key="r"
                class="chat-reason-chip"
                :class="{ 'chat-reason-chip--active': msg.feedbackReason === r }"
                @click="setReason(msg, r)"
              >{{ r }}</span>
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
          placeholder="输入问题…（Enter 发送，Shift+Enter 换行）"
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

    <!-- 窄屏（<1280px）会话抽屉：与桌面侧边栏共用同一份会话面板组件 -->
    <el-drawer v-model="drawerVisible" direction="ltr" size="260px" :with-header="false">
      <ChatConversations
        :conversations="conversations"
        :active-conv-id="activeConvId"
        @new="onNewConv"
        @select="onSelectConv"
        @remove="removeConversation"
      />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ArrowLeft, Menu, Setting } from "@element-plus/icons-vue";
import ChatConversations from "@/components/ChatConversations.vue";
import {
  askStreamRequest,
  parseSseStream,
  conversationApi,
  configApi,
  feedbackApi,
  type Conversation,
  type SourceRef,
} from "@/api/chat";
import { kbApi } from "@/api/kbs";
import { extractErrorMessage, isAbortError } from "@/api/error";

interface Message {
  /** K5：落库后的消息 id（流式在 done 事件回填），反馈定位用 */
  id?: number;
  role: "user" | "assistant";
  content: string;
  sources: SourceRef[];
  streaming: boolean;
  /** K1 真流式：首个 token 到达前显示的后端阶段文案 */
  stageText?: string;
  /** K1：流内 error 事件 / 请求异常导致的失败态 */
  failed?: boolean;
  failReason?: string;
  /** K5：当前用户对这条回答的反馈 */
  feedback?: "up" | "down" | null;
  feedbackReason?: string | null;
  /** K6：本次回答是否来自问答缓存（仅当次会话内显示，不落库） */
  cacheHit?: boolean;
}

/** K1：后端 stage 事件 → 用户可读的进度文案 */
const STAGE_TEXT: Record<string, string> = {
  rewriting: "正在改写问题…",
  retrieving: "正在检索资料…",
  reranking: "正在重排…",
  generating: "正在组织答案…",
};

const route = useRoute();
const router = useRouter();
const kbId = Number(route.params.kbId);

/** 欢迎态示例问题（评审 P2-9）：点击即填入输入框，演示语料域内的问题 */
const exampleQuestions = [
  "供应商准入需要满足哪些条件？",
  "出差住宿标准是多少？",
  "皮具类产品的五金件有什么要求？",
];

const kbName = ref("加载中...");
const input = ref("");
const mode = ref("hybrid");
// K7 参数栏：配置从 /api/config/chat 取，加载失败保持默认（服务端默认生效）
const modelChoices = ref<string[]>([]);
const model = ref("");
const topK = ref(5);
const rerankAvailable = ref(false);
const rerank = ref(false);
// K5：反馈原因标签（清单来自 /api/config/chat，前端不自造）
const feedbackReasons = ref<string[]>([]);
const sending = ref(false);
const errorMsg = ref("");
// K6：缓存命中的消息在本次会话内显示徽标（不落库）
// K7：参数栏状态（见下方各 ref）
// 引用芯片展开状态：`消息下标-来源下标`，同一时间展开一片
const expandedSource = ref<string | null>(null);

function toggleSource(msgIdx: number, si: number) {
  const key = `${msgIdx}-${si}`;
  expandedSource.value = expandedSource.value === key ? null : key;
}
const messages = ref<Message[]>([]);
const msgContainer = ref<HTMLElement>();

// J2 会话：侧边栏列表 + 当前选中会话
const conversations = ref<Conversation[]>([]);
const activeConvId = ref<number | null>(null);
const loadingConv = ref(false);
// K9 响应式（Known Gap #9）：窄屏侧边栏收进抽屉
const drawerVisible = ref(false);

/** 抽屉里的操作顺手把抽屉关掉（桌面侧边栏路径下关的是未打开的抽屉，无副作用） */
function onNewConv() {
  newChat();
  drawerVisible.value = false;
}

function onSelectConv(conv: Conversation) {
  selectConversation(conv);
  drawerVisible.value = false;
}

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

/** J2：切换/新建会话时中断在途流式请求（前端的活跃会话唯一） */
function abortInFlight() {
  abortController?.abort();
  abortController = null;
}

async function loadConversations() {
  try {
    conversations.value = await conversationApi.list(kbId);
  } catch {
    // 列表加载失败静默（发送时自动建会话兜底）
  }
}

/** 新对话：清空消息并取消选中，保持当前知识库 */
function newChat() {
  abortInFlight();
  activeConvId.value = null;
  messages.value = [];
  errorMsg.value = "";
  scrollBottom();
}

/** 选中历史会话：GET 详情加载消息（服务端推导历史，前端只展示） */
async function selectConversation(conv: Conversation) {
  if (conv.id === activeConvId.value || loadingConv.value) return;
  abortInFlight();
  activeConvId.value = conv.id;
  loadingConv.value = true;
  errorMsg.value = "";
  try {
    const detail = await conversationApi.detail(kbId, conv.id);
    messages.value = detail.messages.map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      sources: m.sources,
      streaming: false,
      feedback: m.feedback ?? null,
      feedbackReason: m.feedback_reason ?? null,
    }));
    scrollBottom();
  } catch {
    activeConvId.value = null;
    messages.value = [];
    errorMsg.value = "加载会话失败";
  } finally {
    loadingConv.value = false;
  }
}

/** 删除会话（级联删消息）；若删除的是当前会话则回到新对话 */
async function removeConversation(conv: Conversation) {
  try {
    await conversationApi.remove(kbId, conv.id);
  } catch (e: any) {
    errorMsg.value = extractErrorMessage(e, "删除会话失败");
    return;
  }
  conversations.value = conversations.value.filter((c) => c.id !== conv.id);
  if (activeConvId.value === conv.id) {
    newChat();
  }
}

async function send() {
  const query = input.value.trim();
  if (!query || sending.value) return;

  input.value = "";
  errorMsg.value = "";

  // J2：首次发送无会话 → 自动创建（title=提问前 30 字），之后复用 conversation_id
  let convId = activeConvId.value;
  if (convId == null) {
    try {
      const conv = await conversationApi.create(kbId, query.slice(0, 30));
      convId = conv.id;
      activeConvId.value = conv.id;
      conversations.value.unshift(conv);
    } catch (e: any) {
      errorMsg.value = `创建会话失败：${extractErrorMessage(e, "请重试")}`;
      return;
    }
  }

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
    // J2：带 conversation_id，服务端从库内历史推导多轮上下文并落库，前端不再传 history
    const resp = await askStreamRequest(kbId, query, {
      signal: abortController.signal,
      mode: mode.value,
      conversationId: convId,
      topK: topK.value,
      rerank: rerank.value,
      model: model.value || undefined,
    });
    const reader = resp.body!.getReader();

    // K8-12：后端每条流都以 `data: [DONE]` 收尾（连错误路径也会发）。
    // 没等到它 = 连接中途断了，此时已累积的内容可能只是半句 —— 旧代码会把
    // 半截答案当"回答完成"直接呈现给用户，看不出任何异常。
    let sawTerminator = false;

    for await (const event of parseSseStream(reader)) {
      if (event === "done") {
        // [DONE] 结束标记
        sawTerminator = true;
        break;
      }
      if (event.type === "stage") {
        // 首个 token 到达前显示后端阶段（改写 → 检索 → 重排 → 组织答案）
        assistantMsg.stageText = STAGE_TEXT[event.stage] || "正在处理…";
      } else if (event.type === "sources") {
        // K1 来源前置：答案还在生成时引用卡片已可见
        assistantMsg.sources = event.sources;
        scrollBottom();
      } else if (event.type === "token") {
        assistantMsg.content += event.content;
        assistantMsg.stageText = "";
        scrollBottom();
      } else if (event.type === "done") {
        // K1：用后端完整答案校准累积的 token，避免丢包导致半条答案
        if (event.answer) assistantMsg.content = event.answer;
        assistantMsg.stageText = "";
        // K5：落库后的消息 id 回填，反馈才能定位到这条回答
        if (event.message_id != null) assistantMsg.id = event.message_id;
        // K6：缓存命中的回答显示「缓存」徽标
        assistantMsg.cacheHit = !!event.cache_hit;
      } else if (event.type === "error") {
        assistantMsg.stageText = "";
        assistantMsg.failed = true;
        assistantMsg.failReason = event.message || "生成失败，请重试";
        errorMsg.value = assistantMsg.failReason;
      }
    }
    assistantMsg.streaming = false;
    if (!sawTerminator && !assistantMsg.failed) {
      assistantMsg.failed = true;
      assistantMsg.failReason = "回答传输中断，内容可能不完整，请重新提问";
      errorMsg.value = assistantMsg.failReason;
    }
    if (!assistantMsg.content && !assistantMsg.failed) {
      assistantMsg.content = "（未找到相关信息）";
    }
  } catch (err: any) {
    assistantMsg.streaming = false;
    // K8-12：用户主动点「停止」/ 切换页面触发 abort，不是错误 ——
    // 旧代码把它当失败处理，界面上弹出英文的 "The user aborted a request."
    if (isAbortError(err)) {
      if (!assistantMsg.content) assistantMsg.content = "（已停止生成）";
      return;
    }
    assistantMsg.failed = true;
    if (!assistantMsg.content) {
      assistantMsg.content = "（请求失败）";
    }
    errorMsg.value = extractErrorMessage(err, "连接失败，请稍后重试");
  } finally {
    assistantMsg.streaming = false;
    sending.value = false;
    scrollBottom();
  }
}

/** K7：加载对话页可调参数的服务端配置（模型清单 / 重排可用性 / 默认条数 / 反馈标签） */
async function loadChatOptions() {
  try {
    const opts = await configApi.chatOptions();
    modelChoices.value = opts.models;
    model.value = opts.models[0] || "";
    rerankAvailable.value = opts.rerank;
    rerank.value = opts.rerank;
    topK.value = opts.top_k_default;
    feedbackReasons.value = opts.feedback_reasons || [];
  } catch {
    // 配置加载失败：参数保持默认值，不阻塞对话主流程
  }
}

// ---- K5 答案反馈 ----

/** 点同一个按钮 = 取消（rating=null 清除）；换边 = 更新 */
async function rate(msg: Message, rating: "up" | "down") {
  if (msg.id == null) return;
  const next = msg.feedback === rating ? null : rating;
  try {
    const res = await feedbackApi.set(kbId, msg.id, next);
    msg.feedback = res.rating;
    if (res.rating == null) msg.feedbackReason = null;
  } catch (e: any) {
    errorMsg.value = extractErrorMessage(e, "反馈提交失败");
  }
}

/** 「没用」后选原因标签：同一票上更新 reason */
async function setReason(msg: Message, reason: string) {
  if (msg.id == null) return;
  try {
    const res = await feedbackApi.set(kbId, msg.id, "down", reason);
    msg.feedbackReason = res.reason;
  } catch (e: any) {
    errorMsg.value = extractErrorMessage(e, "反馈提交失败");
  }
}

onMounted(async () => {
  try {
    const kb = await kbApi.list().then((list) => list.find((k) => k.id === kbId));
    kbName.value = kb?.name || `知识库 #${kbId}`;
  } catch {
    kbName.value = `知识库 #${kbId}`;
  }
  loadConversations();
  loadChatOptions();
});

onUnmounted(() => {
  abortController?.abort();
});
</script>

<style scoped>
.chat-page {
  height: 100%;
  display: flex;
  max-width: 1200px;
  margin: 0 auto;
}

/* ---- J2 会话侧边栏（内容在 components/ChatConversations.vue）：桌面常驻，窄屏收进抽屉 ---- */
.chat-sidebar {
  width: 240px;
  flex-shrink: 0;
  border-right: 1px solid var(--app-hairline);
  display: flex;
  flex-direction: column;
  margin-right: var(--app-gap);
}

/* Known Gap #9：<1280px 时 240px 侧边栏挤压对话区 → 收成「会话」抽屉 */
.chat-conv-toggle {
  display: none;
}

@media (max-width: 1279px) {
  .chat-sidebar {
    display: none;
  }

  .chat-conv-toggle {
    display: inline-flex;
  }
}

/* ---- 主聊天区 ---- */
.chat-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.chat-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding-bottom: var(--app-gap);
  border-bottom: 1px solid var(--app-hairline);
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

/* 评审 P0-4：参数按钮推到头部最右，检索参数收进 popover */
.chat-params-btn {
  margin-left: auto;
}

.chat-params {
  display: flex;
  flex-direction: column;
  gap: var(--app-gap-sm);
}

.chat-param-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--app-gap-sm);
}

.chat-param-label {
  font-size: var(--fs-hint);
  color: var(--el-text-color-secondary);
}

.chat-param-muted {
  font-size: var(--fs-hint);
  color: var(--el-text-color-disabled);
}

.chat-topk {
  width: 96px;
}

.chat-model {
  width: 150px;
}

/* K5 答案反馈 */
.chat-feedback {
  margin-top: 8px;
  display: flex;
  gap: 4px;
}

.chat-feedback-btn {
  color: var(--app-ink-muted);
  height: auto;
  padding: 2px 6px;
}

.chat-feedback-btn--active {
  color: var(--el-color-primary);
}

.chat-feedback-reasons {
  margin-top: 6px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
  color: var(--app-ink-muted);
}

.chat-reason-chip {
  border: 1px solid var(--app-hairline);
  border-radius: 4px;
  padding: 2px 8px;
  cursor: pointer;
  color: var(--app-ink-secondary);
}

.chat-reason-chip:hover {
  border-color: var(--app-brand-border);
}

.chat-reason-chip--active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-5);
  color: var(--el-color-primary);
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: var(--app-gap) 0;
}

.chat-welcome {
  margin-top: 24px;
  color: var(--app-ink-muted);
}

.chat-welcome h3 {
  font-size: var(--fs-page);
  margin-bottom: var(--app-gap-sm);
}

.chat-welcome-examples {
  display: flex;
  flex-wrap: wrap;
  gap: var(--app-gap-sm);
  margin-top: 12px;
}

.chat-msg {
  display: flex;
  flex-direction: column;
  margin-bottom: var(--app-gap);
}

/* flex column + align-items 分左右（design-manifest P0-a：float 是老写法，结构一复杂就塌） */
.chat-msg--user {
  align-items: flex-end;
}

.chat-msg--assistant {
  align-items: flex-start;
}

.chat-msg-role {
  font-size: 12px;
  color: var(--app-ink-muted);
  margin-bottom: 4px;
  padding: 0 4px;
}

.chat-cache-tag {
  margin-left: 4px;
}

/* 用户气泡：品牌绿实底 + 白字（DESIGN.md §七规范，评审 P2-11 与代码不一致处） */
.chat-msg--user .chat-msg-content {
  background: var(--el-color-primary);
  color: var(--app-surface);
  border-radius: 8px 8px 2px 8px;
}

/* AI 气泡：白底 + 1px 浅边框。原先 var(--app-canvas) 与页面背景完全同色，边界不可见
   （design-manifest P0-a 列为真 bug，DESIGN.md §二已知陷阱同款） */
.chat-msg--assistant .chat-msg-content {
  background: var(--app-surface);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px 8px 8px 2px;
}

.chat-msg-content {
  padding: 12px 16px;
  max-width: 78%;
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

/* K1 真流式：首个 token 到达前的阶段提示 */
.chat-msg-stage {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--app-ink-muted);
}

.chat-msg-stage::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-color-primary);
  animation: blink 1s infinite;
}

/* K1：流内 error 事件 / 请求异常的失败提示 */
.chat-msg-failed {
  margin-top: 8px;
  font-size: var(--fs-body);
  color: var(--el-color-danger);
}

@keyframes blink {
  0%, 50% { opacity: 1; }
  51%, 100% { opacity: 0; }
}

.chat-sources {
  margin-top: var(--app-gap-sm);
}

.chat-source-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chat-source-chip {
  background: var(--app-surface);
  border: 1px solid var(--app-hairline);
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 12px;
  color: var(--app-ink);
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.chat-source-chip:hover {
  border-color: var(--app-brand-border);
}

.chat-source-chip--active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-5);
  color: var(--el-color-primary);
}

.chat-source-detail {
  margin-top: var(--app-gap-sm);
  padding: 8px 10px;
  background: var(--app-surface-sunken);
  border-radius: 6px;
}

.chat-source-content {
  font-size: var(--fs-body);
  color: var(--app-ink-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}

.chat-error {
  text-align: center;
  color: var(--el-color-danger);
  font-size: var(--fs-body);
  padding: 8px;
}

.chat-input-area {
  display: flex;
  gap: var(--app-gap-sm);
  align-items: flex-end;
  padding-top: 12px;
  border-top: 1px solid var(--app-hairline);
  flex-shrink: 0;
}

.chat-input-area .el-textarea {
  flex: 1;
}

.chat-input-area .el-button {
  /* 随输入框高度自适应（design-manifest P0-a：原硬编码 60px 靠运气对齐） */
  align-self: stretch;
}
</style>
