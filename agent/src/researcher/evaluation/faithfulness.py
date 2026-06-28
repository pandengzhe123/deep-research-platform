"""自实现 Faithfulness 评估器 —— 和 RAGAS 同逻辑，不依赖 RAGAS 库。

Faithfulness: 把答案拆成独立声明 → 逐条检查是否有文档支撑 → 被支撑声明数 / 总声明数。
"""

import json
import os

from openai import OpenAI


SPLIT_PROMPT = """将下面的回答拆解为独立的、可验证的事实声明。每条声明应是一句简洁的陈述。
返回 JSON 数组：["声明1", "声明2", ...]

回答：{answer}

只返回 JSON 数组，不要加任何解释。"""


VERIFY_PROMPT = """你是一个严格的验证器，判断一条声明能否从以下文档中找到支撑。

文档内容：
{context}

声明：{claim}

规则：
- 如果声明和文档内容完全一致或含义一致 → 返回 "yes"
- 如果声明与文档内容矛盾 → 返回 "no"
- 如果文档中完全没有相关信息 → 返回 "no"
- 如果声明是文档内容的合理推断 → 返回 "yes"

只返回 "yes" 或 "no"："""


class FaithfulnessEvaluator:
    def __init__(self):
        self._client = OpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
        self._model = os.getenv("LLM_MODEL", "deepseek-v4-flash")

    def evaluate(self, question: str, answer: str, contexts: list[str]) -> dict:
        """
        返回 {"score": 0.0-1.0, "supported": int, "total": int, "claims": [...]}
        """
        # 1. 拆成声明
        claims = self._split_claims(answer)
        if not claims:
            return {"score": 0.0, "supported": 0, "total": 0, "claims": []}

        ctx = "\n\n".join(contexts)

        # 2. 逐条验证
        supported = 0
        verified = []
        for claim in claims:
            ok = self._verify_claim(claim, ctx)
            verified.append({"claim": claim, "supported": ok})
            if ok:
                supported += 1

        score = supported / len(claims) if claims else 0.0
        return {"score": score, "supported": supported, "total": len(claims), "claims": verified}

    def _split_claims(self, answer: str) -> list[str]:
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                messages=[{"role": "user", "content": SPLIT_PROMPT.format(answer=answer)}],
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            claims = data if isinstance(data, list) else data.get("claims", [])
            return [c for c in claims if isinstance(c, str) and len(c) > 3]
        except Exception:
            return []

    def _verify_claim(self, claim: str, context: str) -> bool:
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                messages=[{"role": "user", "content": VERIFY_PROMPT.format(context=context, claim=claim)}],
            )
            return (resp.choices[0].message.content or "").strip().lower().startswith("yes")
        except Exception:
            return False


def evaluate_generator(results: list[dict], docs: dict) -> dict:
    """对 Generator 测试结果做 Faithfulness 评估。"""
    evaluator = FaithfulnessEvaluator()
    scores = []
    for r in results:
        if r["type"] == "no_answer" or not r["context_docs"]:
            continue
        contexts = [docs.get(d, "") for d in r["context_docs"]]
        result = evaluator.evaluate(r["question"], r["answer"], contexts)
        scores.append(result["score"])

    if not scores:
        return {"avg_faithfulness": "N/A", "n": 0}

    avg = sum(scores) / len(scores)
    return {"avg_faithfulness": f"{avg:.2%}", "n": len(scores), "scores": scores}


if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

    # 测试
    evaluator = FaithfulnessEvaluator()
    r = evaluator.evaluate(
        "Python 是谁创建的",
        "Python 由 Guido van Rossum 创建于 1991 年。它是世界上最好的语言。",
        ["Python is a high-level programming language created by Guido van Rossum and first released in 1991."],
    )
    print(r)
