"""知识库模块 —— Chroma 向量存储 + 阿里云 embedding + 检索。"""

from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path

import chromadb

log = logging.getLogger(__name__)

# ============================================================
# 切块策略
# ============================================================

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100,
               min_size: int = 300, min_chunk_chars: int = 0) -> list[str]:
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

    # 丢弃合并后仍然过短的残块（min_chunk_chars=0 → 不丢，保持既有行为）
    #
    # 为什么需要：合并规则要求「合并后不超过 chunk_size」，所以当一个很短的块
    # 后面跟着一个接近 chunk_size 的块时，两者**无法合并**，那个短块就独立留下。
    # 实测（agent/eval，2026-09-28）：每篇 prod_* 文档开头都有一行「分类: 数据库」，
    # 当正文 > 491 字时它与正文无法合并 → **7 个纯元数据块（占 1.6%）**留在索引里。
    # 这些块能匹配到查询却给不出任何内容，白占检索名额。
    #
    # 为什么是「丢弃」而不是「强行合并」：强行合并会突破 chunk_size 这个契约
    # （已有单测钉住），而这类残块的典型形态就是元数据标签，丢掉没有信息损失。
    if min_chunk_chars > 0:
        kept = [c for c in merged if len(c) >= min_chunk_chars]
        # 全被丢掉时退回原样：否则一个短文档会变成"零块"，
        # 上层会把它当成"文件内容为空"直接拒绝入库
        return kept or merged
    return merged


# 入库时的最小块长（字）。0 = 不丢。
#
# 默认 30：实测能清掉「分类: XX」这类纯元数据残块（最长 17 字），
# 而正常的段落/句子块中位 321 字，远在阈值之上，不会被误伤。
# 设为 0 可关闭（回到旧行为）。
MIN_CHUNK_CHARS = int(os.getenv("KB_MIN_CHUNK_CHARS", "30"))


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

# 「没检索到」的统一措辞。原先 `_fmt()` 里硬编码了一份，而 `run_regression`
# 的拒答判定靠 `"未找到" in result` 这个子串 —— 两处各自维护，改一处就静默失效。
NOT_FOUND_MSG = "知识库中未找到相关信息。"

# 拒答闸：开启后每次 KB 检索会多做一次 LLM 判定（「检索到的内容里到底有没有
# 答案」），判定为「没有」就返回 NOT_FOUND_MSG。
#
# **默认关闭** —— 这给每次 KB 检索加一次 LLM 调用（一次 L4 研究会做 10+ 次），
# 是产品级的成本取舍，需要有意识地打开，不该由代码默认替你决定。
#   KB_ANSWER_CHECK=true 开
ANSWER_CHECK_ENABLED = os.getenv("KB_ANSWER_CHECK", "false").strip().lower() in (
    "1", "true", "yes", "on")

# 拒答判定看的召回窗口大小（开启 KB_ANSWER_CHECK 时生效）。
#
# 不能只看最终返回的 top-5 —— 实测只看 top-5 时误拒率 10.4%，且集中在
# precision(7/38) 与 multi_doc(4/16)，而 simple 是 0/48。机制是：
# 这两类题的答案常常**在候选池里但没排进前 5**，用 top-5 判定等于把
# 「没排进前 5」误读成「知识库里没有」。候选池中位 23 条，所以放到 20
# 几乎不增加成本（那些文本本来就已召回）。
ANSWER_CHECK_WINDOW = int(os.getenv("KB_ANSWER_CHECK_WINDOW", "20"))


