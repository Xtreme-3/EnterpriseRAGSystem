"""Docker 交付（经 nginx 反代）HTTP 层端到端冒烟。

与 ``scripts/demo.py`` 的区别：demo.py 在**进程内**直接 import app、走 SQLite 与
mock 供应商；本脚本走**真实 HTTP**，验证的是"容器起没起来、nginx 路由对不对、
SSE 有没有被缓冲"这一层。交付验收用本脚本，单机开发用 demo.py。

只依赖标准库，避免在交付验收环节引入额外依赖。

用法：
    python scripts/docker_smoke.py                    # 默认 http://localhost:8080
    python scripts/docker_smoke.py http://localhost:18080
    SMOKE_BASE=http://localhost:18080 python scripts/docker_smoke.py

环境变量：
    SMOKE_BASE  服务地址，默认 ``http://localhost:8080``
    SMOKE_USER  用户名（**必填**）
    SMOKE_PWD   密码（**必填**）

    账号密码故意不设默认值——本仓库会推到 GitHub / GitCode 两个公开远端，
    任何形式的凭据都不能落进版本控制。请在本地 shell 里临时传入，例如：
    ``SMOKE_PWD='xxx' python scripts/docker_smoke.py http://localhost:18080``

说明：会往 KB 里上传一份测试文档并在结束时**自动删除**，不留脏数据。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SMOKE_BASE", "http://localhost:8080")).rstrip("/")
# 凭据只从环境变量取，**不设默认值**：本仓库推到 GitHub / GitCode，不能带任何口令。
USER = os.environ.get("SMOKE_USER", "")
PWD = os.environ.get("SMOKE_PWD", "")
# 判断"SSE 有没有被反代缓冲"所需的最短总时长（ms）。低于它首字节与总时长都被连接
# 开销主导，比值没有区分力 —— 见第 6 步的说明。
MIN_STREAM_MS = int(os.environ.get("SMOKE_MIN_STREAM_MS", "200"))

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def call(method: str, path: str, token: str | None = None, body=None,
         raw: bytes | None = None, ctype: str | None = None):
    data = None
    headers = {}
    if raw is not None:
        data, headers["Content-Type"] = raw, (ctype or "application/octet-stream")
    elif body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def sse_stream(path: str, token: str, body: dict):
    """读 SSE，返回 (content-type, 事件块, 首字节 ms, 总时长 ms)。"""
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    t0 = time.perf_counter()
    ttfb = None
    chunks: list[str] = []
    with urllib.request.urlopen(req, timeout=300) as resp:
        ctype = resp.headers.get("content-type", "")
        buf = b""
        while True:
            line = resp.readline()
            if not line:
                break
            if ttfb is None:
                ttfb = (time.perf_counter() - t0) * 1000
            buf += line
            if line in (b"\n", b"\r\n"):
                chunks.append(buf.decode("utf-8", "replace").strip())
                buf = b""
    return ctype, chunks, ttfb, (time.perf_counter() - t0) * 1000


def main() -> int:
    if not USER or not PWD:
        print("缺少凭据：请设置 SMOKE_USER 与 SMOKE_PWD 环境变量（故意不设默认值，避免口令进仓库）。")
        return 2
    print(f"冒烟目标：{BASE}  用户：{USER}")

    print("=== 1. 健康检查 ===")
    st, _ = call("GET", "/health")
    check("GET /health", st == 200, f"http={st}")
    st, raw = call("GET", "/")
    check("GET /（SPA）", st == 200 and b"<div id=" in raw, f"http={st}")

    # 这两条专门防「被 SPA 兜底吃掉」：nginx 的 location / 用 try_files 把未匹配的
    # 路径一律回 index.html，于是 /docs 会返回**状态码 200 的前端首页** —— 只看 http_code
    # 完全发现不了。所以必须断言**内容特征**，光看 200 不算通过。
    print("=== 1b. API 文档在交付入口可达（没有被 SPA 兜底吃掉）===")
    st, raw = call("GET", "/openapi.json")
    paths: dict = {}
    if st == 200 and raw[:1] == b"{":
        paths = json.loads(raw).get("paths", {})
    check("GET /openapi.json 是真 OpenAPI 描述（不是 index.html）",
          bool(paths), f"http={st} paths={len(paths)}")
    st, raw = call("GET", "/docs")
    check("GET /docs 是 Swagger UI（不是 index.html）",
          st == 200 and b"SwaggerUIBundle" in raw,
          f"http={st} 含 SwaggerUIBundle={b'SwaggerUIBundle' in raw}")

    print("=== 2. 登录 ===")
    st, raw = call("POST", "/api/auth/login", body={"username": USER, "password": PWD})
    check("POST /api/auth/login", st == 200, f"http={st}")
    if st != 200:
        print("  " + raw.decode("utf-8", "replace")[:200])
        return 1
    token = json.loads(raw)["access_token"]

    print("=== 3. 知识库列表 ===")
    st, raw = call("GET", "/api/kbs", token)
    check("GET /api/kbs", st == 200, f"http={st}")
    kbs = json.loads(raw)
    items = kbs if isinstance(kbs, list) else kbs.get("items", kbs)
    check("至少可见一个知识库", len(items) > 0, f"{len(items)} 个")
    if not items:
        return 1
    kb_id = items[0]["id"]
    for kb in items:
        print(f"      kb{kb['id']} {kb.get('name')}  docs={kb.get('document_count', '?')}")

    print("=== 4. 上传文档并等待索引 ===")
    fname = "docker_smoke_差旅.txt"
    text = (
        "差旅报销标准\n\n"
        "住宿费：一线城市 600 元/晚，二线城市 450 元/晚，其他城市 350 元/晚。\n"
        "交通费：市内交通实报实销，需附票据。\n"
        "餐补：出差期间每日 100 元。\n"
    ).encode("utf-8")
    b = "----smoke9f2a"
    parts = (
        f"--{b}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode("utf-8") + text + f"\r\n--{b}--\r\n".encode()
    st, raw = call("POST", f"/api/kbs/{kb_id}/documents", token,
                   raw=parts, ctype=f"multipart/form-data; boundary={b}")
    check("POST .../documents", st in (200, 201, 202), f"http={st}")
    doc_id = json.loads(raw).get("id") if st in (200, 201, 202) else None
    if doc_id:
        final = None
        for _ in range(30):
            time.sleep(1)
            _, raw2 = call("GET", f"/api/kbs/{kb_id}/documents", token)
            docs = json.loads(raw2)
            docs = docs if isinstance(docs, list) else docs.get("items", docs)
            cur = next((d for d in docs if d.get("id") == doc_id), None)
            if cur and cur.get("status") in ("indexed", "failed"):
                final = cur
                break
        check("文档在 30s 内收敛为 indexed",
              bool(final and final["status"] == "indexed"),
              f"status={final['status'] if final else '超时'} "
              f"chunks={final.get('chunk_count') if final else '?'}")

    print("=== 5. 非流式问答 ===")
    question = "住宿费一线城市多少钱一晚？"
    st, raw = call("POST", f"/api/kbs/{kb_id}/conversations", token, body={"title": "容器冒烟"})
    conv_id = json.loads(raw).get("id") if st in (200, 201) else None
    st, raw = call("POST", f"/api/kbs/{kb_id}/ask", token,
                   body={"query": question, "top_k": 5, "conversation_id": conv_id})
    check("POST /ask", st == 200, f"http={st}")
    message_id = None
    if st == 200:
        ans = json.loads(raw)
        message_id = ans.get("message_id")
        check("返回 sources", len(ans.get("sources", [])) > 0,
              f"{len(ans.get('sources', []))} 条")
        check("引用无越界", ans.get("citation_issues") == [], str(ans.get("citation_issues")))
        check("答案命中 600", "600" in ans.get("answer", ""))
        print(f"      cache_hit={ans.get('cache_hit')} message_id={message_id}")

    print("=== 6. SSE 真流式问答 ===")
    try:
        ctype, chunks, ttfb, total = sse_stream(
            f"/api/kbs/{kb_id}/ask/stream", token,
            {"query": question, "top_k": 5, "conversation_id": conv_id})
        joined = "\n".join(chunks)
        print(f"      首字节={ttfb:.0f}ms 总时长={total:.0f}ms 事件块={len(chunks)}")
        check("content-type 是 text/event-stream", "text/event-stream" in ctype, ctype)
        check("含 sources 事件", "sources" in joined.lower())
        check("含 done 事件", "done" in joined.lower())
        check("答案命中 600", "600" in joined)

        # 判断「流有没有被 nginx 缓冲」只在生成耗时足够长时才有区分力：被缓冲的表现是
        # **首字节 ≈ 总时长**（整段答案攒到最后一次吐出）。但 mock 供应商是瞬时返回 ——
        # 答案本来就在同一毫秒内生成完毕，首字节与总时长**都只由连接开销主导**，
        # 比值毫无意义（实测撞到过 31ms / 46ms 这种"看起来像被缓冲"的噪声）。
        # 所以设一个下限，低于它只报告、不判定，避免拿假信号当真缺陷。
        # 换真实供应商（生成要数秒）后，这条才是有效的：首字节应 ≪ 总时长。
        if total < MIN_STREAM_MS:
            print(f"  [SKIP] 流未被缓冲 — 总时长仅 {total:.0f}ms（< {MIN_STREAM_MS}ms，"
                  f"mock 供应商瞬时返回），该判据在此无区分力；"
                  f"真实供应商下应看到首字节 ≪ 总时长")
        else:
            check("流未被缓冲（首字节 < 总时长一半）", ttfb < total * 0.5,
                  f"首字节={ttfb:.0f}ms 总时长={total:.0f}ms 事件块={len(chunks)}")
    except Exception as exc:  # noqa: BLE001
        check("SSE 请求成功", False, f"{type(exc).__name__}: {exc}")

    print("=== 7. K5/K6/K7/K9/G6 端点 ===")
    for path in ("/api/config/upload", "/api/config/chat",
                 f"/api/kbs/{kb_id}/dashboard", f"/api/kbs/{kb_id}/feedback",
                 f"/api/kbs/{kb_id}/qa-logs"):
        st, _ = call("GET", path, token)
        check(f"GET {path}", st == 200, f"http={st}")
    st, raw = call("GET", "/api/config/upload", token)
    exts = json.loads(raw).get("supported_exts", []) if st == 200 else []
    check("上传格式清单下发", len(exts) >= 5, str(exts))

    print("=== 8. 反馈落库（K5）===")
    if message_id is None:
        check("拿到 message_id", False, "跳过反馈")
    else:
        st, raw = call("POST", f"/api/kbs/{kb_id}/messages/{message_id}/feedback",
                       token, body={"rating": "up", "reason": None})
        check("POST .../feedback", st == 200, f"http={st}")
        st, raw = call("GET", f"/api/kbs/{kb_id}/feedback", token)
        fb = json.loads(raw) if st == 200 else []
        check("反馈可读回", len(fb) > 0, f"{len(fb)} 条")

    print("=== 9. 清理测试数据 ===")
    if doc_id:
        st, _ = call("DELETE", f"/api/documents/{doc_id}", token)
        check(f"删除测试文档 #{doc_id}", st == 200, f"http={st}")
    if conv_id:
        st, _ = call("DELETE", f"/api/kbs/{kb_id}/conversations/{conv_id}", token)
        check(f"删除测试会话 #{conv_id}", st in (200, 204), f"http={st}")

    print()
    if FAILURES:
        print(f"结果：{len(FAILURES)} 项失败 -> " + "; ".join(FAILURES))
        return 1
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
