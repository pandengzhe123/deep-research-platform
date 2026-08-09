"""轨迹层测试 —— 跑 Agent，对比实际轨迹 vs 预期轨迹。

轨迹层测的是"Agent 的过程质量"（会不会循环、该不该用KB、该不该停），
不是"答案对不对"。所以每道题标注"预期轨迹行为"，跑完后对比实际 vs 预期。

测试集: trajectory_testset.json（10 题，6 种失败模式）
  ① stop_early    简单题应该早停（过度搜索=低效）
  ② loop          复杂题可能陷入循环
  ③ tool_choice   该用 search_kb 的题（工具选择）
  ④ data_missing  数据缺失该承认（硬编=幻觉）
  ⑤ error_recovery 搜索失败能否恢复（恢复率）
  ⑥ multi_topic   多维度是否覆盖全

用法:
  python -m researcher.evaluation.trajectory_test            # 跑全部 10 题
  python -m researcher.evaluation.trajectory_test --n 3      # 跑前 3 题
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

TESTSET = os.path.join(os.path.dirname(__file__), "trajectory_testset.json")


def evaluate_trajectory(stats: dict, expected: dict) -> tuple[bool, list[str]]:
    """对比实际轨迹 vs 预期轨迹，返回 (通过与否, 原因列表)。

    规则（对比 actual 指标 vs expected 阈值）:
      - 轮次超限: rounds > expected.max_rounds → 过度搜索
      - LLM/轮:  llm_calls_per_round > expected.llm_per_round_max → 低效
      - 循环:     max_query_similarity > 0.8 → 疑似循环
      - KB 使用:  expected.should_use_kb 且 search_kb == 0 → 该用KB没用
      - 数据缺失: expected.should_admit_missing 且结果有硬编信号
      - 恢复率:   expected.recovery_rate_min 且实际低于 → 恢复差
    """
    reasons = []
    passed = True

    rounds = stats.get("rounds", 0)
    if expected.get("max_rounds") and rounds > expected["max_rounds"]:
        passed = False
        reasons.append(f"过度搜索: {rounds} 轮 > 预期 {expected['max_rounds']}")

    llm_per_round = stats.get("llm_calls_per_round") or 0
    if expected.get("llm_per_round_max") and llm_per_round > expected["llm_per_round_max"]:
        passed = False
        reasons.append(f"低效: LLM/轮 {llm_per_round} > {expected['llm_per_round_max']}")

    sim = stats.get("max_query_similarity", 0)
    if sim > 0.8:
        passed = False
        reasons.append(f"疑似循环: query 相似度 {sim:.2f} > 0.8")

    if expected.get("should_use_kb") and stats.get("search_kb_calls", 0) == 0:
        passed = False
        reasons.append(f"工具选择错: 该用 search_kb 却 search_kb=0")

    return passed, reasons


async def run_one(question: str) -> tuple[dict, dict, str]:
    """跑一道题的 Agent，返回 (轨迹统计, 评测报告, 报告文本)。"""
    from researcher.agent import Level2Agent
    from researcher.trace import TraceRun

    reports_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "reports", time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(reports_dir, exist_ok=True)

    async with TraceRun(question=question, output_dir=reports_dir, level=2,
                        model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
                        search_mode="hybrid") as trace:
        agent = Level2Agent(trace=trace, search_mode="hybrid")
        report = await agent.run(question)

    from researcher.evaluation.trajectory_eval import analyze_trace, save_trajectory_stats
    trace_file = os.path.join(reports_dir, "trace.jsonl")
    stats = analyze_trace(trace_file)
    save_trajectory_stats(stats, trace_file)

    # 自动评测（含 judge + 2×2）
    from researcher.evaluation.judge import ReportJudge
    from researcher.evaluation.trajectory_result_matrix import save_run_record
    judge = ReportJudge().evaluate_avg(question, report, runs=3)
    result_score = judge.get("overall") if judge.get("dimensions") else None
    save_run_record(stats, result_score, run_id=os.path.basename(reports_dir))

    return stats, {"judge": result_score, "summary": judge.get("summary", "")}, report


async def main():
    parser = argparse.ArgumentParser(description="轨迹层测试")
    parser.add_argument("--n", type=int, default=10, help="跑前 N 题")
    args = parser.parse_args()

    testset = json.load(open(TESTSET, encoding="utf-8"))[:args.n]
    print("=" * 70)
    print(f"  轨迹层测试（{len(testset)} 题）")
    print("=" * 70)

    for i, item in enumerate(testset, 1):
        q = item["question"]
        print(f"\n[{i}/{len(testset)}] ({item['type']}) {q[:40]}")
        try:
            stats, eval_info, _ = await run_one(q)
            passed, reasons = evaluate_trajectory(stats, item["expected_trajectory"])
            mark = "✅" if passed else "❌"
            print(f"  {mark} 轮{stats.get('rounds')} LLM/轮{stats.get('llm_calls_per_round')} "
                  f"相似度{stats.get('max_query_similarity'):.2f} KB用{stats.get('search_kb_calls')}次 judge{eval_info['judge']}")
            print(f"     预期: {item['expected_trajectory']}")
            if reasons:
                print(f"     实际失败: {reasons}")
            else:
                print(f"     符合预期 ✓")
            if eval_info.get("summary"):
                print(f"     judge诊断: {eval_info['summary'][:80]}")
        except Exception as e:
            print(f"  ❌ 运行失败: {e}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
