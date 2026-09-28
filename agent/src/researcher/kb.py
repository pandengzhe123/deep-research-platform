"""知识库模块 —— Chroma 向量存储 + 阿里云 embedding + 检索。"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import chromadb

# ============================================================
# 切块策略
# ============================================================

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100, min_size: int = 300) -> list[str]:
    """段落优先 → 句子 → 字符，逐级降级切分。太短的 chunk 合并到前一个。

    ⚠️ `overlap` 只在**字符级**降级路径里生效，而那条路径要求「切完段落后，
    单个句子仍超过 chunk_size」。段落级与句子级都是整段/整句直接入块，**没有重叠**。

    实测（2026-09-28，agent/eval 全部 82 个文档 / 442 个 chunk）：
      · 超过 500 字的句子：**0 个**（0/82 个文档）
      · 因此字符级路径从不触发 ⇒ `overlap=100` 对 **0.0%** 的块生效

    这不是 bug —— 段落与句子本身就是语义完整单元，按它们切块是合理的，
    而对它们强加重叠反而会重复内容、浪费上下文窗口。问题在**参数名**：
    `overlap=100` 暗示「相邻块有重叠」，据此推断检索行为会得出错误结论
    （本项目的早期分析就踩过这个坑）。要减少块边界的信息损失，
    应该做的是「父子块检索」或「按 Markdown 标题层级切」，不是调这个参数。

    参数自校验（2026-09-09 补）—— 这三个参数互相约束，配错会静默出错：

    - `overlap` 必须小于 `chunk_size`。否则步长 <= 0：步长为 0 时 `range()` 直接抛
      ValueError；步长为负时更糟 —— 一块都不返回，**内容被静默丢弃**。
      这里把 overlap 上限钳到 `chunk_size // 2`（保证步长 > 0）。
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

    # BM25 索引缓存上限（按 user_id 计）。索引常驻内存，条目数必须封顶 ——
    # 多用户场景下无界缓存会随上传用户数线性吃内存。
    _BM25_CACHE_MAX = 4

    def __init__(self, persist_dir: str = "./chroma_data"):
        self._persist_dir = persist_dir
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._v2_embedder = None
        self._trace = None  # TraceRun 实例，由 Agent 在调用前设置
        # BM25 索引缓存：user_id -> (generation, retriever)。见 _get_bm25()。
        self._bm25_cache: dict[str, tuple[int, object]] = {}
        # 文档代际：ingest / delete 时 +1，使该用户的缓存条目失效。
        self._kb_generation: dict[str, int] = {}
        self._bm25_hits = 0
        self._bm25_misses = 0

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

    def _get_bm25(self, user_id: str):
        """取该用户的 BM25 索引（带缓存）；空库返回 None。

        为什么要缓存（2026-09-28 实测，kb_eval_v2 / 439 chunks / 平均 322 字）：

            mode='hybrid' 单次检索耗时拆解
              拉全量文档          0.0146s    1.8%
              建 BM25 索引        0.4030s   49.8%   ← 与 embedding 调用同量级
              向量检索(含 embed)  0.3899s   48.2%
              BM25 打分           0.0010s    0.1%

        构建成本随规模近线性：1,756 chunks → 0.76s、9,658 → 7.94s、29,852 → 23.87s。
        而 `mode='full'`（生产默认）**每次 kb.search 都重建一次**，一次 L4 研究
        会做 10+ 次 KB 检索 —— 缓存省掉的是一次全量分词，不是一次小计算。

        返回的索引以 k=20 构建，调用方按需切片。

        已知限制：缓存是**进程内**的。当前 agent 以单进程 uvicorn 运行
        （Dockerfile 的 CMD 无 --workers），且 ingest/delete 都发生在同一进程内，
        所以代际失效是完整的。将来若改多 worker 部署，别的进程上传的文档
        不会让本进程缓存失效 —— 那时需要把代际计数器挪到 Redis（或直接上 Redis
        做索引共享）。
        """
        gen = self._kb_generation.get(user_id, 0)
        entry = self._bm25_cache.get(user_id)
        if entry is not None and entry[0] == gen:
            self._bm25_hits += 1
            return entry[1]

        all_docs = self._get_v2_docs(user_id)
        if not all_docs:
            return None

        from .retrievers.bm25_retriever import build_bm25_retriever

        bm = build_bm25_retriever(
            [{"page_content": d["content"], "metadata": d["meta"]} for d in all_docs],
            k=20,
        )
        self._bm25_misses += 1

        # 超上限时淘汰最早插入的一条（dict 保序）
        if user_id not in self._bm25_cache and len(self._bm25_cache) >= self._BM25_CACHE_MAX:
            self._bm25_cache.pop(next(iter(self._bm25_cache)))
        self._bm25_cache[user_id] = (gen, bm)
        return bm

    def _invalidate_bm25(self, user_id: str) -> None:
        """文档写入/删除后使该用户的 BM25 缓存失效。

        用「代际计数器」而不是直接删缓存条目：并发场景下，若新查询在写操作
        完成前就重建了索引，缓存里会存下旧内容；+1 代际则保证带旧代际的条目
        再也不会被命中（它会在下次 _get_bm25 时被替换）。
        """
        self._kb_generation[user_id] = self._kb_generation.get(user_id, 0) + 1

    def bm25_cache_stats(self) -> dict:
        """BM25 缓存统计（排查 / 评测用）。"""
        total = self._bm25_hits + self._bm25_misses
        return {
            "hits": self._bm25_hits,
            "misses": self._bm25_misses,
            "cached_users": len(self._bm25_cache),
            "hit_rate": f"{self._bm25_hits / total:.1%}" if total else "N/A",
        }

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

    @staticmethod
    def _fmt(docs: list[dict], label: str = "") -> str:
        """格式化检索结果。

        纯函数（不依赖 self）→ 静态方法，好处是单测能直接钉住输出格式：
        这个格式是**对外契约** —— 所有检索指标（`retriever_test` / `e2e_diagnostic` /
        `run_regression`）都靠解析「--- 来源 N: <doc> <相似度标注>---」拿到文档名，
        格式一变，指标会静默失真而不是报错。解析侧见 `parse_source_docs()`。
        """
        if not docs:
            return "知识库中未找到相关信息。"
        lines = [f"# 知识库检索结果{label}\n"]
        for i, d in enumerate(docs):
            src = d["meta"].get("doc_id", "未知")
            r = d.get("rerank_score")
            # 必须写 `is not None` 而不是 `if r:`：rerank_score 的合法取值包含 0.0
            # （精排判定为最不相关），而 0.0 是 falsy —— 用真值判断会让「精排 0 分」
            # 落到下面的 distance 分支，把「精排判为不相关」显示成另一个分数
            # （实测：rerank_score=0.0 + distance=0.9 会显示「相关度 10%」）。
            if r is not None:
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
        # 文档变了 → 该用户的 BM25 索引必须失效，否则新上传的文档搜不到
        self._invalidate_bm25(user_id)
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
        from .retrievers.ensemble import build_hybrid_retriever

        bm = self._get_bm25(user_id)
        if bm is None:
            return "知识库中未找到相关信息。"

        # 向量检索器
        class VRetriever:
            def __init__(s, kb, uid, dids, k):
                s.kb, s.uid, s.dids, s.k = kb, uid, dids, k

            def invoke(s, q):
                docs = s.kb._v2_vector_search(q, s.uid, s.dids, s.k)
                return [{"page_content": d["content"], "metadata": d["meta"]} for d in docs]

        vr = VRetriever(self, user_id, doc_ids, k=20)
        ens = build_hybrid_retriever(vr, bm)
        docs = ens.invoke(query)[:n_results]

        result = []
        for doc in docs:
            c = doc["page_content"] if isinstance(doc, dict) else getattr(doc, "page_content", "")
            m = doc["metadata"] if isinstance(doc, dict) else getattr(doc, "metadata", {})
            result.append({"content": c, "meta": m})
        return self._fmt(result, "（混合检索）")

    def _search_rerank(self, query, user_id, doc_ids, n_results):
        """精排：向量粗召回 → 阿里云精排 Top N。

        粗召回 k=20 是实测选定的，别再随手调大（2026-09-28，v5 共 159 题）：

          配置            doc_hit@5   chunk_recall@5   chunk_mrr   单次延迟
          不精排             97.8%        92.8%         0.8668       0ms
          vec_only          98.5%        95.5%         0.9119     267ms
          dual20（当前）     99.3%        96.2%         0.9190     278ms
          dual100           99.3%        95.5%         0.9149     336ms

        三条结论：
          · **精排确实有效**：chunk_mrr 0.8668 → 0.9190（+0.052，相对 +6.0%）。
            早先"精排 20 倍延迟 0 收益"的说法是**用文档级指标**得出的 ——
            那个指标在 v4 上模式间相对极差只有 1.7%，分辨不出 6% 量级的改进。
          · **候选集扩到 100 是负收益**（chunk_mrr −0.004、召回 −0.7%、延迟 +27%）。
            多出来的候选主要是 BM25 路文档，边际候选会挤掉正确结果。
          · **BM25 候选不该被过滤掉**：dual20 − vec_only = +0.0071，
            即词法命中的文档在精排池里是帮忙的（补向量路漏掉的内容）。
        """
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
        # BM25 走缓存：原先这里每次检索都全量 jieba 分词 + 重建 BM25Okapi，
        # 实测占单次 hybrid 耗时的 49.8%（与 embedding 调用同量级）。
        # 缓存索引以 k=20 构建（见 _get_bm25），此处只需前 10 条 → 调用后切片。
        t0 = __import__('time').time()
        bm25 = self._get_bm25(user_id)
        if bm25 is None:
            print(f"  [full] ② 知识库为空，跳过检索")
            return "知识库中未找到相关信息。"
        print(f"  [full] ② BM25 索引就绪 ({__import__('time').time() - t0:.2f}s)")

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
            for d in bm25.invoke(v)[:10]:
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
            # 兜底：精排不可用时按粗排顺序取前 n_results。
            #
            # 这个兜底曾被怀疑会「把无阈值的 BM25 文档塞给 LLM」，实测（2026-09-28，
            # v5 共 159 题）结论是**影响可以忽略，不需要改**：
            #   · 向量路中位返回 20 条（满额），只有 15/159（9%）不足 5 条；
            #     只有这些题的兜底结果会被 BM25 文档填充（90.6% 的题全是过滤过的向量文档）
            #   · 整体质量差：兜底 chunk_mrr 0.8668 vs 精排 0.9137（+0.047）
            #     —— 这 0.047 主要来自**正常路径**（向量路取满 20 条的题），
            #     不是来自兜底填充
            #   · 只看那 15 题：兜底 chunk_mrr 0.6133 vs 精排 0.6333（仅 +0.020），
            #     而且逐题看是**双向**的 ——
            #       「1991年诞生了哪些技术」兜底 0.20 vs 精排 1.00（精排救场）
            #       「怎么查 MySQL 查询慢」 兜底 1.00 vs 精排 0.50（兜底反而更好）
            #   · 这 15 题本身是「向量路找不到足够相关文档」的难题
            #     （doc_hit 仅 66.7%，整体是 97.8%），BM25 补位是**症状不是病因**
            #   · 而且 159/159 次精排调用全部成功 → 这条兜底路径实际从未触发
            #
            # 也考虑并否决了「向量路返回 0 条时直接拒答」：那会修好
            # 「比特币的创始人是」（no_answer）却破坏「1991年诞生了哪些技术」
            # （可答，且精排能救回 1.00），净收益为零且风险更高。
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
        # 真的删掉了才失效：没删任何东西时重建索引是纯浪费
        if total:
            self._invalidate_bm25(user_id)
        return {"status": "ok", "deleted_chunks": total}


