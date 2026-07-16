"""深度研究智能体 —— Level 1 到 Level 4 全部实现。

Level 1: 单次搜索 → 生成报告（极速，全程 1 次 LLM 调用）
Level 2: 搜索-反思 Agent 循环（Function Calling 驱动）
Level 3: 多路并行搜索（LLM 拆题 + asyncio.gather）
Level 4: Supervisor-Researcher 双层调度（分批派遣 + 研究员压缩 + Research Brief）
"""

import asyncio
import json
import re
from datetime import datetime

from .config import config
from .llm import LLMClient
from .search import SearchTool


def _today_str() -> str:
    return datetime.now().strftime("%Y年%m月%d日")

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

REPORT_PROMPT = """你是一个深度研究报告撰写助手。基于用户的问题和搜索结果，生成一份全面深入的报告。每个方面都应充分展开——人们使用深度研究报告，期待完整、详细的答案。

用户问题：{question}

搜索结果：
{search_results}

请生成一份 Markdown 格式的报告，要求：
1. 用 # 作为报告标题，用 ## 作为章节标题
2. 报告包含：概述、核心发现、详细分析。充分展开关键发现，次要细节集中概述，避免穷举和重复
3. 默认使用段落叙述，列举数据或步骤时适当使用项目符号，但不要全文写成要点清单
4. 每个引用必须标注来源 URL：[标题](URL)，每个关键事实都应注明出处
5. 结尾包含 ### 参考来源 章节（列出所有引用过的来源）
6. 报告语言与用户问题一致
6. 不写"我是""我认为"等自我指涉——这是一份专业报告
7. <处理矛盾信息>：完整呈现所有不同说法，标注来源和发布时间，不要擅自判断真伪。单一来源标注"未经交叉验证"，非权威来源标注"非官方来源"
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

<对话历史>
{messages}
</对话历史>

**先检查对话历史**：如果历史中已经问过用户澄清问题且用户已回答，绝对不要再问——即使他的回答不够完美，也直接开始研究。

以下情况必须追问（need_clarify=true）：
- 问题太宽泛且缺少关键限定（如"分析市场"——什么市场？什么维度？什么时间段？）
- 包含缩写或不明确的术语
- 缺少必要的时间、地点、数量等限定条件

以下情况不需要追问（need_clarify=false）：
- 问题包含明确的主题+具体维度（如"2025年AI领域三大趋势"）
- 问题即使较宽但有明确的研究框架（如"对比Java和Go的性能"）
- 从对话历史中可以推断出意图
- 用户当前消息是对上一轮追问的直接回答（如你问"要研究哪些方面"，用户说"你提到的所有方面"或"全部"）——必须返回 false，不得反复追问

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
        return await self.llm.structured_output(
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
    {
        "type": "function",
        "function": {
            "name": "search_kb",
            "description": "搜索本地知识库（用户上传的私有文档）。当用户明确提到'我的文档''本地资料''上传的文件'时优先使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "检索查询词",
                    },
                },
                "required": ["query"],
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

FAST_REPORT_PROMPT = """你是一个深度研究报告撰写助手。基于用户的问题和搜索结果，生成一份内容充实的报告。尽可能完整地呈现搜索结果中的信息，不要因为追求简短而省略有价值的内容。

用户问题：{question}

搜索结果：
{search_results}

请生成一份 Markdown 格式的报告，要求：
1. 用 # 作为报告标题
2. 用 ## 作为章节标题
3. 包含概述和核心发现
4. 每个引用必须标注来源：[标题](URL)，每个关键事实都应注明出处
5. 结尾包含 ### 参考来源 章节（列出所有引用过的来源）
6. 报告语言与用户问题的语言一致
7. 不要写"我是""我认为"等自我指涉的话
8. 搜索结果中如有矛盾信息，完整呈现所有说法并标注来源和发布时间，不要擅自判断真伪"""


class FastLevel1Agent:
    """极速 Level 1: 跳过 LLM 规划 + 跳过 LLM 摘要 → 全程只 1 次 LLM 调用。"""

    def __init__(self, on_progress=None, kb_enabled=False, user_id: str = "default", rag_doc_ids: list[str] = None, search_mode: str = "hybrid", trace=None):
        self.emit = on_progress or (lambda e: None)
        self.trace = trace
        self.llm = LLMClient()
        self.llm.trace = trace
        self.search = SearchTool(on_progress=self.emit)
        self.search.trace = trace
        # trace: 同步 emit 事件自动镜像到 trace
        if trace:
            _emit_orig = self.emit
            def _wrap(e):
                _emit_orig(e)
                trace.record_event(
                    step=e.get("step", ""), message=e.get("message", ""),
                    round_num=e.get("round", 0),
                    extra={k: v for k, v in e.items() if k not in ("step", "message", "round")},
                )
            self.emit = _wrap
        self.kb_enabled = kb_enabled
        self.user_id = user_id
        self.rag_doc_ids = rag_doc_ids or []
        self.search_mode = search_mode
        if kb_enabled or search_mode in ("hybrid", "rag_only"):
            from .kb import kb
            self.kb = kb

    async def run(self, question: str) -> str:
        print(f"\n{'='*60}")
        print(f"  问题: {question}")
        print(f"  模式: Level 1 Fast（极速）")
        print(f"{'='*60}")

        self.emit({"step": "searching", "message": "极速搜索中（全程仅 1 次 LLM 调用）..."})

        # 网络搜索 + 知识库检索（并行）
        if self.search_mode == "rag_only":
            search_results = await self._kb_search(question)
        else:
            tasks = [self.search.search_fast(queries=[question], max_results=3)]
            if self.kb_enabled or self.search_mode == "hybrid":
                tasks.append(self._kb_search(question))
            results = await asyncio.gather(*tasks)
            search_results = results[0]
            if len(results) > 1 and results[1]:
                search_results = results[1] + "\n\n---\n\n" + search_results

        self.emit({"step": "reporting", "message": "正在撰写报告（唯一一次 LLM 调用）..."})

        report = await self.llm.chat(
            system_prompt="你是专业的深度研究报告撰写助手。简洁、准确、有引用。",
            user_message=FAST_REPORT_PROMPT.format(
                question=question,
                search_results=search_results,
            ),
        )
        return report

    async def _kb_search(self, query: str) -> str:
        """异步知识库检索，在线程池中执行避免阻塞事件循环。"""
        return await asyncio.to_thread(lambda: self.kb.search(query, user_id=self.user_id, doc_ids=self.rag_doc_ids or None, mode="full"))



class Level1Agent:
    """Level 1: 分析问题 → 规划搜索词 → 搜索 → 生成报告（多次 LLM 调用）"""

    def __init__(self):
        self.llm = LLMClient()
        self.search = SearchTool(on_progress=self.emit)

    async def run(self, question: str) -> str:
        print(f"\n{'='*60}")
        print(f"  问题: {question}")
        print(f"{'='*60}")

        # Step 1: 规划搜索词
        print("\n[1/3] 分析问题，规划搜索...")
        plan = await self.llm.structured_output(
            system_prompt=PLAN_PROMPT,
            user_message=f"用户问题：{question}\n\n今天日期：{_today_str()}",
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
        report = await self.llm.chat(
            system_prompt="你是专业的深度研究报告撰写助手。",
            user_message=REPORT_PROMPT.format(
                question=question,
                search_results=search_results,
            ),
        )
        return report



# ============================================================
# Level 2: 搜索-反思 Agent 循环
# ============================================================

AGENT_SYSTEM = """你是一个研究助手，负责研究用户提供的主题并生成有引用来源的报告。

