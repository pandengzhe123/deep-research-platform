"""BM25 关键词检索器 —— jieba 中文分词 + rank_bm25。"""

import jieba
from rank_bm25 import BM25Okapi


def _tokenize(text: str) -> list[str]:
    """中文 jieba 分词 + 英文空格分词。

    ⚠️ 不要在这里加 `.lower()` —— 试过，实测是**负收益**，已回滚。

    曾经的理由（听起来很合理）：BM25 按 token 字面值统计，`Redis` 和 `redis`、
    `Apple` 和 `apple` 原本是两个词项，中英混排文档里英文专有名词会因此匹配不上。

    实测结论（2026-09-28，MultiHop-RAG 英文语料 609 篇 / 17,871 chunks /
    260 题，A/B 两组唯一差别就是这里有没有 lower）：

        模式      指标       不加 lower   加了 lower      差       配对 p
        hybrid    Hits@4       0.6692      0.6346     −0.0346    0.0360  ❌ 显著更差
        hybrid    Hits@10      0.7808      0.7731     −0.0077    0.7302
        hybrid    MRR@10       0.4982      0.4792     −0.0190    0.0630
        hybrid    MAP@10       0.2534      0.2448     −0.0086    0.0688

    四项**方向全为负**，其中 Hits@4 达到显著。中文库上另测（159 题 / 439 chunks）：
    只有 1/134 题结果变化，配对 p = 1.0000（纯中性，也没收益）。

    机制（为什么直觉错了）：英文新闻里人名、机构名等**专有名词密度很高**，
    大小写本身就是强判别信号。lower() 把 `Apple`(公司)/`apple`(水果)、
    `Trump`(人名)/`trump`(动词) 合并成同一个词项，等于**抹掉了这个信号并降低 idf
    的判别力**；而 jieba 对英文本来只按空格切，没有词干化，合并后高频小写词
    反而权重更高。净效果是排序变差。

    `test_tokenize_does_not_lowercase` 守着这条结论，避免以后又有人「顺手优化」回去。
    """
    return list(jieba.cut(text))


class BM25Retriever:
    """BM25 关键词检索器，支持中文分词。

    只返回**分数严格大于 0** 的文档：0 分意味着该文档与查询没有任何词项重叠，
    也就是没有任何词法相关性依据，不该占 top-k 名额。

    实测（2026-09-28，130 题 / kb_eval_v2 439 chunks / k=20）：
      · 触发频率：只有 2/130 条查询的 top-20 里混进零分文档（共 12 个），
        例如「微服务怎么找到对方」有 5 个。分数分布上，第 20 名的中位是 8.68，
        所以 k=20 的截断在绝大多数情况下已经挡住了它们。
      · 聚合指标影响：0。把下限从「不过滤」扫到 3.0，
        doc_hit@5 / chunk_recall@5 / chunk_mrr 三项一字未变。
    也就是说**这道闸修的是机制，不是指标** —— 它防止「查询词在库里一个都没出现」
    时 BM25 返回一批任意文档，经 RRF 按排名拿到权重后污染融合结果。
    保留它是因为代价近乎为零且方向明确；不要指望它带来可测量的指标提升。

    为什么触发得这么少（值得记住的机制，别指望靠调这个参数提升指标）：
    jieba 会把**空格**切成一个独立 token，而空格几乎出现在每个文档里；
    rank_bm25 的 BM25Okapi 对「语料中过半文档都有」的词项算出的 idf 为负，
    随后用 `epsilon * average_idf` 替换 —— 而 average_idf 通常为正，
    于是空格这个词项拿到了一个**正的** idf。结果：只要查询里含空格，
    每个含空格的文档都会得正分。所以这道闸只在**纯中文查询**（没有空格 token）
    且部分文档确实零词项重叠时才生效。
    """

    def __init__(self, documents: list[dict], k: int = 20,
                 corpus: list[list[str]] | None = None):
        """corpus：可传入**已分好词**的语料（与 documents 一一对应、同序）。

        为什么要这个入参（实测依据，2026-09-28）：
        `BM25Retriever.__init__` 的成本几乎全在 jieba 分词上，不在 BM25Okapi：

            规模          jieba 分词          BM25Okapi      分词占比
            439 chunks       0.19s              0.01s          94%
          9,658 chunks       6.72s              0.25s          96%
         29,852 chunks      22.33s              0.72s          97%

        上传一个新文档会让整个索引失效。若每次重建都对**全部**语料重新分词，
        扩容后代价是线性的（1000 万字 ≈ 25s，每次上传后第一次检索都要等）。
        允许调用方传入已有分词结果后，重建只需对**新增**的那几块分词，
        然后把 list 引用拼起来 —— 实测（上传 100 个新 chunk 后重建）：

            规模        全量重建     增量重建     加速
            1,756       0.80s       0.08s      9.7x
            9,658       7.43s       0.34s     21.6x
           29,852      23.28s       1.02s     22.8x

        ⚠️ 注意传入 corpus 只是省掉「未变 chunk 的分词」这一项（占全量重建的
        94~97%），`BM25Okapi(self._corpus)` 这一行仍然对全量语料重算 idf，
        是 O(全部 token) 的。所以这是约 20 倍的**常数因子**改善，
        不是复杂度改善 —— 增量重建依然随规模线性增长（约 34µs/chunk）。

        调用方见 `KnowledgeBase._get_bm25()`（按 hash(内容) 缓存分词结果）。

        另外注意：传入的 list 会被**直接持有**（不复制），调用方若同时缓存
        这些 list，两者共享同一份内存，不会翻倍。
        """
        self._docs = documents
        self._k = k
        texts = [d["page_content"] for d in documents]
        self._corpus = corpus if corpus is not None else [_tokenize(t) for t in texts]
        self._bm25 = BM25Okapi(self._corpus) if self._corpus else None

    def invoke(self, query: str) -> list[dict]:
        if not self._bm25:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        # 先过滤再排序。全部零分时返回空 —— 那表示查询词在库里一个都没出现，
        # 此时混合检索应完全交给向量路，而不是塞一批"零分但排名靠前"的文档。
        ranked = [(i, s) for i, s in enumerate(scores) if s > 0]
        ranked.sort(key=lambda x: x[1], reverse=True)
        return [self._docs[i] for i, _ in ranked[:self._k]]


def build_bm25_retriever(documents, k=20, corpus=None):
    return BM25Retriever(documents, k=k, corpus=corpus)
