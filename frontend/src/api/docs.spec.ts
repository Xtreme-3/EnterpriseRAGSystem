import { beforeEach, describe, expect, it, vi } from "vitest";

// docApi 走 axios client —— mock 掉它，断言「URL / 方法 / 请求体」这些前后端契约。
vi.mock("./client", () => ({
  default: {
    get: vi.fn(async () => ({ data: {} })),
    post: vi.fn(async () => ({ data: {} })),
    delete: vi.fn(async () => ({ data: {} })),
  },
}));

import client from "./client";
import { docApi } from "./docs";

const getMock = () => client.get as unknown as ReturnType<typeof vi.fn>;
const postMock = () => client.post as unknown as ReturnType<typeof vi.fn>;
const deleteMock = () => client.delete as unknown as ReturnType<typeof vi.fn>;

describe("docApi", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("list 走 GET /kbs/{id}/documents", async () => {
    await docApi.list(3);
    expect(getMock()).toHaveBeenCalledWith("/kbs/3/documents");
  });

  it("upload 以 multipart 发到 /kbs/{id}/documents", async () => {
    const file = new File(["hi"], "a.txt", { type: "text/plain" });
    await docApi.upload(3, file);

    const [url, body, cfg] = postMock().mock.calls[0];
    expect(url).toBe("/kbs/3/documents");
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get("file")).toBe(file);
    // 必须显式声明 multipart，否则 axios 可能按 json 序列化 FormData
    expect(cfg.headers["Content-Type"]).toBe("multipart/form-data");
  });

  // K4：重试是新增的独立端点，不在 /kbs/ 前缀下 —— URL 写错会 404，
  // 而这正是最容易在重构时被改坏的地方。
  it("retry 走 POST /documents/{id}/retry", async () => {
    await docApi.retry(42);
    expect(postMock()).toHaveBeenCalledWith("/documents/42/retry");
  });

  it("remove 走 DELETE /documents/{id}", async () => {
    await docApi.remove(42);
    expect(deleteMock()).toHaveBeenCalledWith("/documents/42");
  });

  // 上传现在返回 202 + job —— 断言 job 字段原样透传，避免将来某层把未知字段吃掉。
  it("upload 原样透传 job（含 total_units 为 0 的排队态）", async () => {
    postMock().mockResolvedValueOnce({
      data: {
        id: 9,
        status: "pending",
        chunk_count: 0,
        job: { stage: "pending", done_units: 0, total_units: 0 },
      },
    });
    const res = await docApi.upload(3, new File(["x"], "a.txt"));
    expect(res.job?.stage).toBe("pending");
    expect(res.job?.total_units).toBe(0);
  });
});
