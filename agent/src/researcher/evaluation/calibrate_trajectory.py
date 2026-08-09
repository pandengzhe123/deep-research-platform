"""轨迹阈值标定 —— 收集真实 Agent 运行的分布，反推数据支撑的阈值。

背景：轨迹指标（LLM/轮>3、相似度>0.8、max_rounds）之前是拍脑袋定的。
正确做法：收集 N 次真实运行，统计分布，看"正常 Agent 的行为"。

本脚本并发跑一批"干净的标定专用题"（分简单/复杂两类），
收集轮次/LLM每轮/相似度分布，输出建议阈值。

用法:
  python -m researcher.evaluation.calibrate_trajectory --n-simple 8 --n-complex 4 --concurrency 3
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

# 标定专用题：干净的简单/复杂，不含数据缺失对抗题
SIMPLE_QS = [
    "什么是 TCP 协议",
    "什么是 HTTP 协议",
    "什么是 Git",
    "什么是 Docker",
    "什么是 Kubernetes",
    "什么是 Linux 操作系统",
    "什么是 SQL 数据库",
    "什么是微服务",
    "什么是 RESTful API",
    "什么是机器学习",
]
COMPLEX_QS = [
    "对比 TCP 和 UDP 在可靠性和实时性上的差异",
    "分析 Docker 容器和虚拟机的隔离机制区别",
    "对比 MySQL 和 PostgreSQL 的事务处理机制",
    "分析缓存淘汰策略 LRU 和 LFU 的适用场景",
    "对比同步复制和异步复制的数据一致性权衡",
    "分析分布式系统中 CAP 定理的取舍",
]


async def run_one(question: str) -> dict:
    """跑一道题，返回轨迹统计。"""
    from researcher.agent import Level2Agent
    from researcher.trace import TraceRun

    reports_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "reports",
                               "calib_" + time.strftime("%Y%m%d_%H%M%S") + "_" + str(time.time())[-4:])
    os.makedirs(reports_dir, exist_ok=True)

    async with TraceRun(question=question, output_dir=reports_dir, level=2,
                        model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
                        search_mode="hybrid") as trace:
        agent = Level2Agent(trace=trace, search_mode="hybrid")
        await agent.run(question)

    from researcher.evaluation.trajectory_eval import analyze_trace, save_trajectory_stats
    trace_file = os.path.join(reports_dir, "trace.jsonl")
    stats = analyze_trace(trace_file)
    save_trajectory_stats(stats, trace_file)
    return stats


async def collect(questions, label, concurrency):
    """并发跑一批题，返回轨迹统计列表。"""
    print(f"\n收集 {label} ({len(questions)} 题，并发 {concurrency})...")
    results = []
    for start in range(0, len(questions), concurrency):
        batch = questions[start:start + concurrency]
        batch_results = await asyncio.gather(*[run_one(q) for q in batch])
        for r in batch_results:
            results.append(r)
            print(f"  [{label}] {r.get('question','')[:25]:<27} 轮{r.get('rounds')} "
                  f"LLM/轮{r.get('llm_calls_per_round')} 相似度{r.get('max_query_similarity'):.2f}")
    return results


def summarize(name, stats_list, question_total):
    """统计分布，输出建议阈值。"""
    rounds = [s.get("rounds", 0) for s in stats_list if s.get("rounds")]
    sim = [s.get("max_query_similarity", 0) for s in stats_list]
    llm_pr = [s.get("llm_calls_per_round") or 0 for s in stats_list]
    if not rounds:
        print(f"\n=== {name}: 无有效数据 ===")
        return

    sr = sorted(rounds)
    print(f"\n=== {name} 标定结果（{len(sr)} 次）===")
    print(f"  轮次: {sr}")
    print(f"  中位 {sr[len(sr)//2]} | 均值 {sum(sr)/len(sr):.1f}")
    for p in [0.8, 0.9, 0.95]:
        idx = min(int(len(sr)*p), len(sr)-1)
        print(f"  P{int(p*100)} = {sr[idx]} 轮")
    # 相似度分布
    print(f"  相似度 >0.6: {sum(1 for x in sim if x>0.6)}/{len(sim)} | "
          f">0.7: {sum(1 for x in sim if x>0.7)}/{len(sim)} | "
          f">0.8: {sum(1 for x in sim if x>0.8)}/{len(sim)}")
    print(f"  LLM/轮 全部 <3.0: {all(x<3.0 for x in llm_pr)}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-simple", type=int, default=6)
    parser.add_argument("--n-complex", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()

    simple_qs = SIMPLE_QS[:args.n_simple]
    complex_qs = COMPLEX_QS[:args.n_complex]

    simple_stats = await collect(simple_qs, "简单题", args.concurrency)
    complex_stats = await collect(complex_qs, "复杂题", args.concurrency)

    # 合并全部（含历史）？本脚本只算新跑的，历史由外部分析
    summarize("简单题", simple_stats, len(simple_qs))
    summarize("复杂题", complex_stats, len(complex_qs))


if __name__ == "__main__":
    asyncio.run(main())
