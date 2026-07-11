"""A/B 对比编排脚本 —— 对比"改 Prompt 前"vs"改 Prompt 后"的报告质量。

设计原则：生成报告（贵）和 judge 打分（便宜）分离，都带版本归档不覆盖。
  - 生成一次 L4 报告 ≈ 2 元/题，慎跑 → 存档后可无限次复用
  - judge 打分 ≈ 几分钱，随便跑 → 调 prompt/锚点后重跑不花钱

两个子命令：
  # ① 生成报告（贵，git 切版本跑 Agent，带时间戳归档）
  python -m researcher.evaluation.ab_compare gen --old 868fa1e --new 570f9a2 --level 4 --n 3

  # ② judge 对比（便宜，用已存报告，judge 跑多次取平均）
  python -m researcher.evaluation.ab_compare judge --run <时间戳> --judge-runs 5

  # 不带子命令 = gen + judge 一条龙
  python -m researcher.evaluation.ab_compare --old 868fa1e --new 570f9a2 --level 4 --n 3

归档结构：
  results/<时间戳>_ab/
    ├── meta.json          （old/new commit, level, 题目）
    ├── old_reports.json
    ├── new_reports.json
    └── judge_<时间戳>.json （每次 judge 单独存，可多份）
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

AGENT_PY = "agent/src/researcher/agent.py"  # 相对 repo 根
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
AGENT_DIR = os.path.join(REPO_ROOT, "agent")
RESULTS_ROOT = os.path.join(os.path.dirname(__file__), "results")

# A/B 对照实验专用题库——全部稳定技术知识题，两次运行搜到的内容基本一致，
# 保证"prompt 版本"是唯一变量。时效题/开放题不放这里（它们进绝对质量评估）。
DEFAULT_QUESTIONS = [
    "Redis 和 Memcached 作为缓存有什么区别",
    "Kafka 为什么能实现高吞吐量",
    "向量数据库在 RAG 系统中的作用",
    "微服务架构相比单体架构的优缺点",
    "HTTP 和 HTTPS 的区别是什么",
    "TCP 和 UDP 协议有什么不同",
    "关系型数据库和 NoSQL 数据库的对比",
    "Docker 容器和虚拟机的区别",
    "什么是 CAP 定理，三者为什么不可兼得",
    "对称加密和非对称加密的原理与区别",
]


def _ts():
    """时间戳（分钟级，可读）。不用 datetime.now() 直接调 —— 用 time.strftime。"""
    return time.strftime("%Y%m%d_%H%M")


def _git(args: list[str]):
    r = subprocess.run(["git"] + args, cwd=REPO_ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  git {' '.join(args)} 失败: {r.stderr}")
    return r


def _run_batch(questions: list[str], level: int, out_path: str):
    """subprocess 跑一批题（在当前磁盘 agent.py 版本上）。"""
    q_arg = "|||".join(questions)
    r = subprocess.run(
        [sys.executable, "-m", "researcher.evaluation.ab_run",
         "--level", str(level), "--out", out_path, "--questions", q_arg],
        cwd=os.path.join(AGENT_DIR, "src"),
        capture_output=True, text=True, env={**os.environ, "PYTHONUTF8": "1"},
    )
    print(r.stdout)
    if r.returncode != 0:
        print(f"  批次运行失败: {r.stderr[-500:]}")
    return out_path


# ================================================================
# 子命令 gen：生成报告（贵）
# ================================================================

def cmd_gen(old_commit: str, new_commit: str, level: int, questions: list[str]) -> str:
    """生成新旧两版报告，归档到 results/<时间戳>_ab/。返回归档目录名。"""
    run_id = _ts() + "_ab"
    run_dir = os.path.join(RESULTS_ROOT, run_id)
    os.makedirs(run_dir, exist_ok=True)

    print(f"\n{'='*70}")
    print(f"  [GEN] 生成 A/B 报告  run={run_id}  L{level}  {len(questions)} 题")
    print(f"  旧={old_commit}  新={new_commit}")
    print(f"  预估成本：{len(questions)*2} 次 L{level} × ~2元 ≈ {len(questions)*4} 元")
    print(f"{'='*70}\n")

    # meta
    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"run_id": run_id, "old": old_commit, "new": new_commit,
                   "level": level, "questions": questions}, f, ensure_ascii=False, indent=2)

    # ① 切旧版 → 跑
    print(f"[1/4] 切换到旧版 agent.py ({old_commit})...")
    _git(["checkout", old_commit, "--", AGENT_PY])
    print(f"[2/4] 旧版跑 {len(questions)} 题...")
    _run_batch(questions, level, os.path.join(run_dir, "old_reports.json"))

    # ② 恢复新版 → 跑
    print(f"[3/4] 恢复新版 agent.py ({new_commit})...")
    _git(["checkout", new_commit, "--", AGENT_PY])
    print(f"[4/4] 新版跑同 {len(questions)} 题...")
    _run_batch(questions, level, os.path.join(run_dir, "new_reports.json"))

    print(f"\n  报告已归档: {run_dir}")
    print(f"  下一步 judge: python -m researcher.evaluation.ab_compare judge --run {run_id}")
    return run_id


# ================================================================
# 子命令 judge：打分对比（便宜，可反复）
# ================================================================

def cmd_judge(run_id: str, judge_runs: int = 5):
    """用已存报告做 judge 对比，每份报告打 judge_runs 次取平均。"""
    from researcher.evaluation.judge import ReportJudge, _WEIGHTS

    run_dir = os.path.join(RESULTS_ROOT, run_id)
    old_path = os.path.join(run_dir, "old_reports.json")
    new_path = os.path.join(run_dir, "new_reports.json")
    if not os.path.exists(old_path):
        print(f"  找不到报告: {old_path}")
        return

    with open(old_path, encoding="utf-8") as f:
        old_reports = {r["question"]: r["report"] for r in json.load(f)}
    with open(new_path, encoding="utf-8") as f:
        new_reports = {r["question"]: r["report"] for r in json.load(f)}

    print(f"\n{'='*70}")
    print(f"  [JUDGE] run={run_id}  每份报告打分 {judge_runs} 次取平均")
    print(f"{'='*70}\n")

    judge = ReportJudge()
    rows = []
    for q in old_reports:
        if q not in new_reports:
            continue
        print(f"  打分中: {q[:30]}...")
        old_s = judge.evaluate_avg(q, old_reports[q], runs=judge_runs)
        new_s = judge.evaluate_avg(q, new_reports[q], runs=judge_runs)
        rows.append({
            "question": q[:24],
            "old": old_s["overall"], "old_std": old_s["std"],
            "new": new_s["overall"], "new_std": new_s["std"],
            "delta": round(new_s["overall"] - old_s["overall"], 2),
            "old_dims": old_s["dimensions"], "new_dims": new_s["dimensions"],
            "old_summary": old_s.get("summary", ""),
            "new_summary": new_s.get("summary", ""),
        })

    _print_and_save(rows, run_dir, judge_runs, _WEIGHTS)


def _print_and_save(rows, run_dir, judge_runs, weights):
    print("\n" + "=" * 74)
    print(f"  A/B 报告质量对比（judge×{judge_runs} 取平均，std=打分波动）")
    print("=" * 74)
    print(f"  {'问题':<26} {'旧版':>6} {'±':>5} {'新版':>6} {'±':>5} {'提升':>7}")
    print("  " + "-" * 60)
    for r in rows:
        print(f"  {r['question']:<26} {r['old']:>6.2f} {r['old_std']:>5.2f} "
              f"{r['new']:>6.2f} {r['new_std']:>5.2f} {r['delta']:>+7.2f}")
    print("  " + "-" * 60)
    if rows:
        avg_old = sum(r["old"] for r in rows) / len(rows)
        avg_new = sum(r["new"] for r in rows) / len(rows)
        avg_std = sum(r["old_std"] + r["new_std"] for r in rows) / (2 * len(rows))
        print(f"  {'平均':<26} {avg_old:>6.2f} {'':>5} {avg_new:>6.2f} {'':>5} {avg_new-avg_old:>+7.2f}")
        print(f"\n  平均打分波动 std={avg_std:.2f}  ——  提升 {avg_new-avg_old:+.2f} "
              f"{'落在噪音内，无法判定' if abs(avg_new-avg_old) < avg_std else '超出噪音，可信'}")

    # 各维度
    print("\n  各维度平均：")
    for dim in weights:
        d_old = sum(r["old_dims"].get(dim, {}).get("score", 0) for r in rows) / len(rows) if rows else 0
        d_new = sum(r["new_dims"].get(dim, {}).get("score", 0) for r in rows) / len(rows) if rows else 0
        print(f"    {dim:<14} {d_old:.2f} → {d_new:.2f}  ({d_new-d_old:+.2f})")
    # Judge 总结（每份新版报告一句话评价）
    print(f"\n  Judge 对新版报告的一两句话总结（每题的首次评价）：")
    for r in rows:
        s = r.get("new_summary", "")
        if s:
            print(f"  [{r['question']}] {s}")
    print("=" * 74)

    out = os.path.join(run_dir, f"judge_{_ts()}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"  Judge 结果已存: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")

    p_gen = sub.add_parser("gen", help="生成报告（贵）")
    p_gen.add_argument("--old", default="868fa1e")
    p_gen.add_argument("--new", default="570f9a2")
    p_gen.add_argument("--level", type=int, default=4)
    p_gen.add_argument("--n", type=int, default=3)

    p_judge = sub.add_parser("judge", help="打分对比（便宜，可反复）")
    p_judge.add_argument("--run", required=True, help="gen 生成的时间戳 run_id")
    p_judge.add_argument("--judge-runs", type=int, default=5)

    # 不带子命令 = 一条龙
    parser.add_argument("--old", default="868fa1e")
    parser.add_argument("--new", default="570f9a2")
    parser.add_argument("--level", type=int, default=4)
    parser.add_argument("--n", type=int, default=3)
    parser.add_argument("--judge-runs", type=int, default=5)

    args = parser.parse_args()
    os.makedirs(RESULTS_ROOT, exist_ok=True)

    if args.cmd == "gen":
        cmd_gen(args.old, args.new, args.level, DEFAULT_QUESTIONS[:args.n])
    elif args.cmd == "judge":
        cmd_judge(args.run, args.judge_runs)
    else:
        # 一条龙：先 gen 后 judge
        rid = cmd_gen(args.old, args.new, args.level, DEFAULT_QUESTIONS[:args.n])
        cmd_judge(rid, args.judge_runs)
