import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from "axios";

const push = vi.hoisted(() => vi.fn());

vi.mock("@/router", () => ({ default: { push } }));

import client from "@/api/client";
import { useAuthStore } from "@/stores/auth";

/**
 * 自定义 adapter：让请求走**真实的拦截器链路**，但不真的发 HTTP。
 * 比直接调拦截器的 handler 更贴近真实行为（拦截器链、config 合并都会跑到）。
 */
function respondWith(
  body: unknown,
  onConfig?: (config: InternalAxiosRequestConfig) => void
): AxiosAdapter {
  return async (config) => {
    onConfig?.(config);
    return {
      data: body,
      status: 200,
      statusText: "OK",
      headers: config.headers as AxiosResponse["headers"],
      config,
    };
  };
}

function failWith(status: number): AxiosAdapter {
  return async (config) => {
    const error = Object.assign(new Error(`Request failed with status code ${status}`), {
      config,
      response: {
        status,
        statusText: "",
        data: { detail: "未授权" },
        headers: config.headers,
        config,
      },
    });
    throw error;
  };
}

describe("api client 拦截器", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    push.mockReset();
  });

  it("请求拦截器自动带上 Bearer token", async () => {
    let seen: InternalAxiosRequestConfig | undefined;
    client.defaults.adapter = respondWith({}, (config) => {
      seen = config;
    });
    useAuthStore().setAuth("tok", "alice");

    await client.get("/kbs");

    expect(seen?.headers?.Authorization).toBe("Bearer tok");
  });

  it("未登录时不带 Authorization 头", async () => {
    let seen: InternalAxiosRequestConfig | undefined;
    client.defaults.adapter = respondWith({}, (config) => {
      seen = config;
    });

    await client.get("/kbs");

    expect(seen?.headers?.Authorization).toBeUndefined();
  });

  it("业务请求返回 401 时登出并跳转登录页", async () => {
    const store = useAuthStore();
    store.setAuth("tok", "alice");
    client.defaults.adapter = failWith(401);

    await expect(client.get("/kbs")).rejects.toBeTruthy();

    expect(store.isLoggedIn).toBe(false);
    expect(push).toHaveBeenCalledWith("/login");
  });

  it("登录接口自身返回 401 时不跳转（否则用户看不到「密码错误」）", async () => {
    const store = useAuthStore();
    store.setAuth("tok", "alice");
    client.defaults.adapter = failWith(401);

    await expect(client.post("/auth/login", { username: "a", password: "b" })).rejects.toBeTruthy();

    expect(push).not.toHaveBeenCalled();
    expect(store.isLoggedIn).toBe(true);
  });

  it("非 401 错误原样抛出，不触发登出", async () => {
    const store = useAuthStore();
    store.setAuth("tok", "alice");
    client.defaults.adapter = failWith(500);

    await expect(client.get("/kbs")).rejects.toBeTruthy();

    expect(push).not.toHaveBeenCalled();
    expect(store.isLoggedIn).toBe(true);
  });

  it("成功响应原样透传", async () => {
    client.defaults.adapter = respondWith({ items: [1, 2] });
    await expect(client.get("/kbs")).resolves.toMatchObject({ data: { items: [1, 2] } });
  });
});
