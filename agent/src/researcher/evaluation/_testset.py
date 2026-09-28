"""测试集路径的唯一出处。

此前 6 个脚本（ablation / e2e_diagnostic / generator_test / retriever_test /
run_eval / run_regression）各自硬编码 `golden_testset_v4.json`。换一版测试集要改
6 处，只要漏掉一处，那个脚本就会**悄悄继续跑旧测试集**并给出不可比的数字 ——
而它报出来的指标看上去一切正常。所以路径只留这一个是权威的。

版本历史（换版必须同时把 run_regression.METRIC_VERSION +1，否则新旧口径的数字
会被直接比较，PASS/FAIL 没有意义）：
  v1  7 题     最早的手写小集
  v2  40 题    引入 type 分类
  v3  77 题    扩充
  v4  130 题   simple 48 / multi_doc 16 / precision 38 / colloquial 18 / no_answer 10
  v5  159 题   v4 + long_doc 14 + no_answer 15（新增题见下）

v5 为什么加这两类（依据 2026-09-28 的诊断）：
  · 语料里 10 个长文档（≥8 chunks，最长 25,123 字）在 v4 里只被引用 12%，
    其中 doc29_git_workflow(35 chunks) 与 doc22_docker_build(13 chunks)
    **被引用 0 次**。而语料 65% 的文档只切出 1 个 chunk，
    「检索到正确文档」与「检索到相关内容」几乎是同一件事 →
    文档级指标饱和（模式间相对极差仅 1.7%），任何检索改进都测不出来。
  · 所以新增 `long_doc` 题型：答案位于某个长文档的**单个** chunk 中，
    expected_chunks 选的是**只在该 chunk 出现**的字面串（已逐条校验：14/14
    关键词都只命中 1 个 chunk）。这样 chunk_mrr 才真正度量"定位能力"。
  · `no_answer` 从 10 题加到 25 题：拒答是当前最大的短板（实测只拒答 1/10），
    而 10 题估出的比例置信区间过宽。候选题经过检索抽查筛除 ——
    其中「Git 的第一个版本是哪一年」被查到语料明写"2005 年创建"，
    属于**假 no_answer**，留着会污染拒答指标。
"""

import json
import os

TESTSET = os.path.join(os.path.dirname(__file__), "golden_testset_v5.json")

# 已知题型。新增题型时**必须**加进这里 —— e2e_diagnostic 的诊断矩阵靠它决定
# 遍历哪些行；硬编码的列表会静默漏掉新题型（long_doc 就是这么差点被漏掉的：
# 加了题目却不出现在诊断表里，等于没加）。
KNOWN_TYPES = ["simple", "multi_doc", "precision", "colloquial", "long_doc", "no_answer"]


def load_testset(path: str | None = None) -> list[dict]:
    """加载测试集。默认用当前版本，可显式传路径以复跑历史版本。"""
    p = path or TESTSET
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def testset_names(path: str | None = None) -> list[str]:
    """测试集里出现过的题型（按首次出现顺序）。"""
    seen: list[str] = []
    for it in load_testset(path):
        t = it.get("type", "")
        if t and t not in seen:
            seen.append(t)
    return seen
