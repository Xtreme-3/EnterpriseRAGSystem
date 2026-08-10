import { defineStore } from "pinia";
import { ref, computed } from "vue";

const TOKEN_KEY = "rag_access_token";
const USER_KEY = "rag_user";

export const useAuthStore = defineStore("auth", () => {
  const token = ref<string | null>(localStorage.getItem(TOKEN_KEY));
  const username = ref<string | null>(localStorage.getItem(USER_KEY));

  const isLoggedIn = computed(() => !!token.value);

  function setAuth(accessToken: string, user: string) {
    token.value = accessToken;
    username.value = user;
    localStorage.setItem(TOKEN_KEY, accessToken);
    localStorage.setItem(USER_KEY, user);
  }

  function logout() {
    token.value = null;
    username.value = null;
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }

  return { token, username, isLoggedIn, setAuth, logout };
});
