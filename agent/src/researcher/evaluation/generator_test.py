"""第 2 层：Generator 单独测试 —— 跳过检索，直接喂完美文档。"""

import json
import os
import sys
import io

# 强制 UTF-8 输出，绕开 Windows GBK
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

from openai import OpenAI

PROMPT = """基于以下文档内容回答问题。如果文档中没有相关信息，请说"未找到相关信息"，不要编造。

文档内容：
{context}

问题：{question}

回答："""


def load_docs():
    doc_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "eval")
    docs = {}
    for f in os.listdir(doc_dir):
        if f.endswith(".txt"):
            with open(os.path.join(doc_dir, f), encoding="utf-8") as fp:
                docs[f] = fp.read()
    return docs


class Generator:
    def __init__(self):
        self._client = OpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
        self._model = os.getenv("LLM_MODEL", "deepseek-v4-flash")

    def ask(self, question, context_docs, all_docs):
        if not context_docs:
            return "[no-answer type, skip]"
        ctx = "\n\n".join(all_docs.get(d, "") for d in context_docs)
        resp = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            messages=[{"role": "user", "content": PROMPT.format(context=ctx, question=question)}],
        )
        return resp.choices[0].message.content or ""


def run_generator_test(testset, docs):
    gen = Generator()
    results = []
    for item in testset:
        expected = item.get("expected_docs", [])
        answer = gen.ask(item["question"], expected, docs)
        results.append(
            {
                "question": item["question"],
                "type": item["type"],
                "context_docs": expected,
                "answer": answer,
                "ground_truth": item.get("ground_truth"),
            }
        )
    return results


def run_faithfulness_eval(results, docs):
    """自实现 Faithfulness 评估器（和 RAGAS 同逻辑，不依赖 RAGAS 库）。"""
    from faithfulness import FaithfulnessEvaluator

    evaluator = FaithfulnessEvaluator()
    scores = []
    details = []
    for r in results:
        if r["type"] == "no_answer" or not r["context_docs"]:
            continue
        contexts = [docs.get(d, "") for d in r["context_docs"]]
        result = evaluator.evaluate(r["question"], r["answer"], contexts)
        scores.append(result["score"])
        details.append(
            {
                "question": r["question"][:40],
                "score": result["score"],
                "supported": result["supported"],
                "total": result["total"],
            }
        )
    avg = sum(scores) / len(scores) if scores else 0.0
    return {"avg_faithfulness": avg, "n": len(scores), "details": details}


def print_report(results, faith_result, docs):
    print("\n" + "=" * 80)
    print("  Generator Layer Test (skip retriever - feed golden docs)")
    print("=" * 80)

    # by type summary
    by_type = {}
    for r in results:
        t = r["type"]
        if t not in by_type:
            by_type[t] = {"total": 0, "correct": 0}
        by_type[t]["total"] += 1
        gt = r.get("ground_truth", "")
        if gt and gt in r["answer"]:
            by_type[t]["correct"] += 1

    print(f"\n  {'type':<15} {'total':<8} {'correct':<10} {'rate':<10}")
    print("  " + "-" * 45)
    for t, s in by_type.items():
        rate = f"{s['correct'] / s['total']:.0%}" if s["total"] else "N/A"
        print(f"  {t:<15} {s['total']:<8} {s['correct']:<10} {rate:<10}")

    # Faithfulness
    print("\n  --- Faithfulness Evaluation (custom impl, same logic as RAGAS) ---")
    print(f"  Evaluable items: {faith_result['n']}")
    print(f"  Average Faithfulness: {faith_result['avg_faithfulness']:.2%}")
    for d in faith_result["details"][:5]:
        print(f"  [{d['supported']}/{d['total']}] {d['question']}...")

    # Sample
    print("\n  --- Sample Answers ---")
    for r in results[:5]:
        q = r["question"][:50]
        ans = r["answer"][:120].replace("\n", " ").replace("\r", "")
        gt = r["ground_truth"] or "-"
        print(f"  Q: {q}")
        print(f"  A: {ans}...")
        print(f"  GT: {gt}\n")

    # save
    from researcher.evaluation._results import run_dir_for
    out = os.path.join(run_dir_for("rag"), "generator_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  Results saved: {out}")
    print("=" * 80)


if __name__ == "__main__":
    testset_path = os.path.join(os.path.dirname(__file__), "golden_testset_v2.json")
    with open(testset_path, encoding="utf-8") as f:
        testset = json.load(f)

    docs = load_docs()
    print(f"Testset: {len(testset)} items, Docs: {len(docs)} documents")

    results = run_generator_test(testset, docs)
    faith_result = run_faithfulness_eval(results, docs)
    print_report(results, faith_result, docs)
