"""置换检验（permutation test）—— 判断两个模式命中率差异是否显著。

背景问题：
  消融实验输出各模式命中率（如 v2=70%, full=82%），但 12 个点的差距可能是真提升，
  也可能只是这 112 道题的一次抽样运气（换一批题差距可能消失甚至反转）。
  命中率本身无法回答"这个差距有多大可能是随机波动"。

核心思想：
  如果两个模式其实一样强，那"每个得分属于哪个模式"的标签就是随机贴的。
  把两组得分打乱、重新随机分两组、重复 N 次，就模拟出了"打平世界"里差距的分布。
  真实差距如果远超这个分布 → 是真差异，不是抽样波动。

  p-value = 打平世界里"随机差距 >= 真实差距"的比例。
  p < 0.05 → 真实差距不像是抽样波动能解释的 → 差异显著。

零成本：纯内存算术，不调 LLM、不跑检索、不消耗 token。
只对"有 expected_chunks 的题"做——no_answer 题型测的是拒绝能力，是另一个维度，
混进来会稀释显著性。
"""

import random


def permutation_test(scores_a: list[int], scores_b: list[int], n_perm: int = 10000) -> dict:
    """比较两组 0/1 命中得分，判断差异是否显著。

    参数：
      scores_a: 模式 A 每题命中(1)/未命中(0) 的列表
      scores_b: 模式 B 每题命中(1)/未命中(0) 的列表
      n_perm:   洗牌次数。默认 10000，纯本地计算约 1 秒，零 API 成本。

    返回：
      {
        "n":          参与比较的题数（两模式应一致）
        "hits_a":     A 命中数
        "hits_b":     B 命中数
        "diff":       真实命中数差（绝对值）
        "p_value":    p 值
        "significant": bool，p < 0.05 为显著
      }
    """
    n = len(scores_a)
    assert len(scores_b) == n, "两组得分数量必须一致"
    assert n > 0, "得分列表不能为空"

    observed = abs(sum(scores_a) - sum(scores_b))
    all_scores = scores_a + scores_b

    count = 0
    for _ in range(n_perm):
        random.shuffle(all_scores)          # 打乱归属标签
        diff = abs(sum(all_scores[:n]) - sum(all_scores[n:]))
        if diff >= observed:                # >=：恰好相等也算，略保守
            count += 1

    p_value = count / n_perm
    return {
        "n": n,
        "hits_a": sum(scores_a),
        "hits_b": sum(scores_b),
        "diff": observed,
        "p_value": p_value,
        "significant": p_value < 0.05,
    }


def compare_all_modes(per_mode_hits: dict[str, list[int]], n_perm: int = 10000) -> list[dict]:
    """对多个模式两两做置换检验。

    参数：
      per_mode_hits: {"mode": [0/1 列表], ...}，只传有答案的题。

    返回：
      每对模式一个结果 dict，按 p_value 升序（差异越显著越靠前）。
    """
    modes = list(per_mode_hits.keys())
    results = []
    for i in range(len(modes)):
        for j in range(i + 1, len(modes)):
            a, b = modes[i], modes[j]
            r = permutation_test(per_mode_hits[a], per_mode_hits[b], n_perm=n_perm)
            results.append({
                "mode_a": a, "mode_b": b,
                **r,
            })
    results.sort(key=lambda x: x["p_value"])
    return results


if __name__ == "__main__":
    # 自测：三组已知差异的数据，验证逻辑正确
    random.seed(42)

    # ① 明显差异：A 全命中，B 全未命中 → p 应该极小（显著）
    a1 = [1] * 100
    b1 = [0] * 100
    r1 = permutation_test(a1, b1, n_perm=500)
    print(f"[全对 vs 全错] A={r1['hits_a']} B={r1['hits_b']} diff={r1['diff']} p={r1['p_value']:.4f} 显著={r1['significant']}")

    # ② 完全一样：两组完全相同 → p 应该约 1（不显著）
    r2 = permutation_test(a1, a1, n_perm=500)
    print(f"[完全一致] A={r2['hits_a']} B={r2['hits_b']} diff={r2['diff']} p={r2['p_value']:.4f} 显著={r2['significant']}")

    # ③ 微小差异：A 比 B 多 4 个命中 → p 应该大（不显著，可能抽样波动）
    a3 = [1] * 52 + [0] * 48
    b3 = [1] * 48 + [0] * 52
    r3 = permutation_test(a3, b3, n_perm=500)
    print(f"[差4题] A={r3['hits_a']} B={r3['hits_b']} diff={r3['diff']} p={r3['p_value']:.4f} 显著={r3['significant']}")

    # ④ 中等差异：A 比 B 多 20 个命中 → p 应该小（显著）
    a4 = [1] * 70 + [0] * 30
    b4 = [1] * 50 + [0] * 50
    r4 = permutation_test(a4, b4, n_perm=500)
    print(f"[差20题] A={r4['hits_a']} B={r4['hits_b']} diff={r4['diff']} p={r4['p_value']:.4f} 显著={r4['significant']}")