class KnowledgeBase:
    """Chroma 向量库封装（阿里云 embedding 单管线）。"""

    # BM25 索引缓存上限（按 user_id 计）。索引常驻内存，条目数必须封顶 ——
    # 多用户场景下无界缓存会随上传用户数线性吃内存。
    #
    # 内存代价（实测 ~6.6KB/chunk，含分词结果；增量分词缓存与之共享同一批 list，
    # 不额外翻倍）：
    #     100 万字 ≈ 3,100 chunks ≈ 21MB
    #     300 万字 ≈ 9,300 chunks ≈ 61MB
    #   1,000 万字 ≈ 31,000 chunks ≈ 205MB
    # 所以上限 N 的稳态内存 ≈ N × 单个用户的库大小。默认 4 是在「几百 MB」量级
    # 内的保守值；用 `KB_BM25_CACHE_MAX` 覆盖（见 __init__）。
    _BM25_CACHE_MAX_DEFAULT = 4

    def __init__(self, persist_dir: str = "./chroma_data"):
        self._persist_dir = persist_dir
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._v2_embedder = None
        self._trace = None  # TraceRun 实例，由 Agent 在调用前设置
        # 可配置的缓存上限。活跃 user_id 超过这个数就会互相淘汰 —— 每个被淘汰的
        # 用户下次检索要付一次完整重建。多租户部署前必须按内存预算调这个值。
        self._BM25_CACHE_MAX = int(os.getenv(
            "KB_BM25_CACHE_MAX", str(self._BM25_CACHE_MAX_DEFAULT)
        ))
        if self._BM25_CACHE_MAX < 1:
            log.warning("KB_BM25_CACHE_MAX=%s 非法，回退到 1（0/负数会让缓存永不生效，"
                        "每次检索都重建索引）", self._BM25_CACHE_MAX)
            self._BM25_CACHE_MAX = 1
        # BM25 索引缓存：user_id -> (generation, retriever)。见 _get_bm25()。
        self._bm25_cache: dict[str, tuple[int, object]] = {}
        # 分词缓存：user_id -> {hash(chunk 内容): [token]}。见 _get_bm25()。
        # 里面的 list 与 retriever._corpus 里的**是同一批对象**（共享内存，
        # 不是副本）；淘汰时与 _bm25_cache 同步，保证内存仍受 _BM25_CACHE_MAX 约束。
        self._token_cache: dict[str, dict[int, list[str]]] = {}
        # 文档代际：ingest / delete 时 +1，使该用户的缓存条目失效。
        self._kb_generation: dict[str, int] = {}
        self._bm25_hits = 0
        self._bm25_misses = 0
        self._tok_new = 0
        self._tok_reused = 0
        self._evictions = 0

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
        """从 v2 collection 获取所有文档。

        用 `get_collection`（不存在则抛异常 → 返回 []）而不是 `get_or_create_collection`：
        后者会让**一次检索**顺手创建一个空集合。历史上这个副作用在 chroma 目录里
        留下了 20 个空集合（网关把 JWT 的数字 uid 当 user_id 传进来，每个新 uid
        第一次被检索就会建一个空库）。读路径不该有写副作用。
        """
        try:
            coll = self._client.get_collection(
                self._v2_collection_name(user_id)
            )
            raw = coll.get()
        except Exception:
            return []
        docs = raw.get("documents") or []
        metas = raw.get("metadatas") or []
        return [
            {"content": c, "meta": (metas[i] if i < len(metas) else None) or {}}
            for i, c in enumerate(docs)
        ]

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

        **增量分词（2026-09-28 追加）** —— 上表的成本几乎全在 jieba 上：

            规模          jieba 分词      BM25Okapi    分词占比
            439 chunks       0.19s          0.01s        94%
          9,658 chunks       6.72s          0.25s        96%
         29,852 chunks      22.33s          0.72s        97%

        而 `ingest_v2` 每写一次就 `_invalidate_bm25`，下次检索要**重建整个索引**。
        若每次都把全量语料重新分词一遍，扩容后「上传一个 5 千字文档 → 下一次检索
        等 25 秒」。所以这里按 `hash(chunk 内容)` 缓存分词结果：重建时只对**新增**
        chunk 分词，其余直接复用 list 引用，再把引用拼成新的 `corpus` 交给
        `BM25Retriever`。

        实测（真实 eval 语料平铺成合成库，上传 100 个新 chunk 后重建）：

            规模        全量重建     增量重建     加速     增量侧 µs/chunk
            1,756       0.80s       0.08s      9.7x         46
            9,658       7.43s       0.34s     21.6x         35
           29,852      23.28s       1.02s     22.8x         34

        ⚠️ 这是**常数因子**改善（约 20 倍），不是复杂度改善 —— 增量重建仍然随规模
        线性增长。原因：省掉的只是「未变 chunk 的分词」，而 `BM25Okapi` 本身
        每次都要对**全量**语料重算 idf（`_initialize` + `_calc_idf` 都遍历全部
        token），这个线性项还在。实测增量侧约 34µs/chunk，外推：
        1,000 万字 ≈ 1.1s，1 亿字 ≈ 10.6s。
        要做到真正的增量，得自己维护 df 计数、不用 `rank_bm25` —— 那是另一个量级
        的改动，暂时不做（10s / 1 亿字 在可接受范围内）。

        用内容哈希而不是 Chroma 的 chunk_id 作 key：id 复用/改写策略以后会变，
        而内容哈希自带校验（id 不变但内容变了也不会读到旧分词），并且同库内
        重复内容只留一份分词。

        额外内存开销：分词缓存字典本身 ≈ 78 B/chunk（实测），占分词语料内存的
        1.2% —— token list 与 `retriever._corpus` 共享对象，不翻倍。

        返回的索引以 k=20 构建，调用方按需切片。

        已知限制（两条，都还没修）：
        1. 缓存是**进程内**的。当前 agent 以单进程 uvicorn 运行（Dockerfile 的 CMD
           无 --workers），且 ingest/delete 都发生在同一进程内，所以代际失效是完整的。
           将来若改多 worker 部署，别的进程上传的文档不会让本进程缓存失效 ——
           那时需要把代际计数器挪到 Redis（或直接上 Redis 做索引共享）。
        2. `_BM25_CACHE_MAX = 4` 是硬编码的。活跃 user_id 超过 4 个时互相淘汰，
           每个用户每次检索都要付一次重建（含全量分词）。多租户部署前必须先把
           这个上限做成配置项，或者把索引挪出进程。
        """
        gen = self._kb_generation.get(user_id, 0)
        entry = self._bm25_cache.get(user_id)
        if entry is not None and entry[0] == gen:
            self._bm25_hits += 1
            return entry[1]

        all_docs = self._get_v2_docs(user_id)
        if not all_docs:
            return None

        from .retrievers.bm25_retriever import build_bm25_retriever, _tokenize

        tok_cache = self._token_cache.setdefault(user_id, {})
        docs: list[dict] = []
        corpus: list[list[str]] = []
        live: set[int] = set()
        for d in all_docs:
            content = d["content"]
            h = hash(content)
            live.add(h)
            toks = tok_cache.get(h)
            if toks is None:
                toks = _tokenize(content)
                tok_cache[h] = toks
                self._tok_new += 1
            else:
                self._tok_reused += 1
            docs.append({"page_content": content, "metadata": d["meta"]})
            corpus.append(toks)

        # 淘汰已从库里删掉的 chunk 的分词结果，否则缓存只增不减。
        if len(tok_cache) > len(live):
            for dead in [k for k in tok_cache if k not in live]:
                del tok_cache[dead]

        bm = build_bm25_retriever(docs, k=20, corpus=corpus)
        self._bm25_misses += 1

        # 超上限时淘汰最早插入的一条（dict 保序）。分词缓存必须跟着一起淘汰，
        # 否则它的内存占用就不再受 _BM25_CACHE_MAX 约束（索引本身只被淘汰的
        # retriever 持有，分词缓存的 key→list 映射却会常驻）。
        if user_id not in self._bm25_cache and len(self._bm25_cache) >= self._BM25_CACHE_MAX:
            oldest = next(iter(self._bm25_cache))
            self._bm25_cache.pop(oldest)
            self._token_cache.pop(oldest, None)
            self._evictions += 1
            # 淘汰意味着这个用户下次检索要付一次完整重建。偶发是正常的；
            # 每次请求都淘汰说明 KB_BM25_CACHE_MAX 小于实际活跃用户数，
            # 缓存已经失效（退化成「每次检索都重建」），此时必须调大或换共享存储。
            log.warning(
                "BM25 缓存已满(%d)，淘汰用户 %s（累计淘汰 %d 次）。"
                "若频繁出现，说明活跃用户数超过 KB_BM25_CACHE_MAX=%d，"
                "缓存正在抖动 —— 每次检索都要重建索引。",
                self._BM25_CACHE_MAX, oldest, self._evictions, self._BM25_CACHE_MAX,
            )
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
        tok_total = self._tok_new + self._tok_reused
        return {
            "hits": self._bm25_hits,
            "misses": self._bm25_misses,
            "cached_users": len(self._bm25_cache),
            "hit_rate": f"{self._bm25_hits / total:.1%}" if total else "N/A",
            # 增量分词：miss 时全量语料里有多少块需要重新分词。
            # 稳态（只新增少量文档）应接近 0%，全量重建才会接近 100%。
            "tokens_new": self._tok_new,
            "tokens_reused": self._tok_reused,
            "token_reuse_rate": f"{self._tok_reused / tok_total:.1%}" if tok_total else "N/A",
            "cached_token_users": len(self._token_cache),
            # >0 说明有用户被挤出缓存，它下次检索要付一次完整重建。
            # 持续增长 = 缓存抖动，KB_BM25_CACHE_MAX 该调大。
            "evictions": self._evictions,
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
            # get_collection（而非 get_or_create）：检索不该顺手建空集合，
            # 见 _get_v2_docs 的说明。
            coll = self._client.get_collection(
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
            return NOT_FOUND_MSG
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
        # min_chunk_chars：丢掉合并后仍过短的残块（如「分类: 数据库」这类元数据行）
        chunks = chunk_text(text, min_chunk_chars=MIN_CHUNK_CHARS)
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
        # 必须显式传 top_n：`HybridRetriever.invoke` 的默认是 top_n=5，
        # 漏传会让本模式**永远最多返回 5 条**，`[:n_results]` 再切也切不出第 6 条。
        # 实测（n_results=10，kb_eval_v2）：v2 返回 10 条、rerank 返回 10 条、
        # 修复前的 hybrid 只返回 5 条 —— 调用方要 5 条以上时全是静默截断。
        docs = ens.invoke(query, top_n=n_results)

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
        if not ANSWER_CHECK_ENABLED:
            return self._dispatch(query, user_id, doc_ids, n_results, mode)

        # 拒答闸开启：**先用更宽的窗口做判定，再裁回 n_results**。
        #
        # 为什么要宽窗口（实测依据）：只看 top-5 判定时误拒率 10.4%（14/134），
        # 且集中在 precision(7/38) 与 multi_doc(4/16)，而 simple 是 0/48。
        # 机制是判定问的是「召回的这 k 条里有没有答案」，而这两类题的答案常常
        # **在候选池里但没排进前 5**（实测候选池中位 23 条）——
        # 用 top-5 判定等于把「没排进前 5」误读成「知识库里没有」，是过度断言。
        #
        # 放宽容量的代价接近零：候选池本来就已经召回（中位 23 条），
        # 加宽只是让判定多看几条已召回的文本，不额外发起检索。
        wide = max(n_results, ANSWER_CHECK_WINDOW)
        text = self._dispatch(query, user_id, doc_ids, wide, mode)

        if "未找到" in text:
            return text                     # 检索本身就是空的，没什么可判

        try:
            from .answer_check import doc_has_answer
            if doc_has_answer(query, [text], default_on_error=True):
                return _trim_sources(text, n_results)
        except Exception:
            pass                            # 判定不可用 → 保持原行为（fail-open）

        print(f"  [kb] 拒答闸：召回窗口（{wide} 条）内不含答案，按「未找到」返回")
        return NOT_FOUND_MSG

    def _dispatch(self, query, user_id, doc_ids, n_results, mode) -> str:
        """按 mode 分发到具体检索实现（不含拒答闸）。"""
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
            # get_collection（而非 get_or_create）：列个文档列表不该建空库。
            # 这是历史上那 20 个空集合的主要来源之一 —— 前端每次进页面都调它。
            results = self._client.get_collection(
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
            # get_collection：库不存在时没什么可删，不必先建一个空库。
            coll = self._client.get_collection(
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


def _trim_sources(text: str, n: int) -> str:
    """把 `_fmt()` 的输出裁到前 n 个来源块（保留头部）。

    用于拒答闸：判定要用更宽的窗口，但返回给调用方的仍应只有 n 条 ——
    否则等于悄悄把 n_results 改大了，会改变 Agent 看到的内容量与 token 成本。
    """
    if n <= 0:
        return text
    lines = text.split("\n")
    out: list[str] = []
    kept = 0
    for line in lines:
        if _SOURCE_LINE_RE.search(line):
            kept += 1
            if kept > n:
                break
        out.append(line)
    return "\n".join(out)


# 全局单例
kb = KnowledgeBase()
