"""查询改写 —— LLM 自动生成多个检索变体。"""

import os
from openai import OpenAI

REWRITE_PROMPT = """你是一个查询改写助手。用户会提出一个问题，你需要生成 3 个不同角度/措辞的检索查询词，用于从知识库中检索相关文档。

规则：
1. 每个查询词应该独立、可检索
2. 从不同角度覆盖问题的核心意图
3. 用简洁的关键词组合，而非完整句子
4. 原问题中的关键实体和术语必须保留
5. 如果原问题已经很具体，可以只做微调

用户问题：{question}

请返回 JSON 数组：["查询变体1", "查询变体2", "查询变体3"]"""


class QueryRewriter:
    """用 LLM 改写查询，生成多个检索变体。"""

    def __init__(self, model=None):
        self._client = OpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
        self._model = model or os.getenv("LLM_MODEL", "deepseek-v4-flash")

    def rewrite(self, question: str) -> list[str]:
        """生成 3 个查询变体，失败返回原问题。"""
        try:
            import json
            resp = self._client.chat.completions.create(
                model=self._model,
                temperature=0.1,
                messages=[{"role": "user", "content": REWRITE_PROMPT.format(question=question)}],
                response_format={"type": "json_object"},
            )
            text = resp.choices[0].message.content or "[]"
            variants = json.loads(text)
            if isinstance(variants, list) and len(variants) > 0:
                # 原问题也加进去
                return variants[:3]
        except Exception:
            pass
        return [question]
