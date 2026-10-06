import { useAuthStore } from "@/stores/auth";
import router from "@/router";
import client from "./client";
import { detailToMessage, safeParseJson } from "./error";

export interface SourceRef {
  filename: string;
  chunk_index: number;
  content: string;
  score: number;
}

/** J1 多轮：一轮对话历史（前端主动携带时用；J2 会话模式下由服务端推导，不再携带） */
export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface AskResponse {
  query: string;
  answer: string;
  sources: SourceRef[];
  rewritten_query: string;
  conversation_id?: number | null;
  /** K5：本轮 assistant 消息 id（带 conversation_id 时有值），供反馈定位 */
  message_id?: number | null;
  /** K6：本次回答是否来自问答缓存 */
  cache_hit?: boolean;
}

export interface SseToken {
  type: "token";
  content: string;
}

/** K1 真流式：链路阶段事件，用于进度提示（改写 → 检索 → 重排 → 生成） */
export type StreamStage = "rewriting" | "retrieving" | "reranking" | "generating";

export interface SseStage {
  type: "stage";
  stage: StreamStage;
}

export interface SseSources {
  type: "sources";
  sources: SourceRef[];
  rewritten_query?: string;
  /** J2 会话持久化：本轮消息归属的会话 id（无会话为 null） */
  conversation_id?: number | null;
  /** K1：来源事件即代表已进入生成阶段 */
  stage?: StreamStage;
}

/** K1：生成期错误（流已开始，无法再用 HTTP 状态码表达） */
export interface SseError {
  type: "error";
  message: string;
}

/** K1：生成结束，携带完整答案（前端可据此校准累积的 token） */
export interface SseDone {
  type: "done";
  answer: string;
  rewritten_query?: string;
  conversation_id?: number | null;
  /** K5：本轮落库后的 assistant 消息 id，前端据此对刚回答的这条提反馈 */
  message_id?: number | null;
  /** K6：本次回答是否来自问答缓存（命中时前端显示「缓存」徽标） */
  cache_hit?: boolean;
}

export type SseEvent = SseToken | SseSources | SseStage | SseError | SseDone;

// ---- K7 对话页参数 ----

/** GET /api/config/chat：服务端下发的可调参数配置 */
export interface ChatOptions {
  /** 可切换的模型清单（llm_model 恒在首位 = 服务端默认） */
  models: string[];
  /** 服务端是否启用了重排（RERANK=false 时前端开关置灰） */
  rerank: boolean;
  /** 服务端默认检索条数（Settings.top_k） */
  top_k_default: number;
  /** K5：反馈原因标签清单（唯一来源，前端不得自造，否则后端 422） */
  feedback_reasons: string[];
}

export const configApi = {
  chatOptions(): Promise<ChatOptions> {
    return client.get("/config/chat").then((r) => r.data);
  },
  /** 上传能力配置（K10）：受支持扩展名由后端注册表下发，前端不再硬编码副本 */
  uploadConfig(): Promise<UploadConfig> {
    return client.get("/config/upload").then((r) => r.data);
  },
};

export interface UploadConfig {
  supported_exts: string[];
}

// ---- K5 答案反馈 ----

export type FeedbackRating = "up" | "down";

export interface FeedbackResult {
  message_id: number;
  rating: FeedbackRating | null;
  reason: string | null;
}

export const feedbackApi = {
  /** 提交/更换/清除一条回答的反馈：rating=null 清除；重复提交由后端 upsert 成更新 */
  set(
    kbId: number,
    messageId: number,
    rating: FeedbackRating | null,
    reason: string | null = null
  ): Promise<FeedbackResult> {
    return client
      .post(`/kbs/${kbId}/messages/${messageId}/feedback`, { rating, reason })
      .then((r) => r.data);
  },
};

// ---- J2 会话 ----

export interface Conversation {
  id: number;
  kb_id: number;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  sources: SourceRef[];
  rewritten_query: string;
  created_at: string;
  /** K5：当前用户对这条回答的反馈（未评分为 null；只返回自己的，不含他人评价） */
  feedback?: "up" | "down" | null;
  feedback_reason?: string | null;
}

