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


class Level1Agent:
    """Level 1: 分析问题 → 一次搜索 → 生成报告"""

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

        # 生成最终报告
        print(f"\n[最终] 生成报告...")
        report = self.llm.chat(
            system_prompt="你是专业的深度研究报告撰写助手。",
            user_message=REPORT_PROMPT.format(
                question=question,
                search_results="\n\n".join(all_search_results),
            ),
        )
        return report

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y年%m月%d日")


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
        agent = Level1Agent()
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
