"""深度研究智能体 —— 从 Level 1 到 Level 4 渐进实现。

Level 1 (当前):  单次搜索 → 生成报告
Level 2 (TODO):  搜索-反思 Agent 循环
Level 3 (TODO):  多路并行搜索
Level 4 (TODO):  Supervisor-Researcher 双层
"""

import asyncio
import json
from datetime import datetime

from .config import config
from .llm import LLMClient
from .search import SearchTool

# ============================================================
# Prompt 模板
# ============================================================

PLAN_PROMPT = """你是一个研究规划助手。用户会提出一个问题，你需要：

1. 理解用户真正想了解什么
2. 列出 2-4 个搜索查询词来查找相关信息
3. 不要追问用户，直接基于已有信息规划搜索

请返回 JSON：
{{
    "understanding": "你对用户问题的理解（一句话）",
    "search_queries": ["查询词1", "查询词2", "查询词3"]
}}

注意：
- 搜索词用中英文皆可，优先用与用户问题相同的语言
- 搜索词应该多样化、覆盖问题的不同方面
- 返回有效的 JSON，不要加其他内容
"""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "understanding": {"type": "string"},
        "search_queries": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 4,
        },
    },
    "required": ["understanding", "search_queries"],
    "additionalProperties": False,
}

REPORT_PROMPT = """你是一个深度研究报告撰写助手。基于用户的问题和搜索结果，生成一份简洁但全面的报告。

用户问题：{question}

搜索结果：
{search_results}

请生成一份 Markdown 格式的报告，要求：
1. 用 # 作为报告标题
2. 用 ## 作为章节标题
3. 报告包含：概述、核心发现、详细分析（按需分章）
4. 每个引用必须标注来源 URL：[标题](URL)
5. 结尾包含 ### 参考来源 章节
6. 报告语言与用户问题的语言一致
7. 不要写"我是AI"或任何自我指涉的话
"""

# ============================================================
# 压缩研究结果 + 澄清用户意图
# ============================================================

COMPRESS_PROMPT = """你是一个研究整理助手。你拿到了一批原始搜索结果，需要清洗整理。

要求：
1. 删去明显不相关或重复信息
2. 关键事实和数据逐字保留
3. 同一主题信息合并
4. 每个来源格式保留
5. 结尾列出所有来源

当前日期：{date}"""

COMPRESS_USER_MESSAGE = """请整理关于「{question}」的搜索结果。

原始搜索结果：
{raw_results}"""

CLARIFY_PROMPT = """严格判断用户问题是否足够具体，能否直接开始研究。

用户消息：{messages}

以下情况必须追问（need_clarify=true）：
- 问题太宽泛且缺少关键限定（如"分析市场"——什么市场？什么维度？什么时间段？）
- 包含缩写或不明确的术语
- 缺少必要的时间、地点、数量等限定条件
- 一句话的问题但明显需要多角度研究（如"什么好""哪个更优"没有标准）

以下情况不需要追问（need_clarify=false）：
- 问题包含明确的主题+具体维度（如"2025年AI领域三大趋势"）
- 问题即使很宽，但有明确的研究框架（如"对比Java、Go性能"）
- 从上下文可以推断出意图

返回JSON：{{"need_clarify": bool, "question": "如果需要追问的话（1-2个关键问题）", "summary": "对需求的理解总结"}}"""

CLARIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "need_clarify": {"type": "boolean"},
        "question": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["need_clarify", "summary"],
    "additionalProperties": False,
}


class ClarifyHelper:
    """澄清助手：研究开始前判断是否需要追问。"""

    def __init__(self):
        self.llm = LLMClient()

    async def check(self, question: str) -> dict:
        return self.llm.structured_output(
            system_prompt="你是用户意图分析助手。",
            user_message=CLARIFY_PROMPT.format(messages=question),
            schema=CLARIFY_SCHEMA,
        )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "搜索互联网获取最新信息。每次可传多个查询词以提高效率。",
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "搜索查询词列表，2-4 个不同角度的查询",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "每个查询返回的最大结果数，默认 5",
                    },
                },
                "required": ["queries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": "停下来反思当前进展：信息够了吗？还缺什么？下一步怎么搜索？",
            "parameters": {
                "type": "object",
                "properties": {
                    "reflection": {
                        "type": "string",
                        "description": "你的反思内容",
                    },
                },
                "required": ["reflection"],
            },
        },
    },
]