<Task>
使用工具收集与研究主题相关的可靠信息。你可以在每轮中并行调用多个工具，也可以只调用一个。研究在工具调用循环中进行——你需要自行判断何时信息足够、何时停止搜索。
</Task>

<Available Tools>
1. **search**: 搜索互联网获取公开信息。每次可传 2-4 个不同角度的查询词以最大化覆盖面。
2. **search_kb**: 搜索用户上传的私有文档/知识库。当系统提示中显示了知识库内容预览，或问题涉及用户已有文档时优先使用。
3. **think**: 暂停并反思当前进展——找到了什么？还缺什么？下一步该搜什么？

**关键规则：每次搜索（search 或 search_kb）之后，必须调用 think 反思。不要将 think 与其他工具并行调用。**
</Available Tools>

<Instructions>
像有时间限制的人类研究员一样思考：
1. **仔细阅读问题** —— 用户具体需要什么信息？哪些方面最重要？
2. **从宽泛搜起** —— 第一轮用 2-4 个不同角度的查询词覆盖问题的全貌，不要一步就钻入细节
3. **每次搜索后暂停评估** —— 用 think 分析：找到了什么关键信息？还缺什么？当前信息足够回答了吗？
4. **随信息积累逐步精准** —— 后续轮次针对信息缺口定向补充，搜索词越来越聚焦
5. **能自信回答时就停** —— 不要追求完美穷举
</Instructions>

<Hard Limits>
**工具调用预算**（防止过度搜索）：
- 简单事实类问题：最多 2-3 次搜索
- 复杂分析类问题：最多 {max_rounds} 次搜索

**立即停止条件**（满足任一）：
- 能全面、自信地回答用户问题
- 已有 3+ 个可靠的独立来源
- **最近 2 轮搜索返回的信息高度相似** —— 说明这个方向已经挖不出新东西了，换方向或停止
</Hard Limits>

<Show Your Thinking>
每次调用 think 时，分析以下问题：
- 这轮搜索找到了什么关键信息？
- 哪些方面还缺信息？
- 当前的信息是否足以写出一份完整的深度报告？
- 下一步应该：继续搜索（搜什么？）还是停止？
</Show Your Thinking>
"""


def _assistant_msg(msg):
    """组装 assistant 消息（含 tool_calls）。"""
    return {
        "role": "assistant",
        "content": msg.content or "",
        "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ],
    }


async def _truncate_context(messages: list[dict], total_chars: int, max_chars: int,
                             llm, emit, round_num: int, context_warned: bool,
                             compressed_summaries: list[str] | None = None) -> tuple[list[dict], bool]:
    """上下文超限保护：80% 预警 + LLM 压缩 + 硬截断。返回 (新 messages, 新 context_warned)。"""
    if not context_warned and total_chars > max_chars * 0.8:
        context_warned = True
        emit({"step": "thinking", "message": f"上下文已用 {total_chars * 100 // max_chars}%，继续追问可能丢失早期内容，建议开新会话"})

    if total_chars > max_chars:
        print(f"  [WARN] 历史过长 ({total_chars} 字符)，压缩旧内容")
        emit({"step": "thinking", "message": "上下文已满，正在压缩早期对话以保留关键信息...", "round": round_num})
        try:
            old_msgs = messages[1:-5]
            if old_msgs:
                raw = "\n".join(str(m) for m in old_msgs)
                summary = await llm.chat(
                    system_prompt="""你是一个对话压缩助手。将对话历史压缩为结构化摘要。

严格按以下格式输出：

## 研究方向
用户的核心问题是什么（1 句）

## 已确认的关键事实
- 有明确来源的事实和数据
- 多个来源交叉验证的结论

## 用户约束与偏好
- 用户明确提出的限制条件（预算、时间、范围等）
- 用户表达过的偏好和侧重
- 宁可多记不要漏——这是后续回答不能违反的

## 已做的决策
- 为什么选了 A 方向、为什么放弃了 B 方向
- 每次重大决策的上下文

## 待覆盖的方面
- 还有哪些角度没有研究到
- 哪些问题用户追问了但还没回答

规则：
- 丢弃：搜索结果全文、LLM 每轮思考过程、重复信息
- 保留：事实 > 决策原因 > 约束条件 > 研究方向
- 用中文，每条简明扼要""",
                    user_message=f"请压缩以下对话，保留关键信息：\n\n{raw}",
                )
                if summary:
                    messages = [messages[0], {"role": "system", "content": f"[早期对话摘要] {summary}"}] + messages[-5:]
                    if compressed_summaries is not None:
                        compressed_summaries.append(f"[第{round_num}轮压缩] {summary}")
        except Exception:
            pass
        if sum(len(str(m)) for m in messages) > max_chars:
            messages = [messages[0]] + messages[-5:]
        emit({"step": "thinking", "message": f"早期对话已压缩（上下文已用 {sum(len(str(m)) for m in messages) * 100 // max_chars}%）。建议开新会话以保证研究质量", "round": round_num})

    return messages, context_warned


RAG_ONLY_SYSTEM = """你是一个研究助手。当前模式：**仅知识库检索** —— 你只能使用 search_kb 工具搜索本地私有文档。

