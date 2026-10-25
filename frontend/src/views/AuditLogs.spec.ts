import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import ElementPlus, { ElMessage } from "element-plus";

const list = vi.hoisted(() => vi.fn());

vi.mock("@/api/audit", () => ({
  auditApi: { list },
}));

import AuditLogs from "@/views/AuditLogs.vue";

const ROWS = [
  {
    id: 2,
    action: "auth.login_failed",
    actor_id: null,
    actor_name: "ghost_user",
    kb_id: null,
    target: "",
    detail: "reason=用户不存在",
    created_at: "2026-10-25T10:00:00",
  },
  {
    id: 1,
    action: "kb.create",
    actor_id: 5,
    actor_name: "alice",
    kb_id: 3,
    target: "kb:3(演示知识库)",
    detail: "",
    created_at: "2026-10-25T09:00:00",
  },
];

async function mountAudit() {
  setActivePinia(createPinia());
  const wrapper = mount(AuditLogs, {
    global: { plugins: [ElementPlus] },
  });
  await flushPromises();
  return wrapper;
}

describe("AuditLogs.vue", () => {
  beforeEach(() => {
    list.mockReset();
    vi.spyOn(ElMessage, "error").mockImplementation(() => undefined as never);
  });

  it("加载成功后渲染行，action 映射为中文标签与语义色 Tag", async () => {
    list.mockResolvedValue({ items: ROWS, total: 2 });
    const wrapper = await mountAudit();

    const text = wrapper.text();
    expect(text).toContain("登录失败");
    expect(text).toContain("创建知识库");
    expect(text).toContain("ghost_user");
    expect(text).toContain("kb:3(演示知识库)");
    // 语义色按 action 类别映射，不是随便挑的颜色
    expect(wrapper.find(".el-tag--danger").exists()).toBe(true);
    expect(wrapper.find(".el-tag--success").exists()).toBe(true);
  });

  // 后端新增 action 忘改前端映射表时的约定：降级显示原文，不报错、不空白
  it("未知 action 降级显示原始名并按 info 处理", async () => {
    list.mockResolvedValue({
      items: [
        {
          id: 9,
          action: "future.thing",
          actor_id: 1,
          actor_name: "alice",
          kb_id: null,
          target: "",
          detail: "",
          created_at: "2026-10-25T08:00:00",
        },
      ],
      total: 1,
    });
    const wrapper = await mountAudit();

    expect(wrapper.text()).toContain("future.thing");
    expect(wrapper.find(".el-tag--info").exists()).toBe(true);
  });

  it("空结果展示空态", async () => {
    list.mockResolvedValue({ items: [], total: 0 });
    const wrapper = await mountAudit();

    expect(wrapper.text()).toContain("暂无审计记录");
  });

  // K8 三态：失败不能永久停在「加载中」，且必须给重试出口。
  // 404 + 空 detail 才走调用点兜底文案（5xx 由 extractErrorMessage 生成「请求失败 (N)」）。
  it("加载失败进入失败态，重试成功后恢复渲染", async () => {
    list.mockRejectedValueOnce({ response: { status: 404, data: null } });
    const wrapper = await mountAudit();

    expect(wrapper.find(".audit-failed").exists()).toBe(true);
    expect(ElMessage.error).toHaveBeenCalledWith("加载审计日志失败");

    list.mockResolvedValue({ items: ROWS, total: 2 });
    await wrapper.findAll("button").find((b) => b.text() === "重试")!.trigger("click");
    await flushPromises();

    expect(wrapper.find(".audit-failed").exists()).toBe(false);
    expect(wrapper.text()).toContain("创建知识库");
  });

  it("切换过滤时带 action 参数重新请求并重置页码", async () => {
    list.mockResolvedValue({ items: ROWS, total: 2 });
    const wrapper = await mountAudit();
    list.mockClear();

    // 触发的是本组件自己的 v-model + @change 接线，不模拟下拉内部交互
    const select = wrapper.findComponent({ name: "ElSelect" }).vm;
    select.$emit("update:modelValue", "kb.delete");
    select.$emit("change", "kb.delete");
    await flushPromises();

    expect(list).toHaveBeenCalledWith({ action: "kb.delete", limit: 50, offset: 0 });
  });
});
