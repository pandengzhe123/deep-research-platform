"""Answer Relevance 评估器 —— 与 RAGAS 同逻辑，不依赖 RAGAS 库。

思路：不看答案对错，而是"反向猜"这个答案在回答什么问题。
  ① LLM 看着答案，生成 N 个"这个答案可能在回答什么问题"
  ② Embedding 算这些反向问题与原始问题的余弦相似度
  ③ 取平均 → 答非所问时反向问题与原问题差很远，分数低

在 asyncio.to_thread 线程内运行，使用同步 HTTP 客户端。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))


GEN_QUESTIONS_PROMPT = """给定下面这段回答，请反向推测它在回答什么问题。生成 {n} 个不同的、这段回答可能对应的问题。

回答：{answer}

只返回 JSON 数组，每个元素是一个问题字符串，不要加任何解释。"""


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class AnswerRelevanceEvaluator:
    """Answer Relevance：答案是否切题（防答非所问）。"""

    def __init__(self, n_questions: int = 3):
        self._api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self._base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self._model = os.getenv("LLM_MODEL", "deepseek-v4-flash")
        self._n = n_questions
        # 复用项目的阿里云 embedding
        from researcher.kb import _DashScopeEmbeddings
        self._embedder = _DashScopeEmbeddings()

    def evaluate(self, question: str, answer: str) -> dict:
        """返回 {score, generated_questions, similarities}。"""
        if not answer or not answer.strip():
            return {"score": 0.0, "generated_questions": [], "similarities": []}

        gen_questions = self._generate_questions(answer)
        if not gen_questions:
            return {"score": 0.0, "generated_questions": [], "similarities": []}

        # 原始问题 + 反向问题一起 embedding
        embeddings = self._embedder.embed([question] + gen_questions)
        q_emb = embeddings[0]
        gen_embs = embeddings[1:]

        sims = [_cosine(q_emb, ge) for ge in gen_embs]
        score = sum(sims) / len(sims) if sims else 0.0
        return {
            "score": score,
            "generated_questions": gen_questions,
            "similarities": [round(s, 4) for s in sims],
        }

    def _generate_questions(self, answer: str) -> list[str]:
        try:
            text = self._call_llm(
                GEN_QUESTIONS_PROMPT.format(n=self._n, answer=answer[:3000]),
                json_mode=True,
            )
            data = json.loads(text.strip())
            questions = data if isinstance(data, list) else data.get("questions", [])
            return [q for q in questions if isinstance(q, str) and len(q) > 3]
        except Exception:
            return []

    def _call_llm(self, prompt: str, json_mode: bool) -> str:
        import httpx
        body = {
            "model": self._model,
            "temperature": 0.3,  # 稍高温度让反向问题多样化
            "messages": [{"role": "user", "content": prompt}],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        resp = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=body,
            timeout=30,
        )
        return resp.json()["choices"][0]["message"]["content"] or ""


if __name__ == "__main__":
    ev = AnswerRelevanceEvaluator()

    # 切题的例子
    r1 = ev.evaluate(
        question="Python 是谁创建的",
        answer="Python 是由 Guido van Rossum 创建的，于 1991 年首次发布。",
    )
    print(f"[切题] score={r1['score']:.3f}")
    print(f"  反向问题: {r1['generated_questions']}")
    print(f"  相似度: {r1['similarities']}\n")

    # 答非所问的例子
    r2 = ev.evaluate(
        question="Python 是谁创建的",
        answer="Python 是一种广泛使用的编程语言，在数据科学和 Web 开发中很流行。",
    )
    print(f"[答非所问] score={r2['score']:.3f}")
    print(f"  反向问题: {r2['generated_questions']}")
    print(f"  相似度: {r2['similarities']}")