<Task>
使用 search_kb 工具检索用户上传的私有文档。你的回答必须严格基于知识库返回的文档内容。如果知识库中没有相关信息，直接告知用户——永远不要编造或依赖你的自身知识补充。
</Task>

<Available Tools>
1. **search_kb**: 搜索用户上传的私有文档/知识库。输入自然语言查询词。
2. **think**: 暂停反思——检索到了什么？是否覆盖了用户问题的所有方面？

**每次 search_kb 后调用 think 反思，不要并行。**
</Available Tools>

<Instructions>
1. 仔细阅读问题，确定需要从知识库中查找哪些方面的信息
2. 第一轮用宽泛的查询词全面检索
3. 每次检索后用 think 评估：找到了什么？还缺什么？是否需要换查询词？
4. 后续轮次针对信息缺口用不同措辞重新检索
5. 引用知识库内容时注明来源文档名称
</Instructions>

<Hard Limits>
- 最多 {max_rounds} 轮检索
- **最近 2 轮检索结果相似 → 立即停止**
- 知识库没有的内容，直接说"知识库中未找到相关信息"，绝对不编造
</Hard Limits>

<Show Your Thinking>
每次 think 时分析：检索到了什么？覆盖了用户的全部问题吗？需要换查询词吗？还是可以结束并写回答了？
</Show Your Thinking>"""

WEB_ONLY_SYSTEM = """你是一个研究助手，负责通过互联网搜索研究用户提供的主题。

<Task>
使用 search 工具收集与主题相关的可靠公开信息。你可以在每轮中并行调用多个搜索，也可以只调用一个。自行判断何时信息足够、何时停止。
</Task>

<Available Tools>
1. **search**: 搜索互联网获取公开信息。每次可传 2-4 个不同角度的查询词。
2. **think**: 暂停反思进展——找到了什么？还缺什么？下一步该搜什么？

**关键规则：每次 search 之后，必须调用 think 反思。不要将 think 与其他工具并行。**
</Available Tools>

<Instructions>
1. 仔细阅读问题，明确需要哪些方面的信息
2. 第一轮用宽泛的查询词覆盖全貌，不要一步就钻进细节
3. 每次搜索后用 think 评估：找到了什么？缺什么？信息够了吗？
4. 后续轮次针对信息缺口精准补充，搜索词逐步聚焦
5. 能自信回答时就停，不追求穷举
</Instructions>

<Hard Limits>
- 简单事实类：最多 2-3 次搜索
- 复杂分析类：最多 {max_rounds} 次搜索
- **最近 2 轮搜索结果高度相似 → 立即停止**，换方向或结束
- 已有 3+ 个可靠来源 → 立即停止
</Hard Limits>