# ============================================================
# 检索结果 → 来源文档名（_fmt 的逆运算）
# ============================================================
# 为什么放在这里而不是评测目录：解析的对象就是上面 `_fmt()` 的输出，两者是
# 同一份契约的两侧。它们曾经分居两处并各自实现 —— 结果 `e2e_diagnostic.py`
# 只 strip 半角括号，而 `_fmt` 输出的是**全角**「（相关度 78%）」，
# 于是解析出的"文档名"变成 "doc1.txt （相关度 78%）"，与 expected_docs 永不相等
# → 命中率假性归零（hybrid 模式因为不带相似度标注而侥幸正确，掩盖了这个 bug）。
# 放在一起，改格式的人一眼能看到解析方。

# 来源行：--- 来源 N: <文档名> <可选相似度标注>---
_SOURCE_LINE_RE = re.compile(r"来源\s*(\d+)\s*[:：]\s*(.*?)\s*-{2,}\s*$")

# 尾部的相似度标注。用前瞻要求括注内含 "%" 或已知关键词，避免误伤
# 文件名本身就以括号结尾的情况（例如「报告(最终版)」不该被剥成「报告」）。
_ANNOT_RE = re.compile(
    r"\s*[（(](?=[^（()）]*(?:%|相关度|精排|相似度|score|relevance))[^（()）]*[)）]\s*$",
    re.IGNORECASE,
)


def parse_source_docs(result_text: str) -> list[str]:
    """从 `_fmt()` 产出的检索结果里按顺序取出来源文档名（已剥离相似度标注）。

    >>> parse_source_docs("--- 来源 1: doc1.txt （相关度 78%）---")
    ['doc1.txt']
    >>> parse_source_docs("--- 来源 1: doc1.txt ---")
    ['doc1.txt']
    """
    out: list[str] = []
    for line in (result_text or "").split("\n"):
        if "来源" not in line:
            continue
        m = _SOURCE_LINE_RE.search(line)
        if not m:
            continue
        rest = m.group(2).strip()
        # 可能叠加多层标注（例如「（精排 85%）」外再包一层），循环剥净
        prev = None
        while rest and prev != rest:
            prev = rest
            rest = _ANNOT_RE.sub("", rest).strip()
        if rest:
            out.append(rest)
    return out


# 全局单例
kb = KnowledgeBase()
