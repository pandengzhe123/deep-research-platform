# RAG 评估体系规划

> 基于三层评估方法论：Retriever 单独测 → Generator 单独测 → 端到端诊断

---

## 一、当前评估的问题

| 问题 | 影响 |
|------|------|
| 纯关键词匹配（`expected_chunks` 字面比对） | 语义等价但字面不同的答案全判错 |
| 只有端到端测试，没有分离 Retriever/Generator | 不知道是检索差还是生成差 |
| 测试集 3 文档 7 题全是简单事实查，100% 命中无区分度 | 看不出各优化的真实差距 |
| 没有覆盖失败模式（KB 无答案、排名靠后、LLM 未提取） | 不知道系统在什么场景下会失败 |
| 没有 LLM-as-Judge 的偏差意识 | 如果引入自动评估，不知道偏差在哪 |

---

## 二、目标

1. **让评估分数有区分度**——不是四个模式都 100%
2. **能定位问题**——检索错了还是生成错了还是交互问题
3. **能解释给面试官**——评估方法论比具体分数更重要

---

## 三、测试集扩展

### 当前（7 条，单一类型）

```
全部：知识库有答案，简单事实查，单文档覆盖
```

### 目标（20 条，覆盖 5 种题型）

| 题型 | 数量 | 示例 | 测什么 |
|------|:---:|------|------|
| 简单事实查 | 5 | "Python 谁创建的" | 基准线，所有模式都应该对 |
| 多文档综合 | 4 | "Python 和 Docker 有什么共同特点" | 需要跨文档检索和合成 |
| 精确术语 | 3 | "RFC 7231 定义的 POST 方法" | 测 BM25 对精确术语的优势 |
| KB 无答案 | 4 | "Git 是什么"（知识库没这篇） | 测"未找到"的拦截能力 |
| 口语化查询 | 4 | "怎么快速搭建一个容器应用" | 测查询改写的价值 |

每条包含：question、expected_doc_ids（相关文档）、expected_chunks（关键词）、ground_truth（标准答案）。

---

## 四、三层评估架构

### 第 1 层：Retriever 单独测试

**跳过 Generator**，只看检索器返回的文档是否包含正确答案。

| 指标 | 公式 | 测什么 |
|------|------|------|
| **Precision@K** | 返回的 Top K 中相关文档数 / K | 检索精度——返回的东西相关吗 |
| **Recall@K** | 返回的相关文档数 / 总相关文档数 | 检索召回——该返回的都返回了吗 |
| **MRR**（平均倒数排名） | Σ 1/first_rank / N | 第一个相关文档排在第几位 |
| **NDCG@K** | 折损累计增益 | 相关文档是否排在前面 |

**这层完全不涉及 LLM**——用传统信息检索指标，没有 LLM 裁判的偏差问题。

**实现**：`evaluation/retriever_test.py`——对每个模式，给问题，取 Top K 文档，和标注的 expected_doc_ids 比对。

### 第 2 层：Generator 单独测试

**跳过 Retriever**，直接把测试集标注的完美文档喂给 Generator。

| 指标 | 测什么 | 怎么测 |
|------|------|--------|
| **Faithfulness** | 答案的每个声明是否有文档支撑 | RAGAS：拆声明→逐条和文档对比→算比例 |
| **Answer Relevance** | 答案是否切题 | RAGAS：LLM 反向生成问题→和原问题比余弦相似度 |
| **Context Relevancy** | 检索到的文档中有用部分占比 | RAGAS：LLM 从文档挑有用句子→占比 |

**意义**：排除检索错误的干扰。如果 Generator 拿到完美上下文仍然出错——问题在生成端。

**LLM-as-Judge 偏差缓解**：
- 用 DeepSeek（你的推理模型）而非 OpenAI/Claude 做裁判，避免同一家族自我偏好
- 同步记录裁判模型的版本，方便发现偏差漂移
- 长答案评 Faithfulness 时提取的核心句子去掉修饰语再说

### 第 3 层：端到端测试

把 Retriever 接回去跑真实管线，四组模式对比。

| 场景 | 诊断 |
|------|------|
| E2E 差 + Retriever 好 + Generator 好 | 两者交互问题（上下文截断、格式不对） |
| E2E 差 + Retriever 差 + Generator 好 | 检索问题（调 embedding、chunk 策略） |
| E2E 差 + Retriever 好 + Generator 差 | 生成问题（调 prompt、换 LLM） |
| E2E 差 + Retriever 差 + Generator 差 | 两都不行，先修检索再修生成 |

---

## 五、指标汇总

| 层 | 指标 | 类型 | 需要 LLM？ | 裁判偏差？ |
|---|------|------|:---:|:---:|
| Retriever | Precision@K, Recall@K, MRR, NDCG | 信息检索 | ❌ | ❌ |
| Generator | Faithfulness | LLM 裁判 | ✅ | ⚠️ 有，需缓解 |
| Generator | Answer Relevance | LLM 裁判 | ✅ | ⚠️ 有，需缓解 |
| Generator | Context Relevancy | LLM 裁判 | ✅ | ⚠️ 有，需缓解 |
| E2E | 所有以上指标 | 混合 | 部分 | 部分 |

---

## 六、实现步骤

### Step 1：测试集扩展（先做）
- 创建 20 条金标准测试集（5 种题型）
- 补充 4 篇测试文档
- `evaluation/golden_testset_v2.json`

### Step 2：Retriever 层测试
- `evaluation/retriever_test.py`
- Precision@5, Recall@5, MRR
- 四种模式对比

### Step 3：Generator 层测试
- `evaluation/generator_test.py`
- 跳过检索，直接给完美文档
- Faithfulness + Answer Relevance + Context Relevancy

### Step 4：E2E 诊断矩阵
- `evaluation/e2e_test.py`
- 综合所有指标 + 诊断矩阵输出

### Step 5：面试叙事
- 三层评估方法论 → 各层指标 → LLM 偏差意识到缓解 → 工具选型诚实表达

---

## 七、面试叙述（先备好）

> "我评估 RAG 用的不是传统的 BLEU 这类词面重叠指标——它不懂语义也检测不了幻觉。我用三层分层测试：第一层单独测 Retriever 用 Precision/Recall/MRR 这些传统 IR 指标完全不依赖 LLM；第二层单独测 Generator 把完美文档喂给 LLM 用 RAGAS 的 Faithfulness 和 Answer Relevance 排除检索错误的干扰；第三层端到端把四组模式（纯向量、混合、精排、全链路）跑同一个测试集对比，用诊断矩阵定位问题是检索端、生成端还是交互层。
>
> LLM-as-Judge 的偏差我是知道的——Nvidia 的研究发现大部分模型和人类相关性超 0.8 但高相关不等于高一致。我有缓解措施：用 DeepSeek 当裁判避开和 OpenAI/Claude 同家族的自我偏好，位置偏差用 AB 互评取平均，冗长偏差在评分标准里强调简洁。不过我会诚实地告诉面试官——整个 RAG 评估领域还不成熟，RAGAS 有一万多星但社区吐槽也不少。我目前的方案是自动化初筛 + 手工金标准测试集 + 关键 case 人工抽检三层叠加。"

---

## 八、已知局限（诚实表达）

- 测试集 20 条还是偏小，真正覆盖所有失败模式需要 50+ 条
- LLM 裁判偏差只能缓解不能消除
- 没有 CI/CD 集成（个人项目不需要）
- 没有收集真实用户反馈（没有线上用户）