<Show Your Thinking>
每次 think 时分析：找到了什么关键信息？还缺什么？信息够写深度报告了吗？下一步搜什么还是停止？
</Show Your Thinking>
"""


class Level2Agent:
    """Level 2: 搜索-反思循环 Agent"""

    def __init__(self, on_progress=None, kb_enabled: bool = False, user_id: str = "default", llm=None, rag_doc_ids: list[str] = None, search_mode: str = "hybrid", trace=None):
        self.llm = llm or LLMClient()
        if trace:
            self.llm.trace = trace
        self.max_rounds = config.max_search_rounds
        self.user_id = user_id
        self.emit = on_progress or (lambda e: None)
        self.trace = trace
        self.search_tool = SearchTool(on_progress=self.emit)
        self.search_tool.trace = trace
        # trace: 同步 emit 事件自动镜像到 trace
        if trace:
            _emit_orig = self.emit
            def _wrap(e):
                _emit_orig(e)
                trace.record_event(
                    step=e.get("step", ""), message=e.get("message", ""),
                    round_num=e.get("round", 0),
                    extra={k: v for k, v in e.items() if k not in ("step", "message", "round")},
                )
            self.emit = _wrap
        self.rag_doc_ids = rag_doc_ids or []
        self.kb_enabled = kb_enabled
        self.search_mode = search_mode
        self._compressed_summaries: list[str] = []  # 持久化压缩摘要，拼入报告随 PostgreSQL 存储
        if search_mode in ("hybrid", "rag_only"):
            from .kb import kb
            self.kb = kb

    def _get_tools(self):
        if self.search_mode == "web_only":
            return [t for t in TOOLS if t["function"]["name"] != "search_kb"]
        elif self.search_mode == "rag_only":
            return [t for t in TOOLS if t["function"]["name"] != "search"]
        return TOOLS  # hybrid

    async def run(self, question: str) -> str:
        print(f"\n{'='*60}")
        print(f"  问题: {question}")
        print(f"  模式: Level 2 (搜索-反思循环, 最多 {self.max_rounds} 轮)")
        print(f"{'='*60}")

        messages: list[dict] = [
            {"role": "user", "content": f"请研究以下问题，并在信息充分时给出报告：\n\n{question}\n\n当前日期：{_today_str()}"}
        ]

        all_search_results: list[str] = []
        MAX_RESULTS_CHARS = config.max_results_chars   # 可配置
        MAX_ROUND_RESULTS = config.max_round_results
        # 根据搜索模式选择 System Prompt
        if self.search_mode == "rag_only":
            system = RAG_ONLY_SYSTEM.format(max_rounds=self.max_rounds)
        elif self.search_mode == "web_only":
            system = WEB_ONLY_SYSTEM.format(max_rounds=self.max_rounds)
        else:
            system = AGENT_SYSTEM.format(max_rounds=self.max_rounds)
        MAX_HISTORY_CHARS = config.max_history_chars
        context_warned = False      # 预警只发一次

        # hybrid 模式：首轮预搜知识库，让 LLM 感知 KB 内容后再自行决策
        if self.search_mode == "hybrid":
            try:
                print(f"  [hybrid] 首轮预搜知识库: {question[:50]}...")
                kb_preview = await asyncio.to_thread(
                    lambda: self.kb.search(question, user_id=self.user_id,
                                           doc_ids=self.rag_doc_ids or None, mode="full")
                )
                if "未找到相关信息" not in kb_preview:
                    # 截取前 500 字作为摘要注入，让 LLM 知道"知识库里有这些"
                    preview = kb_preview[:500]
                    messages.insert(0, {
                        "role": "system",
                        "content": (
                            f"[知识库预检索] 以下是知识库中与问题「{question}」相关的已有内容摘要。"
                            f"你可以据此判断是否需要使用 search_kb 工具深入检索：\n\n{preview}"
                        ),
                    })
                    print(f"  [hybrid] KB 预搜命中，摘要已注入 (前 {len(preview)} 字)")
                else:
                    print(f"  [hybrid] KB 预搜未命中，不注入摘要")
            except Exception as e:
                print(f"  [hybrid] KB 预搜失败: {e}，跳过")

        for round_num in range(1, self.max_rounds + 1):
            # Token 超限保护
            total_chars = sum(len(str(m)) for m in messages)
            messages, context_warned = await _truncate_context(
                messages, total_chars, MAX_HISTORY_CHARS, self.llm, self.emit, round_num, context_warned,
                compressed_summaries=self._compressed_summaries,
            )

            print(f"\n--- 第 {round_num}/{self.max_rounds} 轮 ---")
            self.emit({"step": "thinking", "message": f"第 {round_num}/{self.max_rounds} 轮决策中...", "round": round_num})

            try:
                msg = await self.llm.chat_with_tools(
                    system_prompt=system,
                    messages=messages,
                    tools=self._get_tools(),
                )

                if not msg.tool_calls:
                    print("  LLM 认为信息足够，停止搜索")
                    self.emit({"step": "decided", "message": "信息已足够，停止搜索", "round": round_num})
                    messages.append({"role": "assistant", "content": msg.content or "信息已足够，现在可以写报告。"})
                    break

                messages.append(_assistant_msg(msg))

                round_results: list[str] = []
                for tc in msg.tool_calls:
                    name = tc.function.name
                    raw_args = tc.function.arguments
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        # LLM 可能在 JSON 字符串值里塞了未转义的换行符，尝试修复
                        fixed = re.sub(r'(?<!\\)\n', r'\\n', raw_args)
                        fixed = re.sub(r'(?<!\\)\r', r'\\r', fixed)
                        try:
                            args = json.loads(fixed)
                        except json.JSONDecodeError:
                            print(f"  [WARN] JSON 解析失败 (tc.id={tc.id}): {raw_args[:80]}...")
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": f"JSON 解析失败，请检查参数格式后重试。原始参数: {raw_args[:200]}",
                            })
                            continue

                    try:
                        if name == "search":
                            queries = args.get("queries", [question])
                            print(f"  搜索: {queries}")
                            self.emit({"step": "searching", "message": f"搜索: {', '.join(queries)}", "round": round_num, "queries": queries})
                            result = await self.search_tool.search(queries)
                            # 截断单个结果防止内存溢出
                            if len(result) > MAX_RESULTS_CHARS:
                                result = result[:MAX_RESULTS_CHARS] + "\n\n（结果过长已截断）"
                            round_results.append(result)
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": result,
                            })

                        elif name == "search_kb":
                            query = args.get("query", question)
                            print(f"  知识库检索: {query}")
                            self.emit({"step": "kb_searching", "message": f"知识库检索: {query}", "round": round_num})
                            if self.search_mode in ("hybrid", "rag_only"):
                                result = await asyncio.to_thread(lambda: self.kb.search(query, user_id=self.user_id, doc_ids=self.rag_doc_ids or None, mode="full"))
                            else:
                                result = "知识库未启用。"
                            if self.search_mode == "rag_only" and "未找到相关信息" in result:
                                result = "[系统提示] 知识库中未找到任何相关文档。你必须直接告知用户此事实，绝对不能编造内容或基于自身知识回答。" + result
                            if len(result) > MAX_RESULTS_CHARS:
                                result = result[:MAX_RESULTS_CHARS] + "\n\n（结果过长已截断）"
                            round_results.append(result)
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": result,
                            })

                        elif name == "think":
                            reflection = args.get("reflection", "")
                            print(f"  反思: {reflection[:100]}...")
                            self.emit({"step": "thinking", "message": reflection[:150] + ("..." if len(reflection) > 150 else ""), "round": round_num})
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": f"反思已记录：{args.get('reflection', '')}",
                            })

                    except Exception as e:
                        print(f"  [WARN] 工具 {name} 调用失败 (tc.id={tc.id}): {e}")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"工具 {name} 调用失败: {e}，请基于已有信息继续",
                        })

                # 本轮结束后合并结果，按轮截断（而非按单个结果截断）
                if round_results:
                    all_search_results.append("\n\n---\n\n".join(round_results))
                    if len(all_search_results) > MAX_ROUND_RESULTS:
                        all_search_results = all_search_results[-MAX_ROUND_RESULTS:]

            except Exception as e:
                print(f"  [WARN] 第 {round_num} 轮 LLM 调用异常: {e}，跳过本轮继续")
                # LLM 调用本身失败，没有 tc，用 user 角色通知 Agent
                messages.append({
                    "role": "user",
                    "content": f"（系统提示：本轮 LLM 调用失败: {e}，请基于已有信息继续研究）",
                })

        # 无结果兜底：避免用空字符串调 LLM 产生幻觉报告
        if not all_search_results:
            print("  [WARN] 无任何搜索结果，返回兜底提示")
            self.emit({"step": "reporting", "message": "未获取到有效搜索结果"})
            return f"# 未找到相关信息\n\n关于「{question}」，未能在网络和知识库中找到有效信息。\n\n可能的原因：\n\n1. 搜索 API 暂时不可用\n2. 该问题目前没有公开资料\n3. 搜索关键词与问题不匹配\n\n建议：稍后重试，或尝试更具体的关键词。"

        # 压缩研究结果
        # 压缩前再做一次内存保护
        raw_text = "\n\n---\n\n".join(all_search_results)
        if len(raw_text) > 500000:
            raw_text = raw_text[:500000] + "\n\n（原始数据过长，已截断 50 万字符）"

        print(f"\n[压缩] 整理搜索结果...")
        self.emit({"step": "reporting", "message": "正在压缩整理搜索结果..."})
        try:
            compressed = await self.llm.chat(
                system_prompt=COMPRESS_PROMPT.format(date=_today_str()),
                user_message=COMPRESS_USER_MESSAGE.format(
                    question=question,
                    raw_results=raw_text,
                ),
            )
        except Exception as e:
            print(f"  [WARN] 压缩失败，跳过压缩直接使用原始结果: {e}")
            self.emit({"step": "reporting", "message": "搜索结果压缩失败，报告质量可能下降"})
            compressed = raw_text  # 降级：用未压缩的原始搜索结果

        # 生成最终报告
        print(f"\n[最终] 生成报告...")
        self.emit({"step": "reporting", "message": "正在撰写最终报告..."})
        report = await self.llm.chat(
            system_prompt="你是专业的深度研究报告撰写助手。",
            user_message=REPORT_PROMPT.format(
                question=question,
                search_results=compressed,
            ),
        )
        if self._compressed_summaries:
            prefix = "<!-- 上下文压缩记录 -->\n" + "\n".join(self._compressed_summaries)
            report = prefix + "\n\n" + report
        return report



# ============================================================
# Level 3: 多路并行搜索
# ============================================================

DECOMPOSE_PROMPT = """你是一个研究规划专家。用户会提出一个问题，你需要把它拆成多个独立的子课题，
每个子课题由一个专门的研究员独立研究。

