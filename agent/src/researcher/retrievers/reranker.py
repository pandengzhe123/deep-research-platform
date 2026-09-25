"""检索结果精排 —— 粗召回 N 条 → 精排 Top K（阿里云 gte-rerank-v2）。

原先用本地 Cross-Encoder（BAAI/bge-reranker-base）。改为调用阿里云文本排序 API，
原因是本地模型的代价远超收益：

  1. 镜像体积：sentence-transformers 会带进 torch + transformers，而 torch 在
     Linux 上默认解析到 GPU 版，连带 nvidia-cudnn/cublas/nccl/triton 等
     约 3~4GB CUDA 库 —— 部署机是纯 CPU 服务器，一个都用不上，镜像被撑到 9GB。
  2. 构建阻塞：Dockerfile 要从 huggingface.co 下载模型权重，国内不可达，
     镜像根本构建不出来。
  3. 运行内存：模型常驻约 1GB，把小规格服务器的余量吃掉。

改成 API 后以上三项全部消失，且不引入新依赖（httpx 已在依赖里）。
"""

from __future__ import annotations

import os

import httpx

# 阿里云百炼文本排序。注意 gte-rerank 系列（无 -v2）已于 2026-05-30 下线，
# 不要退回 "gte-rerank"。qwen3-rerank 走的是另一个端点（compatible-api/v1/reranks）。
_RERANK_URL = os.getenv(
    "RERANK_URL",
    "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
)
_RERANK_MODEL = os.getenv("RERANK_MODEL", "gte-rerank-v2")

# 官方限制：gte-rerank-v2 单次请求输入总量 30,000 token、单条 4,000 token。
# chunk_text 切出的块 <= 500 字（中文约 300 token），所以单条不会超；
# 真正的风险是条数 —— 粗召回把多个查询变体的向量+BM25 结果去重后可能上百条，
# 30,000 / 300 ≈ 100 条就是上限。超过时取前 _MAX_DOCS 条（粗排顺序），
# 宁可少排几条也不要触发 400 让整个精排失败。
_MAX_DOCS = 100


class DashScopeReranker:
    """阿里云文本排序（默认 gte-rerank-v2）。

    与本地 CrossEncoder 的语义差异，调用方要注意：
    - 返回的 relevance_score 是 0~1 的相对分，官方明确说明**只在同一次请求内可比**，
      不能跨请求当绝对阈值用（本地 CrossEncoder 输出的是无界 logit，两者不可换算）。
    - 分数会写回 doc 的 `rerank_score` 字段，保持与原实现一致，`_fmt` 无需改动。

    失败时抛异常而不是静默返回空：调用方（`_search_rerank` / `_search_full`）
    已有 try/except 回退到粗排，保留「精排失败仍能出结果」的行为。
    """

    def __init__(self, model_name: str | None = None):
        self._model = model_name or _RERANK_MODEL
        self._api_key = os.getenv("DASHSCOPE_API_KEY", "")

    def rerank(self, query: str, docs: list, top_n: int = 5) -> list:
        if not docs:
            return []
        if not self._api_key:
            raise RuntimeError("缺少 DASHSCOPE_API_KEY，无法调用精排服务")

        candidates = docs[:_MAX_DOCS]
        texts = [self._get_text(d) for d in candidates]

        resp = httpx.post(
            _RERANK_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "input": {"query": query, "documents": texts},
                "parameters": {"top_n": top_n, "return_documents": False},
            },
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()

        # 阿里云的失败响应是 HTTP 200 + code/message（见官方错误码文档），
        # 不检查的话会把「鉴权失败 / 限流」当成「没有相关文档」静默吞掉。
        if payload.get("code"):
            raise RuntimeError(
                f"精排调用失败: {payload.get('code')} {payload.get('message')}"
            )

        results = (payload.get("output") or {}).get("results") or []

        # 响应已按 relevance_score 降序，用 index 映射回原文档（不依赖 return_documents）
        ranked: list = []
        for item in results:
            idx = item.get("index")
            if not isinstance(idx, int) or not (0 <= idx < len(candidates)):
                continue
            doc = candidates[idx]
            score = float(item.get("relevance_score", 0.0))
            if hasattr(doc, "metadata") and isinstance(doc.metadata, dict):
                doc.metadata["rerank_score"] = score
            elif isinstance(doc, dict):
                doc["rerank_score"] = score
            ranked.append(doc)

        # 返回体异常（results 为空）时退化为粗排顺序，别把召回结果整批丢掉
        return ranked[:top_n] if ranked else candidates[:top_n]

    @staticmethod
    def _get_text(doc) -> str:
        if hasattr(doc, "page_content"):
            return doc.page_content
        if isinstance(doc, dict):
            return doc.get("page_content", doc.get("content", ""))
        return str(doc)


def build_reranker(model_name: str | None = None):
    return DashScopeReranker(model_name)
