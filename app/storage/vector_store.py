"""向量库抽象：接口 + ChromaDB 实现 + pgvector 实现。

上层只依赖 VectorStore 接口，后续替换为 pgvector / LanceDB 无需改动检索与入库代码。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, get_settings


@dataclass
class ChunkToIndex:
    """待写入向量库的一个切片。"""

    id: str  # 形如 "3:0"（document_id:chunk_index），保证可去重、可定位
    content: str
    vector: list[float]
    document_id: int
    kb_id: int
    chunk_index: int


@dataclass
class ScoredChunk:
    """检索命中的切片。"""

    document_id: int
    kb_id: int
    chunk_index: int
    content: str
    score: float


class VectorStore(ABC):
    """每个知识库对应一个命名集合/表。"""

    @abstractmethod
    def ensure_collection(self, kb_id: int, dim: int) -> None:
        """确保集合存在（幂等），首次创建时校验/固定维度。"""

    @abstractmethod
    def add(self, kb_id: int, chunks: list[ChunkToIndex]) -> None:
        """写入（或覆盖）切片。"""

    @abstractmethod
    def search(self, kb_id: int, vector: list[float], top_k: int) -> list[ScoredChunk]:
        """余弦相似度检索，返回相关命中（相似度 > similarity_threshold）按相似度降序，最多 top_k 条。

        过滤低于阈值的向量：与 query 无关的切片不视为命中，
        避免噪声污染质检/溯源/问答日志的命中统计。返回条数可能少于 top_k。
        """

    @abstractmethod
    def search_lexical(self, kb_id: int, query: str, top_k: int) -> list[ScoredChunk]:
        """关键词（全文）检索（H1），按词相关度降序返回最多 top_k 条。

        无可用检索词（空/单字符 query）返回空列表；分数为词相关度（无量纲，
        pgvector 用 ts_rank，chroma 用词频），由混合检索融合层做归一化。
        """

    @abstractmethod
    def delete_document(self, kb_id: int, document_id: int) -> None:
        """删除某文档的所有切片。"""

    @abstractmethod
    def delete_collection(self, kb_id: int) -> None:
        """删除整个知识库的向量集合。"""

    @abstractmethod
    def document_counts(self, kb_id: int) -> dict[int, int]:
        """返回知识库内每个文档在向量库中的实际向量数 {document_id: count}。

        用于向量一致性检查（G3）：对比元数据 chunk_count 与实际入库向量数。
        """


class ChromaVectorStore(VectorStore):
    """基于 ChromaDB PersistentClient 的本地文件持久化实现。"""

    def __init__(self, persist_dir: Path, min_score: float = 0.1) -> None:
        import chromadb

        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._collections: dict[int, object] = {}
        self._min_score = min_score

    @staticmethod
    def _coll_name(kb_id: int) -> str:
        return f"kb_{kb_id}"

    def _collection(self, kb_id: int):
        col = self._collections.get(kb_id)
        if col is None:
            col = self._client.get_or_create_collection(
                name=self._coll_name(kb_id), metadata={"hnsw:space": "cosine"}
            )
            self._collections[kb_id] = col
        return col

    def ensure_collection(self, kb_id: int, dim: int) -> None:
        self._collection(kb_id)

    def add(self, kb_id: int, chunks: list[ChunkToIndex]) -> None:
        if not chunks:
            return
        col = self._collection(kb_id)
        col.upsert(
            ids=[c.id for c in chunks],
            embeddings=[c.vector for c in chunks],
            documents=[c.content for c in chunks],
            metadatas=[
                {
                    "document_id": c.document_id,
                    "kb_id": c.kb_id,
                    "chunk_index": c.chunk_index,
                }
                for c in chunks
            ],
        )

    def search(self, kb_id: int, vector: list[float], top_k: int) -> list[ScoredChunk]:
        col = self._collection(kb_id)
        res = col.query(
            query_embeddings=[vector],
            n_results=max(top_k, 1),
            include=["documents", "metadatas", "distances"],
        )
        hits: list[ScoredChunk] = []
        for i in range(len(res["ids"][0])):
            md = res["metadatas"][0][i] or {}
            score = 1.0 - res["distances"][0][i]  # 余弦距离 → 相似度
            if score <= self._min_score:
                continue  # 低于命中阈值（含无关噪声）不算命中
            hits.append(
                ScoredChunk(
                    document_id=int(md.get("document_id", 0)),
                    kb_id=int(md.get("kb_id", kb_id)),
                    chunk_index=int(md.get("chunk_index", 0)),
                    content=res["documents"][0][i],
                    score=score,
                )
            )
        return hits

    def search_lexical(self, kb_id: int, query: str, top_k: int) -> list[ScoredChunk]:
        """关键词（全文）检索（H1）：Python 侧大小写不敏感子串匹配，按命中词数打分。

        离线/开发兜底路径（小型集合全量过滤，性能可接受）；生产精确检索走
        pgvector tsvector（见 PgVectorStore）。tokenize 已统一小写，这里对切片
        内容同样小写后匹配，保证型号/代号（A12、GB 4806）大小写不敏感。
        无可用检索词（空/单字符 query）返回空列表。
        """
        from app.rag.hybrid import tokenize

        terms = tokenize(query)
        if not terms:
            return []

        col = self._collection(kb_id)
        res = col.get(where={"kb_id": kb_id}, include=["documents", "metadatas"])
        scored: list[tuple[float, int, int, str]] = []
        for i in range(len(res["ids"])):
            content = res["documents"][i] or ""
            md = res["metadatas"][i] or {}
            matched = sum(1 for t in terms if t in content.lower())
            if matched:
                scored.append(
                    (
                        float(matched),
                        int(md.get("document_id", 0)),
                        int(md.get("chunk_index", 0)),
                        content,
                    )
                )

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            ScoredChunk(
                document_id=doc_id,
                kb_id=kb_id,
                chunk_index=chunk_idx,
                content=content,
                score=score,
            )
            for score, doc_id, chunk_idx, content in scored[:top_k]
        ]

    def delete_document(self, kb_id: int, document_id: int) -> None:
        self._collection(kb_id).delete(where={"document_id": document_id})

    def delete_collection(self, kb_id: int) -> None:
        self._client.delete_collection(self._coll_name(kb_id))
        self._collections.pop(kb_id, None)

    def document_counts(self, kb_id: int) -> dict[int, int]:
        col = self._collection(kb_id)
        res = col.get(where={"kb_id": kb_id}, include=["metadatas"])
        counts: dict[int, int] = {}
        for md in res.get("metadatas") or []:
            if md is None:
                continue
            doc_id = int(md.get("document_id", 0))
            counts[doc_id] = counts.get(doc_id, 0) + 1
        return counts


def build_vector_store(settings: Settings | None = None) -> VectorStore:
    s = settings or get_settings()
    if s.vector_store == "chroma":
        return ChromaVectorStore(s.chroma_dir, s.similarity_threshold)
    if s.vector_store == "pgvector":
        from app.storage.pgvector_store import PgVectorStore

        return PgVectorStore(s)
    raise ValueError(f"未知的 VECTOR_STORE: {s.vector_store}（当前支持: chroma, pgvector）")