用户问题：{question}

规则：
1. 拆成 1-4 个子课题（简单事实类问题 1 个即可，复杂对比/分析类问题拆 2-4 个）
2. 每个子课题都应该是独立的、可以单独研究的
3. 子课题之间不要重叠

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
1. 用 # 作为报告标题，用 ## 作为章节标题，按主题逻辑组织而非子课题串讲
2. 每一节充分展开关键发现，次要细节集中概述，避免穷举和重复
3. 默认段落叙述，适当使用项目符号但不要全文写成要点清单
4. 所有引用标注来源 [标题](URL)，每个关键事实都应注明出处
5. 结尾包含 ### 参考来源 章节（去重，列出所有引用过的来源）
6. 报告语言与用户问题一致
6. 不写"我是""我认为"等自我指涉
7. 不同研究员可能得出不同结论——不要强行统一，标注各自来源。矛盾信息完整呈现两种说法，不要擅自判断真伪"""


class Level3Agent:
    """Level 3: LLM 拆题 → 多路并行 Level 2 → 汇总"""

    def __init__(self, on_progress=None, kb_enabled: bool = False, user_id: str = "anonymous", rag_doc_ids: list[str] = None, search_mode: str = "hybrid", trace=None):
        self.llm = LLMClient()
        self.llm.trace = trace
        self.emit = on_progress or (lambda e: None)
        self.trace = trace
        # trace: 同步 emit 事件自动镜像到 trace
        if trace:
            _emit_orig = self.emit
            def _wrap(e):
                _emit_orig(e)
                trace.record_event(
                    step=e.get("step", ""), message=e.get("message", ""),
                    round_num=e.get("round", 0),
                    extra={k: v for k, v in e.items() if k not in ("step", "message", "round")},
                )
            self.emit = _wrap
        self.kb_enabled = kb_enabled
        self.user_id = user_id
        self.rag_doc_ids = rag_doc_ids or []
        self.search_mode = search_mode

    async def run(self, question: str) -> str:
        print(f"\n{'='*60}")
        print(f"  问题: {question}")
        print(f"  模式: Level 3（多路并行）")
        print(f"{'='*60}")

        # Step 1: LLM 拆题
        print("\n[1/3] 分析问题，拆分子课题...")
        self.emit({"step": "planning", "message": "正在分析问题，拆分子课题..."})
        plan = await self.llm.structured_output(
            system_prompt="你是研究规划专家。把用户问题拆成 1-4 个独立子课题。",
            user_message=DECOMPOSE_PROMPT.format(question=question),
            schema=DECOMPOSE_SCHEMA,
        )
        sub_topics = plan.get("sub_topics", [question])
        self.emit({"step": "planned", "message": f"拆成 {len(sub_topics)} 个子课题，每个启动一个研究员并行工作", "sub_topics": sub_topics, "understanding": plan.get("understanding", "")})
        print(f"  理解: {plan.get('understanding', '')}")
        print(f"  拆成 {len(sub_topics)} 个子课题:")
        for i, t in enumerate(sub_topics):
            print(f"    [{i+1}] {t[:80]}...")

        # Step 2: 并行跑 Level 2
        print(f"\n[2/3] {len(sub_topics)} 个研究员并行工作...")
        for i, t in enumerate(sub_topics):
            self.emit({"step": "searching", "message": f"研究员 #{i+1}/{len(sub_topics)} 启动: {t[:50]}..."})

        async def safe_run(topic):
            try:
                agent = Level2Agent(on_progress=self.emit, kb_enabled=self.kb_enabled, user_id=self.user_id, llm=self.llm, rag_doc_ids=self.rag_doc_ids, search_mode=self.search_mode, trace=self.trace)
                return (await agent.run(topic), None)
            except Exception as e:
                print(f"    研究员失败: {topic[:40]}... error={e}")
                self.emit({"step": "searching", "message": f"子课题「{topic[:30]}...」失败，跳过"})
                return (None, str(e))

        raw = await asyncio.gather(*[safe_run(t) for t in sub_topics])
        # 过滤失败项，按原始顺序重新编号
        valid = []
        for i, (report, error) in enumerate(raw):
            if report is not None:
                valid.append((i, sub_topics[i], report))
            else:
                self.emit({"step": "searching", "message": f"子课题 {i+1}「{sub_topics[i][:30]}...」失败: {error}"})
        failed = len(sub_topics) - len(valid)
        print(f"   全部完成，共 {len(valid)} 份子报告" + (f"，{failed} 份失败" if failed else ""))

        # Step 3: 汇总
        print(f"\n[3/3] 汇总所有子报告...")
        if not valid:
            return f"# 研究失败\n\n关于「{question}」，所有 {len(sub_topics)} 个子课题均未能完成研究。\n\n建议：简化问题或稍后重试。"

        self.emit({"step": "reporting", "message": f"所有研究员完成，正在汇总 {len(valid)} 份子报告..."})
        # 将子报告标题降一级（#→##，##→###），适配 ## 子课题 的汇总结构
        def downgrade(text: str) -> str:
            return re.sub(r'^(#{1,6})\s', r'#\1 ', text, flags=re.MULTILINE)
        merged = "\n\n---\n\n".join(
            f"## 子课题{i+1}: {topic}\n\n{downgrade(report)}"
            for i, (_, topic, report) in enumerate(valid)
        )
        try:
            final_report = await self.llm.chat(
                system_prompt="你是专业的深度研究报告汇总专家。",
                user_message=MERGE_PROMPT.format(
                    question=question,
                    reports=merged,
                ),
            )
            return final_report
        except Exception as e:
            print(f"  汇总超时，回退到直接拼接: {e}")
            self.emit({"step": "reporting", "message": "LLM 汇总失败，回退到直接拼接子报告"})
            return "# " + question + "\n\n" + merged


# ============================================================
# Research Brief —— 用户问题 → 结构化研究简报
# ============================================================

RESEARCH_BRIEF_PROMPT = """你将收到用户的问题和对话历史。你需要将其转化为一份详细且具体的研究简报，用于指导后续的深度研究。

