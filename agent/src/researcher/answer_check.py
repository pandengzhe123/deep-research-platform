"""答案存在性判定 —— 判断检索到的内容里**有没有**这个问题的答案。

## 为什么需要它（与「相关性」是两件事）

2026-09-28 实测结论：拒答是这条 RAG 链路唯一的真实短板（v5 上可答题 99.3%，
而 no_answer 只拒答 1/25 = 4%），而**任何基于相关性分数的阈值都修不好它**：

  精排分阈值扫描（rerank，119 可答 / 9 no_answer）
    阈值 25 → 拒答 4/9，可答题误拒 24/119   净收益 −20
    阈值 33 → 拒答 7/9，可答题误拒 35/119   净收益 −28
    阈值 36 → 拒答 9/9，可答题误拒 38/119   净收益 −29
  向量距离更差：可答题 60% 落在 no_answer 的分数区间里。

根因是两者本就不同：问「Kafka 和 RabbitMQ 哪个性能更好」时，知识库里**确实有**
这两个词的文档（精排给 33.6% 分），但它们**不含对比数据**。
「相关」不等于「可答」，所以只能做一次分类判定，不能调阈值。

## 与 faithfulness 的关系

`ANSWER_EXISTS_PROMPT` 与判定逻辑原本只存在于
`evaluation/faithfulness.py`（用于 no_answer 分支的评测）。生产侧要用同一件事，
就把 prompt 提到这里，评测侧改为复用 —— 避免同一段 prompt 在两处漂移
（这个项目已经因为「同一个契约两份实现」吃过好几次亏）。

## 实测效果与代价（2026-09-28，v5 共 159 题，mode=v2，KB_ANSWER_CHECK=true）

判定看的**召回窗口大小**对结果影响很大 —— 这是这套 RAG 里最有效的一次调参：

    配置                拒答(no_answer 25)   误拒(可答题 134)   单次检索   净收益
    关闭                    1/25 =  4%         1/134 = 0.7%      176ms       —
    开启，窗口 = top-5      24/25 = 96%       14/134 = 10.4%    1869ms     +9 题
    开启，窗口 = top-20     24/25 = 96%        7/134 =  5.2%    1757ms    +16 题

**误拒减半而拒答不变，延迟基本没变** —— 候选池本来就已经召回了（实测中位 23 条），
放宽窗口只是让判定多看几条**已经召回的**文本，不额外发起检索。净收益接近翻倍。

逐题型看误拒落在哪里（窗口 5 → 窗口 20）：
    simple      48 题   0 → 0
    long_doc    14 题   2 → 0    ← 答案在某长文档的单个 chunk 里，宽窗口才看得到
    colloquial  18 题   1 → 1
    precision   38 题   7 → 3
    multi_doc   16 题   4 → 3

**为什么窗口大小是决定性的**：判定问的是「**你给我的这些文本里**有没有答案」，
不是「知识库里有没有」。只看 top-5 时，答案**在候选池里但没排进前 5** 的题
（precision / multi_doc 尤其常见 —— 需要跨文档组合信息）会被判成"没有答案"，
而知识库里其实有：那是把「没排进前 5」误读成「知识库里没有」，属于过度断言。

**仍有一个已知的过度断言**：窗口再宽也只是候选池。若答案在库里、但这次召回
完全没覆盖到，判定仍会答"没有"。更彻底的做法是先换关键词重试 ——
本模块只提供判定原语 `doc_has_answer`，不做重试编排。

## 因此默认关闭

每次 KB 检索多一次 LLM 调用（约 1.6s，而一次 L4 研究做 10+ 次 KB 检索），
并且即使放宽窗口仍会误拒约 5% 的可答题 —— 这个取舍必须由人决定，
不该由代码默认替你选。
"""

import logging
import os

log = logging.getLogger(__name__)

# 判定用的 prompt。写得很具体是关键：必须让模型区分
# 「文档只是相关」与「文档真的回答了问题」，否则它就退化成第二个相关性判断。
ANSWER_EXISTS_PROMPT = """你是一个严格的验证器，判断给定的文档里是否真的有回答这个问题的信息。

问题：{question}

文档内容：
{context}

规则：
- 如果文档中明确包含了能回答这个问题的信息 → 返回 "yes"
- 如果文档只是相关但并没有回答这个问题 → 返回 "no"
- 如果文档中完全没有相关信息 → 返回 "no"
- 文档"提到相关概念"不等于"回答了问题"——要看是否真的给出了答案

只返回 "yes" 或 "no"："""

# 送进 prompt 的文档字符上限。与 faithfulness 原实现保持一致。
MAX_CONTEXT_CHARS = 6000


def doc_has_answer(question: str, contexts: list[str],
                   default_on_error: bool = True) -> bool:
    """检索到的内容里是否包含这个问题的答案。

    `default_on_error`：判定服务不可用时返回什么。**两个调用方的取舍相反**，
    所以做成显式参数而不是藏着：
      · 生产侧（kb.search 的拒答闸）用 True（fail-open）——
        判定服务抖动不该让整个知识库变成"什么都答不了"，宁可多答不可误拒。
      · 评测侧（faithfulness 的 no_answer 分支）用 False ——
        与它原来的语义一致：判不出来就不算"文档有答案"。
    """
    ctx = "\n\n".join(contexts) if contexts else ""
    if not ctx.strip():
        return False          # 什么都没检索到 = 没答案

    import httpx

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("LLM_MODEL", "deepseek-v4-flash")

    try:
        resp = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "temperature": 0,
                "messages": [{"role": "user", "content": ANSWER_EXISTS_PROMPT.format(
                    question=question, context=ctx[:MAX_CONTEXT_CHARS])}],
            },
            timeout=30,
        )
        data = resp.json()
        if isinstance(data, dict) and data.get("code"):
            log.warning("答案存在性判定被拒绝: %s %s", data.get("code"), data.get("message"))
            return default_on_error
        text = data["choices"][0]["message"]["content"] or ""
        return text.strip().lower().startswith("yes")
    except Exception as e:
        log.warning("答案存在性判定失败，按 %s 处理: %s: %s",
                    "有答案" if default_on_error else "无答案",
                    type(e).__name__, e)
        return default_on_error
