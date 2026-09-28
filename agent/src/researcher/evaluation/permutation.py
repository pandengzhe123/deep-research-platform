"""置换检验（permutation test）—— 判断两个模式得分差异是否显著。

背景问题：
  消融实验输出各模式命中率（如 v2=97%, hybrid=98%），但 1 个点的差距可能是真提升，
  也可能只是这 130 道题的一次抽样运气（换一批题差距可能消失甚至反转）。
  命中率本身无法回答"这个差距有多大可能是随机波动"。

⚠️ 本文件的核心是 **paired（配对）**，默认 True：
  同一批题分别跑两个模式 → `scores_a[i]` 与 `scores_b[i]` 是**同一道题**的两次得分，
  这是配对设计。原来的实现是非配对的（把两组倒在一起重新随机分两组），
  丢掉了配对信息、系统性假阴性 —— 实测有 3/6 个配对的结论被弄反。
  详见 `permutation_test` 的 docstring 与实测表。

核心思想（两种模式）：
  · 配对（默认）：d_i = a_i − b_i。H0 下每个 d_i 的**符号随机** → 随机翻转符号，
    重算 Σd 的分布，看真实 |Σd| 落在哪里。不假设正态。
  · 非配对：若两个模式其实一样强，那"每个得分属于哪个模式"的标签就是随机贴的 →
    把两组得分打乱重新分两组。**只适用于两组是独立样本时。**

  p-value = 打平世界里"随机差距 >= 真实差距"的比例，并做 (count+1)/(n_perm+1) 修正
  （避免报出不可能的精确 0）。p < 0.05 → 差异不像是抽样波动能解释的。

适用输入：不限于 0/1 —— 只要得分可加总即可。命中率（0/1）与 chunk_mrr
（连续值）都能直接跑；后者分辨力更高（相对极差约 8.4% vs 1.7%），
所以消融实验对两者各跑一次。

零成本：纯内存算术，不调 LLM、不跑检索、不消耗 token。
只对"有 expected_chunks 的题"做——no_answer 题型测的是拒绝能力，是另一个维度，
混进来会稀释显著性。
"""

import random
from operator import mul

# 固定种子：不固定的话同一份数据跑两次 p 值会微抖（未固定前实测能差到 0.002 量级），
# 而基准对比、复现、写进报告的数字都要求可复现。
_DEFAULT_SEED = 12345


