"""四级 Agent 同题对比 —— 报告质量（judge 五维） vs token 消耗。

用同一个复杂问题跑 Level 1/2/3/4，对比:
  - 报告质量: judge 五维加权分（3 次平均压噪音）
  - 成本: total_tokens / 耗时 / LLM 调用次数
  - 归因: 质量/token 性价比

问题设计: 多维复杂题，L4（Supervisor 拆解）优势能体现。

用法:
  python -m researcher.evaluation.level_compare
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

# 复杂问题：多维，需要拆解多个子课题
QUESTION = (
    "对比 Redis 和 Kafka 在缓存、消息队列、流处理三种场景下的架构设计差异，"
    "分析各自的适用场景，并给出选型建议"
)

LEVELS = [1, 2, 3, 4]


async def run_level(level: int, question: str) -> dict:
    """跑一个 Level，返回 (judge分, token, 耗时, LLM调用数, 报告)。"""
    from researcher.agent import FastLevel1Agent, Level2Agent, Level3Agent, Level4Agent
    from researcher.trace import TraceRun
    from researcher.evaluation.judge import ReportJudge

    reports_dir = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "reports",
        f"level{level}_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(reports_dir, exist_ok=True)

    t0 = time.time()
    async with TraceRun(question=question, output_dir=reports_dir, level=level,
                        model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
                        search_mode="hybrid") as trace:
        if level == 1:
            agent = FastLevel1Agent(trace=trace, search_mode="hybrid")
        elif level == 3:
            agent = Level3Agent(trace=trace, search_mode="hybrid")
        elif level == 4:
            agent = Level4Agent(trace=trace, search_mode="hybrid")
        else:
            agent = Level2Agent(trace=trace, search_mode="hybrid")
        report = await agent.run(question)

    duration = time.time() - t0

    # 保存报告
    (__import__("pathlib").Path(reports_dir) / "report.md").write_text(report, encoding="utf-8")

    # judge 打分（3 次平均压噪音）
    judge = ReportJudge().evaluate_avg(question, report, runs=3)
    judge_ok = bool(judge.get("dimensions"))
    judge_score = judge.get("overall") if judge_ok else None
    judge_dims = judge.get("dimensions", {}) if judge_ok else {}
    judge_summary = judge.get("summary", "") if judge_ok else ""

    tokens = {
        "prompt": trace._total_prompt_tokens,
        "completion": trace._total_completion_tokens,
        "total": trace._total_prompt_tokens + trace._total_completion_tokens,
    }
    return {
        "level": level,
        "judge": judge_score,
        "dims": {k: v.get("score") for k, v in judge_dims.items()},
        "summary": judge_summary,
        "tokens": tokens,
        "llm_calls": trace._llm_calls,
        "search_calls": trace._search_calls,
        "duration_s": round(duration, 1),
        "report_len": len(report),
    }


async def main():
    print("=" * 80)
    print(f"  四级 Agent 同题对比")
    print(f"  问题: {QUESTION}")
    print("=" * 80)

    results = []
    for level in LEVELS:
        print(f"\n  跑 Level {level}...")
        try:
            r = await run_level(level, QUESTION)
            results.append(r)
            print(f"    Level {level}: judge={r['judge']} token={r['tokens']['total']} "
                  f"耗时={r['duration_s']}s LLM={r['llm_calls']}次")
        except Exception as e:
            print(f"    Level {level} 失败: {e}")

    # 对比表
    print("\n" + "=" * 80)
    print("  四级对比结果")
    print("=" * 80)
    print(f"{'Level':<7} {'judge':<8} {'token':<10} {'耗时':<9} {'LLM':<5} {'质量/token':<12}")
    print("-" * 55)
    for r in results:
        q_per_token = (r['judge'] / r['tokens']['total'] * 10000) if r['judge'] and r['tokens']['total'] else 0
        print(f"L{r['level']:<6} {str(r['judge']):<8} {r['tokens']['total']:<10} "
              f"{r['duration_s']:<9} {r['llm_calls']:<5} {q_per_token:.2f}")
    print("-" * 55)

    # 各维度对比
    if results:
        print("\n  各维度分数:")
        dim_names = ["completeness", "accuracy", "logic", "conciseness", "citation"]
        print(f"{'Level':<7} " + " ".join(f"{d[:6]:<7}" for d in dim_names))
        for r in results:
            row = " ".join(f"{str(r['dims'].get(d)):<7}" for d in dim_names)
            print(f"L{r['level']:<6} {row}")

    # 保存结果
    from researcher.evaluation._results import run_dir_for
    out_dir = run_dir_for("rag")
    out_path = os.path.join(out_dir, "level_compare.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"question": QUESTION, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n  结果已保存: {out_path}")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
