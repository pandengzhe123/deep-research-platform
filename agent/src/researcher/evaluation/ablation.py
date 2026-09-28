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
from researcher.evaluation.semantic_hit import SemanticHit


def run_ablation(testset: list[dict], user_id: str, doc_ids=None, modes: list[str] = None) -> dict:
    """
    对每个模式，逐条运行测试集，对比检索结果。

    testset 格式：[{"question": "...", "expected_chunks": ["关键词1", "关键词2"]}, ...]

    返回：{mode: {hits, total, hit_rate, chunk_mrr, chunk_recall, avg_time,
                 hit_list, mrr_list}}

    为什么除命中率外还要记 chunk_mrr / chunk_recall（2026-09-28 实测）：
      命中率在 v2 / hybrid / rerank 上分别是 96.7% / 97.5% / 98.3%，
      **相对极差只有 1.7%** —— 这个量级撑不起一次显著性检验，所以置换检验
      长期给出 p=1.0、看起来"三种模式一样好"，其实是**指标没有分辨力**。
      同一批题换成块级指标后：
        chunk_mrr        0.8764 / 0.8419 / 0.9187   相对极差 8.4%
        chunk_recall@5   92.8%  / 90.7%  / 94.9%    相对极差 4.5%
      根因是语料 65% 的文档只切出 1 个 chunk，「检索到正确文档」与「检索到相关
      内容」几乎是同一件事，文档级指标天然饱和。

    命中判定：SemanticHit（方案 B）——字面全中直接算（零成本），
    字面不中 embedding 语义相似度判断（修复关键词匹配的语义缺失）。
    chunk_mrr / chunk_recall 只做字符串比对，不额外调 API。

    modes: 要测的检索模式，默认全 4 个。full 慢（9s/题），可用 --retrieval-mode 控制。
    """
    # 懒加载：与检索回归共用同一份块级指标实现，避免两处口径再次漂移
    from researcher.evaluation.run_regression import _calc_mrr

    modes = modes or ["v2", "hybrid", "rerank", "full"]
    results = {}
    semantic_hit = SemanticHit()  # 复用阿里云 embedding

    for mode in modes:
        print(f"\n  Running mode: {mode}...")
        hits = 0
        hit_list = []
        mrr_list: list[float] = []
        recall_list: list[float] = []
        times = []

        for item in testset:
            question = item["question"]
            expected = item.get("expected_chunks") or []
            # no_answer 题型（按 type 判定，不依赖 expected_chunks 是否为空）：
            # 不参与置换检验的命中统计，单独测拒绝能力
            if item.get("type") == "no_answer":
                continue

            start = time.time()
            result = kb.search(question, user_id=user_id, doc_ids=doc_ids, mode=mode)
            elapsed = time.time() - start
            times.append(elapsed)

            # 语义命中判断：字面全中直接命中，字面不中 embedding 语义判断
            all_found, _, hit_mode = semantic_hit.check(question, expected, result)
            if hit_mode == "semantic":
                print(f"    [语义命中] {question[:30]}... 字面不中但语义等价 ✓")
            hit_list.append(1 if all_found else 0)
            if all_found:
                hits += 1

            # 块级指标（纯字符串比对，零额外成本）
            kws = [k for k in expected if k]
            mrr_list.append(_calc_mrr(result, kws))
            recall_list.append(sum(1 for k in kws if k in result) / len(kws) if kws else 0.0)

        results[mode] = {
            "hits": hits,
            "total": len(hit_list),
            "hit_rate": f"{hits / len(hit_list):.0%}" if hit_list else "N/A",
            "chunk_mrr": round(sum(mrr_list) / len(mrr_list), 4) if mrr_list else None,
            "chunk_recall": (round(sum(recall_list) / len(recall_list), 4)
                             if recall_list else None),
            "avg_time": f"{sum(times) / len(times):.2f}s" if times else "N/A",
            "hit_list": hit_list,
            "mrr_list": mrr_list,
        }

    return results


