"""报告质量测试 —— 本地规则检查 + LLM 快速自评。

运行方式:
  cd D:\deep_research\agent
  set PYTHONUTF8=1 && .venv\Scripts\python tests\test_quality.py
"""

import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from researcher.llm import LLMClient
from researcher.agent import FastLevel1Agent, Level2Agent

# ============================================================
# 测试用例
# ============================================================

TEST_QUESTIONS = [
    {
        "question": "什么是量子计算？",
        "expected_lang": "zh",
        "min_sections": 2,
    },
    {
        "question": "列出三个最流行的 Python Web 框架",
        "expected_lang": "zh",
        "min_sections": 1,
    },
    {
        "question": "Compare REST and GraphQL",
        "expected_lang": "en",
        "min_sections": 2,
    },
]

# ============================================================
# 规则检查
# ============================================================

def check_not_empty(report: str) -> tuple[bool, str]:
    if report and len(report) > 100:
        return True, f"报告长度: {len(report)} 字符"
    return False, "报告过短或为空"

def check_has_headings(report: str) -> tuple[bool, str]:
    h1 = len(re.findall(r"^# ", report, re.MULTILINE))
    h2 = len(re.findall(r"^## ", report, re.MULTILINE))
    if h1 >= 1:
        return True, f"# 标题: {h1} 个, ## 章节: {h2} 个"
    return False, "缺少 # 或 ## 标题"

def check_has_sources(report: str) -> tuple[bool, str]:
    # 检查是否有链接 [text](url) 或 Sources/参考来源 章节
    links = re.findall(r"\[([^\]]+)\]\(https?://[^)]+\)", report)
    has_sources_section = bool(re.search(r"(Sources|参考来源|参考源)", report))
    if links or has_sources_section:
        return True, f"链接: {len(links)} 个, Sources 章节: {'有' if has_sources_section else '无'}"
    return False, "没有引用链接或 Sources 章节"

def check_language(report: str, expected: str) -> tuple[bool, str]:
    """简单检查：中文报告应包含中文字符，英文不强制。"""
    if expected == "zh":
        cn_chars = len(re.findall(r"[一-鿿]", report))
        return cn_chars > 20, f"中文字符数: {cn_chars}"
    return True, "英文报告不检测语言"

# ============================================================
# LLM 自评
# ============================================================

REVIEW_PROMPT = """你是报告质量评审员。请为以下报告打分（1-5 分）。

评分维度：
- relevance (相关性): 报告是否紧扣问题
- structure (结构): 章节是否清晰合理
- sources (引用): 是否包含引用和来源

只返回 JSON：
{
    "relevance": <1-5>,
    "structure": <1-5>,
    "sources": <1-5>,
    "note": "一句话评价"
}

报告问题：{question}

报告内容：
{report}"""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance": {"type": "integer", "minimum": 1, "maximum": 5},
        "structure": {"type": "integer", "minimum": 1, "maximum": 5},
        "sources": {"type": "integer", "minimum": 1, "maximum": 5},
        "note": {"type": "string"},
    },
    "required": ["relevance", "structure", "sources"],
    "additionalProperties": False,
}


def llm_review(llm: LLMClient, question: str, report: str) -> dict:
    """用 LLM 自评报告质量。"""
    try:
        return llm.structured_output(
            system_prompt="你是报告质量评审员。",
            user_message=REVIEW_PROMPT.format(question=question, report=report[:3000]),
            schema=REVIEW_SCHEMA,
        )
    except Exception as e:
        return {"relevance": 0, "structure": 0, "sources": 0, "note": str(e)}


# ============================================================
# 运行
# ============================================================

async def run_tests():
    llm = LLMClient()
    # 限制搜索轮数加速测试
    from researcher.config import config

    config.max_search_rounds = 3
    agents = {
        "Level1": FastLevel1Agent(),
        "Level2": Level2Agent(),
    }

    total = 0
    passed = 0
    all_results = []

    print("=" * 70)
    print(f"  报告质量测试  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"  问题数: {len(TEST_QUESTIONS)} × {len(agents)} 个 Agent = {len(TEST_QUESTIONS) * len(agents)} 次测试")
    print("=" * 70)

    for tc in TEST_QUESTIONS:
        for agent_name, agent in agents.items():
            total += 1
            q = tc["question"]
            print(f"\n{'─' * 60}")
            print(f"  [{agent_name}] {q}")

            try:
                report = await agent.run(q)
            except Exception as e:
                print(f"  ❌ Agent 运行失败: {e}")
                all_results.append({
                    "question": q, "agent": agent_name, "status": "error",
                    "error": str(e),
                })
                continue

            # 规则检查
            checks = [
                ("内容", check_not_empty(report)),
                ("标题", check_has_headings(report)),
                ("引用", check_has_sources(report)),
                ("语言", check_language(report, tc["expected_lang"])),
            ]

            rules_ok = 0
            for name, (ok, detail) in checks:
                print(f"    {'✅' if ok else '❌'} {name}: {detail}")
                if ok:
                    rules_ok += 1

            # LLM 评分
            print(f"    ⏳ LLM 评分中...")
            scores = llm_review(llm, q, report)
            print(f"    📊 相关性={scores.get('relevance','?')} "
                  f"结构={scores.get('structure','?')} "
                  f"引用={scores.get('sources','?')}")
            if scores.get("note"):
                print(f"    💬 {scores['note']}")

            rule_pass = rules_ok == 4
            if rule_pass:
                passed += 1
                print(f"    ✅ 通过（规则检查 4/4）")
            else:
                print(f"    ⚠️ 未通过（规则检查 {rules_ok}/4）")

            all_results.append({
                "question": q, "agent": agent_name, "status": "ok",
                "rules_ok": rules_ok, "scores": scores,
            })

    # 汇总
    print(f"\n{'=' * 70}")
    print(f"  结果: {passed}/{total} 通过")
    if total > 0:
        print(f"  通过率: {passed / total:.0%}")
    print("=" * 70)

    return passed == total


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    exit(0 if success else 1)
