import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";
import { resolve } from "path";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  // 测试配置放在这里而不是单独的 vitest.config.ts：
  // ① `@` 别名只声明一次，测试与构建不会漂移；
  // ② tsconfig.node.json 的 include 只有 vite.config.ts，新配置文件会跳出类型检查。
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.spec.ts"],
    // 不开 globals：spec 文件显式 `import { describe, it, expect } from "vitest"`，
    // 这样不用往 tsconfig 的 types 里塞 "vitest/globals"，
    // 而 `npm run build`（vue-tsc && vite build）会连带检查 src 下的 spec，也能过。
    globals: false,
  },
});
