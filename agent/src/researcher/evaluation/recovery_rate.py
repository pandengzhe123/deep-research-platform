"""恢复率评测 —— 判断 Agent 出错后能否恢复继续。

五维评测框架里的第 4 维。恢复率测的是容错体系的运作效果：
  - LLM 调用失败后，重试是否成功（llm_call.retries + success）
  - 搜索失败后，降级是否成功（Tavily→DDG，search_call.success）
  - 出现 error 事件后，研究最终是否完成（run_end.error 是否为空）

输入：trace.jsonl（原始事件流）
输出：恢复率 + 明细（重试/降级/研究级恢复）

恢复率计算：
  recovered_events / total_failure_events
  其中 failure_events = 实际失败但尝试恢复的事件（重试、降级、error 后继续）
  recovered = 失败后最终成功的比例
"""


def recovery_rate(trace_path: str) -> dict:
    """读 trace.jsonl，计算恢复率。

    返回:
      {
        "llm_retries": 重试总次数,
        "llm_recovered": 重试后成功数,
        "llm_recovery_rate": LLM 重试恢复率,
        "search_fallbacks": 搜索降级总次数,
        "search_recovered": 降级后成功数,
        "search_recovery_rate": 搜索降级恢复率,
        "error_events": error 事件数,
        "research_completed": 研究是否完成（无 end_error）,
        "overall_recovery": 综合恢复率,
        "recovery_cases": [明细],
      }
    """
    import json

    with open(trace_path, encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]

    llm_retries = 0
    llm_recovered = 0
    search_fallbacks = 0
    search_recovered = 0
    error_events = 0
    end_error = None
    recovery_cases = []

    for e in events:
        t = e.get("type")
        if t == "llm_call":
            # LLM 重试：retries > 0 说明发生过重试；success 说明最终成功
            retries = e.get("retries", 0)
            if retries > 0:
                llm_retries += 1
                if e.get("success", True):
                    llm_recovered += 1
                    recovery_cases.append({"type": "llm_retry", "recovered": True,
                                           "retries": retries, "method": e.get("method")})
                else:
                    recovery_cases.append({"type": "llm_retry", "recovered": False,
                                           "retries": retries, "method": e.get("method")})
            elif not e.get("success", True):
                # 无重试但失败 —— 恢复失败
                recovery_cases.append({"type": "llm_fail", "recovered": False,
                                       "method": e.get("method")})

        elif t == "search_call":
            # 搜索降级：fallback_used=True 说明 Tavily 失败后降级 DDG 且最终成功 → 恢复成功
            if e.get("fallback_used"):
                search_fallbacks += 1
                if e.get("success", True):
                    search_recovered += 1
                recovery_cases.append({"type": "search_fallback",
                                       "recovered": e.get("success", True),
                                       "queries": e.get("queries", [])[:1]})
            elif not e.get("success", True):
                # 无降级但搜索失败 → 恢复失败
                search_fallbacks += 1
                recovery_cases.append({"type": "search_fail", "recovered": False,
                                       "queries": e.get("queries", [])[:1]})

        elif t == "error":
            error_events += 1
            recovery_cases.append({"type": "error_event", "recovered": True,
                                   "source": e.get("source"), "message": e.get("message", "")[:50]})

        elif t == "run_end":
            end_error = e.get("error")
            # run_end 无 error = 研究完成（即使中间有 error 事件也恢复了）

    # 研究级恢复：出错但最终完成
    research_recovered = (error_events > 0) and (end_error is None)
    research_completed = end_error is None

    # 综合恢复率：恢复成功的事件 / 所有"需要恢复"的事件
    # error 事件只有在研究最终完成（end_error 为空）时才算恢复，
    # 否则 error 是"最终失败的 error"，不应计入恢复数。
    error_recovered = error_events if research_completed else 0
    total_failures = llm_retries + search_fallbacks + error_events
    total_recovered = llm_recovered + search_recovered + error_recovered

    overall = round(total_recovered / total_failures, 3) if total_failures else 1.0

    return {
        "llm_retries": llm_retries,
        "llm_recovered": llm_recovered,
        "llm_recovery_rate": round(llm_recovered / llm_retries, 3) if llm_retries else 1.0,
        "search_fallbacks": search_fallbacks,
        "search_recovered": search_recovered,
        "search_recovery_rate": round(search_recovered / search_fallbacks, 3) if search_fallbacks else 1.0,
        "error_events": error_events,
        "research_completed": research_completed,
        "research_recovered": research_recovered,
        "overall_recovery": overall,
        "total_failures": total_failures,
        "recovery_cases": recovery_cases,
    }


def print_recovery(report: dict):
    """打印恢复率报告。"""
    print("=" * 50)
    print("  恢复率 (Recovery Rate)")
    print("=" * 50)
    print(f"  LLM 重试: {report['llm_retries']} 次, 恢复 {report['llm_recovered']} ({report['llm_recovery_rate']:.0%})")
    print(f"  搜索降级: {report['search_fallbacks']} 次, 恢复 {report['search_recovered']} ({report['search_recovery_rate']:.0%})")
    print(f"  error 事件: {report['error_events']} 个")
    print(f"  研究完成: {'✅' if report['research_completed'] else '❌'} (无结束异常)")
    print(f"  综合恢复率: {report['overall_recovery']:.0%} (总需恢复 {report['total_failures']})")
    print()
    for case in report["recovery_cases"]:
        mark = "✅" if case["recovered"] else "❌"
        detail = case.get("source") or case.get("method") or str(case.get("queries", ""))
        print(f"  {mark} {case['type']}: {detail}")
    print("=" * 50)


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "reports/20260807_122625/trace.jsonl"
    result = recovery_rate(path)
    print_recovery(result)
