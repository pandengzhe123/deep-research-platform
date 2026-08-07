"""工具准确率评测 —— 判断 Agent 调工具是否用对。

五维评测框架里的第 3 维。判定标准：
  标准 1（模式边界）：search_mode 限制了可用工具
    rag_only → 只能用 search_kb + think，用了 search = 错
    web_only → 只能用 search + think，用了 search_kb = 错
    hybrid   → search + search_kb 都允许
  标准 2（工具失衡）：search=0 但 think 很多 → 光反思不行动
  标准 3（重复调用）：同一轮重复调用相同工具且参数几乎一致

输入：trajectory_stats.json（含 tool_calls 明细 + search_mode）
输出：工具准确率 + 每次调用的对错明细
"""

import json

# search_mode → 允许的工具集合
MODE_ALLOWED_TOOLS = {
    "rag_only": {"search_kb", "think"},
    "web_only": {"search", "think"},
    "hybrid": {"search", "search_kb", "think"},
}

# 无 search_mode 时的默认（web_only）
DEFAULT_ALLOWED = {"search", "think"}


def tool_accuracy(trajectory_stats: dict) -> dict:
    """判断轨迹中的工具调用是否正确，返回准确率 + 明细。

    返回:
      {
        "total_calls": 总工具调用数,
        "correct": 正确数,
        "accuracy": 准确率,
        "violations": [{tool, reason, round}],   # 违规调用明细
        "details": [{tool, round, correct, reason}],
      }
    """
    tool_calls = trajectory_stats.get("tool_calls", [])
    search_mode = trajectory_stats.get("search_mode", "") or "web_only"
    allowed = MODE_ALLOWED_TOOLS.get(search_mode, DEFAULT_ALLOWED)

    if not tool_calls:
        return {"total_calls": 0, "correct": 0, "accuracy": None,
                "violations": [], "details": []}

    correct = 0
    details = []
    violations = []
    seen_calls = []  # 已处理调用，用于标准 3 的重复检测

    for tc in tool_calls:
        tool = tc.get("tool", "")
        round_n = tc.get("round", 0)
        args = tc.get("args", "")
        reason = []

        # 标准 1：模式边界 —— 用了不允许的工具
        if tool not in allowed:
            reason.append(f"模式{search_mode}不允许使用 {tool}")

        # 标准 3：重复调用 —— 同一轮内，同一工具且参数几乎一致（字符级 Jaccard）
        args_str = json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args
        args_set = set(args_str)
        for prev in seen_calls:
            if prev["tool"] != tool or prev["round"] != round_n:
                continue  # 不同工具或不同轮，不判重复
            prev_args = json.dumps(prev["args"], ensure_ascii=False) if not isinstance(prev["args"], str) else prev["args"]
            prev_set = set(prev_args)
            union = args_set | prev_set
            sim = len(args_set & prev_set) / len(union) if union else 0.0
            if sim >= 0.8:  # 参数高度相似 → 重复调用
                reason.append("重复调用: 同一轮内相同工具且参数高度相似")
                break
        seen_calls.append({"tool": tool, "round": round_n, "args": args})

        if reason:
            violations.append({"tool": tool, "round": round_n, "reason": "；".join(reason)})
            details.append({"tool": tool, "round": round_n, "correct": False, "reason": "；".join(reason)})
        else:
            correct += 1
            details.append({"tool": tool, "round": round_n, "correct": True, "reason": ""})

    # 标准 2：工具失衡 —— search=0 但 think 多（光反思不行动）
    dist = trajectory_stats.get("tool_distribution", {})
    search_n = dist.get("search", 0)
    search_kb_n = dist.get("search_kb", 0)
    think_n = dist.get("think", 0)
    imbalance_reason = ""
    if search_n == 0 and search_kb_n == 0 and think_n >= 3:
        imbalance_reason = f"工具失衡: 无检索但 think={think_n} 次（光反思不行动）"
        violations.append({"tool": "think", "round": 0, "reason": imbalance_reason})

    total = len(tool_calls)
    return {
        "total_calls": total,
        "correct": correct,
        "accuracy": round(correct / total, 3) if total else None,
        "violations": violations,
        "imbalance": imbalance_reason,
        "details": details,
        "search_mode": search_mode,
    }


if __name__ == "__main__":
    import json
    import sys

    # 用真实轨迹数据测试
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = "reports/20260807_122625/trajectory_stats.json"

    stats = json.load(open(path, encoding="utf-8"))
    result = tool_accuracy(stats)

    print("=" * 50)
    print("  工具准确率 (Tool Accuracy)")
    print("=" * 50)
    print(f"  模式: {result['search_mode']}")
    print(f"  总调用: {result['total_calls']}  正确: {result['correct']}")
    print(f"  准确率: {result['accuracy']}")
    print()
    for d in result["details"]:
        mark = "✅" if d["correct"] else "❌"
        print(f"  {mark} round={d['round']} {d['tool']} {d['reason']}")
    if result["imbalance"]:
        print(f"\n  ⚠️ {result['imbalance']}")
