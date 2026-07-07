"""评测主控脚本 —— 一键跑完整评估流程，汇总所有指标成表。

用法：
  # 检索 + 生成端到端评估（用 golden_testset_v2 的问题跑 RAG）
  python -m researcher.evaluation.run_eval --mode rag --n 5

  # 报告质量评估（跑 Agent 生成报告 → Judge 打分）
  python -m researcher.evaluation.run_eval --mode report --n 3 --level 2

依赖：kb 中已 ingest 对应文档（user_id="eval"）。
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

from researcher.evaluation.faithfulness import FaithfulnessEvaluator
from researcher.evaluation.answer_relevance import AnswerRelevanceEvaluator
from researcher.evaluation.context_relevance import ContextRelevanceEvaluator
from researcher.evaluation.answer_correctness import AnswerCorrectnessEvaluator
from researcher.evaluation.judge import ReportJudge

TESTSET = os.path.join(os.path.dirname(__file__), "golden_testset_v2.json")
DOC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "eval")


def _load_testset():
    with open(TESTSET, encoding="utf-8") as f:
        return json.load(f)


def _load_docs():
    docs = {}
    for fn in os.listdir(DOC_DIR):
        if fn.endswith(".txt"):
            with open(os.path.join(DOC_DIR, fn), encoding="utf-8") as f:
                docs[fn] = f.read()
    return docs


# ================================================================
# 模式 1：RAG 端到端评估（喂完美文档，测生成质量）
# ================================================================

def run_rag_eval(n: int):
    """用 golden testset 的问题 + 标注的完美文档，评估生成质量。

    这是三层评估的第二层（Generator 单独测试）——跳过检索，
    直接喂正确文档给 LLM，排除检索错误干扰。
    """
    import httpx
    testset = _load_testset()[:n]
    docs = _load_docs()

    faith = FaithfulnessEvaluator()
    ansrel = AnswerRelevanceEvaluator()
    ctxrel = ContextRelevanceEvaluator()
    anscorr = AnswerCorrectnessEvaluator()

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("LLM_MODEL", "deepseek-v4-flash")

    def generate_answer(question, context):
        resp = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model, "temperature": 0,
                "messages": [{"role": "user", "content":
                    f"基于以下文档回答问题，没有相关信息就说'未找到'，不要编造。\n\n文档：\n{context}\n\n问题：{question}\n\n回答："}],
            },
            timeout=60,
        )
        return resp.json()["choices"][0]["message"]["content"] or ""

    rows = []
    for item in testset:
        q = item["question"]
        expected_docs = item.get("expected_docs", [])
        gt = item.get("ground_truth")

        if not expected_docs:
            print(f"  跳过（无标注文档）: {q}")
            continue

        contexts = [docs.get(d, "") for d in expected_docs]
        context = "\n\n".join(contexts)
        answer = generate_answer(q, context)

        f_score = faith.evaluate(q, answer, contexts)["score"]
        ar_score = ansrel.evaluate(q, answer)["score"]
        cr_score = ctxrel.evaluate(q, contexts)["score"]
        ac_score = anscorr.evaluate(answer, gt)["score"] if gt else None

        rows.append({
            "question": q[:24],
            "type": item.get("type", ""),
            "faith": f_score,
            "ans_rel": ar_score,
            "ctx_rel": cr_score,
            "ans_corr": ac_score,
        })
        print(f"  ✓ {q[:24]:<26} F={f_score:.2f} AR={ar_score:.2f} CR={cr_score:.2f} AC={ac_score if ac_score is None else round(ac_score,2)}")

    _print_rag_table(rows)


def _print_rag_table(rows):
    print("\n" + "=" * 78)
    print("  RAG 生成质量评估（喂完美文档）")
    print("=" * 78)
    print(f"  {'问题':<26} {'Faith':>7} {'AnsRel':>7} {'CtxRel':>7} {'AnsCorr':>8}")
    print("  " + "-" * 62)
    for r in rows:
        ac = "N/A" if r["ans_corr"] is None else f"{r['ans_corr']:.2f}"
        print(f"  {r['question']:<26} {r['faith']:>7.2f} {r['ans_rel']:>7.2f} {r['ctx_rel']:>7.2f} {ac:>8}")

    def avg(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return sum(vals) / len(vals) if vals else 0.0

    print("  " + "-" * 62)
    print(f"  {'平均':<26} {avg('faith'):>7.2f} {avg('ans_rel'):>7.2f} {avg('ctx_rel'):>7.2f} {avg('ans_corr'):>8.2f}")
    print("=" * 78)

    out = os.path.join(os.path.dirname(__file__), "results", "run_eval_rag.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"  结果已保存: {out}")


# ================================================================
# 模式 2：报告质量评估（跑 Agent → Judge 打分）
# ================================================================

async def run_report_eval(n: int, level: int):
    """跑真实 Agent 生成报告，用 Judge 五维度打分。"""
    from researcher.agent import FastLevel1Agent, Level2Agent, Level3Agent, Level4Agent

    testset = _load_testset()[:n]
    judge = ReportJudge()

    rows = []
    for item in testset:
        q = item["question"]
        print(f"\n  跑 Agent (L{level}): {q}")
        if level == 1:
            agent = FastLevel1Agent(search_mode="web_only")
        elif level == 3:
            agent = Level3Agent(search_mode="web_only")
        elif level == 4:
            agent = Level4Agent(search_mode="web_only")
        else:
            agent = Level2Agent(search_mode="web_only")

        try:
            report = await agent.run(q)
        except Exception as e:
            print(f"    Agent 失败: {e}")
            continue

        result = judge.evaluate(q, report)
        rows.append({"question": q[:24], "overall": result["overall"], "dims": result["dimensions"]})
        print(f"    Judge 总分: {result['overall']}")

    _print_report_table(rows, level)


def _print_report_table(rows, level):
    print("\n" + "=" * 82)
    print(f"  报告质量评估（Level {level}, LLM-as-Judge 五维度）")
    print("=" * 82)
    print(f"  {'问题':<26} {'完整':>5} {'准确':>5} {'逻辑':>5} {'简洁':>5} {'引用':>5} {'总分':>6}")
    print("  " + "-" * 62)
    for r in rows:
        d = r["dims"]
        print(f"  {r['question']:<26} "
              f"{d.get('completeness',{}).get('score',0):>5} "
              f"{d.get('accuracy',{}).get('score',0):>5} "
              f"{d.get('logic',{}).get('score',0):>5} "
              f"{d.get('conciseness',{}).get('score',0):>5} "
              f"{d.get('citation',{}).get('score',0):>5} "
              f"{r['overall']:>6.2f}")
    print("  " + "-" * 62)
    if rows:
        avg = sum(r["overall"] for r in rows) / len(rows)
        print(f"  {'平均总分':<26} {'':>27} {avg:>6.2f}")
    print("=" * 82)

    out = os.path.join(os.path.dirname(__file__), "results", "run_eval_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"  结果已保存: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["rag", "report"], default="rag")
    parser.add_argument("--n", type=int, default=5, help="测试题数量")
    parser.add_argument("--level", type=int, default=2, help="report 模式的 Agent Level")
    args = parser.parse_args()

    os.makedirs(os.path.join(os.path.dirname(__file__), "results"), exist_ok=True)

    if args.mode == "rag":
        run_rag_eval(args.n)
    else:
        asyncio.run(run_report_eval(args.n, args.level))
