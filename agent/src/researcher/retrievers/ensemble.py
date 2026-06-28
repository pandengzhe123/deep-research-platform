"""混合检索 —— 向量 + BM25 双路并行，RRF 融合。"""


class HybridRetriever:
    """混合检索器：向量（语义）+ BM25（关键词），RRF 排名融合。"""

    def __init__(self, vector_retriever, bm25_retriever, k=60):
        """
        Args:
            vector_retriever: 向量检索器（Chroma.as_retriever()）
            bm25_retriever: BM25 关键词检索器（langchain_community）
            k: RRF 排名常数，越小排名靠前的权重差异越大
        """
        self.vector = vector_retriever
        self.bm25 = bm25_retriever
        self.rrf_k = k

    def invoke(self, query: str, top_n: int = 5) -> list:
        """
        RRF 融合两路结果。

        公式：score(doc) = Σ 1/(k + rank_i)
        两路各取 Top N，按排名加权，合并去重，返回 Top K。
        """
        # 两路各取 top_n * 2（多取一些留余量）
        fetch_n = top_n * 5
        vec_docs = self.vector.invoke(query)
        bm_docs = self.bm25.invoke(query)

        # 截断
        vec_docs = vec_docs[:fetch_n]
        bm_docs = bm_docs[:fetch_n]

        # RRF 打分
        scores: dict[str, tuple[float, object]] = {}
        for rank, doc in enumerate(vec_docs):
            key = doc.page_content[:200]  # 用前 200 字符做指纹
            scores[key] = (scores.get(key, (0, doc))[0] + 1.0 / (self.rrf_k + rank + 1), doc)
        for rank, doc in enumerate(bm_docs):
            key = doc.page_content[:200]
            scores[key] = (scores.get(key, (0, doc))[0] + 1.0 / (self.rrf_k + rank + 1), doc)

        # 按 RRF 分数降序，返回 Top N
        ranked = sorted(scores.values(), key=lambda x: x[0], reverse=True)
        return [doc for _, doc in ranked[:top_n]]


def build_hybrid_retriever(vector_retriever, bm25_retriever):
    """构建混合检索器。"""
    return HybridRetriever(vector_retriever, bm25_retriever)
