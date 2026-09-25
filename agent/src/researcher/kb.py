"""知识库模块 —— Chroma 向量存储 + 阿里云 embedding + 检索。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import chromadb

# ============================================================
# 切块策略
# ============================================================

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100, min_size: int = 300) -> list[str]:
    """段落优先 → 句子 → 字符，逐级降级切分。太短的 chunk 合并到前一个。

    参数自校验（2026-09-09 补）—— 这三个参数互相约束，配错会静默出错：

    - `overlap` 必须小于 `chunk_size`。否则步长 <= 0：步长为 0 时 `range()` 直接抛
      ValueError；步长为负时更糟 —— 一块都不返回，**内容被静默丢弃**。
      这里把 overlap 上限钳到 `chunk_size // 2`（保留"相邻块有重叠"的语义，
      同时保证步长 > 0）。
    - `min_size` 不能大于 `chunk_size`。否则"合并短块"会把刚切好的块又粘回一整块，
      等于没切 —— 这正是 `test_chunk_multiple_paragraphs` 暴露的问题。
    - 合并只在**结果不超过 chunk_size** 时进行，保证 chunk_size 这个契约是真的。
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size 必须为正整数，收到 {chunk_size}")
    overlap = max(0, min(overlap, chunk_size // 2))
    min_size = max(0, min(min_size, chunk_size))
    step = chunk_size - overlap

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
                    for i in range(0, len(sent), step):
                        chunks.append(sent[i:i + chunk_size])

    # 合并太短的 chunk 到前一个，保证每个 chunk 至少有 min_size 字
    # （但不允许合并后超过 chunk_size，否则 chunk_size 形同虚设）
    merged: list[str] = []
    for c in chunks:
        if (merged and len(merged[-1]) < min_size
                and len(merged[-1]) + 1 + len(c) <= chunk_size):
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
# Embedding（阿里云 text-embedding-v4，API）
# ============================================================
# 原先这里还有一套本地 MiniLM（sentence-transformers）实现和对应的 v1 检索管线。
# 已整体移除：它会让镜像多背 torch + 约 3~4GB CUDA 库、构建时必须访问
# huggingface.co（国内不可达，镜像构建直接失败）、运行时再常驻约 1GB 内存。
# 而且它在生产里早已是死代码 —— 所有检索模式走的都是下面的阿里云实现。

class _DashScopeEmbeddings:
    """阿里云 embedding 封装（OpenAI 兼容格式）。"""
    def __init__(self):
        from openai import OpenAI
        self._client = OpenAI(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            base_url=os.getenv("EMBED_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        )
        self._model = os.getenv("EMBED_MODEL", "text-embedding-v4")

    def embed(self, texts: list[str], trace=None) -> list[list[float]]:
        # 阿里云限制每批最多 10 条
        import time as _time
        BATCH = 10
        result = []
        for i in range(0, len(texts), BATCH):
            batch = texts[i:i + BATCH]
            t0 = _time.time()
            resp = self._client.embeddings.create(model=self._model, input=batch)
            duration_ms = int((_time.time() - t0) * 1000)
            if trace:
                trace.record_embedding(
                    model=self._model,
                    text_count=len(batch),
                    total_tokens=resp.usage.total_tokens if getattr(resp, "usage", None) else 0,
                    duration_ms=duration_ms,
                    success=True,
                )
            result.extend(d.embedding for d in resp.data)
        return result

    def embed_one(self, text: str, trace=None) -> list[float]:
        return self.embed([text], trace=trace)[0]


# ============================================================
# 知识库
# ============================================================

class KnowledgeBase:
    """Chroma 向量库封装（阿里云 embedding 单管线）。"""

    def __init__(self, persist_dir: str = "./chroma_data"):
        self._persist_dir = persist_dir
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._v2_embedder = None
        self._trace = None  # TraceRun 实例，由 Agent 在调用前设置

    # ================================================================
    # 基础方法
    # ================================================================

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
        query_emb = embedder.embed_one(query, trace=self._trace)

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

        # 最低相似度阈值：过滤明显不相关的结果，防止 LLM 拿到垃圾编造。
        # ChromaDB 余弦距离范围 0-2（0=完全相同, 1=正交, 2=完全相反）。
        # text-embedding-v4 实测：相关文档 distance 约 0.7-0.85，不相关约 1.3+。
        # 用 distance 阈值（默认 1.0）而非 1-dist，后者在余弦距离下会变负数。
        MAX_DISTANCE = float(os.getenv("KB_MAX_DISTANCE", "1.2"))
        docs = []
        for doc, meta, dist in zip(
            result.get("documents", [[]])[0],
            result.get("metadatas", [[]])[0],
            result.get("distances", [[]])[0],
        ):
            if dist > MAX_DISTANCE:
                continue  # 距离太大 = 不相关，过滤掉
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
    # 上传（阿里云 text-embedding-v4）
    # ================================================================

    def ingest_v2(self, file_path: str, user_id: str = "default", doc_id: str | None = None) -> dict:
        """上传：阿里云 embedding（8192 token，完整 500 字 chunk）。"""
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
        """精排：向量粗召回 → 阿里云精排 Top N。"""
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
        print(f"  [full] ① 查询改写...")
        try:
            t0 = __import__('time').time()
            rw = QueryRewriter()
            variants = rw.rewrite(query)
            print(f"  [full] ① 查询改写 [OK]  → {len(variants)} 个变体 ({__import__('time').time() - t0:.1f}s)")
        except Exception as e:
            print(f"  [full] ① 查询改写 [FAIL]  → 回退原始查询 ({e})")
            variants = [query]

        # 2. 向量 + BM25 双路
        from .retrievers.bm25_retriever import build_bm25_retriever
        t0 = __import__('time').time()
        all_v2_docs = self._get_v2_docs(user_id)
        bm_docs = [{"page_content": d["content"], "metadata": d["meta"]} for d in all_v2_docs]
        bm25 = build_bm25_retriever(bm_docs, k=10)
        print(f"  [full] ② BM25 索引就绪 ({len(bm_docs)} 篇文档, {__import__('time').time() - t0:.1f}s)")

        t0 = __import__('time').time()
        all_docs = []
        seen = set()
        for v in variants:
            vec_hits = 0
            for d in self._v2_vector_search(v, user_id, doc_ids, k=20):
                key = d["content"][:200]
                if key not in seen:
                    seen.add(key)
                    all_docs.append(d)
                    vec_hits += 1
            bm_hits = 0
            for d in bm25.invoke(v):
                c = d["page_content"]
                key = c[:200]
                if key not in seen:
                    seen.add(key)
                    all_docs.append({"content": c, "meta": d.get("metadata", {})})
                    bm_hits += 1
        print(f"  [full] ② 双路召回 [OK]  → 去重后 {len(all_docs)} 条 ({__import__('time').time() - t0:.1f}s)")

        if not all_docs:
            print(f"  [full] ③ 粗召回为空，跳过精排")
            return "知识库中未找到相关信息。"

        # 3. 精排
        print(f"  [full] ③ 阿里云精排 (从 {len(all_docs)} 条 → Top {n_results})...")
        try:
            t0 = __import__('time').time()
            reranker = build_reranker()
            all_docs = reranker.rerank(query, all_docs, top_n=n_results)
            print(f"  [full] ③ 精排 [OK]  ({__import__('time').time() - t0:.1f}s)")
        except Exception as e:
            print(f"  [full] ③ 精排 [FAIL]  → 回退粗排取前 {n_results} ({e})")
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
        """mode: default / v2 / hybrid / rerank / full

        `default` 与 `v2` 现在等价（都是阿里云纯向量检索）。原先 default 走本地
        MiniLM，那条管线已随本地模型一并移除；保留 default 这个入参名是为了不
        破坏外部调用方，而不是因为还存在第二种行为。
        """
        if mode == "full":
            return self._search_full(query, user_id, doc_ids, n_results)
        if mode == "rerank":
            return self._search_rerank(query, user_id, doc_ids, n_results)
        if mode == "hybrid":
            return self._search_hybrid(query, user_id, doc_ids, n_results)
        return self._search_v2(query, user_id, doc_ids, n_results)

    # ================================================================
    # 通用
    # ================================================================

    def list_docs(self, user_id: str = "default") -> list[dict]:
        """列出用户已上传的文档。

        只查 v2 collection。v1（本地 MiniLM）的 `kb_{user}` 已随本地模型移除 ——
        若其中还留有旧数据，这些文档不会再出现在列表里；本地开发时直接删掉
        chroma_data 目录重新上传即可。
        """
        docs, seen = [], set()
        try:
            results = self._client.get_or_create_collection(
                self._v2_collection_name(user_id)
            ).get(where={"user_id": user_id})
            for m in results.get("metadatas", []):
                did = m.get("doc_id", "")
                if did and did not in seen:
                    seen.add(did)
                    docs.append({"doc_id": did})
        except Exception:
            pass  # collection 尚未创建 → 视为空库
        return docs

    def delete_doc(self, doc_id: str, user_id: str = "default") -> dict:
        """删除 v2 collection 中的指定文档。"""
        total = 0
        try:
            coll = self._client.get_or_create_collection(
                self._v2_collection_name(user_id)
            )
            ids = coll.get(where={"doc_id": doc_id}).get("ids", [])
            if ids:
                coll.delete(ids=ids)
                total = len(ids)
        except Exception:
            pass
        return {"status": "ok", "deleted_chunks": total}


# 全局单例
kb = KnowledgeBase()
