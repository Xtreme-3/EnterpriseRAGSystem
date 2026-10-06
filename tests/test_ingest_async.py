"""K4：摄取异步化与进度反馈。

覆盖：上传立即 202 / stage 递进 / total_units 口径 / 失败路径 / 重试 / 权限 /
并发 / 重启自愈 / 失败不留残留向量。
"""
from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.models import Document, IngestionJob
from app.main import app
from tests.ingest_helpers import auth, upload_and_wait, upload_raw, wait_document

# 约 4000 字 → 默认 chunk_size 800 + overlap 120 下会产生多块，
# 这样 done_units / total_units 才有意义（单块文档进度恒为 1/1，测不出什么）
LONG_TEXT = (
    "差旅住宿标准：一线城市每晚不超过六百元，其他城市每晚不超过四百元。"
    "交通费用凭票据实报销，市内交通费按每天五十元标准包干。"
    "餐饮补贴：一线城市每天一百二十元，其他城市每天九十元。\n"
) * 40


class _SlowEmbedding:
    """给 mock embedding 套一层：强制小批量 + 每批 sleep。

    **为什么需要它**：mock 是瞬时返回的，整个任务几毫秒就跑完 —— 阶段推进根本
    来不及被轮询观察到，那样"能看到 stage 从 parsing 递进到 done"这条验收标准
    就只能靠"最终态是 done"糊过去。加上延迟后中间态才是可观测的、断言才有牙齿。
    """

    def __init__(self, inner: Any, delay: float = 0.15, batch: int = 1) -> None:
        self._inner, self._delay, self._batch = inner, delay, batch

    @property
    def dim(self) -> int:
        return self._inner.dim

    @property
    def max_batch(self) -> int:
        return self._batch

    def embed(self, texts: list[str]) -> list[list[float]]:
        time.sleep(self._delay)
        return self._inner.embed(texts)


