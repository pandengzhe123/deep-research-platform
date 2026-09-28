"""查询改写 —— LLM 自动生成多个检索变体。"""

import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

log = logging.getLogger(__name__)

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

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------

    _WRAP_KEYS = ("queries", "variants", "list", "result", "items", "data", "content")

    @classmethod
    def _dig(cls, obj, depth: int = 0) -> list[str] | None:
        """从任意嵌套结构里挖出「非空字符串列表」。"""
        if depth > 4:
            return None
        if isinstance(obj, list):
            out = [v.strip() for v in obj if isinstance(v, str) and v.strip()]
            return out or None
        if isinstance(obj, dict):
            for k in cls._WRAP_KEYS:
                if k in obj:
                    got = cls._dig(obj[k], depth + 1)
                    if got:
                        return got
            for v in obj.values():          # 键名未知时兜底遍历
                got = cls._dig(v, depth + 1)
                if got:
                    return got
        return None

    @classmethod
    def _parse(cls, text: str) -> list[str] | None:
        """容错解析模型回复。

        实测这个环节的**唯一产出就是形态问题**（2026-09-28，60 题实测）：
          · 裸数组            ["a","b","c"]
          · 被 json_object 包住  {"queries": ["a","b","c"]}
          · **模型把 response_format 原样回显**  {"type":"json_object","content":[...]}
            甚至只回 {"type":"json_object"}
          · 前后带散文 → json.loads 抛 Extra data
        所以这里不追求"严格解析"，而是尽量把可用信息挖出来；
        真的挖不到才回退原问题（并且会记 warning，不再静默）。
        """
        if not text:
            return None
        try:
            got = cls._dig(json.loads(text))
            if got:
                return got
        except Exception:
            pass
        # 前后有散文：抠出第一个 JSON 数组
        m = re.search(r"\[.*?\]", text, re.DOTALL)
        if m:
            try:
                return cls._dig(json.loads(m.group(0)))
            except Exception:
                pass
        return None

    # ------------------------------------------------------------------
    # 对外
    # ------------------------------------------------------------------

    def rewrite(self, question: str) -> list[str]:
        """返回检索变体列表；任何失败都回退为 [原问题]，但**一定记日志**。

        ⚠️ 这里曾有一个完全静默的 bug，代价很大（2026-09-28 实测确认）：

        旧实现传 `response_format={"type":"json_object"}`，但只认裸数组：
            variants = json.loads(text)
            if isinstance(variants, list) and len(variants) > 0:
                return variants[:3]
            except Exception: pass
            return [question]
        json_object 会强制模型把数组**包进对象**，于是 isinstance 判 False →
        静默回退。实测 30 题里 **22 题（73%）改写什么都没做**，却照样付了
        约 2.6s 的 LLM 调用（占 full 链路延迟 81%）。日志、返回值全都看不出异常。
        这也解释了消融实验里 `full ≈ v2`：73% 的时候 full 确实就是 v2。

        修法两点：
          1. **不再传 response_format** —— 实测不传时模型直接返回干净的
             `["...","...","..."]`，而传了反而引入包装与回显两种新形态。
             prompt 里已经明确要求 JSON 数组，强约束得不偿失。
          2. 解析容错 + 失败必记 warning（见 `_parse`）。

        ⚠️ 另外要知道：**改写目前没有测出收益**。修好后实测 60 题，
        带变体的 chunk_mrr 反而低 0.0186（更好 2 题 / 更差 3 题 / 相同 55 题）。
        变体扩大了召回面（6 次检索 vs 2 次），但多出来的内容会挤掉正确结果。
        所以这个环节的价值仍是**未证实**的，而成本是每次检索一次 LLM 调用。
        保留现状是因为关掉它属于产品取舍，需要有意识的决定。
        """
        try:
            import httpx
            resp = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "temperature": 0.1,
                    "messages": [{"role": "user",
                                  "content": REWRITE_PROMPT.format(question=question)}],
                },
                timeout=30,
            )
            data = resp.json()

            # 有些后端失败时返回 HTTP 200 + 错误码，不检查会把"没改写"当成"改写好了"
            if isinstance(data, dict) and data.get("code"):
                log.warning("查询改写被拒绝: %s %s", data.get("code"), data.get("message"))
                return [question]

            text = data["choices"][0]["message"]["content"]
            variants = self._parse(text)
            if variants:
                return variants[:3]
            log.warning("查询改写返回了无法识别的结构，回退原问题: %s", str(text)[:120])
        except Exception as e:
            log.warning("查询改写失败，回退原问题: %s: %s", type(e).__name__, e)
        return [question]
