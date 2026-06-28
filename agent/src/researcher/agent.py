"""深度研究智能体 —— 从 Level 1 到 Level 4 渐进实现。

Level 1 (当前):  单次搜索 → 生成报告
Level 2 (TODO):  搜索-反思 Agent 循环
Level 3 (TODO):  多路并行搜索
Level 4 (TODO):  Supervisor-Researcher 双层
"""

import asyncio
import json
import re
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
- **重要**：如果用户当前消息是对你上一轮追问的直接回答（如你问了"要研究哪些方面"，用户回答"你提到的所有方面"或"全部"），说明用户已确认你的提议，必须返回 need_clarify=false，不得反复追问

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

    def __init__(self, on_progress=None, kb_enabled=False, user_id: str = "default", rag_doc_ids: list[str] = None):
        self.llm = LLMClient()
        self.search = SearchTool(on_progress=self.emit)
        self.emit = on_progress or (lambda e: None)
        self.kb_enabled = kb_enabled
        self.user_id = user_id
        self.rag_doc_ids = rag_doc_ids or []
        if kb_enabled:
            from .kb import kb
            self.kb = kb

    async def run(self, question: str) -> str:
        print(f"\n{'='*60}")
        print(f"  问题: {question}")
        print(f"  模式: Level 1 Fast（极速）")
        print(f"{'='*60}")

        self.emit({"step": "searching", "message": "极速搜索中（全程仅 1 次 LLM 调用）..."})

        # 网络搜索 + 知识库检索（并行）
        tasks = [self.search.search_fast(queries=[question], max_results=3)]
        if self.kb_enabled:
            tasks.append(self._kb_search(question))  # coroutine 直接传给 gather

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
        return await asyncio.to_thread(self.kb.search, query, user_id=self.user_id, doc_ids=self.rag_doc_ids or None)

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y年%m月%d日")


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
        report = await self.llm.chat(
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

1. **search**: 搜索互联网获取公开信息，每次可传多个查询词
2. **search_kb**: 搜索用户上传的私有文档/知识库（如果可用的话）
3. **think**: 暂停反思当前进展

工作方式：
- 如果用户问题涉及"我的文档""上传的资料""本地上传"，优先使用 search_kb
- 对于需要最新信息或公开知识的问题，使用 search
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

RAG_ONLY_SYSTEM = """你是一个研究助手。当前模式：**仅知识库检索**——你只能使用 search_kb 工具搜索本地文档，不能使用 search 工具联网。

重要约束：
- 你的回答必须严格基于知识库返回的文档内容，不得添加文档中没有的信息
- 如果知识库中没有相关文档，直接告知用户"知识库中未找到相关信息"，不得编造内容或基于常识补充
- 引用知识库内容时注明来源文档名称
- 不要写"我是AI"或任何自我指涉的话"""

WEB_ONLY_SYSTEM = """你是一个研究助手。你可以使用以下工具：

1. **search**: 搜索互联网获取公开信息，每次可传多个查询词
2. **think**: 暂停反思当前进展

工作方式：
- 对于需要最新信息或公开知识的问题，使用 search
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

    def __init__(self, on_progress=None, kb_enabled: bool = False, user_id: str = "default", llm=None, rag_doc_ids: list[str] = None, search_mode: str = "hybrid"):
        self.llm = llm or LLMClient()
        self.max_rounds = config.max_search_rounds
        self.user_id = user_id
        self.emit = on_progress or (lambda e: None)
        self.search_tool = SearchTool(on_progress=self.emit)
        self.rag_doc_ids = rag_doc_ids or []
        self.kb_enabled = kb_enabled
        self.search_mode = search_mode
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
            {"role": "user", "content": f"请研究以下问题，并在信息充分时给出报告：\n\n{question}\n\n当前日期：{self._today()}"}
        ]

        all_search_results: list[str] = []
        MAX_RESULTS_CHARS = 300000  # 单个搜索结果最大 30 万字符，防止 OOM
        MAX_ROUND_RESULTS = 3       # 只保留最近 3 轮（每轮合并后的结果）
        # 根据搜索模式选择 System Prompt
        if self.search_mode == "rag_only":
            system = RAG_ONLY_SYSTEM.format(max_rounds=self.max_rounds)
        elif self.search_mode == "web_only":
            system = WEB_ONLY_SYSTEM.format(max_rounds=self.max_rounds)
        else:
            system = AGENT_SYSTEM.format(max_rounds=self.max_rounds)
        MAX_HISTORY_CHARS = 500000
        context_warned = False      # 预警只发一次

        for round_num in range(1, self.max_rounds + 1):
            # Token 超限保护
            total_chars = sum(len(str(m)) for m in messages)

            # 80% 预警：上下文快满了，建议开新会话
            if not context_warned and total_chars > MAX_HISTORY_CHARS * 0.8:
                context_warned = True
                self.emit({"step": "thinking", "message": f"上下文已用 {total_chars * 100 // MAX_HISTORY_CHARS}%，继续追问可能丢失早期内容，建议开新会话"})

            # 100% 截断：先压缩旧消息再截断，保留关键约束不被丢弃
            if total_chars > MAX_HISTORY_CHARS:
                print(f"  ⚠️ 历史过长 ({total_chars} 字符)，压缩旧内容")
                self.emit({"step": "thinking", "message": "上下文已满，正在压缩早期对话以保留关键信息...", "round": round_num})
                try:
                    old_msgs = messages[1:-5]  # 中间要被丢弃的部分
                    if old_msgs:
                        raw = "\n".join(str(m) for m in old_msgs)
                        summary = await self.llm.chat(
                            system_prompt="你是一个对话压缩助手。将对话历史压缩为简洁摘要，保留关键事实、数据、用户约束条件和研究方向。丢弃搜索细节和冗长报告正文。用中文。",
                            user_message=f"请压缩以下对话，保留关键信息：\n\n{raw}",
                        )
                        if summary:
                            messages = [messages[0], {"role": "system", "content": f"[早期对话摘要] {summary}"}] + messages[-5:]
                except Exception:
                    pass  # 压缩失败 → 降级为原截断方案
                if total_chars > MAX_HISTORY_CHARS:  # 压缩后仍超限 → 硬截断兜底
                    messages = [messages[0]] + messages[-5:]
                self.emit({"step": "thinking", "message": f"早期对话已压缩（上下文已用 {sum(len(str(m)) for m in messages) * 100 // MAX_HISTORY_CHARS}%）。建议开新会话以保证研究质量", "round": round_num})

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
                            print(f"  ⚠️ JSON 解析失败 (tc.id={tc.id}): {raw_args[:80]}...")
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
                                result = await asyncio.to_thread(self.kb.search, query, user_id=self.user_id, doc_ids=self.rag_doc_ids or None)
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
                        print(f"  ⚠️ 工具 {name} 调用失败 (tc.id={tc.id}): {e}")
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
                print(f"  ⚠️ 第 {round_num} 轮 LLM 调用异常: {e}，跳过本轮继续")
                # LLM 调用本身失败，没有 tc，用 user 角色通知 Agent
                messages.append({
                    "role": "user",
                    "content": f"（系统提示：本轮 LLM 调用失败: {e}，请基于已有信息继续研究）",
                })

        # 无结果兜底：避免用空字符串调 LLM 产生幻觉报告
        if not all_search_results:
            print("  ⚠️ 无任何搜索结果，返回兜底提示")
            self.emit({"step": "reporting", "message": "未获取到有效搜索结果"})
            return f"# 未找到相关信息\n\n关于「{question}」，未能在网络和知识库中找到有效信息。\n\n可能的原因：\n\n1. 搜索 API 暂时不可用\n2. 该问题目前没有公开资料\n3. 搜索关键词与问题不匹配\n\n建议：稍后重试，或尝试更具体的关键词。"

        # rag_only 模式：所有 KB 结果都是"未找到" → 拦截，不许编造
        if self.search_mode == "rag_only":
            raw_text = "\n\n---\n\n".join(all_search_results)
            if "知识库检索结果" not in raw_text or "来源" not in raw_text:
                print("  ⚠️ rag_only 模式，知识库无有效内容，返回兜底提示")
                self.emit({"step": "reporting", "message": "知识库中未找到相关信息"})
                return f"# 未找到相关信息\n\n知识库中未找到与「{question}」相关的文档内容。\n\n建议：\n1. 上传相关文档到知识库\n2. 切换到混合搜索模式同时检索网络和知识库\n3. 检查文档是否已正确上传"

        # 压缩研究结果
        # 压缩前再做一次内存保护
        raw_text = "\n\n---\n\n".join(all_search_results)
        if len(raw_text) > 500000:
            raw_text = raw_text[:500000] + "\n\n（原始数据过长，已截断 50 万字符）"

        print(f"\n[压缩] 整理搜索结果...")
        self.emit({"step": "reporting", "message": "正在压缩整理搜索结果..."})
        try:
            compressed = await self.llm.chat(
                system_prompt=COMPRESS_PROMPT.format(date=self._today()),
                user_message=COMPRESS_USER_MESSAGE.format(
                    question=question,
                    raw_results=raw_text,
                ),
            )
        except Exception as e:
            print(f"  ⚠️ 压缩失败，跳过压缩直接使用原始结果: {e}")
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
1. 用 # 作为报告标题，覆盖整个问题
2. 用 ## 作为章节标题，按子课题组织
3. 每个章节整合相关研究员的发现
4. 所有引用标注来源 [标题](URL)
5. 结尾包含 ### 参考来源 章节（去重）
6. 报告语言与用户问题一致
7. 不要写"我是AI"或自我指涉"""


class Level3Agent:
    """Level 3: LLM 拆题 → 多路并行 Level 2 → 汇总"""

    def __init__(self, on_progress=None, kb_enabled: bool = False, user_id: str = "anonymous", rag_doc_ids: list[str] = None, search_mode: str = "hybrid"):
        self.llm = LLMClient()
        self.emit = on_progress or (lambda e: None)
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

        async def safe_run(topic, idx):
            try:
                agent = Level2Agent(on_progress=self.emit, kb_enabled=self.kb_enabled, user_id=self.user_id, llm=self.llm, rag_doc_ids=self.rag_doc_ids, search_mode=self.search_mode)
                return (idx, topic, await agent.run(topic))
            except Exception as e:
                print(f"    子课题 {idx} 失败 ({topic[:40]}...): {e}")
                self.emit({"step": "searching", "message": f"子课题 {idx+1}「{topic[:30]}...」失败，跳过"})
                return (idx, topic, None)  # None 报告不进入汇总，避免污染

        results = await asyncio.gather(*[safe_run(t, i) for i, t in enumerate(sub_topics)])
        # 过滤失败的子课题，保持 (idx, topic, report) 对齐
        valid = [(idx, topic, report) for idx, topic, report in results if report is not None]
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

    def __init__(self, on_progress=None, kb_enabled: bool = False, user_id: str = "anonymous", rag_doc_ids: list[str] = None, search_mode: str = "hybrid"):
        self.llm = LLMClient()
        self.max_rounds = config.max_supervisor_rounds
        self.max_parallel = config.max_parallel_researchers
        self.emit = on_progress or (lambda e: None)
        self.kb_enabled = kb_enabled
        self.user_id = user_id
        self.rag_doc_ids = rag_doc_ids or []
        self.search_mode = search_mode

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

        MAX_HISTORY_CHARS = 500000
        context_warned = False

        for round_num in range(1, self.max_rounds + 1):
            # 消息历史超限保护（与 Level 2 一致）
            total_chars = sum(len(str(m)) for m in messages)

            if not context_warned and total_chars > MAX_HISTORY_CHARS * 0.8:
                context_warned = True
                self.emit({"step": "thinking", "message": f"上下文已用 {total_chars * 100 // MAX_HISTORY_CHARS}%，继续追问可能丢失早期内容，建议开新会话"})

            if total_chars > MAX_HISTORY_CHARS:
                print(f"  ⚠️ Supervisor 历史过长 ({total_chars} 字符)，压缩旧内容")
                self.emit({"step": "thinking", "message": "上下文已满，正在压缩早期对话以保留关键信息...", "round": round_num})
                try:
                    old_msgs = messages[1:-5]
                    if old_msgs:
                        raw = "\n".join(str(m) for m in old_msgs)
                        summary = await self.llm.chat(
                            system_prompt="你是一个对话压缩助手。将对话历史压缩为简洁摘要，保留关键事实、数据、用户约束条件和研究方向。丢弃搜索细节和冗长报告正文。用中文。",
                            user_message=f"请压缩以下对话，保留关键信息：\n\n{raw}",
                        )
                        if summary:
                            messages = [messages[0], {"role": "system", "content": f"[早期对话摘要] {summary}"}] + messages[-5:]
                except Exception:
                    pass
                if total_chars > MAX_HISTORY_CHARS:
                    messages = [messages[0]] + messages[-5:]
                self.emit({"step": "thinking", "message": f"早期对话已压缩（上下文已用 {sum(len(str(m)) for m in messages) * 100 // MAX_HISTORY_CHARS}%）。建议开新会话以保证研究质量", "round": round_num})

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

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ],
            })

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
                    print(f"  ⚠️ Supervisor JSON 解析失败，跳过")
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
                    agent = Level2Agent(on_progress=self.emit, kb_enabled=self.kb_enabled, user_id=self.user_id, llm=self.llm, rag_doc_ids=self.rag_doc_ids, search_mode=self.search_mode)
                    return (await agent.run(topic), None)
                except Exception as e:
                    print(f"    研究员失败: {topic[:40]}... error={e}")
                    return (None, str(e))  # 带出错误原因，让 Supervisor 看到

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

            for (tc, topic), (report, error) in zip(conduct_items, results):
                if report is None:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"【课题】{topic}\n\n研究失败: {error}\n\n请尝试调整方向或简化后重新派遣。",
                    })
                    continue
                all_findings.append(report)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": f"【课题】{topic}\n\n【报告】\n{report[:10000]}",
                })
                print(f"    研究员完成: {topic[:50]}... ({len(report)} 字符)")

        # 汇总所有发现
        print(f"\n  汇总 {len(all_findings)} 份研究报告...")
        self.emit({"step": "reporting", "message": f"正在汇总 {len(all_findings)} 份研究报告..."})
        if not all_findings:
            return "# 研究失败\n\n未能获取有效信息，请简化问题重试。"

        try:
            final = await self.llm.chat(
                system_prompt="你是专业的深度研究报告总编。",
                user_message=FINAL_REPORT_PROMPT.format(
                    question=question,
                    findings="\n\n---\n\n".join(all_findings),
                ),
            )
            return final
        except Exception as e:
            print(f"  汇总超时，回退到原始合并: {e}")
            return "# " + question + "\n\n" + "\n\n---\n\n".join(all_findings)


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
        from researcher.agent import ClarifyHelper  # noqa
        clarify = ClarifyHelper()
        check = await clarify.check(question)
        if check.get("need_clarify"):
            print(f"\n❓ Agent 需要更多信息:\n   {check.get('question', '')}")
            print(f"   请补充后重新运行。")
            return

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