export interface ConversationDetail extends Conversation {
  messages: ConversationMessage[];
}

export const conversationApi = {
  list(kbId: number): Promise<Conversation[]> {
    return client.get(`/kbs/${kbId}/conversations`).then((r) => r.data);
  },
  create(kbId: number, title = "新对话"): Promise<Conversation> {
    return client.post(`/kbs/${kbId}/conversations`, { title }).then((r) => r.data);
  },
  detail(kbId: number, convId: number): Promise<ConversationDetail> {
    return client.get(`/kbs/${kbId}/conversations/${convId}`).then((r) => r.data);
  },
  remove(kbId: number, convId: number): Promise<void> {
    return client.delete(`/kbs/${kbId}/conversations/${convId}`).then((r) => r.data);
  },
};

/**
 * SSE 流式问答：返回 ReadableStream，调用方自行解析。
 * K1 起为真流式：后端逐 token 下发，事件序列
 * stage(rewriting→retrieving→reranking) → sources → token* → done → [DONE]，
 * 生成期异常以 error 事件下发（HTTP 状态码此时已是 200）。
 * 支持 AbortSignal 用于组件卸载时取消请求。
 * J2：带 conversationId 时服务端从库内历史推导多轮上下文并落库本轮消息，
 * 前端不再携带 history；不带时维持 J1 行为（前端传 history，完全向后兼容）。
 */
export interface AskStreamOptions {
  signal?: AbortSignal;
  mode?: string;
  conversationId?: number;
  history?: ChatTurn[];
  /** K7：检索条数（1–10）；不传用服务端默认 */
  topK?: number;
  /** K7：是否重排；不传跟随服务端配置 */
  rerank?: boolean;
  /** K7：本次生成模型（须在 ChatOptions.models 内）；不传用服务端默认 */
  model?: string;
}

export async function askStreamRequest(
  kbId: number,
  query: string,
  opts: AskStreamOptions = {}
): Promise<Response> {
  const auth = useAuthStore();
  const body: Record<string, unknown> = { query, mode: opts.mode ?? "hybrid" };
  if (opts.conversationId != null) {
    body.conversation_id = opts.conversationId;
  } else if (opts.history?.length) {
    body.history = opts.history;
  }
  if (opts.topK != null && opts.topK > 0) {
    body.top_k = opts.topK;
  }
  if (opts.rerank != null) {
    body.rerank = opts.rerank;
  }
  if (opts.model) {
    body.model = opts.model;
  }

  const resp = await fetch(`/api/kbs/${kbId}/ask/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(auth.token ? { Authorization: `Bearer ${auth.token}` } : {}),
    },
    body: JSON.stringify(body),
    signal: opts.signal,
  });

  if (resp.status === 401) {
    auth.logout();
    router.push("/login");
    throw new Error("登录已过期，请重新登录");
  }

  if (!resp.ok) {
    const body = await resp.text();
    // K8-8：`json.detail` 在 422 时是**数组**，直接 new Error(数组) 会渲染成
    // `[object Object]`。统一走 detailToMessage 归一化。
    const json = safeParseJson(body) as { detail?: unknown } | null;
    throw new Error(
      detailToMessage(json?.detail, `请求失败 (${resp.status})`) 
    );
  }

  if (!resp.body) {
    throw new Error("浏览器不支持流式响应");
  }

  return resp;
}

/**
 * 解析 SSE 事件流，返回异步生成器。
 */
export async function* parseSseStream(
  reader: ReadableStreamDefaultReader<Uint8Array>
): AsyncGenerator<SseEvent | "done"> {
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    // 最后一个可能不完整，保留到下轮
    buffer = parts.pop() || "";

    for (const part of parts) {
      if (!part.trim()) continue;
      // 每行 "data: ..."
      for (const line of part.split("\n")) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data: ")) continue;
        const data = trimmed.slice(6);

        if (data === "[DONE]") {
          yield "done";
          return;
        }

        try {
          const parsed = JSON.parse(data);
          yield parsed as SseEvent;
        } catch {
          // 解析失败跳过
        }
      }
    }
  }
}
