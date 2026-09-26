import { describe, expect, it } from "vitest";
import {
  detailToMessage,
  extractErrorMessage,
  isAbortError,
  safeParseJson,
} from "@/api/error";

const FALLBACK = "兜底文案";

describe("detailToMessage", () => {
  it("字符串 detail 原样返回", () => {
    expect(detailToMessage("问答服务暂时不可用", FALLBACK)).toBe("问答服务暂时不可用");
  });

  it("空串或纯空白走兜底", () => {
    expect(detailToMessage("", FALLBACK)).toBe(FALLBACK);
    expect(detailToMessage("   ", FALLBACK)).toBe(FALLBACK);
  });

  // bugfix-log #36：422 的 detail 是数组，直接塞进提示框会渲染成 [object Object]
  it("422 数组带字段名时渲染成「字段名 说明」", () => {
    const got = detailToMessage(
      [{ loc: ["body", "password"], msg: "String should have at most 72 characters" }],
      FALLBACK
    );
    expect(got).toBe("password String should have at most 72 characters");
    expect(got).not.toContain("[object Object]");
  });

  it("422 数组 loc 只剩 body/query 时退化为纯消息", () => {
    expect(detailToMessage([{ loc: ["body", "query"], msg: "too long" }], FALLBACK)).toBe(
      "too long"
    );
  });

  it("422 数组多条用「；」连接", () => {
    const got = detailToMessage(
      [
        { loc: ["body", "username"], msg: "Field required" },
        { loc: ["body", "password"], msg: "Field required" },
      ],
      FALLBACK
    );
    expect(got).toBe("username Field required；password Field required");
  });

  it("数组元素既非字符串也无 msg 时被跳过，全空则兜底", () => {
    expect(detailToMessage([null, 123], FALLBACK)).toBe(FALLBACK);
    expect(detailToMessage([], FALLBACK)).toBe(FALLBACK);
  });

  it("detail 为 undefined / null / 数字时走兜底", () => {
    expect(detailToMessage(undefined, FALLBACK)).toBe(FALLBACK);
    expect(detailToMessage(null, FALLBACK)).toBe(FALLBACK);
    expect(detailToMessage(0, FALLBACK)).toBe(FALLBACK);
  });

  it("对象 detail 序列化后返回", () => {
    expect(detailToMessage({ code: "E_MODEL_DOWN" }, FALLBACK)).toBe('{"code":"E_MODEL_DOWN"}');
  });
});

describe("safeParseJson", () => {
  it("合法 JSON 解析成对象", () => {
    expect(safeParseJson('{"detail":"x"}')).toEqual({ detail: "x" });
  });

  it("非 JSON 文本返回 null 而不抛异常", () => {
    expect(safeParseJson("<html>502 Bad Gateway</html>")).toBeNull();
  });
});

describe("isAbortError", () => {
  // bugfix-log #35：用户点「停止」不是失败，不该标红
  it("认 DOMException 的 AbortError", () => {
    expect(isAbortError({ name: "AbortError" })).toBe(true);
  });

  it("认 axios 取消的 ERR_CANCELED", () => {
    expect(isAbortError({ code: "ERR_CANCELED" })).toBe(true);
  });

  it("普通 Error 不算取消", () => {
    expect(isAbortError(new Error("boom"))).toBe(false);
  });

  it("非对象输入不抛异常", () => {
    expect(isAbortError(undefined)).toBe(false);
    expect(isAbortError(null)).toBe(false);
    expect(isAbortError("AbortError")).toBe(false);
  });
});

describe("extractErrorMessage", () => {
  it("主动取消返回「请求已取消」", () => {
    expect(extractErrorMessage({ name: "AbortError" }, FALLBACK)).toBe("请求已取消");
  });

  it("服务端字符串 detail 优先于兜底", () => {
    const err = { response: { status: 401, data: { detail: "用户名或密码错误" } } };
    expect(extractErrorMessage(err, FALLBACK)).toBe("用户名或密码错误");
  });

  it("422 数组 detail 不再渲染成 [object Object]", () => {
    const err = {
      response: { status: 422, data: { detail: [{ loc: ["body", "password"], msg: "too long" }] } },
    };
    const got = extractErrorMessage(err, FALLBACK);
    expect(got).toBe("password too long");
    expect(got).not.toContain("[object Object]");
  });

  it("响应体是 JSON 字符串时也解析", () => {
    const err = { response: { status: 400, data: '{"detail":"参数不合法"}' } };
    expect(extractErrorMessage(err, FALLBACK)).toBe("参数不合法");
  });

  it("响应体不是 JSON 时（如 nginx 错误页）退回状态码", () => {
    const err = { response: { status: 502, data: "<html>502 Bad Gateway</html>" } };
    expect(extractErrorMessage(err, FALLBACK)).toBe("请求失败 (502)");
  });

  // fallback 参数此前从未被读取：4xx 且消息不可读时，调用点文案更像人话
  it("4xx 但消息不可读时用调用点的兜底文案", () => {
    const err = { response: { status: 404, data: null } };
    expect(extractErrorMessage(err, "登录失败，请检查用户名和密码")).toBe(
      "登录失败，请检查用户名和密码"
    );
  });

  // 5xx 时兜底文案会误导（"请检查用户名和密码"与服务端故障无关），保留状态码
  it("5xx 时保留状态码，不套用调用点兜底文案", () => {
    const err = { response: { status: 500, data: null } };
    expect(extractErrorMessage(err, "登录失败，请检查用户名和密码")).toBe("请求失败 (500)");
  });

  it("axios 的 Network Error 给出可操作提示", () => {
    expect(extractErrorMessage(new Error("Network Error"), FALLBACK)).toBe(
      "无法连接服务器，请确认后端已启动"
    );
  });

  it("其它 Error 用自身 message", () => {
    expect(extractErrorMessage(new Error("boom"), FALLBACK)).toBe("boom");
  });

  it("非 Error 且无 response 时也给网络提示", () => {
    expect(extractErrorMessage("weird", FALLBACK)).toBe("无法连接服务器，请确认后端已启动");
    expect(extractErrorMessage(undefined, FALLBACK)).toBe("无法连接服务器，请确认后端已启动");
  });
});
