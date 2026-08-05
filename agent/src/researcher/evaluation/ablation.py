"""RAG 消融实验 —— 对比 v2 / hybrid / rerank / full 四种模式的检索效果。"""

import json
import time
import sys
import os

# 确保能 import 项目模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

from researcher.kb import kb
from researcher.evaluation.permutation import compare_all_modes


def run_ablation(testset: list[dict], user_id: str, doc_ids=None) -> dict:
    """
    对每个模式，逐条运行测试集，对比检索结果。

    testset 格式：[{"question": "...", "expected_chunks": ["关键词1", "关键词2"]}, ...]

    返回：{mode: {hits, total, hit_rate, avg_time, hit_list}}
      hit_list 是每题命中(1)/未命中(0) 的列表，只含有 expected_chunks 的题
      （no_answer 题型 expected_chunks 为空，单独统计不参与置换检验）。
    """
    modes = ["v2", "hybrid", "rerank", "full"]
    results = {}

    for mode in modes:
        print(f"\n  Running mode: {mode}...")
        hits = 0
        hit_list = []
        total = len(testset)
        times = []

        for item in testset:
            question = item["question"]
            expected = item.get("expected_chunks") or []
            # no_answer 题型（expected_chunks 为空）：不参与置换检验的命中统计
            if not expected:
                continue

            start = time.time()
            result = kb.search(question, user_id=user_id, doc_ids=doc_ids, mode=mode)
            elapsed = time.time() - start
            times.append(elapsed)

            # 检查所有期望的关键词是否在结果中
            all_found = all(kw in result for kw in expected)
            hit_list.append(1 if all_found else 0)
            if all_found:
                hits += 1

        results[mode] = {
            "hits": hits,
            "total": len(hit_list),
            "hit_rate": f"{hits / len(hit_list):.0%}" if hit_list else "N/A",
            "avg_time": f"{sum(times) / len(times):.2f}s" if times else "N/A",
            "hit_list": hit_list,
        }

    return results


def print_ablation_table(results: dict):
    """打印消融实验对比表 + 置换检验显著性判断。"""
    print("\n" + "=" * 70)
    print("  RAG 消融实验结果")
    print("=" * 70)
    print(f"  {'模式':<12} {'命中':>6} {'总数':>6} {'命中率':>8} {'平均耗时':>10}")
    print("  " + "-" * 45)
    for mode, r in results.items():
        print(f"  {mode:<12} {r['hits']:>6} {r['total']:>6} {r['hit_rate']:>8} {r['avg_time']:>10}")
    print("=" * 70)

    # 置换检验：判断模式间差异是否显著（还是抽样波动）
    per_mode_hits = {m: r["hit_list"] for m, r in results.items() if r.get("hit_list")}
    if len(per_mode_hits) >= 2:
        print("\n  置换检验（判断差异是否显著，非抽样波动）：")
        print(f"  {'对比':<20} {'A/B命中':<12} {'差距':>5} {'p-value':>10}  判定")
        print("  " + "-" * 55)
        for r in compare_all_modes(per_mode_hits):
            verdict = "✅ 显著" if r["significant"] else "⚠️ 不显著（可能是抽样波动）"
            print(f"  {r['mode_a']} vs {r['mode_b']:<7} {r['hits_a']}/{r['hits_b']:<6} "
                  f"{r['diff']:>4}  {r['p_value']:>10.3f}  {verdict}")
        print("\n  解读：p < 0.05 表示差异不太可能来自抽样波动；")
        print("  p >= 0.05 表示只凭这批评测题无法确认差异真实存在。")

    # 保存结果（hit_list 不落盘，只存汇总）
    save_results = {m: {k: v for k, v in r.items() if k != "hit_list"} for m, r in results.items()}
    from researcher.evaluation._results import run_dir_for
    out_path = os.path.join(run_dir_for("rag"), "ablation_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(save_results, f, ensure_ascii=False, indent=2)
    print(f"\n  结果已保存: {out_path}")


if __name__ == "__main__":
    # 加载测试集
    testset_path = os.path.join(os.path.dirname(__file__), "golden_testset_v4.json")
    try:
        with open(testset_path, "r", encoding="utf-8") as f:
            testset = json.load(f)
    except FileNotFoundError:
        print(f"测试集不存在: {testset_path}")
        print("请先创建 golden_testset.json")
        sys.exit(1)

    results = run_ablation(testset, user_id="eval")
    print_ablation_table(results)
