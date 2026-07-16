"""回归测试 —— 改代码后快速验证系统没有退化。

用法:
  python -m researcher.evaluation.run_regression --mode retriever              # v2 检索回归 (25s)
  python -m researcher.evaluation.run_regression --mode retriever --retrieval-mode all  # 四种模式全测
  python -m researcher.evaluation.run_regression --mode format                 # 格式回归 (2min)
  python -m researcher.evaluation.run_regression --mode all                    # 全跑
  python -m researcher.evaluation.run_regression --update-baseline             # 更新全部基准
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

def _calc_mrr(result_text: str, expected_chunks: list[str]) -> float:
    """计算 Mean Reciprocal Rank——第一个正确答案排在第几位。"""
    # 从格式化结果中提取 --- 来源 N: 块的位置
    sources = re.findall(r'--- 来源 (\d+):', result_text)
    if not sources:
        return 0.0
    for rank, src_num in enumerate(sources):
        # 检查该来源块前的内容是否包含任一关键词
        block_pattern = rf'--- 来源 {src_num}:.*?(?=--- 来源 \d+:|$)'
        block_match = re.search(block_pattern, result_text, re.DOTALL)
        if block_match:
            block = block_match.group()
            if any(kw in block for kw in expected_chunks if kw):
                return 1.0 / (rank + 1)
    return 0.0


def run_retriever_regression(mode: str = "v2"):
    """跑检索回归，对比基准命中率和 MRR，检测退化。"""
    from researcher.kb import kb

    print("\n" + "=" * 60)
    print(f"  检索回归测试 ({mode})")
    print("=" * 60)

    with open(TESTSET, encoding="utf-8") as f:
        testset = json.load(f)

    hits, total = 0, 0
    mrr_sum = 0.0
    mrr_count = 0
    failures = []
    t0 = time.time()
    for item in testset:
        result = kb.search(item["question"], user_id="eval", mode=mode)
        expected = item.get("expected_chunks", [])
        if not expected:
            # no_answer 题型：文档中无答案，检索结果应返回"未找到"
            total += 1
            if "未找到" in result or "not found" in result.lower():
                hits += 1
                mrr_sum += 1.0
                mrr_count += 1
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
        # MRR: 只对有答案的题目计算
        mrr = _calc_mrr(result, expected)
        mrr_sum += mrr
        mrr_count += 1

    hit_rate = hits / total if total else 0
    mrr = mrr_sum / mrr_count if mrr_count else 0
    elapsed = time.time() - t0

    print(f"  题目: {total}  命中: {hits}  命中率: {hit_rate:.1%}  MRR: {mrr:.3f}  耗时: {elapsed:.1f}s")

    # 加载或创建基准
    passed = True
    baseline = _load_baseline()
    baseline_key_hit = f"retriever_{mode}_hit_rate"
    baseline_key_mrr = f"retriever_{mode}_mrr"
    if baseline and baseline.get(baseline_key_hit) is not None:
        baseline_hit = baseline[baseline_key_hit]
        baseline_mrr = baseline.get(baseline_key_mrr, 0)
        hit_threshold = baseline_hit - 0.02  # 允许 2% 波动
        mrr_threshold = baseline_mrr - 0.05  # 允许 0.05 波动
        print(f"  基准: 命中率 {baseline_hit:.1%} (>= {hit_threshold:.1%})  MRR {baseline_mrr:.3f} (>= {mrr_threshold:.3f})")

        if hit_rate < hit_threshold:
            print(f"\n  [FAIL] 命中率退化！{hit_rate:.1%} < {hit_threshold:.1%}")
            passed = False
        if mrr < mrr_threshold:
            print(f"  [FAIL] MRR 退化！{mrr:.3f} < {mrr_threshold:.3f}")
            passed = False
        if passed:
            print(f"  [PASS] 检索回归通过")
        else:
            print(f"  未命中/低MRR题目:")
            for f in failures[:5]:
                print(f"    - {f['question']}: 缺失 {f['missing']}")
    else:
        baseline = baseline or {}
        baseline[baseline_key_hit] = hit_rate
        baseline[baseline_key_mrr] = mrr
        _save_baseline(baseline)
        print(f"  [*] 已保存基准: 命中率 {hit_rate:.1%}, MRR {mrr:.3f}")

    _save_result(
        f"retriever_{mode}",
        hit_rate=hit_rate, mrr=round(mrr, 4),
        hits=hits, total=total,
        baseline=baseline.get(baseline_key_hit) if baseline else None,
        baseline_mrr=baseline.get(baseline_key_mrr) if baseline else None,
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
    parser.add_argument("--mode", choices=["retriever", "format", "all"], default="all",
                        help="回归类型: retriever(检索) / format(格式) / all(全部)")
    parser.add_argument("--retrieval-mode", choices=["v2", "hybrid", "rerank", "full", "all"], default="v2",
                        help="检索回归覆盖的模式: v2/hybrid/rerank/full/all")
    parser.add_argument("--update-baseline", action="store_true",
                        help="更新基准")
    args = parser.parse_args()

    retrieval_modes = ["v2", "hybrid", "rerank", "full"] if args.retrieval_mode == "all" else [args.retrieval_mode]

    if args.update_baseline:
        print("更新基准...")
        from researcher.kb import kb
        with open(TESTSET, encoding="utf-8") as f:
            testset = json.load(f)
        baseline = _load_baseline() or {}
        for rm in retrieval_modes:
            hits, mrr_sum, mrr_count = 0, 0.0, 0
            for item in testset:
                result = kb.search(item["question"], user_id="eval", mode=rm)
                expected = item.get("expected_chunks", [])
                if not expected:
                    hits += 1 if ("未找到" in result or "not found" in result.lower()) else 0
                    mrr_sum += 1.0; mrr_count += 1
                else:
                    if all(kw in result for kw in expected):
                        hits += 1
                    mrr_sum += _calc_mrr(result, expected)
                    mrr_count += 1
            hit_rate = hits / len(testset) if testset else 0
            mrr_val = mrr_sum / mrr_count if mrr_count else 0
            baseline[f"retriever_{rm}_hit_rate"] = hit_rate
            baseline[f"retriever_{rm}_mrr"] = round(mrr_val, 4)
            print(f"  {rm}: 命中率 {hit_rate:.1%}, MRR {mrr_val:.3f}")
        _save_baseline(baseline)
        print(f"基准已更新 ({len(retrieval_modes)} 个模式)")
        return

    all_pass = True

    if args.mode in ("retriever", "all"):
        for rm in retrieval_modes:
            if not run_retriever_regression(mode=rm):
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
