import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import router from "@/router";
import { useAuthStore } from "@/stores/auth";

describe("router 路由守卫", () => {
  beforeEach(async () => {
    localStorage.clear();
    setActivePinia(createPinia());
    await router.replace("/login").catch(() => undefined);
  });

  it("未登录访问受保护路由时跳转 /login", async () => {
    await router.push("/kbs").catch(() => undefined);
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("未登录访问子路由 /kbs/1/chat 时同样跳转 /login", async () => {
    await router.push("/kbs/1/chat").catch(() => undefined);
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("已登录访问 guest 页面时跳转到 /kbs", async () => {
    useAuthStore().setAuth("tok", "alice");
    // 必须先离开 /login 再 push("/login")：vue-router 对"目标与当前相同"的导航
    // 会直接判定为重复导航并跳过守卫，那样这条断言会假失败。
    await router.replace("/kbs").catch(() => undefined);
    await router.push("/login").catch(() => undefined);
    expect(router.currentRoute.value.path).toBe("/kbs");
  });

  it("已登录访问受保护路由时正常放行", async () => {
    useAuthStore().setAuth("tok", "alice");
    await router.push("/kbs").catch(() => undefined);
    expect(router.currentRoute.value.path).toBe("/kbs");
  });

  it("受保护路由带 requiresAuth 标记，guest 路由带 guest 标记", () => {
    const kbs = router.resolve("/kbs");
    expect(kbs.matched.some((r) => r.meta.requiresAuth)).toBe(true);

    const login = router.resolve("/login");
    expect(login.meta.guest).toBe(true);
  });
});
