/**
 * vitest 全局前置（每个测试文件运行前执行一次）。
 *
 * jsdom 没有实现（或实现不全）下面这些浏览器 API，而 Element Plus 的
 * 表格 / 滚动条 / 响应式断点判断会用到它们。缺了不会报"找不到测试"，
 * 而是在组件挂载时抛 TypeError —— 表现为"这个页面的测试一律失败"。
 */

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
