/**
 * 统一错误消息归一化（K8-8）。
 *
 * 起因：全仓 20 处调用点都写着 `err.response?.data?.detail || "默认文案"`。
 * 而 FastAPI 的 `detail` 有**两种形态**：
 *
 * - 业务错误 → 字符串：`{"detail": "问答服务暂时不可用"}`
 * - 校验错误(422) → **数组**：`{"detail": [{"loc": ["body","query"], "msg": "...", ...}]}`
 *
 * 数组被直接塞进 `ElMessage.error()` / `new Error()` 时会被 `String()` 成
 * `[object Object]`。用户在问答页粘贴超过 2000 字（`AskRequest.query` 有
 * `max_length=2000`）就能立刻复现 —— 界面上只有一句 `[object Object]`，
 * 完全不知道哪里填错了。
 *
 * 所以把"从任意错误对象里抠出一句人话"收敛到这一个模块，调用点只传兜底文案。
 */

/** 把 `detail`（字符串 / 数组 / 对象 / 任意）变成一句可读中文。 */
export function detailToMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string") {
    return detail.trim() || fallback;
  }

  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object") {
          const rec = item as Record<string, unknown>;
          // loc 形如 ["body", "query"]：去掉 "body" 前缀，剩下的才是用户看得懂的字段名
          const loc = Array.isArray(rec.loc)
            ? rec.loc.filter((p) => p !== "body" && p !== "query").join(".")
            : "";
          const msg = typeof rec.msg === "string" ? rec.msg : "";
          if (loc && msg) return `${loc} ${msg}`;
          if (msg) return msg;
        }
        return "";
      })
      .filter(Boolean);
    if (parts.length) return parts.join("；");
    return fallback;
  }

  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      return fallback;
    }
  }

  return fallback;
}

/** 宽松解析 JSON 文本，失败返回 null（错误响应体不一定是 JSON）。 */
export function safeParseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/** 用户主动取消（点"停止"、切页面 abort）——不是错误，不该标红。 */
export function isAbortError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const rec = error as { name?: string; code?: string };
  return rec.name === "AbortError" || rec.code === "ERR_CANCELED";
}

/**
 * 从任意 thrown 值里取出一句给用户看的错误信息。
 *
 * 覆盖四种来源：axios 错误（带 response）、`fetch` 手动抛的 Error、
 * 网络不通（无 response）、主动取消。
 *
 * `fallback` 是调用点为自己这一处准备的文案，**只在服务端返回 4xx
 * 却没给可读消息时生效**（例如 401 空 body）。服务端给了明确消息、或
 * 是 5xx 故障、或压根连不上时，都用更准确的内置文案，不套用 fallback。
 */
export function extractErrorMessage(error: unknown, fallback = "请求失败，请稍后重试"): string {
  if (isAbortError(error)) {
    return "请求已取消";
  }

  const response = (error as { response?: { data?: unknown; status?: number } })?.response;
  if (response) {
    const raw = typeof response.data === "string" ? safeParseJson(response.data) : response.data;
    const detail = (raw as { detail?: unknown })?.detail ?? raw;
    const message = detailToMessage(detail, "");
    if (message) return message;
    if (response.status) {
      // 服务端给了状态码但消息不可读（空 body / nginx 错误页）：
      // 4xx 是「这次的请求本身有问题」，调用点特意传的兜底文案更像人话
      // （如「登录失败，请检查用户名和密码」）；
      // 5xx 是服务端故障，套用调用点文案会误导 —— 保留状态码。
      return response.status < 500 ? fallback || `请求失败 (${response.status})` : `请求失败 (${response.status})`;
    }
    // 有 response 却没有 status（手工构造的错误对象）：兜底文案是唯一可用的信息
    return fallback;
  }

  if (error instanceof Error && error.message && error.message !== "Network Error") {
    return error.message;
  }

  // axios 在连接失败时只给 "Network Error" 且没有 response：这句对用户毫无信息量
  return "无法连接服务器，请确认后端已启动";
}