<对话历史>
{messages}
</对话历史>

用户当前问题：{question}

今天是 {date}。

请将上述信息转化为一份研究简报。遵循以下规则：

1. **最大化具体性和细节**：包含用户所有的已知偏好，明确列出需要研究的关键维度和属性
2. **填补未说明但必要的维度**：如果某些属性对研究至关重要但用户没有提供，将其列为开放性研究点
3. **避免无根据的假设**：用户没有提供的细节不要编造，明确标注"用户未指定"
4. **使用第一人称**：从用户的角度表述研究需求
5. **来源偏好**：优先使用官方网站、学术论文、一手资料；如果问题涉及中文内容，优先检索中文来源

返回的简报应该是一个完整的段落，包含以下要素：
- 研究的核心问题是什么
- 需要覆盖的关键维度和子方向
- 来源偏好和语言偏好
- 输出格式偏好（如果有的话）
- 已知的用户约束条件

只返回研究简报文本，不要加任何前缀或评论。"""


# ============================================================
# Level 4: Supervisor-Researcher 双层循环
# ============================================================

SUPERVISOR_SYSTEM = """你是一个研究主管（Supervisor），管理一支研究团队。每个研究员可以独立搜索网络和知识库，返回完整的研究报告。你需要协调他们的工作，确保最终收集到回答用户问题所需的全部信息。

<Available Tools>
1. **ConductResearch**: 派遣研究员研究一个具体课题。课题描述越详细越好——研究员无法看到其他研究员的工作，需要你提供完整的上下文。
2. **ResearchComplete**: 所有研究已经完成，信息足够回答用户问题，进入汇总阶段。
3. **think_tool**: 反思研究进展——目前掌握了什么？还有哪些信息缺口？下一步该部署哪些研究方向？

**关键规则：每次调用 ConductResearch 前后都应使用 think_tool。不要在 think_tool 的同时调用其他工具。**
</Available Tools>

<Instructions>
像有时间压力和研究预算的项目经理一样思考：
1. 仔细阅读研究简报，理解用户真正需要什么信息
2. 用 think_tool 先分析问题，规划研究方向
3. 派遣研究员执行研究，等所有研究员返回
4. 用 think_tool 评估收集到的信息——够了吗？还需要补什么？
5. 信息充分后调用 ResearchComplete
</Instructions>

<Hard Limits>
- 最多 {max_rounds} 轮派遣（含 think_tool 调用）
- 每轮最多 {max_parallel} 个研究员并行
- 不要为了完美而过度派遣——能回答用户问题时立即 ResearchComplete
</Hard Limits>

<Scaling Rules>
如何决定派遣多少研究员：

**简单事实查询、列表、排名** → 1 个研究员即可
  例："量子计算机目前有哪些主要公司" → 1 个

**用户明确要对比多个对象** → 每个对象 1 个研究员
  例："比较 IBM、Google、微软在量子计算的布局" → 3 个研究员
  确保各课题互相独立、互不重叠

**复杂的多维度分析** → 按维度分配
  例："量子计算对密码学的威胁" → 理论威胁 1 个 + 硬件进展 1 个 + 行业应对 1 个

**补充轮次**通常只需 1 个研究员，针对明确的缺口精准补充。

**默认偏向单研究员** —— 除非问题天然可分，否则不要过度并行。每个 ConductResearch 启动一个独立研究员，单独的汇总模型将撰写最终报告。不要在 ConductResearch 的课题中使用缩写或简称。
</Scaling Rules>"""

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

FINAL_REPORT_PROMPT = """你是一个深度研究报告总编。基于研究简报、原始研究发现和决策笔记，创建一份全面、深入、结构清晰的深度研究报告。

今天是 {date}。

<研究简报>
{research_brief}
</研究简报>

<用户原始问题>
{question}
</用户原始问题>

<对话历史>
{messages}
</对话历史>

以下是各研究员提交的详细研究报告：
{findings}

以下是研究主管的决策笔记（供参考结构，帮助你理解研究的重点和逻辑）：
{notes}

---

## 报告结构指南

你可以根据问题类型选择最合适的结构。以下是一些参考范例，但不必拘泥——报告的节（section）是一个流动的概念，你觉得怎么组织对读者最好就怎么组织。

**对比型问题**（如"比较 A 和 B"）：
1. 引言
2. A 的概述
3. B 的概述
4. A 与 B 的逐维对比
5. 结论

**概述型问题**（如"分析某事物的发展"）：
1. 背景与概述
2. 维度一
3. 维度二
4. 维度三
5. 结论与展望

**列表型问题**（如"列出某领域的十大事件"）：
你可以用单个节列表呈现，也可以把每一项单独作为一节展开。列表型报告不需要引言和结论。

**简单问题**：一段话回答即可。

无论选择哪种结构，确保各节之间的衔接自然流畅，形成一篇连贯的报告而非子报告的拼凑。

---

## 写作要求

1. **标题**：用 # 作为报告标题，用 ## 作为章节标题，用 ### 作为子章节标题

2. **每个节都应充分展开**：在信息量和克制之间找平衡——充分展开关键发现和核心论点，次要细节集中概述即可，避免在同一主题下穷举和重复

3. **融合多来源信息**：将不同研究员、不同来源的信息有机融合，而不是"A 研究员说了 X，B 研究员说了 Y"式的串讲

4. **写作风格**：默认使用段落叙述，表达清晰、专业。在列举数据、步骤或要点时适当使用项目符号，但不要全文都写成要点清单

5. **引用规范**：每个关键事实使用 [标题](URL) 格式直接标注来源。在报告末尾的 ### 参考来源 节中列出所有引用过的来源

6. **语言**：报告语言必须与用户问题的语言一致。如果用户用中文提问，整个报告用中文撰写；如果用户用英文提问，整个报告用英文撰写

---

## 严禁事项

