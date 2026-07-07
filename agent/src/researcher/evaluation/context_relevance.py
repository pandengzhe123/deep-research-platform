"""Context Relevance 评估器 —— 与 RAGAS 同逻辑，不依赖 RAGAS 库。

思路：评检索器质量，不看答案对错，只看检索到的文档有多少是有用的。
  ① LLM 从检索文档中挑出对回答问题真正有用的句子
  ② 得分 = 有用句子数 / 文档总句子数
  分数低 → 检索器返回了一堆废料，在浪费 Token

在 asyncio.to_thread 线程内运行，使用同步 HTTP 客户端。
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))


EXTRACT_PROMPT = """给定一个问题和一段检索到的文档内容，请从文档中挑出对回答该问题"真正有用"的句子。

问题：{question}

文档内容：
{context}

只返回 JSON 数组，每个元素是文档中对回答问题有用的原句（逐字摘录）。如果没有任何有用句子，返回空数组 []。不要加任何解释。"""


def _split_sentences(text: str) -> list[str]:
    """按中英文标点切句。"""
    # 中文句号感叹问号 + 英文句号（后接空格）
    parts = re.split(r"(?<=[。！？])|(?<=\.)\s+|(?<=[!?])\s+", text)
    return [p.strip() for p in parts if p and len(p.strip()) > 3]


class ContextRelevanceEvaluator:
    """Context Relevance：检索到的文档有多少是有用的（测检索噪音）。"""

    def __init__(self):
        self._api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self._base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self._model = os.getenv("LLM_MODEL", "deepseek-v4-flash")

    def evaluate(self, question: str, contexts: list[str]) -> dict:
        """返回 {score, useful, total, useful_sentences}。"""
        context = "\n\n".join(contexts)
        if not context.strip():
            return {"score": 0.0, "useful": 0, "total": 0, "useful_sentences": []}

        total_sentences = _split_sentences(context)
        total = len(total_sentences)
        if total == 0:
            return {"score": 0.0, "useful": 0, "total": 0, "useful_sentences": []}

        useful = self._extract_useful(question, context)
        useful_count = len(useful)
        # 有用句子数可能因 LLM 摘录不精确略有偏差，上限截断到 total
        useful_count = min(useful_count, total)

        score = useful_count / total if total else 0.0
        return {
            "score": score,
            "useful": useful_count,
            "total": total,
            "useful_sentences": useful,
        }

    def _extract_useful(self, question: str, context: str) -> list[str]:
        try:
            text = self._call_llm(
                EXTRACT_PROMPT.format(question=question, context=context[:6000]),
            )
            data = json.loads(text.strip())
            sentences = data if isinstance(data, list) else data.get("sentences", [])
            return [s for s in sentences if isinstance(s, str) and len(s) > 3]
        except Exception:
            return []

    def _call_llm(self, prompt: str) -> str:
        import httpx
        resp = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
        return resp.json()["choices"][0]["message"]["content"] or ""


if __name__ == "__main__":
    ev = ContextRelevanceEvaluator()

    # 检索质量好：文档大部分内容都跟问题相关
    r1 = ev.evaluate(
        question="Python 是谁创建的",
        contexts=["Python 是由 Guido van Rossum 创建的。Guido 在 1989 年圣诞节期间开始设计 Python。Python 于 1991 年首次发布。"],
    )
    print(f"[检索精准] score={r1['score']:.3f} ({r1['useful']}/{r1['total']})\n")

    # 检索质量差：文档大部分是噪音
    r2 = ev.evaluate(
        question="Python 是谁创建的",
        contexts=["天气今天很好。股市今天上涨了 2%。Python 是由 Guido van Rossum 创建的。足球比赛昨晚结束了。中午吃了炒饭。"],
    )
    print(f"[检索有噪音] score={r2['score']:.3f} ({r2['useful']}/{r2['total']})")
    print(f"  有用句子: {r2['useful_sentences']}")
