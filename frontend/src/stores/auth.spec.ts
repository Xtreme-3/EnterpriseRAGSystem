import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const me = vi.hoisted(() => vi.fn());

vi.mock("@/api/auth", () => ({
  authApi: { me, login: vi.fn(), register: vi.fn() },
}));

import { useAuthStore } from "@/stores/auth";

describe("useAuthStore", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    me.mockReset();
  });

  it("没有 token 时是未登录、非管理员", () => {
    const store = useAuthStore();
    expect(store.isLoggedIn).toBe(false);
    expect(store.isAdmin).toBe(false);
  });

  it("setAuth 写入状态并持久化到 localStorage", () => {
    const store = useAuthStore();
    store.setAuth("tok", "alice", "admin");

    expect(store.isLoggedIn).toBe(true);
    expect(store.isAdmin).toBe(true);
    expect(localStorage.getItem("rag_access_token")).toBe("tok");
    expect(localStorage.getItem("rag_user")).toBe("alice");
    expect(localStorage.getItem("rag_role")).toBe("admin");
  });

  it("setAuth 不传角色时保留原角色", () => {
    const store = useAuthStore();
    store.setAuth("tok", "alice", "admin");
    store.setAuth("tok2", "alice");

    expect(store.isAdmin).toBe(true);
    expect(store.token).toBe("tok2");
  });

  it("新建 store 时从 localStorage 恢复登录态（页面刷新场景）", () => {
    localStorage.setItem("rag_access_token", "tok");
    localStorage.setItem("rag_user", "bob");
    localStorage.setItem("rag_role", "user");
    setActivePinia(createPinia());

    const store = useAuthStore();
    expect(store.isLoggedIn).toBe(true);
    expect(store.username).toBe("bob");
    expect(store.isAdmin).toBe(false);
  });

  it("logout 同时清空内存状态与 localStorage", () => {
    const store = useAuthStore();
    store.setAuth("tok", "alice", "admin");
    store.logout();

    expect(store.token).toBeNull();
    expect(store.username).toBeNull();
    expect(store.role).toBeNull();
    expect(store.isLoggedIn).toBe(false);
    expect(localStorage.getItem("rag_access_token")).toBeNull();
    expect(localStorage.getItem("rag_user")).toBeNull();
    expect(localStorage.getItem("rag_role")).toBeNull();
  });

  it("未登录时 refreshMe 不发请求", async () => {
    const store = useAuthStore();
    await store.refreshMe();
    expect(me).not.toHaveBeenCalled();
  });

  it("refreshMe 成功时刷新用户名与角色", async () => {
    const store = useAuthStore();
    store.setAuth("tok", "stale");
    me.mockResolvedValue({ username: "alice", role: "admin" });

    await store.refreshMe();

    expect(store.username).toBe("alice");
    expect(store.isAdmin).toBe(true);
    expect(localStorage.getItem("rag_role")).toBe("admin");
  });

  it("refreshMe 失败时保留本地缓存且不抛异常（401 由拦截器统一登出）", async () => {
    const store = useAuthStore();
    store.setAuth("tok", "alice", "user");
    me.mockRejectedValue(new Error("500"));

    await expect(store.refreshMe()).resolves.toBeUndefined();

    expect(store.username).toBe("alice");
    expect(store.isLoggedIn).toBe(true);
  });
});
