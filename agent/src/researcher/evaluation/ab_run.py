"""A/B 对比子脚本 —— 跑一批题生成报告，存 JSON。

被 ab_compare.py 通过 subprocess 调用，每次在当前磁盘上的 agent.py 版本运行。
之所以独立成脚本：Python import 缓存，同进程内切换 agent.py 文件不会重新加载。

用法：
  python -m researcher.evaluation.ab_run --level 4 --out old_reports.json --questions "问题1|||问题2|||问题3"
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))


async def run_one(question: str, level: int) -> dict:
    """跑单题 Agent，返回 {question, report}。"""
    from researcher.agent import FastLevel1Agent, Level2Agent, Level3Agent, Level4Agent

    if level == 1:
        agent = FastLevel1Agent(search_mode="web_only")
    elif level == 3:
        agent = Level3Agent(search_mode="web_only")
    elif level == 4:
        agent = Level4Agent(search_mode="web_only")
    else:
        agent = Level2Agent(search_mode="web_only")

    try:
        report = await agent.run(question)
    except Exception as e:
        report = f"[Agent 失败] {e}"
    return {"question": question, "report": report}


async def main(questions: list[str], level: int, out: str):
    print(f"  跑 {len(questions)} 题 (L{level}) 并行...")
    # 版本内部 5 题并行
    results = await asyncio.gather(*[run_one(q, level) for q in questions])
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  已存: {out}")
    for r in results:
        print(f"    {r['question'][:30]:<32} 报告 {len(r['report'])} 字")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--level", type=int, default=4)
    parser.add_argument("--out", required=True)
    parser.add_argument("--questions", required=True, help="用 ||| 分隔的问题")
    args = parser.parse_args()

    qs = [q.strip() for q in args.questions.split("|||") if q.strip()]
    asyncio.run(main(qs, args.level, args.out))
