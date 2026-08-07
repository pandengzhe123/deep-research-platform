"""轨迹-结果关联分析（2×2 矩阵）—— Agent 评测的灵魂。

把"轨迹质量"（Agent 执行过程好不好）和"结果质量"（最终报告好不好）合并，
定位"结果差到底是 Agent 决策错，还是检索/数据错"。

矩阵逻辑：
          轨迹好          轨迹差
结果好    ✅ 正常         ⚠️ 碰运气（结果对但过程烂）
结果差    ⚠️ 数据问题     ❌ Agent 问题
                （过程对但结论错）

定位规则：
  轨迹好 + 结果好 → 真正常，可上线
  轨迹差 + 结果好 → Agent 碰运气答对，不可靠（过程要修）
  轨迹好 + 结果差 → 不是 Agent 的错，是检索/数据/知识库问题
  轨迹差 + 结果差 → Agent 本身有问题，要修

用法：
  from researcher.evaluation.trajectory_result_matrix import classify_run
  cell = classify_run(trajectory_stats, result_score)
"""

# 轨迹质量判定阈值
TRAJ_LLM_PER_ROUND_MAX = 3.0     # LLM/轮 > 3 判低效
TRAJ_SIMILARITY_MAX = 0.8        # query 相似度 > 0.8 判循环
TRAJ_ERROR_MAX = 0               # 错误事件数 > 0 判异常
TRAJ_TOOL_BALANCE_MIN = 0.1      # think/工具比例失衡判决策差

# 结果质量判定阈值
RESULT_SCORE_GOOD = 6.0          # judge 总分 > 6 判好（0-10 制）


def trajectory_quality(trajectory_stats: dict) -> tuple[str, list[str]]:
    """判定轨迹质量，返回 (good/bad, 原因列表)。

    轨迹差的条件（任一触发）：
      1. 低效：LLM/轮 > 阈值
      2. 循环：query 相似度 > 阈值
      3. 错误：有错误事件
      4. 失衡：think 次数远多于 search（光反思不行动）或工具全失败
    """
    reasons = []
    bad = False

    llm_per_round = trajectory_stats.get("llm_calls_per_round")
    if llm_per_round is not None and llm_per_round > TRAJ_LLM_PER_ROUND_MAX:
        bad = True
        reasons.append(f"低效: LLM/轮={llm_per_round} > {TRAJ_LLM_PER_ROUND_MAX}")

    sim = trajectory_stats.get("max_query_similarity", 0)
    if sim > TRAJ_SIMILARITY_MAX:
        bad = True
        reasons.append(f"疑似循环: query 相似度={sim} > {TRAJ_SIMILARITY_MAX}")

    if trajectory_stats.get("error_events"):
        bad = True
        reasons.append(f"错误事件: {len(trajectory_stats['error_events'])} 个")

    # 工具失衡：search 和 search_kb 都为 0，但 think 很多 → 光想不搜
    # （与 tool_accuracy.py 的判定一致：rag_only 模式 search_kb>0 不算失衡）
    dist = trajectory_stats.get("tool_distribution", {})
    search_n = dist.get("search", 0)
    search_kb_n = dist.get("search_kb", 0)
    think_n = dist.get("think", 0)
    if search_n == 0 and search_kb_n == 0 and think_n >= 3:
        bad = True
        reasons.append(f"工具失衡: 无检索但 think={think_n}（光反思不行动）")

    if trajectory_stats.get("search_calls") == 0 and trajectory_stats.get("search_kb_calls") == 0:
        bad = True
        reasons.append("无任何检索（没搜就答，不可靠）")

    return ("bad" if bad else "good", reasons)


def result_quality(result_score: float | None) -> tuple[str, str]:
    """判定结果质量，返回 (good/bad, 说明)。

    result_score 为 judge 总分（0-10）。None 表示没有结果分（不可评）。
    """
    if result_score is None:
        return ("unknown", "无结果分")
    if result_score > RESULT_SCORE_GOOD:
        return ("good", f"结果分 {result_score:.1f} > {RESULT_SCORE_GOOD}")
    return ("bad", f"结果分 {result_score:.1f} <= {RESULT_SCORE_GOOD}")


