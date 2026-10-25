import { beforeEach, describe, expect, it, vi } from "vitest";

// auditApi 走 axios client —— mock 掉它，断言「URL / 方法 / 查询参数」这些前后端契约。
vi.mock("./client", () => ({
  default: {
    get: vi.fn(async () => ({ data: { items: [], total: 0 } })),
    post: vi.fn(async () => ({ data: {} })),
  },
}));

import client from "./client";
import { auditApi } from "./audit";

const getMock = () => client.get as unknown as ReturnType<typeof vi.fn>;

describe("auditApi", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // L2 的查询是本仓第一个非 /kbs 前缀的全局接口 —— URL 前缀写错会 404，
  // 这是最容易在重构时被顺手改坏的地方。
  it("list 走 GET /audit-logs（全局前缀，不带 /kbs）", async () => {
    await auditApi.list();
    expect(getMock()).toHaveBeenCalledWith("/audit-logs", { params: {} });
  });

  it("过滤与分页参数原样透传", async () => {
    await auditApi.list({ action: "kb.delete", kb_id: 3, limit: 20, offset: 40 });
    expect(getMock()).toHaveBeenCalledWith("/audit-logs", {
      params: { action: "kb.delete", kb_id: 3, limit: 20, offset: 40 },
    });
  });

  it("返回 items + total 分页结构", async () => {
    getMock().mockResolvedValueOnce({
      data: { items: [{ id: 1, action: "auth.login" }], total: 57 },
    });
    const page = await auditApi.list();
    expect(page.total).toBe(57);
    expect(page.items[0].action).toBe("auth.login");
  });
});