- ❌ 不要写"我是""我认为""让我们来""下面我将"等自我指涉或工作过程描述——这是一份专业报告，不应出现作者的声音
- ❌ 不要写"综合来看""总体而言""普遍认为"等暗示一致性的措辞——除非确实有多个独立来源一致支持同一个结论
- ❌ 不要擅自取舍矛盾信息。如果有两个来源给出了不同的事实或数据，完整呈现两者，标注各自的来源和发布时间，让读者自行判断
- ❌ 不要强行统一不同研究员的结论。标注"研究员 A 认为...，研究员 B 则认为..."，分歧本身也是信息
- ❌ 不要将个人博客、论坛帖子等非权威来源的说法当作确定事实。如有此类来源，标注"非官方来源，未经独立验证"
- ❌ 如果某个说法只有单一来源支撑，必须标注"单一来源，未经交叉验证"
- ❌ 不要因为篇幅考虑而省略有价值的信息。详细和全面优于简洁"""

# ============================================================
# 研究员压缩 Prompt —— 子报告 → 结构化摘要，Supervisor 快速决策用
# ============================================================

RESEARCHER_COMPRESS_PROMPT = """你是一个研究结果整理助手。请将以下研究报告清洁整理，供研究主管快速判断进展。不要总结——所有关键信息必须逐字保留，只是去掉重复和明显无关的内容。

研究报告：
{report}

请用以下格式输出：

## 核心发现
- （保留所有关键发现，标注来源）

## 关键事实与数据
- （保留所有具体数据、时间、人名、引用来源 URL）

