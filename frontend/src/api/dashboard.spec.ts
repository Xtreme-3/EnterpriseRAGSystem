import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

// dashboardApi 走 axios client —— mock 掉它，断言「URL」这一前后端契约。
vi.mock("./client", () => ({
  default: {
    get: vi.fn(async () => ({ data: {} })),
    post: vi.fn(async () => ({ data: {} })),
  },
}));

import client from "./client";
import { dashboardApi } from "./dashboard";

describe("dashboardApi (G6)", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("GET /kbs/{id}/dashboard，一次取回全部聚合块", async () => {
    await dashboardApi.get(7);
    const getMock = client.get as unknown as ReturnType<typeof vi.fn>;
    expect(getMock).toHaveBeenCalledWith("/kbs/7/dashboard");
  });
});
