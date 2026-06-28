"""Cross-Encoder 精排 —— 粗召回 20 条 → 精排 Top 5。"""


class CrossEncoderReranker:
    """
    Cross-Encoder 精排器。embedding 是双塔轻量模型（快但不准），
    Cross-Encoder 把 query 和 doc 拼接后一起编码逐对打分（慢但准）。
    """

    def __init__(self, model_name="BAAI/bge-reranker-base"):
        from sentence_transformers import CrossEncoder
        self._model = CrossEncoder(model_name)

    def rerank(self, query: str, docs: list, top_n: int = 5) -> list:
        """
        对候选文档逐个精排。

        Args:
            query: 用户查询
            docs: 粗召回的候选文档列表（LangChain Document 或 dict）
            top_n: 最终返回 Top N

        Returns:
            精排后的文档列表，附带 rerank_score
        """
        if not docs:
            return []

        # 构建 (query, doc) 对
        pairs = [(query, self._get_text(doc)) for doc in docs]

        # Cross-Encoder 逐对打分
        scores = self._model.predict(pairs)

        # 按分数降序排列
        ranked = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)

        # 标注分数并返回 Top N
        result = []
        for doc, score in ranked[:top_n]:
            if hasattr(doc, "metadata"):
                doc.metadata["rerank_score"] = float(score)
            elif isinstance(doc, dict):
                doc["rerank_score"] = float(score)
            result.append(doc)
        return result

    def _get_text(self, doc) -> str:
        if hasattr(doc, "page_content"):
            return doc.page_content
        if isinstance(doc, dict):
            return doc.get("page_content", doc.get("content", ""))
        return str(doc)


def build_reranker(model_name="BAAI/bge-reranker-base"):
    return CrossEncoderReranker(model_name)
