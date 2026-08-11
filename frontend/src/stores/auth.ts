import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { authApi } from "@/api/auth";

const TOKEN_KEY = "rag_access_token";
const USER_KEY = "rag_user";
const ROLE_KEY = "rag_role";

export const useAuthStore = defineStore("auth", () => {
  const token = ref<string | null>(localStorage.getItem(TOKEN_KEY));
  const username = ref<string | null>(localStorage.getItem(USER_KEY));
  // I2 RBAC：全局角色 user | admin（页面刷新后由 /me 恢复）
  const role = ref<string | null>(localStorage.getItem(ROLE_KEY));

  const isLoggedIn = computed(() => !!token.value);
  const isAdmin = computed(() => role.value === "admin");

  function setAuth(accessToken: string, user: string, userRole?: string) {
    token.value = accessToken;
    username.value = user;
    role.value = userRole || role.value;
    localStorage.setItem(TOKEN_KEY, accessToken);
    localStorage.setItem(USER_KEY, user);
    if (role.value) localStorage.setItem(ROLE_KEY, role.value);
  }

  function setRole(userRole: string) {
    role.value = userRole;
    localStorage.setItem(ROLE_KEY, userRole);
  }

  /** 用 /me 刷新用户名 + 全局角色（登录后 / 页面刷新时调用）。 */
  async function refreshMe() {
    if (!token.value) return;
    try {
      const me = await authApi.me();
      username.value = me.username;
      role.value = me.role;
      localStorage.setItem(USER_KEY, me.username);
      localStorage.setItem(ROLE_KEY, me.role);
    } catch {
      // 401 由拦截器统一登出；其余静默失败，保留本地缓存
    }
  }

  function logout() {
    token.value = null;
    username.value = null;
    role.value = null;
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(ROLE_KEY);
  }

  return { token, username, role, isLoggedIn, isAdmin, setAuth, setRole, refreshMe, logout };
});