# ============================================================
# Level 1: 单次搜索 → 报告
# ============================================================

SEARCH_SYSTEM = """你是一个研究助手。你的任务是根据用户问题搜索信息，然后生成报告。

工作流程：
1. 分析用户问题，规划 2-4 个搜索查询词
2. 调用 search 工具执行搜索
3. 基于搜索结果写报告

注意：
- 第一次就规划好搜索词，一次性调用 search
- 搜索完成后直接写报告，不要继续搜索
- 优先使用与用户问题相同的语言搜索"""

# ============================================================
# Level 1 Fast: 搜索 → 报告（全程只调 1 次 LLM）
# ============================================================

FAST_REPORT_PROMPT = """你是一个深度研究报告撰写助手。基于用户的问题和搜索结果，生成一份简洁但全面的报告。

用户问题：{question}

搜索结果：
{search_results}

请生成一份 Markdown 格式的报告，要求：
1. 用 # 作为报告标题
2. 用 ## 作为章节标题
3. 包含概述和核心发现
4. 每个引用必须标注来源：[标题](URL)
5. 结尾包含 ### 参考来源 章节
6. 报告语言与用户问题的语言一致
7. 不要写"我是AI"或任何自我指涉的话
8. 控制篇幅，简洁有力，不要堆砌信息"""


class FastLevel1Agent:
    """极速 Level 1: 跳过 LLM 规划 + 跳过 LLM 摘要 → 全程只 1 次 LLM 调用。"""

    def __init__(self):
        self.llm = LLMClient()
        self.search = SearchTool()

    async def run(self, question: str) -> str:
        print(f"\n{'='*60}")
        print(f"  问题: {question}")
        print(f"  模式: Level 1 Fast（极速）")
        print(f"{'='*60}")

        # Step 1: 直接搜索，不调 LLM 规划（问题本身就是搜索词）
        print("\n[1/2] 快速搜索中...")
        search_results = await self.search.search_fast(
            queries=[question],
            max_results=3,  # 只取前 3 个结果
        )
        print(f"  搜索完成，0 次 LLM 调用")

        # Step 2: 生成报告（全程唯一一次 LLM 调用）
        print(f"\n[2/2] 生成报告（唯一一次 LLM 调用）...")
        report = self.llm.chat(
            system_prompt="你是专业的深度研究报告撰写助手。简洁、准确、有引用。",
            user_message=FAST_REPORT_PROMPT.format(
                question=question,
                search_results=search_results,
            ),
        )
        return report

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y年%m月%d日")


class Level1Agent:
    """Level 1: 分析问题 → 规划搜索词 → 搜索 → 生成报告（多次 LLM 调用）"""

    def __init__(self):
        self.llm = LLMClient()
        self.search = SearchTool()

    async def run(self, question: str) -> str:
        print(f"\n{'='*60}")
        print(f"  问题: {question}")
        print(f"{'='*60}")

        # Step 1: 规划搜索词
        print("\n[1/3] 分析问题，规划搜索...")
        plan = self.llm.structured_output(
            system_prompt=PLAN_PROMPT,
            user_message=f"用户问题：{question}\n\n今天日期：{self._today()}",
            schema=PLAN_SCHEMA,
        )
        queries = plan.get("search_queries", [question])
        print(f"  理解: {plan.get('understanding', '')}")
        print(f"  搜索词: {queries}")

        # Step 2: 执行搜索
        print(f"\n[2/3] 搜索中...")
        search_results = await self.search.search(queries)
        print(f"  搜索完成")

        # Step 3: 生成报告
        print(f"\n[3/3] 生成报告...")
        report = self.llm.chat(
            system_prompt="你是专业的深度研究报告撰写助手。",
            user_message=REPORT_PROMPT.format(
                question=question,
                search_results=search_results,
            ),
        )
        return report

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y年%m月%d日")


# ============================================================
# Level 2: 搜索-反思 Agent 循环
# ============================================================

AGENT_SYSTEM = """你是一个研究助手。你可以使用以下工具：

1. **search**: 搜索互联网，每次可传多个查询词
2. **think**: 暂停反思当前进展

工作方式：
- 收到问题后，先 plan 搜索策略（心里想，不用调 think）
- 每次搜索后，必须调 think 反思
- 信息够了就停止，不要过度搜索
- 最多搜索 {max_rounds} 轮

搜索策略：
- 第一轮：用 2-4 个不同角度的查询词覆盖问题全貌
- 之后每轮：针对信息缺口精准搜索
- 如果两轮搜索结果相似，说明信息足够了

停止条件（满足任一即停）：
- 你有足够信息写出全面回答
- 已找到 3+ 个相关来源
- 已达搜索轮次上限
"""


