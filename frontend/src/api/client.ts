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
