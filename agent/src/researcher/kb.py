"""知识库模块 —— Chroma 向量存储 + sentence-transformers embedding + 检索。

设计要点：
- 按段落→句子→字符三级切分，不用 LangChain
- 元数据标记 user_id + doc_id，天然支持多租户
- embedding 模型本地运行，免费
"""

from __future__ import annotations

import uuid
from pathlib import Path

import chromadb

# ============================================================
# 切块策略
# ============================================================

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """段落优先 → 句子 → 字符，逐级降级切分。"""
    chunks: list[str] = []

    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) <= chunk_size:
            chunks.append(para)
        else:
            # 段落太长，按句子切
            for sent in para.replace("。", "。\n").replace("！", "！\n").replace("？", "？\n").replace(". ", ".\n").split("\n"):
                sent = sent.strip()
                if not sent:
                    continue
                if len(sent) <= chunk_size:
                    chunks.append(sent)
                else:
                    # 句子还太长，硬切 + overlap
                    for i in range(0, len(sent), chunk_size - overlap):
                        chunks.append(sent[i:i + chunk_size])
    return chunks


# ============================================================
# 文件读取
# ============================================================

def read_file(file_path: Path) -> str:
    """读取 TXT/MD/PDF，返回纯文本。"""
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        import fitz  # PyMuPDF
        doc = fitz.open(str(file_path))
        return "\n\n".join(page.get_text() for page in doc)

    elif suffix in (".txt", ".md"):
        return file_path.read_text(encoding="utf-8", errors="ignore")

    else:
        raise ValueError(f"不支持的文件类型: {suffix}")


# ============================================================
# 知识库
# ============================================================

def _embed_semantic(texts: list[str]) -> list[list[float]]:
    """用 sentence-transformers 生成语义向量（384 维，中英文）。模型缓存在内存。"""
    import os
    import ssl

    ssl._create_default_https_context = ssl._create_unverified_context
    os.environ.setdefault("CURL_CA_BUNDLE", "")
    os.environ.setdefault("SSL_CERT_FILE", "")

    if not hasattr(_embed_semantic, "_model"):
        from sentence_transformers import SentenceTransformer

        _embed_semantic._model = SentenceTransformer(
            "paraphrase-multilingual-MiniLM-L12-v2"
        )
    return _embed_semantic._model.encode(texts, show_progress_bar=False).tolist()


class KnowledgeBase:
    """Chroma 向量库封装。使用 sentence-transformers 语义 embedding。"""

    def __init__(self, persist_dir: str = "./chroma_data"):
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._embedder = _embed_semantic
        self._collections: dict[str, object] = {}

    # ================================================================
    # 文档管理
    # ================================================================

    def _collection_name(self, user_id: str) -> str:
        return f"kb_{user_id}"

    def _get_collection(self, user_id: str):
        name = self._collection_name(user_id)
        if name not in self._collections:
            self._collections[name] = self._client.get_or_create_collection(name)
        return self._collections[name]

    def ingest(
        self,
        file_path: str,
        user_id: str = "default",
        doc_id: str | None = None,
    ) -> dict:
        """上传文件 → 切块 → embedding → 入库。"""
        path = Path(file_path)
        if not path.exists():
            return {"status": "error", "message": f"文件不存在: {file_path}"}

        text = read_file(path)
        chunks = chunk_text(text)

        if not chunks:
            return {"status": "error", "message": "文件内容为空"}

        # 截断到模型最大输入（~100 中文字符）
        max_len = 100
        truncated = [c[:max_len] for c in chunks]

        # embedding
        embeddings = self._embedder(truncated)

        # 入库（先删旧的同文档，防止重复）
        doc_id = doc_id or path.name
        collection = self._get_collection(user_id)
        try:
            old = collection.get(where={"doc_id": doc_id})
            if old["ids"]:
                collection.delete(ids=old["ids"])
        except Exception:
            pass
        chunk_ids = [f"{doc_id}_{uuid.uuid4().hex[:6]}" for _ in chunks]

        collection.add(
            documents=truncated,
            embeddings=embeddings,
            metadatas=[{"user_id": user_id, "doc_id": doc_id} for _ in chunks],
            ids=chunk_ids,
        )

        return {
            "status": "ok",
            "doc_id": doc_id,
            "chunks": len(chunks),
            "characters": len(text),
        }

    def search(
        self,
        query: str,
        user_id: str = "default",
        doc_ids: Optional[list[str]] = None,
        n_results: int = 5,
    ) -> str:
        """向量检索，返回格式化文本。"""
        try:
            collection = self._get_collection(user_id)

            # 构建 where 过滤
            where: dict | None = None
            if doc_ids:
                where = {
                    "user_id": user_id,
                    "doc_id": {"$in": doc_ids},
                }

            # 自己算 query 向量，不让 Chroma 调默认模型（避免下载）
            query_emb = self._embedder([query])
            results = collection.query(
                query_embeddings=query_emb,
                n_results=n_results,
                where=where,
            )

            # 格式化返回
            if not results.get("documents") or not results["documents"][0]:
                return "知识库中未找到相关信息。"

            lines = ["# 知识库检索结果\n"]
            for i, (doc, meta, dist) in enumerate(zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )):
                similarity = max(0, 1 - dist) if dist else 1.0
                source = meta.get("doc_id", "未知")
                lines.append(f"\n--- 来源 {i+1}: {source}（相关度 {similarity:.0%}）---")
                lines.append(doc)
                lines.append("")

            return "\n".join(lines)

        except Exception as e:
            return f"知识库检索失败: {e}"

    def list_docs(self, user_id: str = "default") -> list[dict]:
        """列出用户已上传的文档。"""
        try:
            collection = self._get_collection(user_id)
            metadatas = collection.get()["metadatas"]
            seen: set[str] = set()
            result = []
            for m in metadatas:
                did = m.get("doc_id", "")
                if did and did not in seen:
                    seen.add(did)
                    result.append({"doc_id": did})
            return result
        except Exception:
            return []

    def delete_doc(self, doc_id: str, user_id: str = "default") -> dict:
        """删除指定文档的所有 chunk。"""
        try:
            collection = self._get_collection(user_id)
            # 找到该文档的所有 chunk id
            results = collection.get(where={"doc_id": doc_id})
            ids = results.get("ids", [])
            if ids:
                collection.delete(ids=ids)
                return {"status": "ok", "deleted_chunks": len(ids)}
            return {"status": "ok", "deleted_chunks": 0}
        except Exception as e:
            return {"status": "error", "message": str(e)}


# 全局单例
kb = KnowledgeBase()