def classify_run(trajectory_stats: dict, result_score: float | None) -> dict:
    """对一次研究做轨迹-结果关联分类。

    返回 2×2 矩阵定位 + 详细原因。
    """
    traj_q, traj_reasons = trajectory_quality(trajectory_stats)
    res_q, res_reason = result_quality(result_score)

    # 矩阵定位
    matrix = {
        ("good", "good"): "✅ 正常：轨迹好 + 结果好，可上线",
        ("good", "bad"): "⚠️ 数据问题：轨迹好但结果差，是检索/数据/知识库问题，不是 Agent 的错",
        ("bad", "good"): "⚠️ 碰运气：结果好但轨迹差，Agent 靠运气答对，不可靠，要修过程",
        ("bad", "bad"): "❌ Agent 问题：轨迹差 + 结果差，Agent 本身有问题，要修",
        ("good", "unknown"): "— 仅轨迹：结果未评测",
        ("bad", "unknown"): "— 仅轨迹：结果未评测（但轨迹已暴露问题）",
    }

    return {
        "trajectory_quality": traj_q,
        "trajectory_reasons": traj_reasons,
        "result_quality": res_q,
        "result_reason": res_reason,
        "matrix_cell": matrix.get((traj_q, res_q), "未知"),
    }


def build_matrix(runs: list[dict]) -> dict:
    """对多次研究构建 2×2 矩阵汇总。

    runs: [{"id", "trajectory_stats", "result_score"}, ...]
    返回: {cell: count} 分布 + 每格的研究 id 列表。
    """
    cells = {"✅ 正常": [], "⚠️ 数据问题": [], "⚠️ 碰运气": [], "❌ Agent 问题": []}
    # 用 key 映射到统计维度
    key_map = {
        ("good", "good"): "✅ 正常",
        ("good", "bad"): "⚠️ 数据问题",
        ("bad", "good"): "⚠️ 碰运气",
        ("bad", "bad"): "❌ Agent 问题",
    }
    for run in runs:
        cls = classify_run(run["trajectory_stats"], run.get("result_score"))
        cell = key_map.get((cls["trajectory_quality"], cls["result_quality"]))
        if cell:
            cells[cell].append(run["id"])

    return cells


# 评测日志路径（统一存所有研究的轨迹-结果判定）
EVAL_LOG_PATH = "reports/evaluation_log.json"


def save_run_record(trajectory_stats: dict, result_score: float | None,
                    run_id: str = "", log_path: str = EVAL_LOG_PATH) -> str:
    """把一次研究的 轨迹分 + 结果分 + 2×2 定位 追加到统一评测日志。

    之前矩阵判定只打印不存盘——关终端就丢，且无法统计多次研究。
    现在每次评测记录追加到 evaluation_log.json，可复现、可聚合。
    """
    cls = classify_run(trajectory_stats, result_score)
    if not run_id:
        run_id = trajectory_stats.get("question", "unknown")[:40]

    record = {
        "run_id": run_id,
        "trajectory": {
            "quality": cls["trajectory_quality"],
            "reasons": cls["trajectory_reasons"],
            "rounds": trajectory_stats.get("rounds"),
            "llm_per_round": trajectory_stats.get("llm_calls_per_round"),
            "max_query_similarity": trajectory_stats.get("max_query_similarity"),
        },
        "result": {
            "score": result_score,
            "quality": cls["result_quality"],
            "reason": cls["result_reason"],
        },
        "matrix": cls["matrix_cell"],
    }

    # 追加到日志（保留历史记录）
    import json
    from pathlib import Path
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    if log_path.exists():
        try:
            records = json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            records = []
    records.append(record)
    log_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  [matrix] 评测记录已保存: {log_path}")
    return str(log_path)