## 信息缺口
- 报告中提到但没有找到的信息
- 如果需要补充搜索的方向，请列出"""


class Level4Agent:
    """Level 4: Supervisor 循环 → 分批派遣 Level 2 → ResearchComplete → 汇总"""

    def __init__(self, on_progress=None, kb_enabled: bool = False, user_id: str = "anonymous", rag_doc_ids: list[str] = None, search_mode: str = "hybrid", trace=None):
        self.llm = LLMClient()
        self.llm.trace = trace
        self.max_rounds = config.max_supervisor_rounds
        self.max_parallel = config.max_parallel_researchers
        self.emit = on_progress or (lambda e: None)
        self.trace = trace
        # trace: 同步 emit 事件自动镜像到 trace
        if trace:
            _emit_orig = self.emit
            def _wrap(e):
                _emit_orig(e)
                trace.record_event(
                    step=e.get("step", ""), message=e.get("message", ""),
                    round_num=e.get("round", 0),
                    extra={k: v for k, v in e.items() if k not in ("step", "message", "round")},
                )
            self.emit = _wrap
        self.kb_enabled = kb_enabled
        self.user_id = user_id
        self.rag_doc_ids = rag_doc_ids or []
        self.search_mode = search_mode
        self._compressed_summaries: list[str] = []

    async def _generate_research_brief(self, question: str, context: str = "") -> str:
        """将用户问题转化为结构化研究简报，填±模糊维度，指导 Supervisor 决策。"""
        try:
            brief = await self.llm.chat(
                system_prompt="你是研究规划专家。将用户问题转化为详细的研究简报。",
                user_message=RESEARCH_BRIEF_PROMPT.format(
                    messages=context or question,
                    question=question,
                    date=_today_str(),
                ),
            )
            print(f"  Research brief ({len(brief)} 字):\n    {brief[:200]}...")
            return brief or question
        except Exception as e:
            print(f"  Research brief 生成失败: {e}，回退为原始问题")
            return question

    async def _compress_research(self, report: str) -> str:
        """清洁整理研究员报告，供 Supervisor 快速决策。保留所有关键信息，只去重去噪。"""
        try:
            compressed = await self.llm.chat(
                system_prompt="你是研究结果整理专家。保留所有关键事实、数据和来源，只删除重复和明显无关的内容。",
                user_message=RESEARCHER_COMPRESS_PROMPT.format(report=report[:15000]),
            )
            return compressed or report  # 压缩失败降级为原始报告
        except Exception as e:
            print(f"    压缩失败: {e}，降级为原始报告")
            return report

    async def run(self, question: str) -> str:
        print(f"\n{'='*60}")
        print(f"  问题: {question}")
        print(f"  模式: Level 4（Supervisor-Researcher 双层，最多 {self.max_rounds} 轮）")
        print(f"{'='*60}")

        # 生成研究简报（对标原项目 write_research_brief）
        research_brief = await self._generate_research_brief(question)
        self.emit({"step": "planning", "message": f"研究简报已生成 ({len(research_brief)} 字)"})

        all_findings: list[str] = []    # 压缩版摘要，给 Supervisor 决策用
        raw_findings: list[str] = []   # 原始完整报告，给最终报告用
        messages: list[dict] = [
            {"role": "user", "content": (
                f"请根据以下研究简报组织研究，回答用户问题：\n\n"
                f"<研究简报>\n{research_brief}\n</研究简报>\n\n"
                f"<用户问题>\n{question}\n</用户问题>"
            )}
        ]
        system = SUPERVISOR_SYSTEM.format(
            max_rounds=self.max_rounds,
            max_parallel=self.max_parallel,
        )

        MAX_HISTORY_CHARS = config.max_history_chars
        context_warned = False

        for round_num in range(1, self.max_rounds + 1):
            # 消息历史超限保护（与 Level 2 一致）
            total_chars = sum(len(str(m)) for m in messages)

            if not context_warned and total_chars > MAX_HISTORY_CHARS * 0.8:
                context_warned = True
                self.emit({"step": "thinking", "message": f"上下文已用 {total_chars * 100 // MAX_HISTORY_CHARS}%，继续追问可能丢失早期内容，建议开新会话"})

            if total_chars > MAX_HISTORY_CHARS:
                messages, _ = await _truncate_context(
                    messages, total_chars, MAX_HISTORY_CHARS, self.llm, self.emit, round_num, context_warned,
                    compressed_summaries=self._compressed_summaries,
                )

            print(f"\n{'='*40}")
            print(f"  Supervisor 第 {round_num}/{self.max_rounds} 轮决策")
            print(f"{'='*40}")
            self.emit({"step": "thinking", "message": f"Supervisor 第 {round_num}/{self.max_rounds} 轮决策", "round": round_num})

            # Supervisor 决策
            msg = await self.llm.chat_with_tools(
                system_prompt=system,
                messages=messages,
                tools=SUPERVISOR_TOOLS,
            )

            if not msg.tool_calls:
                print("  Supervisor: 信息足够，ResearchComplete")
                break

            messages.append(_assistant_msg(msg))

            # 优先检测 ResearchComplete，跳过所有工具执行直接结束
            if any(tc.function.name == "ResearchComplete" for tc in msg.tool_calls):
                print(f"  Supervisor: 调用 ResearchComplete → 结束")
                break

            conduct_items: list[tuple] = []  # (tc, topic) 配对收集，避免 zip 对齐风险
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    print(f"  [WARN] Supervisor JSON 解析失败，跳过")
                    continue

                if name == "ConductResearch":
                    topic = args["research_topic"]
                    conduct_items.append((tc, topic))

                elif name == "think_tool":
                    r = args.get("reflection", "")
                    print(f"  Supervisor 反思: {r[:120]}...")
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "content": f"反思：{r}",
                    })

            if not conduct_items:
                continue

            async def safe_run(topic):
                try:
                    agent = Level2Agent(on_progress=self.emit, kb_enabled=self.kb_enabled, user_id=self.user_id, llm=self.llm, rag_doc_ids=self.rag_doc_ids, search_mode=self.search_mode, trace=self.trace)
                    raw = await agent.run(topic)
                    # 压缩为结构化摘要，给 Supervisor 决策；原始报告保留给最终汇总
                    compressed = await self._compress_research(raw)
                    print(f"    研究员完成: {topic[:50]}...")
                    print(f"      原始 {len(raw)} 字 → 压缩 {len(compressed)} 字")
                    print(f"      摘要: {compressed[:150]}...")
                    return (compressed, raw, None)
                except Exception as e:
                    print(f"    研究员失败: {topic[:40]}... error={e}")
                    return (None, None, str(e))

            # 分批并行派遣，每批最多 max_parallel 个，防止资源耗尽
            batch_size = self.max_parallel
            total_batches = (len(conduct_items) + batch_size - 1) // batch_size
            results = []
            for i in range(0, len(conduct_items), batch_size):
                batch = conduct_items[i:i + batch_size]
                batch_num = i // batch_size + 1
                print(f"  派遣第 {batch_num}/{total_batches} 批，{len(batch)} 个研究员...")
                self.emit({"step": "searching", "message": f"派遣第 {batch_num}/{total_batches} 批，{len(batch)} 个研究员并行工作", "round": round_num})
                batch_results = await asyncio.gather(*[safe_run(topic) for _, topic in batch])
                results.extend(batch_results)

            for (tc, topic), (compressed, raw, error) in zip(conduct_items, results):
                if compressed is None:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"【课题】{topic}\n\n研究失败: {error}\n\n请尝试调整方向或简化后重新派遣。",
                    })
                    continue
                # 压缩版给 Supervisor 看（快速决策），原始版留给最终报告
                all_findings.append(compressed)
                raw_findings.append(raw)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": f"【课题】{topic}\n\n【摘要】\n{compressed}",
                })

        # 汇总所有发现：用原始完整报告（raw_findings）保证信息量，
        # 同时传入决策笔记（all_findings = compressed）辅助结构组织
        print(f"\n  汇总 {len(raw_findings)} 份研究报告...")
        self.emit({"step": "reporting", "message": f"正在汇总 {len(raw_findings)} 份研究报告..."})
        if not raw_findings:
            return "# 研究失败\n\n未能获取有效信息，请简化问题重试。"

        notes_text = "\n\n---\n\n".join(all_findings) if all_findings else ""
        try:
            final = await self.llm.chat(
                system_prompt="你是专业的深度研究报告总编。",
                max_tokens=32768,  # DeepSeek 默认 4096 不够 L4 长报告
                user_message=FINAL_REPORT_PROMPT.format(
                    question=question,
                    research_brief=research_brief,
                    messages="",
                    findings="\n\n---\n\n".join(raw_findings),
                    notes=notes_text,
                    date=_today_str(),
                ),
            )
            if self._compressed_summaries:
                prefix = "<!-- 上下文压缩记录 -->\n" + "\n".join(self._compressed_summaries)
                final = prefix + "\n\n" + final
            return final
        except Exception as e:
            print(f"  汇总超时，回退到原始合并: {e}")
            fallback = "# " + question + "\n\n" + "\n\n---\n\n".join(raw_findings)
            if self._compressed_summaries:
                prefix = "<!-- 上下文压缩记录 -->\n" + "\n".join(self._compressed_summaries)
                fallback = prefix + "\n\n" + fallback
            return fallback


# ============================================================
# 入口
# ============================================================

async def main():
    """命令行入口。"""
    import sys

    if len(sys.argv) > 1:
        args = sys.argv[1:]
        # 最后一个参数如果是数字，当作 level
        if args[-1].isdigit():
            level = int(args[-1])
            question = " ".join(args[:-1])
        else:
            level = 2
            question = " ".join(args)
    else:
        question = input("请输入你想研究的问题: ")
        level = 2

    # 澄清判断（Level 2/3/4 默认开启，Level 1 跳过保持极速）
    if level != 1:
        clarify = ClarifyHelper()
        check = await clarify.check(question)
        if check.get("need_clarify"):
            print(f"\n❓ Agent 需要更多信息:\n   {check.get('question', '')}")
            print(f"   请补充后重新运行。")
            return

    # 创建 trace 目录和 TraceRun 上下文
    from pathlib import Path
    from .trace import TraceRun
    reports_dir = Path(__file__).parent.parent.parent / "reports" / datetime.now().strftime("%Y%m%d_%H%M%S")
    reports_dir.mkdir(parents=True, exist_ok=True)

    async with TraceRun(
        question=question, output_dir=str(reports_dir),
        level=level, model=config.llm_model, search_mode="web_only",
    ) as trace:
        if level == 1:
            agent = FastLevel1Agent(trace=trace)
        elif level == 3:
            agent = Level3Agent(trace=trace)
        elif level == 4:
            agent = Level4Agent(trace=trace)
        else:
            agent = Level2Agent(trace=trace)

        report = await agent.run(question)
        print(f"\n{'='*60}")
        print(report)
        print(f"\n{'='*60}")

        filename = reports_dir / "report.md"
        filename.write_text(report, encoding="utf-8")
        print(f"\n报告已保存至: {filename}")


if __name__ == "__main__":
    asyncio.run(main())
