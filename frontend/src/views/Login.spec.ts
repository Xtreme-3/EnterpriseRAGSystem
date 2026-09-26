import { beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import type { VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import ElementPlus, { ElMessage } from "element-plus";

const login = vi.hoisted(() => vi.fn());
const me = vi.hoisted(() => vi.fn());

vi.mock("@/api/auth", () => ({
  authApi: { login, me, register: vi.fn() },
}));

import Login from "@/views/Login.vue";
import { useAuthStore } from "@/stores/auth";

/** el-form 替身：校验永远失败，用于确定性验证 Login.vue 自己的守卫分支。 */
const RejectingForm = defineComponent({
  name: "ElForm",
  setup(_props, { expose, slots }) {
    expose({ validate: () => Promise.reject(new Error("validation failed")) });
    return () => h("form", slots.default?.());
  },
});

/** el-form-item 替身：只透传插槽，避免依赖真实 form 上下文。 */
const PassthroughFormItem = defineComponent({
  name: "ElFormItem",
  setup(_props, { slots }) {
    return () => h("div", slots.default?.());
  },
});

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", redirect: "/login" },
      { path: "/login", component: Login },
      { path: "/register", component: { template: "<div />" } },
      { path: "/kbs", component: { template: "<div />" } },
    ],
  });
}

async function mountLogin() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = makeRouter();
  await router.push("/login");
  await router.isReady();

  const wrapper = mount(Login, {
    global: { plugins: [pinia, router, ElementPlus] },
  });

  return { wrapper, router, store: useAuthStore(pinia) };
}

/** 填表并提交，等所有异步链路（校验 → 请求 → 跳转）走完。 */
async function submit(wrapper: VueWrapper, username: string, password: string) {
  const inputs = wrapper.findAll("input");
  await inputs[0].setValue(username);
  await inputs[1].setValue(password);
  await wrapper.find(".auth-btn").trigger("click");
  await flushPromises();
  await flushPromises();
  await flushPromises();
}

/** ElMessage 会真的往 DOM 插节点，测试里统一替换成 spy。 */
function spyOnElMessage() {
  return {
    error: vi.spyOn(ElMessage, "error").mockImplementation(() => undefined as never),
    success: vi.spyOn(ElMessage, "success").mockImplementation(() => undefined as never),
  };
}

describe("Login.vue", () => {
  let msg: ReturnType<typeof spyOnElMessage>;

  beforeEach(() => {
    localStorage.clear();
    login.mockReset();
    me.mockReset();
    msg = spyOnElMessage();
  });

  it("登录成功后写入 token 并跳转 /kbs", async () => {
    login.mockResolvedValue({ access_token: "tok" });
    me.mockResolvedValue({ username: "alice", role: "user" });
    const { wrapper, router, store } = await mountLogin();

    await submit(wrapper, "alice", "secret");

    expect(store.isLoggedIn).toBe(true);
    expect(store.username).toBe("alice");
    expect(router.currentRoute.value.path).toBe("/kbs");
  });

  it("登录失败时展示服务端返回的消息", async () => {
    login.mockRejectedValue({ response: { status: 401, data: { detail: "用户名或密码错误" } } });
    const { wrapper } = await mountLogin();

    await submit(wrapper, "alice", "wrong");

    expect(msg.error).toHaveBeenCalledWith("用户名或密码错误");
  });

  // bugfix-log #36：422 的 detail 是数组，直接塞进 ElMessage 会渲染成 [object Object]
  it("422 校验错误展示可读文案，而不是 [object Object]", async () => {
    login.mockRejectedValue({
      response: {
        status: 422,
        data: { detail: [{ loc: ["body", "password"], msg: "String should have at most 72 characters" }] },
      },
    });
    const { wrapper } = await mountLogin();

    await submit(wrapper, "alice", "secret");

    const shown = msg.error.mock.calls[0]?.[0] as string;
    expect(shown).toBe("password String should have at most 72 characters");
    expect(shown).not.toContain("[object Object]");
  });

  // fallback 参数此前从未生效：4xx 且消息不可读时应该用调用点自己的文案
  it("服务端 4xx 且无消息时用登录页自己的兜底文案", async () => {
    login.mockRejectedValue({ response: { status: 404, data: null } });
    const { wrapper } = await mountLogin();

    await submit(wrapper, "alice", "secret");

    expect(msg.error).toHaveBeenCalledWith("登录失败，请检查用户名和密码");
  });

  it("后端未启动时给出可操作提示", async () => {
    login.mockRejectedValue(new Error("Network Error"));
    const { wrapper } = await mountLogin();

    await submit(wrapper, "alice", "secret");

    expect(msg.error).toHaveBeenCalledWith("无法连接服务器，请确认后端已启动");
  });

  // 不依赖 Element Plus 自身校验行为：用一个"校验必然失败"的 el-form 替身，
  // 确定性地验证 Login.vue 自己的守卫分支（valid === false 时直接 return）。
  it("表单校验失败时不发请求、不弹提示", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const router = makeRouter();
    await router.push("/login");

    const wrapper = mount(Login, {
      global: {
        plugins: [pinia, router, ElementPlus],
        stubs: { ElForm: RejectingForm, ElFormItem: PassthroughFormItem },
      },
    });

    await wrapper.find(".auth-btn").trigger("click");
    await flushPromises();

    expect(login).not.toHaveBeenCalled();
    expect(msg.error).not.toHaveBeenCalled();
  });

  // 与上面互补：不假设校验是否拦住，只断言"空表单点了也不会建立登录态"
  it("空表单提交不会建立登录态", async () => {
    const { wrapper, store } = await mountLogin();

    await wrapper.find(".auth-btn").trigger("click");
    await flushPromises();
    await flushPromises();

    expect(store.isLoggedIn).toBe(false);
    expect(msg.success).not.toHaveBeenCalled();
  });

  it("登录失败不会写入登录态", async () => {
    login.mockRejectedValue({ response: { status: 401, data: { detail: "用户名或密码错误" } } });
    const { wrapper, store } = await mountLogin();

    await submit(wrapper, "alice", "wrong");

    expect(store.isLoggedIn).toBe(false);
    expect(msg.success).not.toHaveBeenCalled();
  });
});
