import { useAuthStore } from "@/stores/auth";
import router from "@/router";
import client from "./client";

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
}

export interface SseToken {
  type: "token";
  content: string;
}

export interface SseSources {
  type: "sources";
  sources: SourceRef[];
  rewritten_query?: string;
  /** J2 会话持久化：本轮消息归属的会话 id（无会话为 null） */
  conversation_id?: number | null;
}

export type SseEvent = SseToken | SseSources;

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
 * V1 客户端分片效果等同于 LLM 流式输出。
 * 支持 AbortSignal 用于组件卸载时取消请求。
 * J2：带 conversationId 时服务端从库内历史推导多轮上下文并落库本轮消息，
 * 前端不再携带 history；不带时维持 J1 行为（前端传 history，完全向后兼容）。
 */
export interface AskStreamOptions {
  signal?: AbortSignal;
  mode?: string;
  conversationId?: number;
  history?: ChatTurn[];
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
    let detail = `请求失败 (${resp.status})`;
    try {
      const json = JSON.parse(body);
      detail = json.detail || detail;
    } catch {}
    throw new Error(detail);
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