class _FailingEmbedding:
    """每次 embed 都抛错 —— 用来制造"通过了解析、倒在向量化"的失败。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @property
    def dim(self) -> int:
        return self._inner.dim

    @property
    def max_batch(self) -> int:
        return 8

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding 服务暂不可用")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _setup(client: TestClient, username: str = "test_k4") -> tuple[str, int]:
    client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    token = client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    ).json()["access_token"]
    kb_id = client.post(
        "/api/kbs", json={"name": f"{username}_kb"}, headers=auth(token)
    ).json()["id"]
    return token, kb_id


def _poll_stages(client: TestClient, doc_id: int, token: str, timeout: float = 15.0):
    """高频轮询，收集观察到的全部 stage，返回最终文档 JSON。"""
    seen: list[str] = []
    final: dict[str, Any] | None = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = client.get(f"/api/documents/{doc_id}", headers=auth(token)).json()
        stage = (d.get("job") or {}).get("stage")
        if stage and (not seen or seen[-1] != stage):
            seen.append(stage)
        if d["status"] in ("indexed", "failed"):
            final = d
            break
        time.sleep(0.01)
    assert final is not None, f"未在 {timeout}s 内收敛，已观察：{seen}"
    return seen, final


# ---- 1. 上传立即返回 ----

def test_upload_returns_202_immediately(client: TestClient) -> None:
    """上传不得阻塞：立刻 202 + pending。"""
    token, kb_id = _setup(client)

    t0 = time.perf_counter()
    resp = upload_raw(client, kb_id, token, "big.txt", LONG_TEXT.encode("utf-8"))
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert body["chunk_count"] == 0
    assert body["job"] is not None
    assert body["job"]["stage"] == "pending"
    assert body["job"]["total_units"] == 0
    # 同步时代这里要等完整条流水线；异步后只剩建记录 + 落盘 + 投递
    assert elapsed_ms < 300, f"上传应立即返回，实测 {elapsed_ms:.0f}ms"

    wait_document(client, body["id"], token)


# ---- 2. 阶段递进 ----

def test_stage_progresses_to_done(client: TestClient) -> None:
    """轮询能看到中间态，最终 done。"""
    token, kb_id = _setup(client)
    # 换成慢速 embedding，否则 mock 太快、中间态一闪而过
    client.app.state.ingestion.embedding = _SlowEmbedding(
        client.app.state.ingestion.embedding
    )

    resp = upload_raw(client, kb_id, token, "slow.txt", LONG_TEXT.encode("utf-8"))
    seen, final = _poll_stages(client, resp.json()["id"], token)

    assert final["status"] == "indexed", final
    assert final["job"]["stage"] == "done"
    assert "embedding" in seen, f"未观察到 embedding 中间态，实际观察：{seen}"
    assert final["job"]["done_units"] == final["job"]["total_units"] > 0


def test_total_units_matches_chunk_count(client: TestClient) -> None:
    """total_units 在 chunking 完成后回填，且与实际切片数一致。"""
    token, kb_id = _setup(client)
    doc = upload_and_wait(client, kb_id, token, "p.txt", LONG_TEXT.encode("utf-8"))

    assert doc["status"] == "indexed"
    assert doc["job"]["total_units"] == doc["chunk_count"] > 0
    assert doc["job"]["done_units"] == doc["chunk_count"]


# ---- 3. 失败路径 ----

def test_corrupt_file_fails_with_reason(client: TestClient) -> None:
    """解析期失败 → failed + error 非空 + job stage=failed。"""
    token, kb_id = _setup(client)
    doc = upload_and_wait(
        client, kb_id, token, "broken.docx", "这不是 zip，python-docx 打不开".encode("utf-8")
    )

    assert doc["status"] == "failed"
    assert doc["error"]
    assert doc["job"]["stage"] == "failed"
    assert doc["job"]["error"] == doc["error"]


def test_failure_leaves_no_partial_vectors(client: TestClient) -> None:
    """倒在向量化的文档不得在检索里露头，chunk_count 归零、向量也清干净。

    否则用户会看到一份"上传失败但还能被引用"的文档，且 G3 的
    「向量数 == chunk_count」一致性检查会因为半截数据漂移。
    """
    token, kb_id = _setup(client)
    client.app.state.ingestion.embedding = _FailingEmbedding(
        client.app.state.ingestion.embedding
    )

    doc = upload_and_wait(client, kb_id, token, "fail.txt", LONG_TEXT.encode("utf-8"))
    assert doc["status"] == "failed"
    assert doc["chunk_count"] == 0
    assert client.app.state.ingestion.vector_store.document_counts(kb_id) == {}


# ---- 4. 重试 ----

def test_retry_failed_document_succeeds(client: TestClient) -> None:
    """failed → 重试 202 → 重新跑成功。"""
    token, kb_id = _setup(client)
    inner = client.app.state.ingestion.embedding
    client.app.state.ingestion.embedding = _FailingEmbedding(inner)

    doc = upload_and_wait(client, kb_id, token, "retry.txt", LONG_TEXT.encode("utf-8"))
    assert doc["status"] == "failed"

    # 故障恢复（把 embedding 换回来），用户点「重试」
    client.app.state.ingestion.embedding = inner
    resp = client.post(f"/api/documents/{doc['id']}/retry", headers=auth(token))
    assert resp.status_code == 202
    assert resp.json()["id"] == doc["id"]

    final = wait_document(client, doc["id"], token)
    assert final["status"] == "indexed", final
    assert final["chunk_count"] > 0
    assert final["error"] is None


def test_retry_non_failed_returns_409(client: TestClient) -> None:
    """已成功 / 正在跑的文档重复入队会写出重复向量 → 409。"""
    token, kb_id = _setup(client)
    doc = upload_and_wait(client, kb_id, token, "ok.txt", LONG_TEXT.encode("utf-8"))
    assert doc["status"] == "indexed"

    resp = client.post(f"/api/documents/{doc['id']}/retry", headers=auth(token))
    assert resp.status_code == 409
    assert "failed" in resp.json()["detail"]


def test_retry_not_found_returns_404(client: TestClient) -> None:
    token, _ = _setup(client)
    assert client.post("/api/documents/99999/retry", headers=auth(token)).status_code == 404


def test_retry_requires_editor(client: TestClient) -> None:
    """viewer 能看文档但不能重试（重试是写操作）。"""
    token, kb_id = _setup(client)
    inner = client.app.state.ingestion.embedding
    client.app.state.ingestion.embedding = _FailingEmbedding(inner)
    doc = upload_and_wait(client, kb_id, token, "rbac.txt", LONG_TEXT.encode("utf-8"))
    client.app.state.ingestion.embedding = inner
    assert doc["status"] == "failed"

    # owner 把 bob 加成 viewer
    client.post("/api/auth/register", json={"username": "test_k4_viewer", "password": "secret123"})
    client.post(
        f"/api/kbs/{kb_id}/members",
        json={"username": "test_k4_viewer", "role": "viewer"},
        headers=auth(token),
    )
    viewer = client.post(
        "/api/auth/login", json={"username": "test_k4_viewer", "password": "secret123"}
    ).json()["access_token"]

    resp = client.post(f"/api/documents/{doc['id']}/retry", headers=auth(viewer))
    assert resp.status_code == 403


def test_retry_other_users_document_forbidden(client: TestClient) -> None:
    token_a, kb_id = _setup(client, "test_k4_own_a")
    inner = client.app.state.ingestion.embedding
    client.app.state.ingestion.embedding = _FailingEmbedding(inner)
    doc = upload_and_wait(client, kb_id, token_a, "other.txt", LONG_TEXT.encode("utf-8"))
    client.app.state.ingestion.embedding = inner
    assert doc["status"] == "failed"

    client.post("/api/auth/register", json={"username": "test_k4_own_b", "password": "secret123"})
    token_b = client.post(
        "/api/auth/login", json={"username": "test_k4_own_b", "password": "secret123"}
    ).json()["access_token"]

    resp = client.post(f"/api/documents/{doc['id']}/retry", headers=auth(token_b))
    assert resp.status_code == 403


# ---- 5. 并发 ----

def test_concurrent_uploads_all_succeed(client: TestClient) -> None:
    """同时上传 3 份：执行器上限内排队，各自状态不互相覆盖。

    注意断言的是"每份文档的 job 进度与**它自己**的切片数一致" —— 并发 bug 的
    典型表现正是 job 行被另一个任务串了（done_units 张冠李戴）。
    """
    token, kb_id = _setup(client)
    payloads = [
        (f"c{i}.txt", (f"第{i}份文档的差旅住宿标准：一线城市六百元。" * 40).encode("utf-8"))
        for i in range(3)
    ]

    doc_ids = [upload_raw(client, kb_id, token, name, body).json()["id"] for name, body in payloads]
    finals = [wait_document(client, did, token) for did in doc_ids]

    for doc in finals:
        assert doc["status"] == "indexed", doc
        assert doc["job"]["stage"] == "done"
        assert doc["job"]["total_units"] == doc["chunk_count"] > 0
    # 三份互不相同（时间倒序），确认没有互相覆盖
    assert len({d["id"] for d in finals}) == 3


# ---- 6. 重启自愈 ----

def test_startup_marks_stale_jobs_failed(tmp_path, monkeypatch) -> None:
    """进程内执行器随进程消失 —— 重启后卡住的 job 必须显式置 failed，
    否则前端永远转圈且没有任何出口。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    with TestClient(app, raise_server_exceptions=False) as c:
        token, kb_id = _setup(c, "test_k4_stale")
        doc = upload_and_wait(c, kb_id, token, "stale.txt", LONG_TEXT.encode("utf-8"))

    # 模拟"跑到一半进程被杀"：直接把 job 与 document 拨回非终态
    with app.state.session_factory() as db:
        job = db.query(IngestionJob).filter(IngestionJob.document_id == doc["id"]).first()
        job.stage = "embedding"
        job.done_units = 1
        db.get(Document, doc["id"]).status = "processing"
        db.commit()

    # 重启：lifespan 里的 fail_stale_jobs 应当把它收掉
    with TestClient(app, raise_server_exceptions=False) as c2:
        d = c2.get(f"/api/documents/{doc['id']}", headers=auth(token)).json()
    assert d["status"] == "failed"
    assert d["job"]["stage"] == "failed"
    assert "重启" in (d["job"]["error"] or "")


