"""BM25 关键词检索器 —— jieba 中文分词 + rank_bm25。"""

import jieba
from rank_bm25 import BM25Okapi


def _tokenize(text: str) -> list[str]:
    """中文 jieba 分词 + 英文空格分词。"""
    return list(jieba.cut(text))


class BM25Retriever:
    """BM25 关键词检索器，支持中文分词。"""

    def __init__(self, documents: list[dict], k: int = 20):
        self._docs = documents
        self._k = k
        texts = [d["page_content"] for d in documents]
        self._corpus = [_tokenize(t) for t in texts]
        self._bm25 = BM25Okapi(self._corpus) if self._corpus else None

    def invoke(self, query: str) -> list[dict]:
        if not self._bm25:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [self._docs[i] for i, _ in ranked[:self._k]]


def build_bm25_retriever(documents, k=20):
    return BM25Retriever(documents, k=k)