class Level2Agent:
    """Level 2: 搜索-反思循环 Agent"""

    def __init__(self):
        self.llm = LLMClient()
        self.search = SearchTool()
        self.max_rounds = config.max_search_rounds

    async def run(self, question: str) -> str:
        print(f"\n{'='*60}")
        print(f"  问题: {question}")
        print(f"  模式: Level 2 (搜索-反思循环, 最多 {self.max_rounds} 轮)")
        print(f"{'='*60}")

        # 构建消息历史
        messages: list[dict] = [
            {"role": "user", "content": f"请研究以下问题，并在信息充分时给出报告：\n\n{question}\n\n当前日期：{self._today()}"}
        ]

        all_search_results: list[str] = []
        system = AGENT_SYSTEM.format(max_rounds=self.max_rounds)

        for round_num in range(1, self.max_rounds + 1):
            print(f"\n--- 第 {round_num}/{self.max_rounds} 轮 ---")

            # 调用 LLM（带工具）
            msg = self.llm.chat_with_tools(
                system_prompt=system,
                messages=messages,
                tools=TOOLS,
            )

            # 如果没有工具调用 → LLM 认为该停了
            if not msg.tool_calls:
                print("  LLM 认为信息足够，停止搜索")
                messages.append({"role": "assistant", "content": msg.content or "信息已足够，现在可以写报告。"})
                break

            # 执行工具
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })

            for tc in msg.tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments)

                if name == "search":
                    queries = args.get("queries", [question])
                    print(f"  搜索: {queries}")
                    result = await self.search.search(queries)
                    all_search_results.append(result)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

                elif name == "think":
                    print(f"  反思: {args.get('reflection', '')[:100]}...")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"反思已记录：{args.get('reflection', '')}",
                    })

        # 压缩研究结果（去重、去噪、保留关键信息）
        print(f"\n[压缩] 整理搜索结果...")
        compressed = self.llm.chat(
            system_prompt=COMPRESS_PROMPT.format(date=self._today()),
            user_message=COMPRESS_USER_MESSAGE.format(
                question=question,
                raw_results="\n\n---\n\n".join(all_search_results),
            ),
        )

        # 生成最终报告
        print(f"\n[最终] 生成报告...")
        report = self.llm.chat(
            system_prompt="你是专业的深度研究报告撰写助手。",
            user_message=REPORT_PROMPT.format(
                question=question,
                search_results=compressed,
            ),
        )
        return report

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y年%m月%d日")


# ============================================================
# Level 3: 多路并行搜索
# ============================================================

DECOMPOSE_PROMPT = """你是一个研究规划专家。用户会提出一个问题，你需要把它拆成多个独立的子课题，
每个子课题由一个专门的研究员独立研究。

用户问题：{question}

规则：
1. 拆成 2-4 个子课题
2. 每个子课题都应该是独立的、可以单独研究的
3. 子课题之间不要重叠
4. 如果用户问题比较简单（比如问一个事实），可以不拆，返回 1 个子课题即可

返回 JSON：
{{
    "understanding": "对问题的理解",
    "sub_topics": ["子课题1的详细描述", "子课题2的详细描述"]
}}"""

DECOMPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "understanding": {"type": "string"},
        "sub_topics": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 4,
        },
    },
    "required": ["understanding", "sub_topics"],
    "additionalProperties": False,
}

MERGE_PROMPT = """你是一个研究报告汇总专家。多个研究员已经针对用户问题的不同方面进行了独立研究，
现在你需要把所有研究成果汇总成一份完整的报告。

用户总问题：{question}

以下是各子课题的研究报告：
{reports}

请生成一份汇总报告，要求：
1. 用 # 作为报告标题，覆盖整个问题
2. 用 ## 作为章节标题，按子课题组织
3. 每个章节整合相关研究员的发现
4. 所有引用标注来源 [标题](URL)
5. 结尾包含 ### 参考来源 章节（去重）
6. 报告语言与用户问题一致
7. 不要写"我是AI"或自我指涉"""


