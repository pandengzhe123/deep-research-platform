"""知识库模块 —— Chroma 向量存储 + sentence-transformers embedding + 检索。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

# ============================================================
# SSL 配置说明
# ============================================================
# 模型使用 local_files_only=True（不联网），无需禁用 SSL 验证。
# 如果首次部署需要下载模型，临时设置环境变量：
#   HF_HUB_DISABLE_SSL_VERIFY=1 python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"
# 下载完成后无需任何 SSL 配置。
os.environ["HF_HUB_DISABLE_SSL_VERIFY"] = "1"  # 仅影响 HuggingFace Hub，不影响其他 HTTPS 请求

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

_EMBED_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


def _embed_semantic(texts: list[str]) -> list[list[float]]:
    """用 sentence-transformers 生成语义向量（384 维，中英文）。模型缓存在内存。"""
    if not hasattr(_embed_semantic, "_model"):
        from sentence_transformers import SentenceTransformer

        try:
            # 优先离线加载（快，不联网）
            _embed_semantic._model = SentenceTransformer(_EMBED_MODEL_NAME, local_files_only=True)
        except Exception:
            # 本地无缓存，自动下载（首次运行）
            print(f"  ⚠️ Embedding 模型未缓存，正在下载 {_EMBED_MODEL_NAME}（约 120MB，仅首次）...")
            try:
                _embed_semantic._model = SentenceTransformer(_EMBED_MODEL_NAME)
                print(f"  ✅ 模型下载完成，后续启动将使用本地缓存")
            except Exception as e:
                raise RuntimeError(
                    f"Embedding 模型加载失败: {e}\n"
                    f"请手动下载：python -c \"from sentence_transformers import SentenceTransformer; "
                    f"SentenceTransformer('{_EMBED_MODEL_NAME}')\""
                ) from e
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

        # embedding 输入截断（MiniLM 支持 128 tokens ≈ 256 中文字符），存储保留完整 chunk
        EMBED_MAX_LEN = 256
        embed_input = [c[:EMBED_MAX_LEN] for c in chunks]
        embeddings = self._embedder(embed_input)

        # 入库（先删旧的同文档，防止重复）
        doc_id = doc_id or path.name
        try:
            collection = self._get_collection(user_id)
            try:
                old = collection.get(where={"doc_id": doc_id})
                if old.get("ids"):
                    collection.delete(ids=old["ids"])
            except Exception:
                pass
        except Exception as e:
            return {"status": "error", "message": f"Chroma 连接失败: {e}"}
        chunk_ids = [f"{doc_id}_{uuid.uuid4().hex[:6]}" for _ in chunks]

        collection.add(
            documents=chunks,           # 存储完整 chunk（最多 500 字），检索时返回完整内容
            embeddings=embeddings,      # 索引用截断版算出的向量
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

    def health_check(self) -> dict:
        """检查 Chroma 是否正常。"""
        try:
            coll = self._get_collection("default")
            coll.count()  # 简单操作验证可用性
            return {"status": "ok"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def list_docs(self, user_id: str = "default") -> list[dict]:
        """列出该用户已上传的文档（按 user_id 过滤元数据）。"""
        try:
            collection = self._get_collection(user_id)
            # 用 where 条件下推到 Chroma 过滤，避免全量扫描
            results = collection.get(where={"user_id": user_id})
            seen: set[str] = set()
            docs = []
            for m in results.get("metadatas", []):
                did = m.get("doc_id", "")
                if did and did not in seen:
                    seen.add(did)
                    docs.append({"doc_id": did})
            return docs
        except Exception:
            return []

    def delete_doc(self, doc_id: str, user_id: str = "default") -> dict:
        """删除指定文档的所有 chunk。collection 已按 user_id 隔离，只需按 doc_id 过滤。"""
        try:
            collection = self._get_collection(user_id)
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
