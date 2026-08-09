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


# ============================================================
# 方案 C：答案存在性判断 —— Faithfulness 的 no_answer 分支
#
# 背景问题：
#   Faithfulness 隐含假设"一定有答案"——它只测"答案忠于文档"。
#   no_answer 题（知识库没答案）被 run_faithfulness_eval 跳过：
#     - 生成层正确拒绝（"未找到相关信息"）→ 拆不出声明 → 判 0 分（错！应该满分）
#     - 生成层硬编（编造答案）→ 拆出声明 → 文档没支撑 → 判 0 分（对）
#   → 分不清"正确拒绝"和"硬编"，拒绝能力这一维整个丢了。
#
# 方案 C：用 LLM 判断"检索到的文档里到底有没有这个问题的答案"。
#   - 文档里没答案 + 生成层正确拒绝 → 满分（没编造任何东西）
#   - 文档里没答案 + 生成层硬编     → 0 分（编造了无支撑声明）
#   - 文档里有答案（no_answer 误标）→ 应改为有答案的题，另行处理
#
# 为什么用 LLM 而不是关键词（原 _is_rejection）：
#   - 关键词漏判："我无法给出确切答案" 没触发关键词但确实拒绝了
#   - 关键词误判："未找到相关内容，以下是相关知识" 有"未找到"但后半句硬编
# ============================================================

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


class AnswerExistenceEvaluator:
    """方案 C：答案存在性判断。判断文档里有没有这个问题的答案。

    用于 Faithfulness 的 no_answer 分支——区分"正确拒绝"和"硬编"。
    """

    def __init__(self):
        import httpx
        self._api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self._base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self._model = os.getenv("LLM_MODEL", "deepseek-v4-flash")

    def evaluate(self, question: str, answer: str, contexts: list[str]) -> dict:
        """判断"文档里有没有答案" + "生成层是否拒绝"。

        返回:
          {
            "doc_has_answer": bool,   # 文档里是否有答案（LLM 判断）
            "answer_rejected": bool|None,  # 生成层是否拒绝（None=空答案无法判断）
            "correct": bool,          # 判定是否合理：
                                     #   doc无答案 且 拒绝 → 正确拒绝（满分）
                                     #   doc无答案 且 硬编 → 硬编（0分）
                                     #   doc有答案 且 拒绝 → 误拒（该答不答）
                                     #   doc有答案 且 回答 → 正常（不是no_answer题）
          }
        """
        doc_has_answer = self._check_doc_has_answer(question, contexts)
        answer_rejected = self._is_answer_rejection(answer)
        correct = self._judge(doc_has_answer, answer_rejected)
        return {
            "doc_has_answer": doc_has_answer,
            "answer_rejected": answer_rejected,
            "correct": correct,
        }

    def _check_doc_has_answer(self, question: str, contexts: list[str]) -> bool:
        """LLM 判断文档里有没有这个问题的答案（方案 C 核心）。"""
        ctx = "\n\n".join(contexts) if contexts else ""
        if not ctx.strip():
            return False  # 没喂文档 = 没答案
        try:
            text = self._call_llm(
                ANSWER_EXISTS_PROMPT.format(question=question, context=ctx[:6000])
            )
            return text.strip().lower().startswith("yes")
        except Exception:
            return False

    def _is_answer_rejection(self, answer: str) -> bool | None:
        """判断生成层是否拒绝（诚实说没有信息）。用关键词，配合 doc_has_answer 一起判。"""
        if not answer or not answer.strip():
            return None
        low = answer.lower()
        # 复用 generator_test 的拒绝短语（避免两处维护不一致）
        try:
            from researcher.evaluation.generator_test import REJECT_KEYWORDS
        except ImportError:
            REJECT_KEYWORDS = ["未找到", "没有找到", "不存在", "not found", "无法回答", "没有相关信息"]
        return any(k in low for k in REJECT_KEYWORDS)

    def _judge(self, doc_has_answer: bool, answer_rejected: bool | None) -> bool:
        """综合判定：这个答案对于 no_answer 题合不合理。"""
        if answer_rejected is None:
            return False  # 空答案：无法判断，不算正确
        if doc_has_answer:
            # 文档有答案 → 不是真 no_answer（标注错误或检索到了答案）
            return answer_rejected is False  # 有答案却拒绝 = 误拒（错）；有答案且回答 = 正常
        else:
            # 文档没答案 → 真 no_answer → 必须拒绝
            return answer_rejected is True  # 正确拒绝 = 满分；硬编 = 0分

    def _call_llm(self, prompt: str) -> str:
        import httpx
        resp = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        return resp.json()["choices"][0]["message"]["content"] or ""


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
