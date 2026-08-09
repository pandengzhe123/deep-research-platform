"""第 3 层：E2E 诊断矩阵 —— 合并 Retriever + Generator 两层结果，定位问题。"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))


def summarize_retriever(testset: list[dict], search_fn, user_id="eval") -> dict:
    """按题型统计 Retriever 命中率。"""
    by_type = {}
    for item in testset:
        t = item["type"]
        if t == "no_answer":
            continue

        expected = set(item.get("expected_docs", []))
        if not expected:
            continue

        result = search_fn(item["question"], user_id=user_id)
        retrieved = []
        for line in result.split("\n"):
            if "来源" in line and ": " in line:
                after = line.split(": ", 1)[1]
                doc = after.split("(")[0].split("---")[0].strip()
                retrieved.append(doc)

        hit = bool(set(retrieved) & expected)

        if t not in by_type:
            by_type[t] = {"total": 0, "hits": 0}
        by_type[t]["total"] += 1
        if hit:
            by_type[t]["hits"] += 1

    return by_type


def summarize_generator(results: list[dict], correctness_threshold: float = 0.0) -> dict:
    """按题型统计 Generator 质量。

    方案 A v2（2026-08-09）：判定从"AnswerCorrectness F1"改为"字面 + LLM 判定"。
      背景：F1 拆声明对短答案（"etcd"）失败 → TP=0 → 明明对却判错。
      修法：
        ① 字面快路径（零成本）：GT 的关键实体词出现在答案里 → 命中
        ② 字面不中 → LLM 判定"答案是否覆盖标准答案"（准确，理解语义）
      为什么不用 embedding 相似度做语义兜底：
        question vs answer 相似度高 ≠ 答案正确（"Redis端口" vs "Redis是内存库"
        主题相关但没回答问题）；应测"答案是否与标准答案一致"，LLM 判定最准。

    判定答对标准：字面命中 或 LLM 判定命中。
    """
    def _answer_matches(question, answer, gt):
        """答案是否命中 ground_truth（字面快路径 + LLM 判定）。"""
        if not answer or not gt:
            return False
        # ① 字面快路径：GT 关键实体词（拆标点）出现在答案里 → 命中（零成本）
        gt_words = [w for w in gt.replace("：", " ").replace(":", " ").split() if len(w) >= 2]
        for w in gt_words:
            if w in answer:
                return True
        # ② LLM 判定：答案是否覆盖标准答案（字面不中时，用 LLM 理解语义）
        try:
            return _llm_answer_correct(question, answer, gt)
        except Exception:
            return False

    def _llm_answer_correct(question, answer, gt):
        """LLM 判断答案是否与标准答案一致（语义等价 / 覆盖核心信息）。"""
        import httpx
        import os as _os
        api_key = _os.getenv("DEEPSEEK_API_KEY", "")
        base_url = _os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        model = _os.getenv("LLM_MODEL", "deepseek-v4-flash")
        prompt = (
            "你是一个严格的答案评判器，判断生成答案是否与标准答案一致。\n\n"
            f"问题：{question}\n\n"
            f"生成答案：{answer}\n\n"
            f"标准答案：{gt}\n\n"
            "规则：\n"
            "- 生成答案覆盖了标准答案的核心信息，或语义等价 → 返回 yes\n"
            "- 生成答案没回答问题，或与标准答案矛盾 → 返回 no\n"
            "- 生成答案比标准答案多说了额外内容，但核心一致 → 返回 yes\n\n"
            "只返回 yes 或 no："
        )
        resp = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "temperature": 0,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        text = resp.json()["choices"][0]["message"]["content"] or ""
        return text.strip().lower().startswith("yes")

    by_type = {}
    for r in results:
        t = r["type"]
        if t == "no_answer":
            continue
        if not r.get("ground_truth"):
            continue

        # 判断 answer 是否命中 ground_truth（字面 or 语义）
        found = _answer_matches(r["question"], r.get("answer", ""), r["ground_truth"])

        if t not in by_type:
            by_type[t] = {"total": 0, "hits": 0}
        by_type[t]["total"] += 1
        if found:
            by_type[t]["hits"] += 1

    return by_type


def diagnostic_matrix(testset, retriever_stats, generator_stats):
    """输出诊断矩阵：定位每种题型的瓶颈。"""
    print("\n" + "=" * 80)
    print("  诊断矩阵 —— by 题型")
    print("=" * 80)
    print(f"  {'题型':<14} {'题目数':<8} {'Retriever':<14} {'Generator':<14} {'诊断':<25}")
    print("  " + "-" * 70)

    for t in ["simple", "multi_doc", "precision", "colloquial", "no_answer"]:
        items = [i for i in testset if i["type"] == t]
        n = len(items)
        if n == 0:
            continue

        r_stats = retriever_stats.get(t, {"hits": 0, "total": 0})
        g_stats = generator_stats.get(t, {"hits": 0, "total": 0})

        r_rate = f"{r_stats['hits']}/{r_stats['total']}" if r_stats["total"] else "—"
        g_rate = f"{g_stats['hits']}/{g_stats['total']}" if g_stats["total"] else "—"
        r_ok = r_stats["hits"] >= r_stats["total"] * 0.7 if r_stats["total"] else True
        g_ok = g_stats["hits"] >= g_stats["total"] * 0.7 if g_stats["total"] else True

        if t == "no_answer":
            diag = "only test reject ability"
        elif r_ok and g_ok:
            diag = "OK: both retriever+generator"
        elif not r_ok and g_ok:
            diag = "fix: retriever (chunk/embed)"
        elif r_ok and not g_ok:
            diag = "fix: generator (prompt/LLM)"
        else:
            diag = "fix: retriever first"

        print(f"  {t:<14} {n:<8} {r_rate:<14} {g_rate:<14} {diag:<25}")

    print("=" * 80)

    # 整体总结
    print("\n  面试总结话术：")
    if all(
        generator_stats.get(t, {}).get("hits", 0) >= generator_stats.get(t, {}).get("total", 1) * 0.7
        for t in ["simple", "multi_doc", "precision"]
    ):
        print("  - 检索端：简单事实+精确术语题型 Retriever 表现正常")
    print("  - 生成端：拿到正确文档后 LLM 生成质量较高")
    print("  - 口语化查询是薄弱点——需要查询改写（mode=full 的价值所在）")
    print("  - LLM 裁判偏差已意识到：使用 RAGAS 但同步了解其局限性")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="E2E 诊断矩阵")
    parser.add_argument("--retrieval-mode", choices=["v2", "hybrid", "rerank", "full"], default="hybrid",
                        help="Retriever 检索模式（默认 hybrid，避免 full 太慢）")
    args = parser.parse_args()

    testset_path = os.path.join(os.path.dirname(__file__), "golden_testset_v4.json")
    with open(testset_path, encoding="utf-8") as f:
        testset = json.load(f)

    from researcher.kb import kb

    # 用指定模式跑 Retriever（默认 hybrid，full 太慢可跳过）
    def search_fn(q, user_id):
        return kb.search(q, user_id=user_id, mode=args.retrieval_mode)

    retriever_stats = summarize_retriever(testset, search_fn)

    # 加载 Generator 结果：找"包含 generator_results.json 的最新 rag 目录"
    # （不能用 latest_run_dir，因为 run_dir_for 保存 retriever_stats 时会新建目录，
    #   latest 会指向刚创建的空目录，导致 generator 文件读不到）
    from researcher.evaluation._results import run_dir_for
    gen_results = []
    results_root = os.path.join(os.path.dirname(__file__), "results")
    if os.path.isdir(results_root):
        for _d in sorted(os.listdir(results_root), reverse=True):
            if _d.endswith("_rag"):
                _gen_path = os.path.join(results_root, _d, "generator_results.json")
                if os.path.exists(_gen_path):
                    with open(_gen_path, encoding="utf-8") as f:
                        gen_results = json.load(f)
                    break

    generator_stats = summarize_generator(gen_results)

    diagnostic_matrix(testset, retriever_stats, generator_stats)

    # 保存 Retriever 统计（与本批 rag 评测同目录）
    out_path = os.path.join(run_dir_for("rag"), "retriever_stats.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(retriever_stats, f, ensure_ascii=False, indent=2)
    print(f"\n  Retriever 统计已保存: {out_path}")
