import axios from "axios";
import { useAuthStore } from "@/stores/auth";
import router from "@/router";

const client = axios.create({
  baseURL: "/api",
  timeout: 30000,
  headers: { "Content-Type": "application/json" },
});

// 请求拦截：自动带 JWT
client.interceptors.request.use((config) => {
  const auth = useAuthStore();
  if (auth.token) {
    config.headers.Authorization = `Bearer ${auth.token}`;
  }
  return config;
});

// 响应拦截：401 自动登出（排除登录接口自身返回的 401）
//
// K8-9：这里**故意不做统一 toast**。全仓 20 个调用点都在自己的 catch 里弹提示，
// 拦截器再弹一次会让同一个失败弹两条。统一的是"错误消息怎么解析"，
// 见 `@/api/error` 的 extractErrorMessage —— 各调用点传自己的兜底文案即可。
client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !error.config.url?.includes("/auth/login")) {
      const auth = useAuthStore();
      auth.logout();
      router.push("/login");
    }
    return Promise.reject(error);
  }
);

export default client;
