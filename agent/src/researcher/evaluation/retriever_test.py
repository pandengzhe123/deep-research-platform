"""第 1 层：Retriever 单独测试 —— 用传统 IR 指标，不涉及 LLM。"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))
from researcher.kb import kb


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


def run_retriever_test(testset: list[dict], user_id: str) -> dict:
    """仅测检索器——不涉及 LLM。比较四个模式的 Precision/Recall/MRR。"""
    modes = ["v2", "hybrid", "rerank", "full"]
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

            # 从结果中提取文档名作为 retrieved 列表
            # 格式："--- 来源 1: doc1.txt（相关度 N/A）---"
            retrieved = []
            for line in result.split("\n"):
                if "来源" in line and ": " in line:
                    # 取冒号后面、括号前面的部分
                    after_colon = line.split(": ", 1)[1] if ": " in line else ""
                    doc_name = after_colon.split("（")[0].split("(")[0].split("---")[0].strip()
                    if doc_name:
                        retrieved.append(doc_name)

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
    testset_path = os.path.join(os.path.dirname(__file__), "golden_testset_v2.json")
    with open(testset_path, encoding="utf-8") as f:
        testset = json.load(f)
    print(f"Loaded {len(testset)} test items")

    results = run_retriever_test(testset, user_id="eval")
    print_results(results)