def permutation_test(
    scores_a: list[float], scores_b: list[float],
    n_perm: int = 10000, paired: bool = True, seed: int | None = _DEFAULT_SEED,
) -> dict:
    """比较两组得分，判断差异是否显著。得分可以是 0/1，也可以是连续值。

    ⚠️ **paired 默认 True（配对检验），这是本文件最重要的一个决定。**

    为什么：本项目的用法是「**同一批题**分别跑两个模式」，即
    `scores_a[i]` 和 `scores_b[i]` 是**同一道题**在两个模式下的得分 —— 这是标准的
    **配对设计**。而原来的实现是**非配对**的（把两组分数倒在一起重新随机分两组），
    等于假设「同一题在两个模式下的表现互相独立」，丢掉了配对信息，
    **系统性地偏向假阴性**（更难判显著）。

    实测影响（**本项目自己的测试集** kb_eval_v2 / 159 题里 134 道可答题 /
    439 chunks，四模式一次跑完，指标 chunk_mrr，n_perm=100000）：

        对比              非配对 p（原）  配对 p（现在）  逐题好/差   结论变化
        v2 vs rerank         0.177        0.02211      7/19      ❌→✅ **反转**
        v2 vs full           0.116        0.01795      8/21      ❌→✅ **反转**
        v2 vs hybrid         0.168        0.02126     27/11      ❌→✅ **反转**
        hybrid vs rerank     0.005        0.00003      7/33      一致（都显著）
        hybrid vs full       0.004        0.00003      7/33      一致（都显著）
        rerank vs full       0.815        0.57063       5/3      一致（都不显著）

    **6 个配对里有 3 个的结论被非配对检验弄反了** —— 「各模式都没差别、是分辨力不足」
    这个结论，有一半是检验方法造成的，不是数据造成的。
    （非配对 p 取自改造前那次运行的控制台输出；当前 `ablation.py` 只把 p 打印出来，
      没有落盘，所以那组数无法从 JSON 复算。）

    ⚠️ 但配对检验**不是把门槛降低**：`rerank vs full`（效应量 0.057）两种口径下都不显著
    （0.815 → 0.571），真正没差异的它照样测不出来。配对只是去掉了「题目难度差异」
    这个噪声，从而能看见**稳定存在**的差异（v2 vs hybrid 是 27 题好 / 11 题差）。

    paired=False 只在**两组是独立样本**时才对（例如「A 组用户」vs「B 组用户」）。
    本仓库目前的调用点（`ablation.py` 的 hit_list / mrr_list）**全部是配对数据**。

    参数：
      scores_a: 模式 A 每题的得分（命中 1/0，或 chunk_mrr 这类连续值）
      scores_b: 模式 B 每题的得分。paired=True 时必须与 scores_a **同序同题**
      n_perm:   置换次数。默认 10000，纯本地计算约 0.1~0.3 秒，零 API 成本。
      paired:   True=配对符号翻转检验（默认）；False=非配对洗牌检验
      seed:     随机种子。默认固定，保证同数据可复现；传 None 则每次不同

    返回：
      {
        "n":          参与比较的题数（两模式应一致）
        "hits_a":     A 的得分总和（0/1 输入时即命中数）
        "hits_b":     B 的得分总和
        "diff":       真实得分差（绝对值）
        "p_value":    p 值（已做 (count+1)/(n_perm+1) 修正，不会出现不可能的精确 0）
        "significant": bool，p < 0.05 为显著
        "paired":     本次用的是哪种检验
        "better":     paired=True 时，A 比 B 好的题数（否则 None）
        "worse":      paired=True 时，A 比 B 差的题数（否则 None）
      }

    配对检验的原理：记 d_i = a_i − b_i（同一题的差）。H0 下每个 d_i 的**符号是随机的**
    （两个模式等价 ⇒ 谁高谁低没有系统性倾向），于是把符号随机翻转重算 Σd 的分布，
    看真实 |Σd| 落在这分布的什么位置。这正是置换检验的配对版本，且**不假设正态**。
    """
    n = len(scores_a)
    assert len(scores_b) == n, "两组得分数量必须一致"
    assert n > 0, "得分列表不能为空"

    rng = random.Random(seed)
    observed = abs(sum(scores_a) - sum(scores_b))

    if not paired:
        # 非配对：把两组倒在一起，重新随机分成两组（只适用于独立样本）
        all_scores = list(scores_a) + list(scores_b)
        count = 0
        for _ in range(n_perm):
            rng.shuffle(all_scores)
            if abs(sum(all_scores[:n]) - sum(all_scores[n:])) >= observed:
                count += 1
        better = worse = None
    else:
        # 配对：对逐题差值做符号翻转
        d = [a - b for a, b in zip(scores_a, scores_b)]
        better = sum(1 for x in d if x > 0)
        worse = sum(1 for x in d if x < 0)
        if observed == 0:
            # 所有题得分完全相同 —— 无信息，p=1（也避免下面的循环白跑）
            return {
                "n": n, "hits_a": sum(scores_a), "hits_b": sum(scores_b),
                "diff": 0.0, "p_value": 1.0, "significant": False,
                "paired": True, "better": better, "worse": worse,
            }
        count = 0
        for _ in range(n_perm):
            # random.choices 与 map/sum 都是 C 层实现 —— 比逐元素 Python 循环快一个量级
            signs = rng.choices((-1, 1), k=n)
            if abs(sum(map(mul, d, signs))) >= observed:
                count += 1

    # +1 修正：n_perm 次置换可能一次都没达到观测差距，直接 count/n_perm 会报出
    # 「精确 0」这种不可能的 p 值（只有无穷次置换才能断言 p=0）。这也是置换检验的
    # 标准做法（Phipson & Smyth 2010）。
    p_value = (count + 1) / (n_perm + 1)
    return {
        "n": n,
        "hits_a": sum(scores_a),
        "hits_b": sum(scores_b),
        "diff": observed,
        "p_value": p_value,
        "significant": p_value < 0.05,
        "paired": paired,
        "better": better,
        "worse": worse,
    }


def compare_all_modes(per_mode_hits: dict[str, list[float]], n_perm: int = 10000,
                      paired: bool = True, seed: int | None = _DEFAULT_SEED) -> list[dict]:
    """对多个模式两两做置换检验。

    参数：
      per_mode_hits: {"mode": [每题得分], ...}，只传有答案的题。
                     得分可以是 0/1，也可以是 chunk_mrr 这类连续值。
                     ⚠️ 各模式的列表**必须同序同题**（本仓库的调用点都是这样），
                        否则 paired=True 的配对检验没有意义。
      n_perm: 每对置换次数
      paired: 默认 True —— 同批题跑多模式是配对设计，见 permutation_test 的说明
      seed:   随机种子，固定以保证可复现

    返回：
      每对模式一个结果 dict，按 p_value 升序（差异越显著越靠前）。
    """
    modes = list(per_mode_hits.keys())
    results = []
    for i in range(len(modes)):
        for j in range(i + 1, len(modes)):
            a, b = modes[i], modes[j]
            r = permutation_test(per_mode_hits[a], per_mode_hits[b],
                                 n_perm=n_perm, paired=paired, seed=seed)
            results.append({
                "mode_a": a, "mode_b": b,
                **r,
            })
    results.sort(key=lambda x: x["p_value"])
    return results


