import { createRouter, createWebHistory } from "vue-router";
import type { RouteRecordRaw } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import DefaultLayout from "@/layouts/DefaultLayout.vue";

const routes: RouteRecordRaw[] = [
  {
    path: "/login",
    name: "Login",
    component: () => import("@/views/Login.vue"),
    meta: { guest: true },
  },
  {
    path: "/register",
    name: "Register",
    component: () => import("@/views/Register.vue"),
    meta: { guest: true },
  },
  {
    path: "/",
    component: DefaultLayout,
    meta: { requiresAuth: true },
    children: [
      {
        path: "",
        redirect: "/kbs",
      },
      {
        path: "kbs",
        name: "KbsList",
        component: () => import("@/views/KbsList.vue"),
      },
      {
        path: "kbs/:kbId/docs",
        name: "DocList",
        component: () => import("@/views/DocList.vue"),
      },
      {
        path: "kbs/:kbId/chat",
        name: "Chat",
        component: () => import("@/views/Chat.vue"),
      },
      {
        path: "kbs/:kbId/inspect",
        name: "InspectBench",
        component: () => import("@/views/InspectBench.vue"),
      },
      {
        path: "kbs/:kbId/diagnostics",
        name: "Diagnostics",
        component: () => import("@/views/Diagnostics.vue"),
      },
      {
        path: "kbs/:kbId/doc-health",
        name: "DocHealth",
        component: () => import("@/views/DocHealth.vue"),
      },
      {
        path: "kbs/:kbId/dashboard",
        name: "Dashboard",
        component: () => import("@/views/Dashboard.vue"),
      },
      {
        path: "kbs/:kbId/qa-logs",
        name: "QaLogs",
        component: () => import("@/views/QaLogs.vue"),
      },
      {
        // L2 审计日志：全站第一个非 KB 作用域页面，仅 admin（守卫挡误入，后端 403 挡越权）
        path: "audit",
        name: "AuditLogs",
        component: () => import("@/views/AuditLogs.vue"),
        meta: { requiresAdmin: true },
      },
      {
        path: ":pathMatch(.*)*",
        name: "NotFound",
        component: () => import("@/views/NotFound.vue"),
      },
    ],
  },
  {
    path: "/:pathMatch(.*)*",
    redirect: "/login",
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to, _from, next) => {
  const auth = useAuthStore();

  if (to.matched.some((r) => r.meta.requiresAuth) && !auth.isLoggedIn) {
    next("/login");
  } else if (to.matched.some((r) => r.meta.requiresAdmin) && !auth.isAdmin) {
    // 非 admin 访问 admin 页面：回知识库列表（后端 403 是越权防线，这里挡误入）
    next("/kbs");
  } else if (to.meta.guest && auth.isLoggedIn) {
    next("/kbs");
  } else {
    next();
  }
});

export default router;