def print_ablation_table(results: dict):
    """打印消融实验对比表 + 置换检验显著性判断。"""
    print("\n" + "=" * 82)
    print("  RAG 消融实验结果")
    print("=" * 82)
    print(f"  {'模式':<10} {'命中':>5} {'总数':>5} {'命中率':>7} "
          f"{'chunk_mrr':>10} {'chunk_recall':>13} {'平均耗时':>9}")
    print("  " + "-" * 68)
    for mode, r in results.items():
        mrr = f"{r['chunk_mrr']:.4f}" if r.get("chunk_mrr") is not None else "—"
        rec = f"{r['chunk_recall']:.1%}" if r.get("chunk_recall") is not None else "—"
        print(f"  {mode:<10} {r['hits']:>5} {r['total']:>5} {r['hit_rate']:>7} "
              f"{mrr:>10} {rec:>13} {r['avg_time']:>9}")
    print("=" * 82)
    print("  说明：命中率（SemanticHit）相对极差约 1.7%，分辨力不足；")
    print("        chunk_mrr / chunk_recall 是块级指标，极差约 8.4% / 4.5%，")
    print("        模式之间要比较请看后两列。")

    # 置换检验：判断模式间差异是否显著（还是抽样波动）
    per_mode_hits = {m: r["hit_list"] for m, r in results.items() if r.get("hit_list")}
    permutation_results = compare_all_modes(per_mode_hits) if len(per_mode_hits) >= 2 else []
    if permutation_results:
        print("\n  置换检验（判断差异是否显著，非抽样波动）：")
        print(f"  {'对比':<20} {'A/B命中':<12} {'差距':>5} {'p-value':>10}  判定")
        print("  " + "-" * 55)
        for r in permutation_results:
            verdict = "✅ 显著" if r["significant"] else "⚠️ 不显著（可能是抽样波动）"
            pair = f"{r['mode_a']} vs {r['mode_b']}"
            ab = f"{r['hits_a']}/{r['hits_b']}"
            print(f"  {pair:<20} {ab:<12} {r['diff']:>5}  "
                  f"{r['p_value']:>10.3f}  {verdict}")
        print("\n  解读：p < 0.05 表示差异不太可能来自抽样波动；")
        print("  p >= 0.05 表示只凭这批评测题无法确认差异真实存在。")
        print("  ⚠️ 这里的检验跑在**命中率**上，而它的相对极差只有约 1.7% ——")
        print("     多数情况下它会给出 p≈1.0，那是分辨力不足，不是「模式一样好」。")

    # 同一批数据再对 chunk_mrr 做一次置换检验。
    # 置换检验本身只要求可加总的数值列表，不要求 0/1，所以连续值可以直接跑。
    # chunk_mrr 的相对极差约 8.4%，是这里唯一有希望区分出模式的指标。
    per_mode_mrr = {m: r["mrr_list"] for m, r in results.items() if r.get("mrr_list")}
    if len(per_mode_mrr) >= 2:
        mrr_perm = compare_all_modes(per_mode_mrr)
        print("\n  置换检验（chunk_mrr —— 分辨力更高的指标）：")
        print(f"  {'对比':<20} {'A/B 均分':<20} {'差距':>8} {'p-value':>10}  判定")
        print("  " + "-" * 62)
        for r in mrr_perm:
            n = max(r.get("n", 1), 1)
            verdict = "✅ 显著" if r["significant"] else "⚠️ 不显著"
            pair = f"{r['mode_a']} vs {r['mode_b']}"
            ab = f"{r['hits_a']/n:.4f}/{r['hits_b']/n:.4f}"
            print(f"  {pair:<20} {ab:<20} {r['diff']/n:>+8.4f} "
                  f"{r['p_value']:>10.3f}  {verdict}")
    else:
        mrr_perm = []

    # 保存结果：汇总 + 逐题命中列表 + 置换检验结果 全部落盘（可复现）
    save_results = {
        "results": {
            m: {k: v for k, v in r.items()} for m, r in results.items()
        },
        "permutation_test": permutation_results,
        "permutation_test_chunk_mrr": mrr_perm,
    }
    from researcher.evaluation._results import run_dir_for
    out_path = os.path.join(run_dir_for("rag"), "ablation_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(save_results, f, ensure_ascii=False, indent=2)
    print(f"\n  结果已保存: {out_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="RAG 消融实验")
    parser.add_argument("--retrieval-mode", choices=["v2", "hybrid", "rerank", "full", "all"], default="all",
                        help="检索模式: v2/hybrid/rerank/full/all(默认)。all 不含 full（full 慢 9s/题），跑 full 需显式 --retrieval-mode full")
    args = parser.parse_args()
    # all 默认跑 v2/hybrid/rerank，full 需显式指定（避免误触发慢模式）
    modes = ["v2", "hybrid", "rerank"] if args.retrieval_mode == "all" else [args.retrieval_mode]

    # 加载测试集
    testset_path = os.path.join(os.path.dirname(__file__), "golden_testset_v4.json")
    try:
        with open(testset_path, "r", encoding="utf-8") as f:
            testset = json.load(f)
    except FileNotFoundError:
        print(f"测试集不存在: {testset_path}")
        print("请先创建 golden_testset.json")
        sys.exit(1)

    results = run_ablation(testset, user_id="eval", modes=modes)
    print_ablation_table(results)
