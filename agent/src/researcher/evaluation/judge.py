"""LLM-as-Judge 报告质量打分框架。

对一份研究报告做五维度打分（1-5），用于对比改动前后的报告质量变化。

核心认知：
  - 绝对分不可靠（同一报告两次打分可能差 0.5），只看相对变化
  - 位置偏差：A/B 对比时顺序会影响判断 → 互换各评一次取平均
  - 冗长偏差：LLM 偏好长答案 → 评分维度内置"简洁性"对抗
  - 自我偏好：LLM 给自己生成的内容打高分 → 可选换模型家族复核

在 asyncio.to_thread 线程内运行，使用同步 HTTP 客户端。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))


JUDGE_PROMPT = """你是一个严格的研究报告质量评审员。请对下面这份报告在五个维度上打分（1-5 分，5 分最高），并给出简短理由。

{brief_block}用户问题：{question}

报告内容：
{report}

评分维度：
- completeness（完整性）：是否覆盖了问题的所有关键方面，有没有遗漏重要信息
- accuracy（准确性）：事实陈述是否有来源支撑，有没有明显错误或编造
- logic（逻辑性）：报告结构是否清晰，章节间过渡是否自然，论证是否有层次
- conciseness（简洁性）：信息密度是否高，有没有冗余和无意义的重复堆砌
- citation（引用质量）：关键事实是否标注来源，来源是否可靠、格式是否规范

只返回 JSON：
{{
  "completeness": {{"score": <1-5>, "reason": "..."}},
  "accuracy": {{"score": <1-5>, "reason": "..."}},
  "logic": {{"score": <1-5>, "reason": "..."}},
  "conciseness": {{"score": <1-5>, "reason": "..."}},
  "citation": {{"score": <1-5>, "reason": "..."}}
}}
不要加任何解释。"""

_WEIGHTS = {
    "completeness": 0.30,
    "accuracy": 0.30,
    "logic": 0.20,
    "conciseness": 0.10,
    "citation": 0.10,
}


class ReportJudge:
    """报告质量 LLM-as-Judge，五维度加权打分。"""

    def __init__(self, model: str = None):
        self._api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self._base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self._model = model or os.getenv("LLM_MODEL", "deepseek-v4-flash")

    def evaluate(self, question: str, report: str, research_brief: str = "") -> dict:
        """单份报告打分。返回 {overall, dimensions}。"""
        if not report or not report.strip():
            return {"overall": 0.0, "dimensions": {}}

        brief_block = f"研究简报：{research_brief}\n\n" if research_brief else ""
        try:
            text = self._call_llm(JUDGE_PROMPT.format(
                brief_block=brief_block,
                question=question,
                report=report[:12000],
            ))
            data = json.loads(text.strip())
        except Exception as e:
            print(f"  Judge 打分失败: {e}")
            return {"overall": 0.0, "dimensions": {}}

        # 加权总分
        overall = 0.0
        dims = {}
        for dim, weight in _WEIGHTS.items():
            entry = data.get(dim, {})
            score = entry.get("score", 0) if isinstance(entry, dict) else 0
            dims[dim] = {"score": score, "reason": entry.get("reason", "") if isinstance(entry, dict) else ""}
            overall += score * weight

        return {"overall": round(overall, 3), "dimensions": dims}

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
            timeout=60,
        )
        return resp.json()["choices"][0]["message"]["content"] or ""


def compare_reports(question: str, report_old: str, report_new: str,
                    research_brief: str = "", judge: ReportJudge = None) -> dict:
    """A/B 对比两份报告（改动前后）。返回各维度分差 + 总分差。

    绝对分不可靠，看的是相对变化——report_new 相比 report_old 的提升。
    """
    judge = judge or ReportJudge()
    old = judge.evaluate(question, report_old, research_brief)
    new = judge.evaluate(question, report_new, research_brief)

    dim_delta = {}
    for dim in _WEIGHTS:
        s_old = old["dimensions"].get(dim, {}).get("score", 0)
        s_new = new["dimensions"].get(dim, {}).get("score", 0)
        dim_delta[dim] = s_new - s_old

    return {
        "old_overall": old["overall"],
        "new_overall": new["overall"],
        "delta": round(new["overall"] - old["overall"], 3),
        "dim_delta": dim_delta,
        "old_detail": old["dimensions"],
        "new_detail": new["dimensions"],
    }


if __name__ == "__main__":
    judge = ReportJudge()

    good = """# 量子计算对密码学的威胁

## 概述
量子计算利用量子叠加和纠缠原理，对现有公钥密码体系构成理论威胁。

## 核心威胁：Shor 算法
Shor 算法可在多项式时间内分解大整数 [1]。RSA-2048 的安全性依赖大整数分解难题，
一旦有足够规模的量子计算机，RSA 将被攻破。破解 2048 位 RSA 约需 4000 个逻辑量子比特 [2]。

## 应对：后量子密码学
NIST 已在 2024 年标准化了首批后量子密码算法 [3]，包括 CRYSTALS-Kyber。

### 参考来源
[1] Shor 1994: https://example.com/shor
[2] Nature 2025: https://example.com/nature
[3] NIST PQC: https://example.com/nist"""

    bad = "量子计算很厉害，可能会破解密码。以后加密方式要改进。这是个重要问题。"

    print("=== 好报告 ===")
    r1 = judge.evaluate("量子计算对密码学的威胁", good)
    print(f"总分: {r1['overall']}")
    for d, v in r1["dimensions"].items():
        print(f"  {d}: {v['score']}  {v['reason'][:40]}")

    print("\n=== 差报告 ===")
    r2 = judge.evaluate("量子计算对密码学的威胁", bad)
    print(f"总分: {r2['overall']}")
    for d, v in r2["dimensions"].items():
        print(f"  {d}: {v['score']}  {v['reason'][:40]}")

    print(f"\n=== A/B 对比（差→好）===")
    cmp = compare_reports("量子计算对密码学的威胁", bad, good, judge=judge)
    print(f"总分: {cmp['old_overall']} → {cmp['new_overall']} (delta {cmp['delta']:+})")
    print(f"各维度提升: {cmp['dim_delta']}")
