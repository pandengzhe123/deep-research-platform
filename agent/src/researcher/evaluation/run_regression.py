"""回归测试 —— 改代码后快速验证系统没有退化。

用法:
  python -m researcher.evaluation.run_regression --mode retriever              # v2 检索回归 (25s)
  python -m researcher.evaluation.run_regression --mode retriever --retrieval-mode all  # 四种模式全测
  python -m researcher.evaluation.run_regression --mode format                 # 格式回归 (2min)
  python -m researcher.evaluation.run_regression --mode all                    # 全跑
  python -m researcher.evaluation.run_regression --update-baseline             # 更新全部基准
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

# ============================================================
# 配置
# ============================================================

BASELINE_FILE = os.path.join(os.path.dirname(__file__), ".regression_baseline.json")

# 测试集路径从共享模块取，不再各脚本硬编码（见 _testset.py 的说明）
from researcher.evaluation._testset import TESTSET

# 保存回归结果到 results/ 下
from researcher.evaluation._results import run_dir_for as _run_dir_for

# 格式回归用的固定题目（简单 + 中等 + 口语化，确保 Agent 能稳定出报告）
FORMAT_QUESTIONS = [
    "什么是 Python 协程",
    "Docker 和虚拟机有什么区别",
    "Redis 怎么配置哨兵模式",
    "怎么用 JWT 做登录",
    "分布式锁是怎么工作的",
]

# 格式检查规则（正则，全部通过才算合格）
FORMAT_RULES = {
    "report_non_empty": (r".", "报告不能为空"),
    "has_heading": (r"^#+\s", "必须有 Markdown 标题"),
    "has_citation": (r"\[.+\]\(https?://.+\)", "必须有引用链接 [标题](URL)"),
    "has_sources_section": (r"(?i)(参考来源|Sources|参考资料|引用来源)", "必须有参考来源章节"),
    "min_length": (r"^.{500,}$", "报告长度 >= 500 字符", re.DOTALL),
    "has_conclusion": (r"(?i)(总结|结论|概述|Overview|Conclusion|Summary)", "必须有概述或总结段落"),
}


# ============================================================
# 检索回归
# ============================================================

# ============================================================
# 指标口径版本
# ============================================================
# 改动任何「命中判定 / MRR 计算 / 题目集合」的逻辑时 +1。
#
# 为什么需要：基准文件存的是**某个口径下**测出来的数。口径变了还拿新数去比旧基准，
# 得出的 PASS/FAIL 没有意义。这个项目的 `.regression_baseline.json` 自 2026-07-16
# 起再未更新，期间测试集从 112 题涨到 130 题、no_answer 题的 MRR 处理也被改过，
# 而基准一直作为有效门禁在跑 —— 甚至出现过「实测 MRR 低于基准 MRR，却 passed=true」。
# 现在口径不匹配时**不静默放行**：打印重建指令并判定失败，逼一次有意识的决定。
#
# 版本历史：
#   1  最初（隐含，未显式记录）
#   2  no_answer 的 MRR 改为不参与计算；运行路径与 --update-baseline 共用
#      classify_retrieval()；命中判定统一
#   3  测试集切到 golden_testset_v5（130 → 159 题：+14 long_doc、+15 no_answer）
METRIC_VERSION = 3


def _calc_mrr(result_text: str, expected_chunks: list[str]) -> float:
    """第一个「含期望关键词的来源块」排在第几位，取其倒数。

    ⚠️ 与 `retriever_test.mrr()` **不是同一个指标**，两者不可互换：
      · 本函数按 **chunk 文本里是否出现 expected_chunks 关键词** 判定，
        衡量「答案内容有没有被排到前面」——贴近端到端可用性。
      · `retriever_test.mrr()` 按 **来源文档名是否命中 expected_docs** 判定，
        衡量「正确的文档有没有被排到前面」——经典 IR 口径。
    同名不同义是这套评测体系最容易误读的地方，改动时请保持二者各自的名字清晰。
    """
    # 从格式化结果中提取 --- 来源 N: 块的位置
    sources = re.findall(r'--- 来源 (\d+):', result_text)
    if not sources:
        return 0.0
    for rank, src_num in enumerate(sources):
        # 检查该来源块前的内容是否包含任一关键词
        block_pattern = rf'--- 来源 {src_num}:.*?(?=--- 来源 \d+:|$)'
        block_match = re.search(block_pattern, result_text, re.DOTALL)
        if block_match:
            block = block_match.group()
            if any(kw in block for kw in expected_chunks if kw):
                return 1.0 / (rank + 1)
    return 0.0


def classify_retrieval(item: dict, result_text: str, semantic_hit) -> dict:
    """把一个测试项的检索结果归类 —— 运行路径与 --update-baseline 共用这一份判定。

    抽出来的原因：这两条路径原先各写了一份同样的逻辑，**而且已经漂移了**。
    no_answer 题的 MRR 处理不一致：
      · 运行路径：只有「正确拒答」的才 mrr_sum += 1.0，漏拒的既不进分子也不进分母
      · --update-baseline：**无论是否拒答**都 mrr_sum += 1.0
    于是同一个 MRR 在两条路径下是两套口径，基准值被凭空抬高（等于给 no_answer 题
    发满分奖励），门禁因此系统性偏松。

    返回：
      kind    : "refused" / "not_refused" / "hit" / "miss"
      hit     : 是否计入命中数（refused 与 hit 为 True）
      mrr     : 该题倒数排名；**仅对有答案的题目计算** —— no_answer 题在知识库里
                没有相关文档，MRR 无定义，记 1.0 等于奖励分。
      missing : 未命中说明，供 failures 记录
    """
    expected = item.get("expected_chunks", [])
    # no_answer 判定按 type，不按 expected_chunks 是否为空 ——
    # 测试集统一用 chunks=['未找到'] 标注 no_answer，chunks 非空但题型是 no_answer
    if item.get("type") == "no_answer":
        refused = "未找到" in result_text or "not found" in result_text.lower()
        if refused:
            return {"kind": "refused", "hit": True, "mrr": None, "missing": []}
        return {
            "kind": "not_refused", "hit": False, "mrr": None,
            "missing": ["应返回未找到但实际有结果"],
        }

    # 语义命中判断：字面全中直接命中，字面不中 embedding 语义判断
    found, _, _ = semantic_hit.check(item["question"], expected, result_text)
    return {
        "kind": "hit" if found else "miss",
        "hit": found,
        "mrr": _calc_mrr(result_text, expected),
        "missing": [] if found else [kw for kw in expected if kw not in result_text],
    }


def run_retriever_regression(mode: str = "v2"):
    """跑检索回归，对比基准命中率和 MRR，检测退化。"""
    from researcher.kb import kb
    from researcher.evaluation.semantic_hit import SemanticHit

    print("\n" + "=" * 60)
    print(f"  检索回归测试 ({mode})")
    print("=" * 60)

    # 口径检查放在检索**之前**：口径不符时这次检索的结果不会被采用，
    # 没必要先烧掉 130 次 embedding 调用再告诉用户"请重建基准"。
    #
    # 版本按**模式**记，不是文件级：各模式的基准值是不同时间、可能不同口径下
    # 测出来的（现存文件里 hybrid/rerank/full 仍是 2026-07-16 的旧口径值），
    # 文件级版本会让它们被误认为有效。
    baseline = _load_baseline() or {}
    baseline_key_hit = f"retriever_{mode}_hit_rate"
    baseline_key_mrr = f"retriever_{mode}_mrr"
    ver_key = f"retriever_{mode}_metric_version"
    if baseline.get(baseline_key_hit) is not None and baseline.get(ver_key) != METRIC_VERSION:
        print(f"\n  [FAIL] {mode} 的基准口径版本为 {baseline.get(ver_key)!r}，"
              f"当前为 {METRIC_VERSION} —— 两者不可比。")
        print("         已跳过本次检索（避免白跑一遍拿不到可用的数）。")
        print("         请先重建基准（会覆盖该模式的键）：")
        print("           python -m src.researcher.evaluation.run_regression --update-baseline")
        print("         注意 --update-baseline 默认只更新 v2；覆盖全部模式需加 "
              "--retrieval-mode all。")
        return False

    with open(TESTSET, encoding="utf-8") as f:
        testset = json.load(f)

    hits, total = 0, 0
    mrr_sum = 0.0
    mrr_count = 0
    failures = []
    refused, not_refused = 0, 0
    semantic_hit = SemanticHit()  # embedding 语义判定
    t0 = time.time()
    for item in testset:
        result = kb.search(item["question"], user_id="eval", mode=mode)
        cls = classify_retrieval(item, result, semantic_hit)
        total += 1
        if cls["hit"]:
            hits += 1
        else:
            failures.append({"question": item["question"][:50], "missing": cls["missing"]})
        if cls["kind"] == "refused":
            refused += 1
        elif cls["kind"] == "not_refused":
            not_refused += 1
        # MRR 只对有答案的题目计算：no_answer 题在知识库里没有相关文档，
        # MRR 无定义。此前给它记 1.0（且只在拒答时记）等于发奖励分。
        if cls["mrr"] is not None:
            mrr_sum += cls["mrr"]
            mrr_count += 1

    hit_rate = hits / total if total else 0
    mrr = mrr_sum / mrr_count if mrr_count else 0
    elapsed = time.time() - t0

    print(f"  题目: {total}  命中: {hits}  命中率: {hit_rate:.1%}  MRR: {mrr:.3f}  耗时: {elapsed:.1f}s")
    # 分层报数：命中率把两类题混在一起算，会把「拒答」这个独立维度藏起来。
    # 实测（2026-09-28，用户 kb_eval_v2 / golden_testset_v4）：可答题 116~118/120，
    # 而 no_answer 只拒答 1/10 —— 合起来的 92.3% 看起来"还行"，
    # 完全掩盖了「该拒答却答了 9 次」这个真正的缺陷。必须分开报。
    na_total = refused + not_refused
    if na_total:
        ans_total = total - na_total
        ans_hits = hits - refused
        print(f"  分层: 可答题 {ans_hits}/{ans_total} = {ans_hits/ans_total:.1%}"
              f"   |   no_answer 拒答 {refused}/{na_total} = {refused/na_total:.0%}")

    # 口径检查已在检索前做过，这里直接比较（baseline 与两个 key 均已就绪）
    passed = True
    if baseline.get(baseline_key_hit) is not None:
        baseline_hit = baseline[baseline_key_hit]
        baseline_mrr = baseline.get(baseline_key_mrr, 0)
        hit_threshold = baseline_hit - 0.02  # 允许 2% 波动
        mrr_threshold = baseline_mrr - 0.05  # 允许 0.05 波动
        print(f"  基准: 命中率 {baseline_hit:.1%} (>= {hit_threshold:.1%})  MRR {baseline_mrr:.3f} (>= {mrr_threshold:.3f})")

        if hit_rate < hit_threshold:
            print(f"\n  [FAIL] 命中率退化！{hit_rate:.1%} < {hit_threshold:.1%}")
            passed = False
        if mrr < mrr_threshold:
            print(f"  [FAIL] MRR 退化！{mrr:.3f} < {mrr_threshold:.3f}")
            passed = False
        if passed:
            print(f"  [PASS] 检索回归通过")
        else:
            print(f"  未命中/低MRR题目:")
            for f in failures[:5]:
                print(f"    - {f['question']}: 缺失 {f['missing']}")
    else:
        baseline[baseline_key_hit] = hit_rate
        baseline[baseline_key_mrr] = mrr
        baseline[ver_key] = METRIC_VERSION
        _save_baseline(baseline)
        print(f"  [*] 已保存基准: 命中率 {hit_rate:.1%}, MRR {mrr:.3f} "
              f"(口径版本 {METRIC_VERSION})")

    _save_result(
        f"retriever_{mode}",
        hit_rate=hit_rate, mrr=round(mrr, 4),
        hits=hits, total=total,
        baseline=baseline.get(baseline_key_hit) if baseline else None,
        baseline_mrr=baseline.get(baseline_key_mrr) if baseline else None,
        passed=passed, elapsed_s=round(elapsed, 1),
        failures=failures[:20],
    )
    return passed


# ============================================================
# 格式回归
# ============================================================

async def run_format_regression():
    """跑 Level 2 生成报告，正则检查格式规则，检测退化。"""
    from researcher.agent import Level2Agent

    print("\n" + "=" * 60)
    print("  格式回归测试 (Level 2)")
    print("=" * 60)

    t0_all = time.time()
    agent = Level2Agent(search_mode="web_only")
    failed_any = False

    for i, question in enumerate(FORMAT_QUESTIONS):
        print(f"\n  [{i+1}/{len(FORMAT_QUESTIONS)}] {question}")
        t0 = time.time()
        try:
            report = await agent.run(question)
        except Exception as e:
            print(f"    [FAIL] Agent 运行失败: {e}")
            failed_any = True
            continue
        elapsed = time.time() - t0

        # 逐条检查格式规则
        all_pass = True
        for rule_name, rule_def in FORMAT_RULES.items():
            if len(rule_def) == 3:
                pattern, description, flags = rule_def
            else:
                pattern, description = rule_def
                flags = 0
            regex_flags = flags or (re.DOTALL if rule_name == "min_length" else re.MULTILINE)
            if re.search(pattern, report, regex_flags):
                print(f"    [OK] {rule_name}")
            else:
                print(f"    [FAIL] {rule_name} — {description}")
                all_pass = False

        if all_pass:
            print(f"    [PASS] 全部通过 ({elapsed:.1f}s)")
        else:
            print(f"    [FAIL] 格式检查失败 ({elapsed:.1f}s)")
            print(f"    --- 报告预览（前 200 字）---")
            print(f"    {report[:200]}...")
            failed_any = True

    _save_result(
        "format",
        questions=FORMAT_QUESTIONS,
        passed=not failed_any,
        elapsed_s=round(time.time() - t0_all if 't0_all' in dir() else 0, 1),
    )
    print(f"\n  {'[FAIL] 格式回归失败' if failed_any else '[PASS] 格式回归通过'}")
    return not failed_any


# ============================================================
# 工具函数
# ============================================================

def _load_baseline():
    if os.path.exists(BASELINE_FILE):
        with open(BASELINE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_baseline(data):
    with open(BASELINE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _save_result(mode: str, **kwargs):
    """保存回归结果到 results/<timestamp>_regression/ 目录。"""
    out_dir = os.path.join(_run_dir_for("regression"), mode)
    os.makedirs(out_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"{timestamp}.json")
    result = {"mode": mode, "timestamp": timestamp, **kwargs}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"  [*] 结果已保存: {out_path}")


async def main():
    parser = argparse.ArgumentParser(description="回归测试")
    parser.add_argument("--mode", choices=["retriever", "format", "all"], default="all",
                        help="回归类型: retriever(检索) / format(格式) / all(全部)")
    parser.add_argument("--retrieval-mode", choices=["v2", "hybrid", "rerank", "full", "all"], default="v2",
                        help="检索回归覆盖的模式: v2/hybrid/rerank/full/all")
    parser.add_argument("--update-baseline", action="store_true",
                        help="重建基准。只重建 --retrieval-mode 指定的模式 —— "
                             "默认仅 v2；要覆盖全部模式需显式 --retrieval-mode all")
    args = parser.parse_args()

    retrieval_modes = ["v2", "hybrid", "rerank", "full"] if args.retrieval_mode == "all" else [args.retrieval_mode]

    if args.update_baseline:
        print("更新基准...")
        from researcher.kb import kb
        from researcher.evaluation.semantic_hit import SemanticHit
        with open(TESTSET, encoding="utf-8") as f:
            testset = json.load(f)
        baseline = _load_baseline() or {}
        semantic_hit = SemanticHit()
        for rm in retrieval_modes:
            hits, total, mrr_sum, mrr_count = 0, 0, 0.0, 0
            refused = not_refused = 0
            for item in testset:
                result = kb.search(item["question"], user_id="eval", mode=rm)
                # 与运行路径共用同一份判定 —— 此前两条路径各写一份，且已经漂移
                # （no_answer 题的 MRR 处理不一致，基准被凭空抬高）。
                cls = classify_retrieval(item, result, semantic_hit)
                total += 1
                if cls["hit"]:
                    hits += 1
                if cls["kind"] == "refused":
                    refused += 1
                elif cls["kind"] == "not_refused":
                    not_refused += 1
                if cls["mrr"] is not None:
                    mrr_sum += cls["mrr"]
                    mrr_count += 1
            hit_rate = hits / total if total else 0
            mrr_val = mrr_sum / mrr_count if mrr_count else 0
            baseline[f"retriever_{rm}_hit_rate"] = hit_rate
            baseline[f"retriever_{rm}_mrr"] = round(mrr_val, 4)
            # 版本按模式记：只给本次真正重建过的模式盖章，其余模式的旧口径值
            # 保持"无版本"状态 → 它们自己那关会失败并要求重建，而不会被误用。
            baseline[f"retriever_{rm}_metric_version"] = METRIC_VERSION
            na = refused + not_refused
            extra = f"，拒答 {refused}/{na}" if na else ""
            print(f"  {rm}: 命中率 {hit_rate:.1%}, MRR {mrr_val:.3f}{extra}")
        _save_baseline(baseline)
        print(f"基准已更新（{len(retrieval_modes)} 个模式），口径版本 = {METRIC_VERSION}")
        return

    all_pass = True

    if args.mode in ("retriever", "all"):
        for rm in retrieval_modes:
            if not run_retriever_regression(mode=rm):
                all_pass = False

    if args.mode in ("format", "all"):
        if not await run_format_regression():
            all_pass = False

    print("\n" + "=" * 60)
    if all_pass:
        print("  [PASS] 全部回归测试通过")
        print("=" * 60)
        sys.exit(0)
    else:
        print("  [FAIL] 回归测试失败，请检查上述失败项")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
