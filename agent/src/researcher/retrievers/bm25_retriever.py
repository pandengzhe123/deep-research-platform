"""BM25 关键词检索器 —— 直接使用 rank_bm25，不依赖 langchain_community。"""

from rank_bm25 import BM25Okapi


class BM25Retriever:
    """BM25 关键词检索器。"""

    def __init__(self, documents: list[dict], k: int = 20):
        """
        Args:
            documents: [{"page_content": "text", "metadata": {...}}, ...]
            k: 返回 Top K 个结果
        """
        self._docs = documents
        self._k = k

        # 分词：简单空格分割（中文需要先分好词）
        texts = [d["page_content"] for d in documents]
        self._corpus = [t.split() for t in texts]
        self._bm25 = BM25Okapi(self._corpus) if self._corpus else None

    def invoke(self, query: str) -> list[dict]:
        """检索并返回 Top K 文档。"""
        if not self._bm25:
            return []
        tokenized = query.split()
        scores = self._bm25.get_scores(tokenized)

        # 按分数降序排列
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [self._docs[i] for i, _ in ranked[:self._k]]


def build_bm25_retriever(documents, k=20):
    return BM25Retriever(documents, k=k)
