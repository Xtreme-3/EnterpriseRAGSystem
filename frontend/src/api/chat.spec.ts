import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

// chat.ts 顶层 import client（axios）—— feedbackApi 的契约测试直接 mock 掉它，
// 断言「URL + 请求体」这一前后端契约，不真发请求。
vi.mock("./client", () => ({
  default: {
    post: vi.fn(async () => ({ data: {} })),
    get: vi.fn(async () => ({ data: {} })),
  },
}));

import client from "./client";
import { askStreamRequest, feedbackApi } from "./chat";

describe("askStreamRequest K7 参数", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubFetch() {
    // 显式声明入参类型：无参 vi.fn 的 calls 是空元组，取 calls[0][1] 会被 TS 拒绝
    // （容器里的 vue-tsc 抓出来过 —— 本地验证命令曾把退出码接在管道上而漏报）
    const fetchMock = vi.fn(
      async (_input: string | URL, _init?: RequestInit) => ({ ok: true, status: 200, body: {} })
    );
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  function requestBody(fetchMock: ReturnType<typeof stubFetch>): Record<string, unknown> {
    const init = fetchMock.mock.calls[0][1] as RequestInit | undefined;
    return JSON.parse((init?.body ?? "{}") as string) as Record<string, unknown>;
  }

  it("把 top_k / rerank / model 写进请求体", async () => {
    const fetchMock = stubFetch();
    await askStreamRequest(1, "出差住宿标准", {
      topK: 3,
      rerank: true,
      model: "qwen-turbo",
    });
    expect(requestBody(fetchMock)).toMatchObject({
      query: "出差住宿标准",
      top_k: 3,
      rerank: true,
      model: "qwen-turbo",
    });
  });

  it("不传 K7 参数时不写对应键（服务端默认生效）", async () => {
    const fetchMock = stubFetch();
    await askStreamRequest(1, "问题", { mode: "hybrid" });
    expect(requestBody(fetchMock)).toEqual({ query: "问题", mode: "hybrid" });
  });
});

describe("feedbackApi.set K5 反馈", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  function postMock() {
    return client.post as unknown as ReturnType<typeof vi.fn>;
  }

  it("POST rating/reason 到 message feedback 端点（键名与后端 FeedbackRequest 对齐）", async () => {
    await feedbackApi.set(1, 5, "down", "答非所问");
    expect(postMock()).toHaveBeenCalledWith("/kbs/1/messages/5/feedback", {
      rating: "down",
      reason: "答非所问",
    });
  });

  it("rating=null 清除反馈（reason 一并置空）", async () => {
    await feedbackApi.set(1, 5, null);
    expect(postMock()).toHaveBeenCalledWith("/kbs/1/messages/5/feedback", {
      rating: null,
      reason: null,
    });
  });
});