# ---- 7. batch 调参的契约 ----

def test_embed_batch_matches_provider_ceiling() -> None:
    """批次大小只有**一个**真相源：provider 的 max_batch。

    摄取流水线按它分批上报进度，provider 内部也按它切 HTTP 请求 —— 两处各写一个
    数字必然会漂移（改了这边忘了那边 → 进度条走一半就不再动）。
    """
    from app.providers.openai_compat import _EMBED_BATCH, OpenAICompatEmbedding
    from app.providers.mock import MockEmbedding

    assert _EMBED_BATCH == 25, "百炼单次上限 25；换供应商要按对方上限改这个值"
    emb = OpenAICompatEmbedding(base_url="http://x", api_key="k", model="m", dim=8)
    assert emb.max_batch == _EMBED_BATCH
    # 未覆写的实现继承默认值，不会因为新增属性而报错
    assert MockEmbedding().max_batch == 16


def test_embed_round_trips_shrink_with_larger_batch() -> None:
    """`_EMBED_BATCH` 调参的**实测**依据：一份 400 切片文档的 embedding 往返次数。

    数的是**往返次数**——这正是 `_EMBED_BATCH` 唯一影响的东西（分批只改串行往返数，
    不改进单个请求的耗时）。所以这里不需要真接口，用计数桩替换
    `client.embeddings.create` 即可，且结果完全确定。

    卡片要求这条附实测对比，而不是写"应该能快一点"。
    """
    from types import SimpleNamespace

    from app.providers.openai_compat import OpenAICompatEmbedding

    emb = OpenAICompatEmbedding(base_url="http://x", api_key="k", model="m", dim=4)
    calls: list[list[str]] = []

    def fake_create(*, model, input):  # noqa: A002 - 与 SDK 关键字签名一致
        calls.append(list(input))
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[0.0] * 4) for i in range(len(input))]
        )

    emb._client = SimpleNamespace(embeddings=SimpleNamespace(create=fake_create))
    emb.embed([f"t{i}" for i in range(400)])

    assert sum(len(c) for c in calls) == 400  # 一条不丢
    assert max(len(c) for c in calls) == 25  # 不超接口上限
    assert len(calls) == 16  # 400 / 25 → 16 次往返
    # 对照旧值：16 条/批 需要 25 次 → 减少 9 次（36%）
    assert -(-400 // 16) == 25
