"""自实现 Faithfulness 评估器 —— 和 RAGAS 同逻辑，不依赖 RAGAS 库。

Faithfulness: 把答案拆成独立声明 → 逐条检查是否有文档支撑 → 被支撑声明数 / 总声明数。
在 asyncio.to_thread 线程内运行，使用同步 HTTP 客户端。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))


SPLIT_PROMPT = """将下面的回答拆解为独立的、可验证的事实声明。每条声明应是一句简洁的陈述。

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
        import httpx
        self._api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self._base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self._model = os.getenv("LLM_MODEL", "deepseek-v4-flash")

    def evaluate(self, question: str, answer: str, contexts: list[str]) -> dict:
        claims = self._split_claims(answer)
        if not claims:
            return {"score": 0.0, "supported": 0, "total": 0, "claims": []}

        ctx = "\n\n".join(contexts)
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
            text = self._call_llm(SPLIT_PROMPT.format(answer=answer), json_mode=True)
            data = json.loads(text.strip())
            claims = data if isinstance(data, list) else data.get("claims", [])
            return [c for c in claims if isinstance(c, str) and len(c) > 3]
        except Exception:
            return []

    def _verify_claim(self, claim: str, context: str) -> bool:
        try:
            text = self._call_llm(VERIFY_PROMPT.format(context=context, claim=claim), json_mode=False)
            return text.strip().lower().startswith("yes")
        except Exception:
            return False

    def _call_llm(self, prompt: str, json_mode: bool) -> str:
        import httpx
        body = {
            "model": self._model,
            "temperature": 0,
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