class Level3Agent:
    """Level 3: LLM 拆题 → 多路并行 Level 2 → 汇总"""

    def __init__(self):
        self.llm = LLMClient()

    async def run(self, question: str) -> str:
        print(f"\n{'='*60}")
        print(f"  问题: {question}")
        print(f"  模式: Level 3（多路并行）")
        print(f"{'='*60}")

        # Step 1: LLM 拆题
        print("\n[1/3] 分析问题，拆分子课题...")
        plan = self.llm.structured_output(
            system_prompt="你是研究规划专家。把用户问题拆成 2-4 个独立子课题。",
            user_message=DECOMPOSE_PROMPT.format(question=question),
            schema=DECOMPOSE_SCHEMA,
        )
        sub_topics = plan.get("sub_topics", [question])
        print(f"  理解: {plan.get('understanding', '')}")
        print(f"  拆成 {len(sub_topics)} 个子课题:")
        for i, t in enumerate(sub_topics):
            print(f"    [{i+1}] {t[:80]}...")

        # Step 2: 并行跑 Level 2
        print(f"\n[2/3] {len(sub_topics)} 个研究员并行工作...")
        agents = [Level2Agent() for _ in sub_topics]
        tasks = [agent.run(topic) for agent, topic in zip(agents, sub_topics)]
        reports = await asyncio.gather(*tasks)
        print(f"   全部完成，共 {len(reports)} 份子报告")

        # Step 3: 汇总
        print(f"\n[3/3] 汇总所有子报告...")
        merged = "\n\n---\n\n".join(
            f"## 子课题{i+1}: {t}\n\n{r}"
            for i, (t, r) in enumerate(zip(sub_topics, reports))
        )
        final_report = self.llm.chat(
            system_prompt="你是专业的深度研究报告汇总专家。",
            user_message=MERGE_PROMPT.format(
                question=question,
                reports=merged,
            ),
        )
        return final_report


# ============================================================
# Level 4: Supervisor-Researcher 双层循环
# ============================================================

SUPERVISOR_SYSTEM = """你是一个研究主管（Supervisor）。你的工作是管理一个研究团队，每个研究员可以独立搜索网络并返回报告。

你有三个工具：
1. **ConductResearch**: 派遣研究员去研究一个具体课题，他会搜索网络并返回一份完整报告
2. **ResearchComplete**: 所有研究已经完成，可以进入汇总阶段
3. **think_tool**: 反思当前进展：还缺什么？哪些方面信息不足？

工作方式：
- 先使用 think_tool 分析问题，制定研究计划
- 一次可以派遣多个研究员并行工作（互相独立、互不重叠的课题）
- 等所有研究员返回后，用 think_tool 评估结果
- 如果还缺信息，再派遣一批研究员补全
- 信息充分后，调用 ResearchComplete

停止条件：
- 信息足够回答用户问题 → ResearchComplete
- 已达 {max_rounds} 轮上限 → 必须 ResearchComplete

重要提醒：
- 初轮最多派遣 {max_parallel} 个研究员
- 补充轮次通常只需 1 个研究员"""

SUPERVISOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ConductResearch",
            "description": "派遣研究员研究一个具体课题。研究员会搜索网络并返回完整的研究报告。",
            "parameters": {
                "type": "object",
                "properties": {
                    "research_topic": {
                        "type": "string",
                        "description": "要研究的课题，越详细越好，研究员需要足够的信息来独立工作",
                    },
                },
                "required": ["research_topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ResearchComplete",
            "description": "所有研究已完成，调用此工具进入报告汇总阶段。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "think_tool",
            "description": "反思研究进展：当前已掌握什么信息？还缺什么？下一步该查什么？",
            "parameters": {
                "type": "object",
                "properties": {
                    "reflection": {"type": "string", "description": "你的反思内容"},
                },
                "required": ["reflection"],
            },
        },
    },
]

FINAL_REPORT_PROMPT = """你是一个深度研究报告总编。你的研究员已经完成了所有子课题的研究，
现在你需要把所有成果汇总成一份完整、连贯、专业的深度研究报告。

用户总问题：{question}

以下是研究员提交的所有报告：
{findings}

请生成一份汇总报告，要求：
1. 用 # 作为报告标题
2. 用 ## 作为章节标题，按主题逻辑组织，而非按子课题拼凑
3. 每个章节融合不同来源的信息，形成连贯叙述
4. 所有引用标注 [标题](URL)
5. 结尾包含 ### 参考来源 章节（去重）
6. 报告语言与用户问题一致
7. 不写"我是AI"或自我指涉"""


