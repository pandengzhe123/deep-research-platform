"""LLM 成本归因 —— 按 system prompt 分组，看 prompt 缓存的命中/漏掉情况。

用法：
  # 零成本：分析已有 trace
  python -m researcher.evaluation.cost_breakdown --all
  python -m researcher.evaluation.cost_breakdown --analyze reports/level2_xxx/trace.jsonl

  # 跑一次研究并立即分析（消耗真实 token，L2 约 4 分钟）
  python -m researcher.evaluation.cost_breakdown --run --level 2

为什么需要它：trace.jsonl 里每次 llm_call 都带 usage（含 prompt_cache_hit_tokens），
但「哪一类调用没命中」必须按 system prompt 分组才看得出来，手工统计很烦。

分组口径：用 system prompt 的前 24 字（record_llm 的 purpose 字段存的就是这个）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve()
_AGENT_DIR = _HERE.parent.parent.parent.parent          # agent/
sys.path.insert(0, str(_AGENT_DIR / "src"))

from dotenv import load_dotenv                          # noqa: E402

load_dotenv(_AGENT_DIR / ".env")

DEFAULT_QUESTION = (
    "对比 Redis 和 Kafka 在缓存、消息队列、流处理三种场景下的架构设计差异，"
    "分析各自的适用场景，并给出选型建议"
)
GROUP_KEY_LEN = 24


# ============================================================
# 分析
# ============================================================

def _cache_hit(usage: dict) -> int:
    """取缓存命中 token（兼容顶层字段与 OpenAI 风格 details）。"""
    h = usage.get("prompt_cache_hit_tokens")
    if h is None:
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            h = details.get("cached_tokens")
    try:
        return int(h or 0)
    except (TypeError, ValueError):
        return 0


def group_calls(trace_path: str) -> tuple[dict, dict]:
    """读一份 trace.jsonl，按 system prompt 分组累计。"""
    rows: dict[str, dict] = {}
    total = {"calls": 0, "prompt": 0, "hit": 0, "completion": 0}

    with open(trace_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or '"llm_call"' not in line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") != "llm_call":
                continue

            usage = e.get("usage") or {}
            prompt = int(usage.get("prompt_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
            hit = _cache_hit(usage)

            purpose = (e.get("purpose") or "(空)")[:GROUP_KEY_LEN]
            r = rows.setdefault(purpose, {"calls": 0, "prompt": 0, "hit": 0, "completion": 0})
            r["calls"] += 1
            r["prompt"] += prompt
            r["hit"] += hit
            r["completion"] += completion

            total["calls"] += 1
            total["prompt"] += prompt
            total["hit"] += hit
            total["completion"] += completion

    return rows, total


def print_report(rows: dict, total: dict, title: str = "") -> None:
    if title:
        print(f"\n{'=' * 92}")
        print(f"  {title}")
        print("=" * 92)
    if not rows:
        print("  （没有 llm_call 记录）")
        return

    prompt_all = total["prompt"] or 1
    print(f"\n  {'system prompt(前24字)':<40} {'调用':>5} {'prompt':>10} {'命中':>10} "
          f"{'命中率':>8} {'占全价':>8}")
    print("  " + "-" * 88)

    zero_prompt = 0
    zero_calls = 0
    for key in sorted(rows, key=lambda k: -rows[k]["prompt"]):
        v = rows[key]
        rate = (v["hit"] / v["prompt"] * 100) if v["prompt"] else 0.0
        miss = v["prompt"] - v["hit"]
        flag = "  <<< 零命中" if v["hit"] == 0 and v["prompt"] > 0 else ""
        if v["hit"] == 0 and v["prompt"] > 0:
            zero_prompt += miss
            zero_calls += v["calls"]
        print(f"  {key:<40} {v['calls']:>5} {v['prompt']:>10} {v['hit']:>10} "
              f"{rate:>7.1f}% {miss:>8}{flag}")

    print("  " + "-" * 88)
    rate_all = total["hit"] / prompt_all * 100
    print(f"  {'合计':<40} {total['calls']:>5} {total['prompt']:>10} {total['hit']:>10} "
          f"{rate_all:>7.1f}% {prompt_all - total['hit']:>8}")
    print(f"\n  completion tokens: {total['completion']}")
    print(f"  全价计费 prompt: {prompt_all - total['hit']} / {prompt_all} "
          f"（命中 {rate_all:.1f}%）")
    if zero_prompt:
        print(f"  ⚠️ 零命中调用：{zero_calls} 次、{zero_prompt} tokens "
              f"= 全部 prompt 的 {zero_prompt / prompt_all * 100:.1f}%，且 100% 全价计费")


# ============================================================
# 跑一次研究
# ============================================================

async def run_once(level: int, question: str, search_mode: str) -> str:
    """跑一次研究，返回 trace.jsonl 路径。"""
    from researcher.agent import (FastLevel1Agent, Level2Agent, Level3Agent,
                                  Level4Agent)
    from researcher.trace import TraceRun

    reports_dir = _AGENT_DIR / "reports" / ("cost_" + time.strftime("%Y%m%d_%H%M%S"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    print(f"  trace 目录: {reports_dir}")

    t0 = time.time()
    async with TraceRun(question=question, output_dir=str(reports_dir), level=level,
                        model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
                        search_mode=search_mode) as trace:
        if level == 1:
            agent = FastLevel1Agent(trace=trace, search_mode=search_mode)
        elif level == 3:
            agent = Level3Agent(trace=trace, search_mode=search_mode)
        elif level == 4:
            agent = Level4Agent(trace=trace, search_mode=search_mode)
        else:
            agent = Level2Agent(trace=trace, search_mode=search_mode)
        report = await agent.run(question)

    (reports_dir / "report.md").write_text(report, encoding="utf-8")
    print(f"\n  研究完成: {time.time() - t0:.1f}s, 报告 {len(report)} 字符")
    return str(reports_dir / "trace.jsonl")


def all_trace_files() -> list[Path]:
    return sorted((_AGENT_DIR / "reports").rglob("trace.jsonl"))


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM 成本归因：按 system prompt 看缓存命中")
    ap.add_argument("--analyze", metavar="TRACE", help="分析指定 trace.jsonl")
    ap.add_argument("--all", action="store_true", help="汇总 reports/ 下全部 trace")
    ap.add_argument("--run", action="store_true", help="先跑一次研究再分析")
    ap.add_argument("--level", type=int, default=2, help="--run 的 Level（默认 2）")
    ap.add_argument("--question", default=DEFAULT_QUESTION, help="--run 的问题")
    ap.add_argument("--search-mode", default="hybrid", help="--run 的检索模式")
    args = ap.parse_args()

    if args.run:
        trace_path = asyncio.run(run_once(args.level, args.question, args.search_mode))
        rows, total = group_calls(trace_path)
        print_report(rows, total, f"本次研究（L{args.level}）成本归因")
        return

    if args.analyze:
        rows, total = group_calls(args.analyze)
        print_report(rows, total, f"trace: {args.analyze}")
        return

    if args.all:
        files = all_trace_files()
        print(f"扫描 {len(files)} 份 trace ...")
        rows: dict[str, dict] = {}
        total = {"calls": 0, "prompt": 0, "hit": 0, "completion": 0}
        for fp in files:
            r, t = group_calls(str(fp))
            for k, v in r.items():
                acc = rows.setdefault(k, {"calls": 0, "prompt": 0, "hit": 0, "completion": 0})
                for f2 in acc:
                    acc[f2] += v[f2]
            for f2 in total:
                total[f2] += t[f2]
        print_report(rows, total, f"全部 {len(files)} 份 trace 汇总")
        return

    ap.print_help()


if __name__ == "__main__":
    main()
