import { useAuthStore } from "@/stores/auth";
import router from "@/router";

export interface SourceRef {
  filename: string;
  chunk_index: number;
  content: string;
  score: number;
}

export interface AskResponse {
  query: string;
  answer: string;
  sources: SourceRef[];
}

export interface SseToken {
  type: "token";
  content: string;
}

export interface SseSources {
  type: "sources";
  sources: SourceRef[];
}

export type SseEvent = SseToken | SseSources;

/**
 * SSE 流式问答：返回 ReadableStream，调用方自行解析。
 * V1 客户端分片效果等同于 LLM 流式输出。
 * 支持 AbortSignal 用于组件卸载时取消请求。
 */
export async function askStreamRequest(
  kbId: number,
  query: string,
  signal?: AbortSignal
): Promise<Response> {
  const auth = useAuthStore();
  const resp = await fetch(`/api/kbs/${kbId}/ask/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(auth.token ? { Authorization: `Bearer ${auth.token}` } : {}),
    },
    body: JSON.stringify({ query }),
    signal,
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
