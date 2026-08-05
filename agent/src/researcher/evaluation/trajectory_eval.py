"""轨迹级评估解析器 —— 读 trace.jsonl，聚合成一次研究的轨迹统计。

轨迹评估（Trajectory Evaluation）是 Agent 评测区别于普通 LLM 评测的关键：
普通评测只看"结果"，轨迹评估看"过程"——工具调用是否合理、是否循环、
是否高效、是否在正确时机停止。

输入：一次研究的 trace.jsonl（由 trace.py 的 TraceRun 生成）
输出：聚合统计 dict，含效率指标、工具使用分布、循环检测、错误事件。

用法：
  from researcher.evaluation.trajectory_eval import analyze_trace
  stats = analyze_trace("reports/20260805_134427/trace.jsonl")
  print_trajectory_stats(stats)

指标（当前实现）：
  - 效率：llm_calls_per_round = LLM 调用数 / 轮次数（>3 视为低效）
  - 工具使用分布：search / search_kb / think 各自次数
  - 工具决策：search_mode 边界内是否用了正确工具（rag_only 不该 search）
  - 循环检测：max_query_similarity（trace 已算好，这里读出）
  - 错误事件：error 类型的 message
"""

import json
from collections import Counter
from pathlib import Path


def analyze_trace(path: str) -> dict:
    """读 trace.jsonl，聚合出一次研究的轨迹统计。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"trace 文件不存在: {path}")

    with open(path, encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]

    stats = {
        "question": "",
        "level": None,
        "search_mode": "",
        "model": "",
        "duration_s": 0.0,
        "rounds": 0,
        "max_rounds": 0,
        "llm_calls": 0,
        "llm_errors": 0,
        "llm_retries": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "search_calls": 0,
        "search_kb_calls": 0,
        "think_calls": 0,
        "other_tool_calls": 0,
        "tool_calls": [],            # [{"tool", "args", "round"}]
        "embedding_calls": 0,
        "compress_events": 0,
        "max_query_similarity": 0.0,
        "query_history_len": 0,
        "error_events": [],
        "end_error": None,
    }

    for e in events:
        t = e.get("type")
        if t == "run_start":
            stats["question"] = e.get("question", "")
            stats["level"] = e.get("level")
            stats["search_mode"] = e.get("search_mode", "")
            stats["model"] = e.get("model", "")

        elif t == "run_end":
            stats["duration_s"] = e.get("duration_s", 0.0)
            stats["end_error"] = e.get("error")
            summary = e.get("summary", {})
            if summary:
                stats["max_query_similarity"] = max(
                    stats["max_query_similarity"], summary.get("max_query_similarity", 0)
                )

        elif t == "agent_event":
            # 工具调用事件：带 tool 字段
            if e.get("tool"):
                tool = e["tool"]
                stats["tool_calls"].append({
                    "tool": tool,
                    "args": e.get("tool_args"),
                    "round": e.get("round", 0),
                })
                key = f"{tool}_calls"
                if key in stats:
                    stats[key] += 1
                elif tool not in ("search", "search_kb", "think"):
                    stats["other_tool_calls"] += 1

        elif t == "llm_call":
            stats["llm_calls"] += 1
            if not e.get("success", True):
                stats["llm_errors"] += 1
            stats["llm_retries"] += e.get("retries", 0)
            usage = e.get("usage") or {}
            stats["total_prompt_tokens"] += usage.get("prompt_tokens", 0)
            stats["total_completion_tokens"] += usage.get("completion_tokens", 0)

        elif t == "search_call":
            stats["max_query_similarity"] = max(
                stats["max_query_similarity"], e.get("max_query_similarity", 0)
            )
            stats["query_history_len"] = max(
                stats["query_history_len"], e.get("query_history_len", 0)
            )

        elif t == "embedding_call":
            stats["embedding_calls"] += 1

        elif t == "context_compress":
            stats["compress_events"] += 1

        elif t == "round_start":
            stats["rounds"] = max(stats["rounds"], e.get("round", 0))
            stats["max_rounds"] = max(stats["max_rounds"], e.get("max_rounds", 0))

        elif t == "error":
            stats["error_events"].append(e.get("message", "")[:100])

    # 派生指标
    stats["total_tokens"] = stats["total_prompt_tokens"] + stats["total_completion_tokens"]
    stats["llm_calls_per_round"] = round(stats["llm_calls"] / stats["rounds"], 2) if stats["rounds"] else None
    stats["tool_distribution"] = {
        "search": stats["search_calls"],
        "search_kb": stats["search_kb_calls"],
        "think": stats["think_calls"],
    }

    return stats


def print_trajectory_stats(stats: dict):
    """打印轨迹统计。"""
    print("=" * 60)
    print("  轨迹评估 (Trajectory Evaluation)")
    print("=" * 60)
    print(f"  问题: {stats['question'][:50]}")
    print(f"  模式: Level {stats['level']} | {stats['search_mode']} | 耗时 {stats['duration_s']}s")

    print(f"\n  效率指标:")
    print(f"    轮次数: {stats['rounds']}/{stats['max_rounds']}")
    print(f"    LLM 调用: {stats['llm_calls']} (错误 {stats['llm_errors']}, 重试 {stats['llm_retries']})")
    if stats["llm_calls_per_round"] is not None:
        flag = "⚠️ 低效" if stats["llm_calls_per_round"] > 3 else "✅ 正常"
        print(f"    LLM/轮: {stats['llm_calls_per_round']} {flag}")
    print(f"    总 token: {stats['total_tokens']} (prompt {stats['total_prompt_tokens']} + completion {stats['total_completion_tokens']})")

    print(f"\n  工具使用分布:")
    for tool, cnt in stats["tool_distribution"].items():
        print(f"    {tool}: {cnt}")
    if stats["other_tool_calls"]:
        print(f"    其他工具: {stats['other_tool_calls']}")

    print(f"\n  循环检测:")
    sim = stats["max_query_similarity"]
    flag = "⚠️ 疑似循环" if sim > 0.8 else "✅ 无循环"
    print(f"    最大 query 相似度: {sim} {flag} (历史 {stats['query_history_len']} 个 query)")

    print(f"\n  其他:")
    print(f"    embedding 调用: {stats['embedding_calls']}")
    print(f"    上下文压缩事件: {stats['compress_events']}")
    if stats["error_events"]:
        print(f"    错误事件 ({len(stats['error_events'])}):")
        for msg in stats["error_events"][:5]:
            print(f"      - {msg}")
    if stats["end_error"]:
        print(f"    结束异常: {stats['end_error'][:100]}")
    if not stats["tool_calls"]:
        print("    ⚠️ 无工具调用记录（检查 agent.py 是否传入 tool 参数）")

    print("=" * 60)


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "reports/20260805_134427/trace.jsonl"
    stats = analyze_trace(path)
    print_trajectory_stats(stats)
