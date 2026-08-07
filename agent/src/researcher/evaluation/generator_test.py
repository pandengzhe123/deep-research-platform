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
        # 不再跳过 no_answer：空/无关上下文也喂给 LLM，
        # 测试生成端在"没有答案可查"时是拒绝（说未找到）还是硬编。
        ctx = "\n\n".join(all_docs.get(d, "") for d in (context_docs or []))
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


# 拒绝信号关键词：答案中出现这些词 → 判定为"拒绝"（没说未找到则视为硬编）
REJECT_KEYWORDS = ["未找到", "没有", "不存在", "无法", "没有找到", "not found", "no information", "no answer", "n/a", "暂未"]


def _is_rejection(answer: str) -> bool:
    """判断答案是否"拒绝"（诚实说没有相关信息）而非硬编。"""
    if not answer or not answer.strip():
        return False
    low = answer.lower()
    # 拒绝信号：明确说没有/找不到/无法回答
    if any(k in low for k in REJECT_KEYWORDS):
        return True
    # 空话式拒绝："我没有足够的信息" "无法确定" 等
    if "足够的信息" in low or "无法确定" in low or "不能回答" in low:
        return True
    return False


def run_rejection_test(results: list[dict]) -> dict:
    """测生成端拒绝能力：no_answer 题喂空/无关上下文后，模型是拒绝还是硬编。

    返回按题型分组的拒绝率。重点看 no_answer 组。
    """
    by_type: dict[str, dict] = {}
    for r in results:
        t = r["type"]
        if t not in by_type:
            by_type[t] = {"total": 0, "rejected": 0, "hallucinated": []}
        by_type[t]["total"] += 1
        if _is_rejection(r["answer"]):
            by_type[t]["rejected"] += 1
        else:
            # 记录硬编样例，供人工检查
            by_type[t]["hallucinated"].append(
                {"question": r["question"][:50], "answer": r["answer"][:120]}
            )
    return by_type


def run_faithfulness_eval(results, docs):
    """自实现 Faithfulness 评估器（和 RAGAS 同逻辑，不依赖 RAGAS 库）。"""
    from researcher.evaluation.faithfulness import FaithfulnessEvaluator

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


def print_report(results, faith_result, docs, rejection_result=None):
    print("\n" + "=" * 80)
    print("  Generator Layer Test (skip retriever - feed golden docs)")
    print("=" * 80)

    # by type summary
    # 用声明级 F1 比对（而非字符串匹配），避免语义等价被误判。
    # 与 summarize_generator 保持一致：默认 F1 > 0 即算对。
    from researcher.evaluation.answer_correctness import AnswerCorrectnessEvaluator
    correctness_ev = AnswerCorrectnessEvaluator()

    by_type = {}
    for r in results:
        t = r["type"]
        if t not in by_type:
            by_type[t] = {"total": 0, "correct": 0}
        by_type[t]["total"] += 1
        gt = r.get("ground_truth", "")
        answer = r.get("answer", "")
        if gt and answer:
            score = correctness_ev.evaluate(answer, gt)["score"]
            if score > 0.0:  # 有声明与标准答案一致即算对
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

    # 拒绝能力（生成端防硬编）
    if rejection_result:
        print("\n  --- Rejection Test (防硬编：空/无关上下文下是否拒绝) ---")
        print(f"  {'type':<15} {'total':<8} {'rejected':<10} {'rate':<10}")
        print("  " + "-" * 45)
        for t, s in rejection_result.items():
            rate = f"{s['rejected'] / s['total']:.0%}" if s["total"] else "N/A"
            print(f"  {t:<15} {s['total']:<8} {s['rejected']:<10} {rate:<10}")
        no_answer = rejection_result.get("no_answer", {})
        hallu = no_answer.get("hallucinated", [])
        if hallu:
            print(f"\n  ⚠️ no_answer 题型 {len(hallu)} 个硬编样例（模型编造了答案）：")
            for h in hallu[:5]:
                print(f"    Q: {h['question']}")
                print(f"    A: {h['answer']}...")

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
    rejection_result = run_rejection_test(results)
    print_report(results, faith_result, docs, rejection_result=rejection_result)