class Level4Agent:
    """Level 4: Supervisor 循环 → 分批派遣 Level 2 → ResearchComplete → 汇总"""

    def __init__(self):
        self.llm = LLMClient()
        self.max_rounds = config.max_supervisor_rounds
        self.max_parallel = config.max_parallel_researchers

    async def run(self, question: str) -> str:
        print(f"\n{'='*60}")
        print(f"  问题: {question}")
        print(f"  模式: Level 4（Supervisor-Researcher 双层，最多 {self.max_rounds} 轮）")
        print(f"{'='*60}")

        all_findings: list[str] = []
        messages: list[dict] = [
            {"role": "user", "content": f"请组织研究回答以下问题：\n\n{question}"}
        ]
        system = SUPERVISOR_SYSTEM.format(
            max_rounds=self.max_rounds,
            max_parallel=self.max_parallel,
        )

        for round_num in range(1, self.max_rounds + 1):
            print(f"\n{'='*40}")
            print(f"  Supervisor 第 {round_num}/{self.max_rounds} 轮决策")
            print(f"{'='*40}")

            # Supervisor 决策
            msg = self.llm.chat_with_tools(
                system_prompt=system,
                messages=messages,
                tools=SUPERVISOR_TOOLS,
            )

            if not msg.tool_calls:
                print("  Supervisor: 信息足够，ResearchComplete")
                break

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ],
            })

            conduct_calls = []
            for tc in msg.tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments)

                if name == "ResearchComplete":
                    print(f"  Supervisor: 调用 ResearchComplete → 结束")
                    break

                elif name == "ConductResearch":
                    topic = args["research_topic"]
                    conduct_calls.append(topic)

                elif name == "think_tool":
                    r = args.get("reflection", "")
                    print(f"  Supervisor 反思: {r[:120]}...")
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "content": f"反思已记录",
                    })

            # 如果 Supervisor 调了 ResearchComplete
            if any(tc.function.name == "ResearchComplete" for tc in msg.tool_calls):
                break

            if not conduct_calls:
                continue

            # 并行派遣研究员（每个封装 try/except，一个挂了不影响其他）
            async def safe_run(topic):
                try:
                    agent = Level2Agent()
                    return await agent.run(topic)
                except Exception as e:
                    print(f"    研究员失败: {topic[:40]}... error={e}")
                    return f"# 研究失败\n\n子课题「{topic}」执行出错: {e}"

            print(f"  派遣 {len(conduct_calls)} 个研究员...")
            tasks = [safe_run(t) for t in conduct_calls]
            results = await asyncio.gather(*tasks)

            for tc, topic, report in zip(
                [tc for tc in msg.tool_calls if tc.function.name == "ConductResearch"],
                conduct_calls, results
            ):
                all_findings.append(report)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": f"【课题】{topic}\n\n【报告】\n{report[:3000]}...",
                })
                print(f"    研究员完成: {topic[:50]}... ({len(report)} 字符)")

        # 汇总所有发现
        print(f"\n  汇总 {len(all_findings)} 份研究报告...")
        if not all_findings:
            return "# 研究失败\n\n未能获取有效信息，请简化问题重试。"

        final = self.llm.chat(
            system_prompt="你是专业的深度研究报告总编。",
            user_message=FINAL_REPORT_PROMPT.format(
                question=question,
                findings="\n\n---\n\n".join(all_findings),
            ),
        )
        return final


# ============================================================
# 入口
# ============================================================

async def main():
    """命令行入口。"""
    import sys

    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
    else:
        question = input("请输入你想研究的问题: ")

    # 默认用 Level 2（搜索-反思循环）
    level = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2

    if level == 1:
        agent = FastLevel1Agent()
    elif level == 3:
        agent = Level3Agent()
    elif level == 4:
        agent = Level4Agent()
    else:
        agent = Level2Agent()

    report = await agent.run(question)
    print(f"\n{'='*60}")
    print(report)
    print(f"\n{'='*60}")

    # 保存到文件
    from pathlib import Path
    reports_dir = Path(__file__).parent.parent.parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    filename = reports_dir / f"{datetime.now():%Y%m%d_%H%M%S}_报告.md"
    filename.write_text(report, encoding="utf-8")
    print(f"\n报告已保存至: {filename}")


if __name__ == "__main__":
    asyncio.run(main())
