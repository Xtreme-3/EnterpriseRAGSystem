import client from "./client";

export interface LoginRequest {
  username: string;
  password: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
}

export interface UserInfo {
  id: number;
  username: string;
  /** 全局角色：user | admin */
  role: string;
  created_at: string;
}

export const authApi = {
  login(data: LoginRequest): Promise<AuthResponse> {
    return client.post("/auth/login", data).then((r) => r.data);
  },

  register(data: LoginRequest): Promise<UserInfo> {
    return client.post("/auth/register", data).then((r) => r.data);
  },

  me(): Promise<UserInfo> {
    return client.get("/auth/me").then((r) => r.data);
  },

  logout(): Promise<void> {
    return client.post("/auth/logout").then((r) => r.data);
  },
};
