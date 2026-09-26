import { createApp } from "vue";
import { createPinia } from "pinia";
import ElementPlus, { ElMessage } from "element-plus";
import "element-plus/dist/index.css";
import App from "./App.vue";
import router from "./router";
import "./style.css";

const app = createApp(App);

// K8-13：渲染期/生命周期钩子里的未捕获异常此前完全无人接管 ——
// 表现是白屏或组件静默失效，控制台里才有一行堆栈，用户拿不到任何提示。
// 这里至少给出一句可读反馈（详情仍打控制台，便于排查）。
app.config.errorHandler = (err, _instance, info) => {
  console.error("[vue errorHandler]", info, err);
  ElMessage.error("页面出现异常，请刷新重试");
};

app.use(createPinia());
app.use(router);
app.use(ElementPlus);
app.mount("#app");
