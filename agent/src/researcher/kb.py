"""知识库模块 —— Chroma 向量存储 + sentence-transformers embedding + 检索。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

os.environ["HF_HUB_DISABLE_SSL_VERIFY"] = "1"

import chromadb

# ============================================================
# 切块策略
# ============================================================

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100, min_size: int = 300) -> list[str]:
    """段落优先 → 句子 → 字符，逐级降级切分。太短的 chunk 合并到前一个。"""
    chunks: list[str] = []

    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) <= chunk_size:
            chunks.append(para)
        else:
            for sent in para.replace("。", "。\n").replace("！", "！\n").replace("？", "？\n").replace(". ", ".\n").split("\n"):
                sent = sent.strip()
                if not sent:
                    continue
                if len(sent) <= chunk_size:
                    chunks.append(sent)
                else:
                    for i in range(0, len(sent), chunk_size - overlap):
                        chunks.append(sent[i:i + chunk_size])

    # 合并太短的 chunk 到前一个，保证每个 chunk 至少有 min_size 字
    merged = []
    for c in chunks:
        if merged and len(merged[-1]) < min_size:
            merged[-1] = merged[-1] + "\n" + c
        else:
            merged.append(c)
    return merged


# ============================================================
# 文件读取
# ============================================================

def read_file(file_path: Path) -> str:
    """读取 TXT/MD/PDF/DOCX，返回纯文本。"""
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        import fitz
        doc = fitz.open(str(file_path))
        return "\n\n".join(page.get_text() for page in doc)

    elif suffix == ".docx":
        from docx import Document
        doc = Document(str(file_path))
        return "\n\n".join(para.text for para in doc.paragraphs)

    elif suffix in (".txt", ".md"):
        return file_path.read_text(encoding="utf-8", errors="ignore")

    else:
        raise ValueError(f"不支持的文件类型: {suffix}")


# ============================================================
# 旧版 Embedding（MiniLM，本地）
# ============================================================

_EMBED_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


def _embed_semantic(texts: list[str]) -> list[list[float]]:
    """用 sentence-transformers 生成语义向量（384 维，中英文）。模型缓存在内存。"""
    if not hasattr(_embed_semantic, "_model"):
        from sentence_transformers import SentenceTransformer

        try:
            _embed_semantic._model = SentenceTransformer(_EMBED_MODEL_NAME, local_files_only=True)
        except Exception:
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


# ============================================================
# v2 Embedding（阿里云 text-embedding-v4，API）
# ============================================================

class _DashScopeEmbeddings:
    """阿里云 embedding 封装（OpenAI 兼容格式）。"""
    def __init__(self):
        from openai import OpenAI
        self._client = OpenAI(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            base_url=os.getenv("EMBED_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        )
        self._model = os.getenv("EMBED_MODEL", "text-embedding-v4")

    def embed(self, texts: list[str]) -> list[list[float]]:
        # 阿里云限制每批最多 10 条
        BATCH = 10
        result = []
        for i in range(0, len(texts), BATCH):
            batch = texts[i:i + BATCH]
            resp = self._client.embeddings.create(model=self._model, input=batch)
            result.extend(d.embedding for d in resp.data)
        return result

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


# ============================================================
# 知识库
# ============================================================

class KnowledgeBase:
    """Chroma 向量库封装。旧版 MiniLM + 新版阿里云 embedding 两条管线共存。"""

    def __init__(self, persist_dir: str = "./chroma_data"):
        self._persist_dir = persist_dir
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._embedder = _embed_semantic
        self._collections: dict[str, object] = {}
        self._v2_embedder = None

    # ================================================================
    # 基础方法
    # ================================================================

    def _collection_name(self, user_id: str) -> str:
        return f"kb_{user_id}"

    def _get_collection(self, user_id: str):
        name = self._collection_name(user_id)
        if name not in self._collections:
            self._collections[name] = self._client.get_or_create_collection(name)
        return self._collections[name]

    def _v2_collection_name(self, user_id: str) -> str:
        return f"kb_{user_id}_v2"

    def _get_v2_embedder(self):
        if self._v2_embedder is None:
            self._v2_embedder = _DashScopeEmbeddings()
        return self._v2_embedder

    def _get_v2_docs(self, user_id: str) -> list[dict]:
        """从 v2 collection 获取所有文档。"""
        try:
            coll = self._client.get_or_create_collection(
                self._v2_collection_name(user_id)
            )
            raw = coll.get()
            return [
                {"content": c, "meta": m or {}}
                for c, m in zip(raw.get("documents", []), raw.get("metadatas", []))
            ]
        except Exception:
            return []

    def _v2_vector_search(self, query: str, user_id: str, doc_ids: list[str] | None, k: int) -> list[dict]:
        """纯向量检索。"""
        embedder = self._get_v2_embedder()
        query_emb = embedder.embed_one(query)

        if doc_ids:
            where = {"$and": [{"user_id": user_id}, {"doc_id": {"$in": doc_ids}}]}
        else:
            where = {"user_id": user_id}

        try:
            coll = self._client.get_or_create_collection(
                self._v2_collection_name(user_id)
            )
            result = coll.query(query_embeddings=[query_emb], n_results=k, where=where)
        except Exception:
            return []

        docs = []
        for doc, meta, dist in zip(
            result.get("documents", [[]])[0],
            result.get("metadatas", [[]])[0],
            result.get("distances", [[]])[0],
        ):
            docs.append({"content": doc, "meta": meta or {}, "distance": dist})
        return docs

    def _fmt(self, docs: list[dict], label: str = "") -> str:
        """格式化检索结果。"""
        if not docs:
            return "知识库中未找到相关信息。"
        lines = [f"# 知识库检索结果{label}\n"]
        for i, d in enumerate(docs):
            src = d["meta"].get("doc_id", "未知")
            r = d.get("rerank_score")
            if r:
                sim = f"（精排 {r:.1%}）"
            elif d.get("distance") is not None:
                sim = f"（相关度 {max(0, 1 - d['distance']):.0%}）"
            else:
                sim = ""
            lines.append(f"\n--- 来源 {i+1}: {src} {sim}---")
            lines.append(d["content"])
            lines.append("")
        return "\n".join(lines)

    # ================================================================
    # 旧版 ingest + search（MiniLM）
    # ================================================================

    def ingest(self, file_path: str, user_id: str = "default", doc_id: str | None = None) -> dict:
        """上传文件 → 切块 → MiniLM embedding → 入库。"""
        path = Path(file_path)
        if not path.exists():
            return {"status": "error", "message": f"文件不存在: {file_path}"}

        text = read_file(path)
        chunks = chunk_text(text)
        if not chunks:
            return {"status": "error", "message": "文件内容为空"}

        EMBED_MAX_LEN = 256
        embed_input = [c[:EMBED_MAX_LEN] for c in chunks]
        embeddings = self._embedder(embed_input)

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
            documents=chunks,
            embeddings=embeddings,
            metadatas=[{"user_id": user_id, "doc_id": doc_id} for _ in chunks],
            ids=chunk_ids,
        )
        return {"status": "ok", "doc_id": doc_id, "chunks": len(chunks), "characters": len(text)}

    # ================================================================
    # 新版 ingest_v2（阿里云 text-embedding-v4）
    # ================================================================

    def ingest_v2(self, file_path: str, user_id: str = "default", doc_id: str | None = None) -> dict:
        """v2 上传：阿里云 embedding（8192 token，完整 500 字 chunk）。"""
        path = Path(file_path)
        if not path.exists():
            return {"status": "error", "message": f"文件不存在: {file_path}"}

        text = read_file(path)
        chunks = chunk_text(text)
        if not chunks:
            return {"status": "error", "message": "文件内容为空"}

        doc_id = doc_id or path.name
        embedder = self._get_v2_embedder()
        embeddings = embedder.embed(chunks)

        coll_name = self._v2_collection_name(user_id)
        try:
            client = self._client
            coll = client.get_or_create_collection(coll_name)
            try:
                old = coll.get(where={"doc_id": doc_id})
                if old.get("ids"):
                    coll.delete(ids=old["ids"])
            except Exception:
                pass
        except Exception as e:
            return {"status": "error", "message": f"Chroma 连接失败: {e}"}

        chunk_ids = [f"{doc_id}_{uuid.uuid4().hex[:6]}" for _ in chunks]
        coll.add(
            documents=chunks,
            embeddings=embeddings,
            metadatas=[{"user_id": user_id, "doc_id": doc_id} for _ in chunks],
            ids=chunk_ids,
        )
        return {
            "status": "ok", "doc_id": doc_id, "chunks": len(chunks),
            "characters": len(text), "embedding_model": os.getenv("EMBED_MODEL", "text-embedding-v4"),
        }

    # ================================================================
    # 新版检索模式
    # ================================================================

    def _search_v2(self, query, user_id, doc_ids, n_results):
        """v2 纯向量检索。"""
        docs = self._v2_vector_search(query, user_id, doc_ids, n_results)
        return self._fmt(docs)

    def _search_hybrid(self, query, user_id, doc_ids, n_results):
        """混合检索：向量 + BM25 双路 RRF。"""
        from .retrievers.bm25_retriever import build_bm25_retriever
        from .retrievers.ensemble import build_hybrid_retriever

        all_docs = self._get_v2_docs(user_id)
        if not all_docs:
            return "知识库中未找到相关信息。"

        # 向量检索器
        class VRetriever:
            def __init__(s, kb, uid, dids, k):
                s.kb, s.uid, s.dids, s.k = kb, uid, dids, k

            def invoke(s, q):
                docs = s.kb._v2_vector_search(q, s.uid, s.dids, s.k)
                return [{"page_content": d["content"], "metadata": d["meta"]} for d in docs]

        vr = VRetriever(self, user_id, doc_ids, k=20)
        lang_docs = [{"page_content": d["content"], "metadata": d["meta"]} for d in all_docs]
        bm = build_bm25_retriever(lang_docs, k=20)
        ens = build_hybrid_retriever(vr, bm)
        docs = ens.invoke(query)[:n_results]

        result = []
        for doc in docs:
            c = doc["page_content"] if isinstance(doc, dict) else getattr(doc, "page_content", "")
            m = doc["metadata"] if isinstance(doc, dict) else getattr(doc, "metadata", {})
            result.append({"content": c, "meta": m})
        return self._fmt(result, "（混合检索）")

    def _search_rerank(self, query, user_id, doc_ids, n_results):
        """精排：混合粗召回 → CrossEncoder Top 5。"""
        from .retrievers.reranker import build_reranker

        try:
            docs = self._v2_vector_search(query, user_id, doc_ids, k=20)
            if not docs:
                return "知识库中未找到相关信息。"

            reranker = build_reranker()
            ranked = reranker.rerank(query, docs, top_n=n_results)
            for d in ranked:
                if "rerank_score" not in d:
                    d["rerank_score"] = 0
            return self._fmt(ranked, "（精排）")
        except Exception:
            return self._search_v2(query, user_id, doc_ids, n_results)

    def _search_full(self, query, user_id, doc_ids, n_results):
        """全链路：查询改写 → 混合检索 → 精排。"""
        from .retrievers.query_rewriter import QueryRewriter
        from .retrievers.reranker import build_reranker

        # 1. 查询改写
        try:
            rw = QueryRewriter()
            variants = rw.rewrite(query)
        except Exception:
            variants = [query]

        # 2. 每个变体：向量 + BM25 双路，合并去重
        from .retrievers.bm25_retriever import build_bm25_retriever
        all_v2_docs = self._get_v2_docs(user_id)
        bm_docs = [{"page_content": d["content"], "metadata": d["meta"]} for d in all_v2_docs]
        bm25 = build_bm25_retriever(bm_docs, k=10)

        all_docs = []
        seen = set()
        for v in variants:
            for d in self._v2_vector_search(v, user_id, doc_ids, k=20):
                key = d["content"][:200]
                if key not in seen:
                    seen.add(key)
                    all_docs.append(d)
            for d in bm25.invoke(v):
                c = d["page_content"]
                key = c[:200]
                if key not in seen:
                    seen.add(key)
                    all_docs.append({"content": c, "meta": d.get("metadata", {})})

        if not all_docs:
            return "知识库中未找到相关信息。"

        # 3. 精排
        try:
            reranker = build_reranker()
            all_docs = reranker.rerank(query, all_docs, top_n=n_results)
        except Exception:
            all_docs = all_docs[:n_results]

        for d in all_docs:
            d["rerank_score"] = d.get("rerank_score", 0)
        return self._fmt(all_docs, "（全链路）")

    # ================================================================
    # 统一搜索入口
    # ================================================================

    def search(
        self, query: str, user_id: str = "default",
        doc_ids: list[str] | None = None, n_results: int = 5, mode: str = "default",
    ) -> str:
        """mode: default / v2 / hybrid / rerank / full"""
        if mode == "full":
            return self._search_full(query, user_id, doc_ids, n_results)
        if mode == "rerank":
            return self._search_rerank(query, user_id, doc_ids, n_results)
        if mode == "hybrid":
            return self._search_hybrid(query, user_id, doc_ids, n_results)
        if mode == "v2":
            return self._search_v2(query, user_id, doc_ids, n_results)

        # ---- 旧版检索（MiniLM）----
        try:
            collection = self._get_collection(user_id)
            where = None
            if doc_ids:
                where = {"user_id": user_id, "doc_id": {"$in": doc_ids}}
            query_emb = self._embedder([query])
            results = collection.query(query_embeddings=query_emb, n_results=n_results, where=where)

            if not results.get("documents") or not results["documents"][0]:
                return "知识库中未找到相关信息。"

            lines = ["# 知识库检索结果\n"]
            for i, (doc, meta, dist) in enumerate(zip(
                results["documents"][0], results["metadatas"][0], results["distances"][0],
            )):
                similarity = max(0, 1 - dist) if dist else 1.0
                source = meta.get("doc_id", "未知")
                lines.append(f"\n--- 来源 {i+1}: {source}（相关度 {similarity:.0%}）---")
                lines.append(doc)
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            return f"知识库检索失败: {e}"

    # ================================================================
    # 通用
    # ================================================================

    def health_check(self) -> dict:
        try:
            self._get_collection("default").count()
            return {"status": "ok"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def list_docs(self, user_id: str = "default") -> list[dict]:
        """列出用户已上传的文档（合并旧版 + v2 collection）。"""
        seen, docs = set(), []
        for coll_name in [self._collection_name(user_id), self._v2_collection_name(user_id)]:
            try:
                client = self._client
                results = client.get_or_create_collection(coll_name).get(where={"user_id": user_id})
                for m in results.get("metadatas", []):
                    did = m.get("doc_id", "")
                    if did and did not in seen:
                        seen.add(did)
                        docs.append({"doc_id": did})
            except Exception:
                pass
        return docs

    def delete_doc(self, doc_id: str, user_id: str = "default") -> dict:
        """删除文档（旧版 + v2 collection 都删）。"""
        total = 0
        client = self._client
        for coll_name in [self._collection_name(user_id), self._v2_collection_name(user_id)]:
            try:
                coll = client.get_or_create_collection(coll_name)
                ids = coll.get(where={"doc_id": doc_id}).get("ids", [])
                if ids:
                    coll.delete(ids=ids)
                    total += len(ids)
            except Exception:
                pass
        return {"status": "ok", "deleted_chunks": total}


# 全局单例
kb = KnowledgeBase()