if __name__ == "__main__":
    # 自测：几组已知答案的数据，验证逻辑正确（不调 API，纯本地）
    print("=" * 78)
    print("  基本正确性")
    print("=" * 78)

    # ① 明显差异：A 全命中，B 全未命中 → p 应该极小（显著）
    a1 = [1.0] * 100
    b1 = [0.0] * 100
    for label, a, b, want in (
        ("全对 vs 全错（应显著）", a1, b1, True),
        ("完全一致（应 p≈1 不显著）", a1, a1, False),
        ("差 4 题 / 100（应不显著）", [1.0] * 52 + [0.0] * 48, [1.0] * 48 + [0.0] * 52, False),
        ("差 20 题 / 100（应显著）", [1.0] * 70 + [0.0] * 30, [1.0] * 50 + [0.0] * 50, True),
    ):
        r = permutation_test(a, b, n_perm=2000)
        ok = "✅" if r["significant"] == want else "❌"
        print(f"  {ok} {label:<28} A={r['hits_a']:>5.0f} B={r['hits_b']:>5.0f} "
              f"diff={r['diff']:>5.0f} p={r['p_value']:.4f} "
              f"显著={r['significant']} (配对={r['paired']})")

    print()
    print("=" * 78)
    print("  配对 vs 非配对 —— 为什么默认必须是配对")
    print("=" * 78)
    print("  构造：一批题有难易之分（base），A 在**每一题**上都稳定高 0.1（delta）。")
    print("  配对检验把题目难度差掉，只看那个稳定的 +0.1；")
    print("  非配对检验看到的是「题目难度」这个大方差，于是测不出来。")
    print()
    n_items = 40
    base = [1.0] * 10 + [0.5] * 10 + [0.3] * 10 + [0.0] * 10
    delta = [0.1] * n_items
    A = [x + y for x, y in zip(base, delta)]
    B = list(base)
    rp = permutation_test(A, B, n_perm=5000, paired=True)
    ru = permutation_test(A, B, n_perm=5000, paired=False)
    print(f"  同一批数据（A 每题都比 B 高 0.1，n={n_items}）：")
    print(f"    配对  (paired=True)  p = {rp['p_value']:.4f}  "
          f"显著={rp['significant']}  逐题 {rp['better']}好/{rp['worse']}差")
    print(f"    非配对(paired=False) p = {ru['p_value']:.4f}  "
          f"显著={ru['significant']}")
    print()
    print("  → 真实差异是稳定存在的，非配对检验看不见它。这就是默认 paired=True 的原因。")

    print()
    print("=" * 78)
    print("  其它特性")
    print("=" * 78)
    # 可复现：同数据跑两次 p 完全一样
    rr1 = permutation_test(a1, b1, n_perm=1000)
    rr2 = permutation_test(a1, b1, n_perm=1000)
    print(f"  固定种子可复现：两次 p = {rr1['p_value']:.6f} / {rr2['p_value']:.6f} "
          f"{'✅ 一致' if rr1['p_value'] == rr2['p_value'] else '❌ 不一致'}")
    # 全同数据：p 必须是 1.0（不能因为 observed==0 就报 0）
    rz = permutation_test([0.5] * 30, [0.5] * 30, n_perm=500)
    print(f"  全同数据：p = {rz['p_value']:.4f}  {'✅' if rz['p_value'] == 1.0 else '❌ 应为 1.0'}")
    # p 不会出现不可能的精确 0
    rmin = permutation_test([1.0] * 200, [0.0] * 200, n_perm=1000)
    print(f"  极端差异：p = {rmin['p_value']:.6f}  "
          f"{'✅ 有 +1 修正，不报 0' if rmin['p_value'] > 0 else '❌ 报了精确 0'}")
    import time
    t0 = time.perf_counter()
    compare_all_modes({"v2": A, "hybrid": B, "rerank": [x + 0.2 for x in base],
                       "full": [x + 0.3 for x in base]}, n_perm=10000)
    print(f"  4 模式 6 个配对 × 10000 次置换耗时 {time.perf_counter()-t0:.2f}s（纯本地）")
