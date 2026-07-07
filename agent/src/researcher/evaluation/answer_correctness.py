"""Answer Correctness 评估器 —— 与 RAGAS 同逻辑，不依赖 RAGAS 库。

Faithfulness 只测"答案是否忠于文档"，不测"文档本身对不对"。
Answer Correctness 测的是事实真实性——答案与标准答案（ground truth）的一致程度。

思路（RAGAS 的 F1 式做法）：
  ① 把答案和标准答案各自拆成事实声明
  ② 逐条比对，分成三类：
     TP — 答案里有、标准答案里也有（正确陈述）
     FP — 答案里有、标准答案里没有（多余/编造陈述）
     FN — 标准答案里有、答案里没有（遗漏陈述）
  ③ F1 = 2·TP / (2·TP + FP + FN)

在 asyncio.to_thread 线程内运行，使用同步 HTTP 客户端。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))


CLASSIFY_PROMPT = """你是一个严格的事实核对器。给定"生成答案"和"标准答案"，请把它们的事实陈述分成三类。

生成答案：{answer}

标准答案：{ground_truth}

请分类：
- TP（正确）：生成答案中出现、且标准答案支持的事实陈述
- FP（多余）：生成答案中出现、但标准答案中没有或矛盾的事实陈述
- FN（遗漏）：标准答案中出现、但生成答案没有覆盖的事实陈述

只返回 JSON：{{"TP": ["陈述1", ...], "FP": ["陈述1", ...], "FN": ["陈述1", ...]}}
不要加任何解释。"""


class AnswerCorrectnessEvaluator:
    """Answer Correctness：答案与标准答案的事实一致程度（F1）。"""

    def __init__(self):
        self._api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self._base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self._model = os.getenv("LLM_MODEL", "deepseek-v4-flash")

    def evaluate(self, answer: str, ground_truth: str) -> dict:
        """返回 {score, tp, fp, fn, precision, recall}。"""
        if not answer or not answer.strip() or not ground_truth or not ground_truth.strip():
            return {"score": 0.0, "tp": 0, "fp": 0, "fn": 0, "precision": 0.0, "recall": 0.0}

        cls = self._classify(answer, ground_truth)
        tp = len(cls.get("TP", []))
        fp = len(cls.get("FP", []))
        fn = len(cls.get("FN", []))

        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0

        return {
            "score": f1,
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "detail": cls,
        }

    def _classify(self, answer: str, ground_truth: str) -> dict:
        try:
            text = self._call_llm(
                CLASSIFY_PROMPT.format(answer=answer[:3000], ground_truth=ground_truth[:2000]),
            )
            data = json.loads(text.strip())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

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
    ev = AnswerCorrectnessEvaluator()

    # 完全正确
    r1 = ev.evaluate(
        answer="Python 由 Guido van Rossum 创建，于 1991 年发布。",
        ground_truth="Python 是 Guido van Rossum 创建的，首次发布于 1991 年。",
    )
    print(f"[完全正确] F1={r1['score']:.3f} (TP={r1['tp']} FP={r1['fp']} FN={r1['fn']})\n")

    # 部分正确 + 编造
    r2 = ev.evaluate(
        answer="Python 由 Guido van Rossum 在 MIT 创建，于 1995 年发布。",
        ground_truth="Python 是 Guido van Rossum 创建的，首次发布于 1991 年。",
    )
    print(f"[部分错误] F1={r2['score']:.3f} (TP={r2['tp']} FP={r2['fp']} FN={r2['fn']})")
    print(f"  precision={r2['precision']} recall={r2['recall']}")
