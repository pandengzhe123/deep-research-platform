"""回归测试 —— 改代码后快速验证系统没有退化。

用法:
  python -m researcher.evaluation.run_regression --mode retriever   # 检索回归 (20s)
  python -m researcher.evaluation.run_regression --mode format      # 格式回归 (2min)
  python -m researcher.evaluation.run_regression --mode all         # 全跑
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

# ============================================================
# 配置
# ============================================================

TESTSET = os.path.join(os.path.dirname(__file__), "golden_testset_v4.json")
BASELINE_FILE = os.path.join(os.path.dirname(__file__), ".regression_baseline.json")

# 保存回归结果到 results/ 下
from researcher.evaluation._results import run_dir_for as _run_dir_for

# 格式回归用的固定题目（简单 + 中等 + 口语化，确保 Agent 能稳定出报告）
FORMAT_QUESTIONS = [
    "什么是 Python 协程",
    "Docker 和虚拟机有什么区别",
    "Redis 怎么配置哨兵模式",
    "怎么用 JWT 做登录",
    "分布式锁是怎么工作的",
]

# 格式检查规则（正则，全部通过才算合格）
FORMAT_RULES = {
    "report_non_empty": (r".", "报告不能为空"),
    "has_heading": (r"^#+\s", "必须有 Markdown 标题"),
    "has_citation": (r"\[.+\]\(https?://.+\)", "必须有引用链接 [标题](URL)"),
    "has_sources_section": (r"(?i)(参考来源|Sources|参考资料|引用来源)", "必须有参考来源章节"),
    "min_length": (r"^.{500,}$", "报告长度 >= 500 字符", re.DOTALL),
    "has_conclusion": (r"(?i)(总结|结论|概述|Overview|Conclusion|Summary)", "必须有概述或总结段落"),
}


# ============================================================
# 检索回归
# ============================================================

def run_retriever_regression():
    """跑 v2 检索，对比基准命中率，检测退化。"""
    from researcher.kb import kb

    print("\n" + "=" * 60)
    print("  检索回归测试 (v2)")
    print("=" * 60)

    with open(TESTSET, encoding="utf-8") as f:
        testset = json.load(f)

    hits, total = 0, 0
    failures = []
    t0 = time.time()
    for item in testset:
        result = kb.search(item["question"], user_id="eval", mode="v2")
        expected = item.get("expected_chunks", [])
        if not expected:
            # no_answer 题型：文档中无答案，检索结果应返回"未找到"
            total += 1
            if "未找到" in result or "not found" in result.lower():
                hits += 1
            else:
                failures.append({"question": item["question"][:50], "missing": ["应返回未找到但实际有结果"]})
            continue
        all_found = all(kw in result for kw in expected)
        total += 1
        if all_found:
            hits += 1
        else:
            missing = [kw for kw in expected if kw not in result]
            failures.append({"question": item["question"][:50], "missing": missing})

    hit_rate = hits / total if total else 0
    elapsed = time.time() - t0

    print(f"  题目: {total}  命中: {hits}  命中率: {hit_rate:.1%}  耗时: {elapsed:.1f}s")

    # 加载或创建基准
    passed = True
    baseline = _load_baseline()
    if baseline:
        baseline_rate = baseline.get("retriever_hit_rate", 0)
        threshold = baseline_rate - 0.02  # 允许 2% 波动
        print(f"  基准命中率: {baseline_rate:.1%}  阈值: {threshold:.1%}")

        if hit_rate < threshold:
            print(f"\n  [FAIL] 检索回归失败！命中率 {hit_rate:.1%} < {threshold:.1%}")
            print(f"  未命中题目:")
            for f in failures[:5]:
                print(f"    - {f['question']}: 缺失 {f['missing']}")
            passed = False
        else:
            print(f"  [PASS] 检索回归通过 ({hit_rate:.1%} >= {threshold:.1%})")
    else:
        _save_baseline({"retriever_hit_rate": hit_rate})
        print(f"  [*] 已保存基准: {hit_rate:.1%}（首次运行，无对比）")

    # 保存结果
    _save_result(
        "retriever",
        hit_rate=hit_rate, hits=hits, total=total,
        baseline=baseline.get("retriever_hit_rate") if baseline else None,
        passed=passed, elapsed_s=round(elapsed, 1),
        failures=failures[:20],
    )
    return passed


# ============================================================
# 格式回归
# ============================================================

async def run_format_regression():
    """跑 Level 2 生成报告，正则检查格式规则，检测退化。"""
    from researcher.agent import Level2Agent

    print("\n" + "=" * 60)
    print("  格式回归测试 (Level 2)")
    print("=" * 60)

    t0_all = time.time()
    agent = Level2Agent(search_mode="web_only")
    failed_any = False

    for i, question in enumerate(FORMAT_QUESTIONS):
        print(f"\n  [{i+1}/{len(FORMAT_QUESTIONS)}] {question}")
        t0 = time.time()
        try:
            report = await agent.run(question)
        except Exception as e:
            print(f"    [FAIL] Agent 运行失败: {e}")
            failed_any = True
            continue
        elapsed = time.time() - t0

        # 逐条检查格式规则
        all_pass = True
        for rule_name, (pattern, description) in FORMAT_RULES.items():
            flags = 0
            if len(pattern) == 3:  # (pattern, desc, flags)
                pattern, description, flags = pattern, description, pattern if isinstance(description, int) else 0
            # re.DOTALL 处理
            regex_flags = re.DOTALL if rule_name == "min_length" else re.MULTILINE
            if re.search(pattern, report, regex_flags):
                print(f"    [OK] {rule_name}")
            else:
                print(f"    [FAIL] {rule_name} — {description}")
                all_pass = False

        if all_pass:
            print(f"    [PASS] 全部通过 ({elapsed:.1f}s)")
        else:
            print(f"    [FAIL] 格式检查失败 ({elapsed:.1f}s)")
            print(f"    --- 报告预览（前 200 字）---")
            print(f"    {report[:200]}...")
            failed_any = True

    _save_result(
        "format",
        questions=FORMAT_QUESTIONS,
        passed=not failed_any,
        elapsed_s=round(time.time() - t0_all if 't0_all' in dir() else 0, 1),
    )
    print(f"\n  {'[FAIL] 格式回归失败' if failed_any else '[PASS] 格式回归通过'}")
    return not failed_any


# ============================================================
# 工具函数
# ============================================================

def _load_baseline():
    if os.path.exists(BASELINE_FILE):
        with open(BASELINE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_baseline(data):
    with open(BASELINE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _save_result(mode: str, **kwargs):
    """保存回归结果到 results/<timestamp>_regression/ 目录。"""
    out_dir = os.path.join(_run_dir_for("regression"), mode)
    os.makedirs(out_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"{timestamp}.json")
    result = {"mode": mode, "timestamp": timestamp, **kwargs}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"  [*] 结果已保存: {out_path}")


async def main():
    parser = argparse.ArgumentParser(description="回归测试")
    parser.add_argument("--mode", choices=["retriever", "format", "all"], default="all")
    parser.add_argument("--update-baseline", action="store_true",
                        help="更新基准（改了 Prompt 后用新分数更新基准）")
    args = parser.parse_args()

    if args.update_baseline:
        print("更新基准...")
        from researcher.kb import kb
        with open(TESTSET, encoding="utf-8") as f:
            testset = json.load(f)
        hits = 0
        for item in testset:
            result = kb.search(item["question"], user_id="eval", mode="v2")
            expected = item.get("expected_chunks", [])
            if not expected:
                hits += 1 if ("未找到" in result or "not found" in result.lower()) else 0
            elif all(kw in result for kw in expected):
                hits += 1
        _save_baseline({"retriever_hit_rate": hits / len(testset) if testset else 0})
        print(f"基准已更新: {hits}/{len(testset)}")
        return

    all_pass = True

    if args.mode in ("retriever", "all"):
        if not run_retriever_regression():
            all_pass = False

    if args.mode in ("format", "all"):
        if not await run_format_regression():
            all_pass = False

    print("\n" + "=" * 60)
    if all_pass:
        print("  [PASS] 全部回归测试通过")
        print("=" * 60)
        sys.exit(0)
    else:
        print("  [FAIL] 回归测试失败，请检查上述失败项")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