def build_matrix_from_log(log_path: str = EVAL_LOG_PATH) -> dict:
    """从评测日志读取历史记录，统计 2×2 矩阵分布。

    返回: {cell: [run_ids]} + 汇总。用于分析 N 次研究的整体健康度。
    """
    from pathlib import Path
    log_path = Path(log_path)
    cells = {"✅ 正常": [], "⚠️ 数据问题": [], "⚠️ 碰运气": [], "❌ Agent 问题": []}
    if not log_path.exists():
        return cells
    try:
        records = json.loads(log_path.read_text(encoding="utf-8"))
    except Exception:
        return cells
    for rec in records:
        cell = rec.get("matrix", "")
        # matrix 字段形如 "✅ 正常：轨迹好 + 结果好，可上线" → 取前半
        key = cell.split("：")[0] if "：" in cell else cell
        if key in cells:
            cells[key].append(rec.get("run_id", ""))
    return cells


if __name__ == "__main__":
    # 自测：4 种矩阵情况，验证分类逻辑
    import json
    from pathlib import Path

    # 用真实轨迹数据 + 假结果分，构造 4 种情况
    real_traj = json.load(open("reports/20260805_140215/trajectory_stats.json", encoding="utf-8"))

    print("=" * 60)
    print("  轨迹-结果关联矩阵（2×2）")
    print("=" * 60)

    # 情况 1：好轨迹 + 好结果
    t1 = dict(real_traj)
    r1 = classify_run(t1, 8.0)
    print(f"\n[1] 轨迹好 + 结果好 → {r1['matrix_cell']}")
    print(f"    轨迹理由: {r1['trajectory_reasons'] or '无'}")

    # 情况 2：好轨迹 + 差结果（数据问题）
    r2 = classify_run(t1, 4.0)
    print(f"\n[2] 轨迹好 + 结果差 → {r2['matrix_cell']}")
    print(f"    结果理由: {r2['result_reason']}")

    # 情况 3：差轨迹 + 好结果（碰运气）—— 制造低效轨迹
    t3 = dict(real_traj)
    t3["llm_calls_per_round"] = 5.0  # 超过阈值 3
    t3["max_query_similarity"] = 0.9  # 超过阈值 0.8
    r3 = classify_run(t3, 8.0)
    print(f"\n[3] 轨迹差 + 结果好 → {r3['matrix_cell']}")
    print(f"    轨迹理由: {r3['trajectory_reasons']}")

    # 情况 4：差轨迹 + 差结果（Agent 问题）
    r4 = classify_run(t3, 4.0)
    print(f"\n[4] 轨迹差 + 结果差 → {r4['matrix_cell']}")

    # 批量矩阵汇总（内存版）
    print(f"\n=== 批量矩阵（内存版，模拟 6 次研究）===")
    runs = [
        {"id": "run1", "trajectory_stats": dict(real_traj), "result_score": 8.0},
        {"id": "run2", "trajectory_stats": dict(real_traj), "result_score": 4.0},
        {"id": "run3", "trajectory_stats": t3, "result_score": 8.0},
        {"id": "run4", "trajectory_stats": t3, "result_score": 4.0},
        {"id": "run5", "trajectory_stats": dict(real_traj), "result_score": 7.5},
        {"id": "run6", "trajectory_stats": t3, "result_score": 5.0},
    ]
    matrix = build_matrix(runs)
    for cell, ids in matrix.items():
        print(f"  {cell}: {len(ids)} 次  {ids}")

    # 落盘版：把每次研究记录追加到 evaluation_log.json
    print(f"\n=== 落盘版：评测记录写入日志 ===")
    test_log = "reports/_test_eval_log.json"
    import os
    if os.path.exists(test_log):
        os.remove(test_log)  # 测试用，先清空
    for run in runs:
        save_run_record(run["trajectory_stats"], run["result_score"], run_id=run["id"], log_path=test_log)

    # 从日志读回，统计矩阵分布
    print(f"\n=== 从日志统计矩阵分布 ===")
    from_log = build_matrix_from_log(test_log)
    for cell, ids in from_log.items():
        print(f"  {cell}: {len(ids)} 次  {ids}")
    os.remove(test_log)
    print("\n测试日志已清理")
