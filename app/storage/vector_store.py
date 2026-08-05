"""向量库抽象：接口 + ChromaDB 实现。

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
        """余弦相似度检索 top_k，按相似度降序。"""

    @abstractmethod
    def delete_document(self, kb_id: int, document_id: int) -> None:
        """删除某文档的所有切片。"""

    @abstractmethod
    def delete_collection(self, kb_id: int) -> None:
        """删除整个知识库的向量集合。"""


class ChromaVectorStore(VectorStore):
    """基于 ChromaDB PersistentClient 的本地文件持久化实现。"""

    def __init__(self, persist_dir: Path) -> None:
        import chromadb

        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._collections: dict[int, object] = {}

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
            hits.append(
                ScoredChunk(
                    document_id=int(md.get("document_id", 0)),
                    kb_id=int(md.get("kb_id", kb_id)),
                    chunk_index=int(md.get("chunk_index", 0)),
                    content=res["documents"][0][i],
                    score=1.0 - res["distances"][0][i],  # 余弦距离 → 相似度
                )
            )
        return hits

    def delete_document(self, kb_id: int, document_id: int) -> None:
        self._collection(kb_id).delete(where={"document_id": document_id})

    def delete_collection(self, kb_id: int) -> None:
        self._client.delete_collection(self._coll_name(kb_id))
        self._collections.pop(kb_id, None)


def build_vector_store(settings: Settings | None = None) -> VectorStore:
    s = settings or get_settings()
    if s.vector_store == "chroma":
        return ChromaVectorStore(s.chroma_dir)
    raise ValueError(f"未知的 VECTOR_STORE: {s.vector_store}（当前支持: chroma）")
