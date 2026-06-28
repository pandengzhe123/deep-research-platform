"""查询改写 —— LLM 自动生成多个检索变体。"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

REWRITE_PROMPT = """你是一个查询改写助手。用户会提出一个问题，你需要生成 3 个不同角度/措辞的检索查询词。

用户问题：{question}

请返回 JSON 数组：["查询变体1", "查询变体2", "查询变体3"]"""


class QueryRewriter:
    """查询改写——在 asyncio.to_thread 的线程内运行，使用同步 HTTP 客户端。"""
    def __init__(self):
        import httpx
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))
        self._api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self._base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self._model = os.getenv("LLM_MODEL", "deepseek-v4-flash")

    def rewrite(self, question: str) -> list[str]:
        try:
            import httpx
            resp = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "temperature": 0.1,
                    "messages": [{"role": "user", "content": REWRITE_PROMPT.format(question=question)}],
                    "response_format": {"type": "json_object"},
                },
                timeout=30,
            )
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            variants = json.loads(text)
            if isinstance(variants, list) and len(variants) > 0:
                return variants[:3]
        except Exception:
            pass
        return [question]
