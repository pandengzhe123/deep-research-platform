"""阈值标定工具 —— 用标注数据反推最优阈值，替代"拍脑袋"。

背景问题：
  2×2 矩阵的判定阈值（RESULT_SCORE_GOOD=6.0 / TRAJ_LLM_PER_ROUND_MAX=3.0
  / TRAJ_SIMILARITY_MAX=0.8）全是拍脑袋定的，没有校准实验支撑。
  拍脑袋的阈值可能让 2×2 矩阵误分类。

正确做法：
  ① 收集 N 次真实研究的指标 + 结果分
  ② 人工标注每次"真的好/坏"（ground truth）
  ③ 阈值扫描：遍历所有候选阈值，找"误分类最少"的最优阈值
  ④ 留出验证集，确认阈值泛化

用法：
  # 数据格式（标注文件 threshold_data.json）：
  # [{"llm_per_round": 1.67, "similarity": 0.0, "judge_score": 7.3, "human_label": "good"}, ...]

  from researcher.evaluation.threshold_calibration import scan_threshold
  best = scan_threshold(data, "llm_per_round", direction="higher_is_bad")
  # → {"best_threshold": 3.0, "error_rate": 0.0, ...}
"""

import json


def scan_threshold(data: list[dict], metric_key: str, direction: str = "higher_is_bad") -> dict:
    """对单个指标做阈值扫描，找误分类最少的阈值。

    参数：
      data: [{"<metric_key>": 值, "human_label": "good"/"bad"}, ...]
      metric_key: 要标定的指标字段名
      direction:
        "higher_is_bad"  → 指标越高越差（LLM/轮、相似度）
        "lower_is_bad"   → 指标越低越差（结果分）

    返回：
      {"best_threshold", "error_rate", "misclassified", "n",
       "confusion": {"TP","FP","FN","TN"}}  # 混淆矩阵
    """
    # 去重所有候选阈值（指标的观测值即候选切分点）
    candidates = sorted({d[metric_key] for d in data if metric_key in d})
    if not candidates:
        return {"best_threshold": None, "error_rate": None, "misclassified": [], "n": 0}

    best = None
    best_err = len(data) + 1
    best_conf = None

    for t in candidates:
        tp = fp = fn = tn = 0
        for d in data:
            v = d.get(metric_key)
            if v is None:
                continue
            if direction == "higher_is_bad":
                pred = "bad" if v > t else "good"
            else:  # lower_is_bad
                pred = "bad" if v < t else "good"

            if pred == "good" and d["human_label"] == "good":
                tp += 1
            elif pred == "bad" and d["human_label"] == "good":
                fp += 1
            elif pred == "bad" and d["human_label"] == "bad":
                tn += 1
            else:
                fn += 1

        err = fp + fn
        if err < best_err:
            best_err = err
            best = t
            best_conf = {"TP": tp, "FP": fp, "FN": fn, "TN": tn}

    return {
        "best_threshold": best,
        "error_rate": round(best_err / len(data), 3) if data else None,
        "misclassified": best_err,
        "n": len(data),
        "confusion": best_conf,
    }


def scan_all(data: list[dict], specs: list[dict]) -> dict:
    """对多个指标做标定。

    specs: [{"metric_key": "llm_per_round", "direction": "higher_is_bad"}, ...]
    """
    return {s["metric_key"]: scan_threshold(data, s["metric_key"], s["direction"]) for s in specs}


if __name__ == "__main__":
    # 用假标注数据演示标定过程
    # 模拟 20 次研究：LLM/轮 正常 1-2，异常 3-4；结果分 正常 6.5-8，异常 4-5.5
    data = []
    # 正常研究：低 LLM/轮 + 高结果分
    data += [{"llm_per_round": 1.2, "similarity": 0.1, "judge_score": 7.5, "human_label": "good"}] * 3
    data += [{"llm_per_round": 1.8, "similarity": 0.3, "judge_score": 7.0, "human_label": "good"}] * 3
    data += [{"llm_per_round": 2.5, "similarity": 0.5, "judge_score": 6.5, "human_label": "good"}] * 3
    # 异常研究：高 LLM/轮 + 低结果分
    data += [{"llm_per_round": 3.2, "similarity": 0.7, "judge_score": 5.5, "human_label": "bad"}] * 3
    data += [{"llm_per_round": 4.0, "similarity": 0.9, "judge_score": 4.5, "human_label": "bad"}] * 3
    data += [{"llm_per_round": 5.0, "similarity": 0.95, "judge_score": 3.0, "human_label": "bad"}] * 3

    print("=" * 60)
    print("  阈值标定（用标注数据反推，替代拍脑袋）")
    print("=" * 60)
    print(f"  标注数据: {len(data)} 条")

    specs = [
        {"metric_key": "llm_per_round", "direction": "higher_is_bad"},
        {"metric_key": "similarity", "direction": "higher_is_bad"},
        {"metric_key": "judge_score", "direction": "lower_is_bad"},
    ]
    results = scan_all(data, specs)
    print()
    for key, r in results.items():
        print(f"  {key}: 最优阈值={r['best_threshold']}  误分类={r['misclassified']}/{r['n']} (err={r['error_rate']})")
        print(f"    混淆矩阵: {r['confusion']}")

    print()
    print("  → 现在阈值是数据标定出来的，不是拍脑袋")
