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


def run_ablation(testset: list[dict], user_id: str, doc_ids=None) -> dict:
    """
    对每个模式，逐条运行测试集，对比检索结果。

    testset 格式：[{"question": "...", "expected_chunks": ["关键词1", "关键词2"]}, ...]

    返回：{mode: {hits, total, hit_rate, avg_time}}
    """
    modes = ["v2", "hybrid", "rerank", "full"]
    results = {}

    for mode in modes:
        print(f"\n  Running mode: {mode}...")
        hits = 0
        total = len(testset)
        times = []

        for item in testset:
            question = item["question"]
            expected = item["expected_chunks"]

            start = time.time()
            result = kb.search(question, user_id=user_id, doc_ids=doc_ids, mode=mode)
            elapsed = time.time() - start
            times.append(elapsed)

            # 检查所有期望的关键词是否在结果中
            all_found = all(kw in result for kw in expected)
            if all_found:
                hits += 1

        results[mode] = {
            "hits": hits,
            "total": total,
            "hit_rate": f"{hits / total:.0%}" if total > 0 else "N/A",
            "avg_time": f"{sum(times) / len(times):.2f}s",
        }

    return results


def print_ablation_table(results: dict):
    """打印消融实验对比表。"""
    print("\n" + "=" * 70)
    print("  RAG 消融实验结果")
    print("=" * 70)
    print(f"  {'模式':<12} {'命中':>6} {'总数':>6} {'命中率':>8} {'平均耗时':>10}")
    print("  " + "-" * 45)
    for mode, r in results.items():
        print(f"  {mode:<12} {r['hits']:>6} {r['total']:>6} {r['hit_rate']:>8} {r['avg_time']:>10}")
    print("=" * 70)

    # 保存结果
    out_path = os.path.join(os.path.dirname(__file__), "results", "ablation_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n  结果已保存: {out_path}")


if __name__ == "__main__":
    # 加载测试集
    testset_path = os.path.join(os.path.dirname(__file__), "golden_testset.json")
    try:
        with open(testset_path, "r", encoding="utf-8") as f:
            testset = json.load(f)
    except FileNotFoundError:
        print(f"测试集不存在: {testset_path}")
        print("请先创建 golden_testset.json")
        sys.exit(1)

    results = run_ablation(testset, user_id="eval")
    print_ablation_table(results)
