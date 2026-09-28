"""第 1 层：Retriever 单独测试 —— 用传统 IR 指标，不涉及 LLM。"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))
from researcher.kb import kb, parse_source_docs


def precision_at_k(relevant: set, retrieved: list, k: int) -> float:
    rel = set(retrieved[:k]) & relevant
    return len(rel) / k if k > 0 else 0.0


def recall_at_k(relevant: set, retrieved: list, k: int) -> float:
    if not relevant:
        return 0.0
    rel = set(retrieved[:k]) & relevant
    return len(rel) / len(relevant)


def mrr(relevant: set, retrieved: list) -> float:
    for i, doc in enumerate(retrieved):
        if doc in relevant:
            return 1.0 / (i + 1)
    return 0.0


def run_retriever_test(testset: list[dict], user_id: str, modes: list[str] = None) -> dict:
    """仅测检索器——不涉及 LLM。比较多个模式的 Precision/Recall/MRR。

    modes: 要测的检索模式，默认 ["v2", "hybrid", "rerank", "full"]。
      rerank/full 慢（含模型加载+精排），可用 --retrieval-mode 控制。
    """
    modes = modes or ["v2", "hybrid", "rerank", "full"]
    results = {}

    for mode in modes:
        p5 = r5 = m = 0.0
        n = 0
        for item in testset:
            q = item["question"]
            expected = set(item.get("expected_docs", []))
            if not expected:
                continue  # no_answer 型不参与 IR 指标
            n += 1

            result = kb.search(q, user_id=user_id, mode=mode)

            # 从结果中提取文档名作为 retrieved 列表。
            # 解析统一走 kb.parse_source_docs()（与 _fmt() 同源），不再各自手写 ——
            # 手写版本曾因只 strip 半角括号而在带相似度标注的模式下解析失败。
            retrieved = parse_source_docs(result)

            p5 += precision_at_k(expected, retrieved, 5)
            r5 += recall_at_k(expected, retrieved, 5)
            m += mrr(expected, retrieved)

        if n > 0:
            p5 /= n
            r5 /= n
            m /= n

        results[mode] = {
            "Precision@5": f"{p5:.2%}",
            "Recall@5": f"{r5:.2%}",
            "MRR": f"{m:.2%}",
            "queries": n,
        }

    return results


def print_results(results: dict):
    print("\n" + "=" * 60)
    print("  Retriever 层测试结果（传统 IR 指标，无 LLM 裁判）")
    print("=" * 60)
    header = f"  {'模式':<10} {'Precision@5':<14} {'Recall@5':<14} {'MRR':<14}"
    print(header)
    print("  " + "-" * 55)
    for mode, r in results.items():
        print(f"  {mode:<10} {r['Precision@5']:<14} {r['Recall@5']:<14} {r['MRR']:<14}")
    print("=" * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Retriever 层测试")
    parser.add_argument("--retrieval-mode", choices=["v2", "hybrid", "rerank", "full", "all"], default="all",
                        help="检索模式: v2/hybrid/rerank/full/all(默认)")
    args = parser.parse_args()
    modes = ["v2", "hybrid", "rerank", "full"] if args.retrieval_mode == "all" else [args.retrieval_mode]

    # 测试集路径见 _testset.py —— 全项目唯一出处
    from researcher.evaluation._testset import load_testset
    testset = load_testset()
    print(f"Loaded {len(testset)} test items, modes: {modes}")

    results = run_retriever_test(testset, user_id="eval", modes=modes)
    print_results(results)
