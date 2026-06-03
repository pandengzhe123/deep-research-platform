"""FastAPI 服务 —— 将 Agent 暴露为 HTTP API，支持 SSE 流式推送进度。

Java 网关调用方式：
  POST /research/stream   SSE 流式（实时推送进度）
  POST /research          同步等待（返回完整结果）
  GET  /health            健康检查
"""

from __future__ import annotations

import asyncio
import json
import traceback
import uuid
from datetime import datetime
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import asyncio as aio
import logging

log = logging.getLogger(__name__)

from .agent import ClarifyHelper, FastLevel1Agent, Level1Agent, Level2Agent, Level3Agent, Level4Agent
from .config import config

app = FastAPI(title="Deep Researcher Agent", version="0.1.0")

# CORS —— 允许 Java 网关跨域调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# 模型定义（Java 网关调用的 API 契约）
# ============================================================

class ResearchRequest(BaseModel):
    question: str
    level: int = 2
    max_rounds: int | None = None
    language: str = "auto"
    context: str = ""  # 之前的对话上下文（用于多轮追问）


class ProgressEvent(BaseModel):
    """SSE 推送的进度事件。"""
    event: str          # "planning" | "searching" | "thinking" | "reporting" | "done" | "error"
    message: str = ""   # 人类可读的状态描述
    data: dict | None = None  # 附加数据


# ============================================================
# 核心：Agent 运行 + SSE 推送
# ============================================================

# 全局字典：task_id → cancel_event（用于取消任务）
_active_tasks: dict[str, asyncio.Event] = {}


async def run_agent_with_sse(
    question: str,
    level: int,
    max_rounds: int | None,
    language: str,
    context: str,
    cancel: asyncio.Event,
) -> AsyncGenerator[dict, None]:
    """运行 Agent 并以 SSE 事件流返回进度。"""
    try:
        # ---- Step 1: 搜索 ----
        from .llm import LLMClient
        from .search import SearchTool
        from .agent import (
            AGENT_SYSTEM,
            FAST_REPORT_PROMPT,
            REPORT_PROMPT,
            TOOLS,
        )

        llm = LLMClient()
        search_tool = SearchTool()
        rounds = max_rounds or config.max_search_rounds

        # ---- 澄清用户意图（Level 2/3/4 默认开启，Level 1 跳过保持极速） ----
        if level != 1:
            yield {"event": "status", "data": json.dumps({
                "step": "planning",
                "message": "正在分析问题是否需要澄清...",
            })}
            # 拼接上下文：之前的对话 + 当前问题
            full_context = context + "\n\n---\n用户最新消息：" + question if context else question
            clarify = ClarifyHelper()
            check = await clarify.check(full_context)
            if check.get("need_clarify"):
                yield {"event": "status", "data": json.dumps({
                    "step": "clarify",
                    "message": f"需要澄清: {check.get('question', '')}",
                    "clarify_question": check.get("question", ""),
                    "understanding": check.get("summary", ""),
                })}
                yield {"event": "done", "data": json.dumps({
                    "report": "",
                    "language": language,
                    "need_clarify": True,
                    "question": check.get("question", ""),
                })}
                return
            else:
                yield {"event": "status", "data": json.dumps({
                    "step": "planned",
                    "message": f"需求明确: {check.get('summary', '')}",
                })}

        # ---- Level 4: Supervisor-Researcher 双层循环 ----
        if level == 4:
            agent = Level4Agent()
            yield {"event": "status", "data": json.dumps({
                "step": "planning",
                "message": f"Supervisor 开始调度，最多 {agent.max_rounds} 轮...",
            })}
            try:
                result = await agent.run(question)
                yield {"event": "done", "data": json.dumps({
                    "report": result,
                    "language": language,
                })}
            except Exception as e:
                yield {"event": "error", "data": json.dumps({
                    "message": str(e),
                    "traceback": traceback.format_exc(),
                })}
            return

        # ---- Level 3: 拆题 → 并行 Level 2 → 汇总 ----
        if level == 3:
            from .agent import DECOMPOSE_PROMPT, DECOMPOSE_SCHEMA, MERGE_PROMPT

            yield {"event": "status", "data": json.dumps({
                "step": "planning",
                "message": "正在分析问题，拆分子课题...",
            })}

            plan = llm.structured_output(
                system_prompt="你是研究规划专家。",
                user_message=DECOMPOSE_PROMPT.format(question=question),
                schema=DECOMPOSE_SCHEMA,
            )
            sub_topics = plan.get("sub_topics", [question])

            yield {"event": "status", "data": json.dumps({
                "step": "planned",
                "message": f"拆成 {len(sub_topics)} 个子课题，每个启动一个研究员并行工作",
                "sub_topics": sub_topics,
                "understanding": plan.get("understanding", ""),
            })}

            if cancel.is_set():
                return

            # 并行跑 Level 2（每个子课题独立 Agent 实例，gather 等待）
            for i, topic in enumerate(sub_topics):
                yield {"event": "status", "data": json.dumps({
                    "step": "searching",
                    "message": f"研究员 #{i+1}/{len(sub_topics)} 启动: {topic[:50]}...",
                })}

            async def safe_run(topic, idx):
                """每个子研究员独立运行，出错不影响其他。"""
                try:
                    agent = Level2Agent()
                    result = await agent.run(topic)
                    log.info("研究员 #%d 完成: topic=%s, len=%d", idx, topic[:40], len(result))
                    return result
                except Exception as e:
                    log.error("研究员 #%d 失败: %s", idx, str(e))
                    return f"# 研究失败\n\n子课题「{topic}」研究出错: {e}"

            tasks = [safe_run(t, i) for i, t in enumerate(sub_topics)]
            results = await aio.gather(*tasks)
            reports = [r for r in results if r]

            yield {"event": "status", "data": json.dumps({
                "step": "reporting",
                "message": f"所有研究员完成，正在汇总 {len(reports)} 份子报告...",
            })}

            if not reports:
                yield {"event": "done", "data": json.dumps({
                    "report": "# 研究失败\n\n所有子研究员未能获取有效结果，请简化问题重试。",
                    "language": language,
                })}
                return

            merged = "\n\n---\n\n".join(
                f"## 子课题{i+1}\n{r}"
                for i, r in enumerate(reports)
            )
            final_report = llm.chat(
                system_prompt="你是专业的深度研究报告汇总专家。",
                user_message=MERGE_PROMPT.format(
                    question=question,
                    reports=merged,
                ),
            )

            yield {"event": "done", "data": json.dumps({
                "report": final_report,
                "language": language,
            })}
            return

        # ---- Level 1: 极速模式 ----
        if level == 1:
            yield {"event": "status", "data": json.dumps({
                "step": "searching",
                "message": "极速搜索中（跳过 LLM 规划 + LLM 摘要，全程仅 1 次 LLM 调用）...",
            })}

            if cancel.is_set():
                return

            search_results = await search_tool.search_fast(
                queries=[question],
                max_results=3,
            )
            all_results = [search_results]

            yield {"event": "status", "data": json.dumps({
                "step": "reporting",
                "message": "正在撰写报告（全程唯一一次 LLM 调用）...",
            })}

            if cancel.is_set():
                return

            report = llm.chat(
                system_prompt="你是专业的深度研究报告撰写助手。简洁、准确、有引用。",
                user_message=FAST_REPORT_PROMPT.format(
                    question=question,
                    search_results="\n\n".join(all_results),
                ),
            )

            yield {"event": "done", "data": json.dumps({
                "report": report,
                "language": language,
            })}
            return

        # ---- Level 2: 规划 + 搜索-反思循环 ----
        yield {"event": "status", "data": json.dumps({
            "step": "planning",
            "message": "正在分析问题，规划搜索策略...",
        })}

        if cancel.is_set():
            return

        from .agent import PLAN_PROMPT, PLAN_SCHEMA

        plan = llm.structured_output(
            system_prompt=PLAN_PROMPT,
            user_message=f"用户问题：{question}\n\n今天日期：{datetime.now().strftime('%Y年%m月%d日')}",
            schema=PLAN_SCHEMA,
        )
        queries = plan.get("search_queries", [question])
        yield {"event": "status", "data": json.dumps({
            "step": "planned",
            "message": f"搜索计划已生成（Level 2 将进行多轮搜索+反思）",
            "queries": queries,
            "understanding": plan.get("understanding", ""),
        })}

        if cancel.is_set():
            return

        # ---- Step 2: 搜索-反思循环（Level 2） ----
        messages: list[dict] = [
            {"role": "user", "content": f"请研究以下问题，并在信息充分时给出报告：\n\n{question}"}
        ]
        all_results: list[str] = []
        system = AGENT_SYSTEM.format(max_rounds=rounds)

        for round_num in range(1, rounds + 1):
            if cancel.is_set():
                return

            yield {"event": "status", "data": json.dumps({
                "step": "thinking",
                "message": f"第 {round_num}/{rounds} 轮决策中...",
                "round": round_num,
            })}

            msg = llm.chat_with_tools(
                system_prompt=system,
                messages=messages,
                tools=TOOLS,
            )

            if not msg.tool_calls:
                yield {"event": "status", "data": json.dumps({
                    "step": "decided",
                    "message": "信息已足够，停止搜索",
                    "round": round_num,
                })}
                break

            # 执行工具调用
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
                    qs = args.get("queries", [question])
                    yield {"event": "status", "data": json.dumps({
                        "step": "searching",
                        "message": f"搜索: {', '.join(qs)}",
                        "round": round_num,
                        "queries": qs,
                    })}
                    result = await search_tool.search(qs)
                    all_results.append(result)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

                elif name == "think":
                    reflection = args.get("reflection", "")
                    yield {"event": "status", "data": json.dumps({
                        "step": "thinking",
                        "message": reflection[:150] + ("..." if len(reflection) > 150 else ""),
                        "round": round_num,
                    })}
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": "反思已记录",
                    })

        if cancel.is_set():
            return

        # ---- Step 3: 生成报告 ----
        yield {"event": "status", "data": json.dumps({
            "step": "reporting",
            "message": "正在撰写报告...",
        })}

        report = llm.chat(
            system_prompt="你是专业的深度研究报告撰写助手。",
            user_message=REPORT_PROMPT.format(
                question=question,
                search_results="\n\n".join(all_results),
            ),
        )

        yield {"event": "done", "data": json.dumps({
            "report": report,
            "language": language,
        })}

    except Exception as e:
        yield {"event": "error", "data": json.dumps({
            "message": str(e),
            "traceback": traceback.format_exc(),
        })}


# ============================================================
# API 端点
# ============================================================

@app.get("/health")
async def health():
    """健康检查 —— Java 网关用来判断 Agent 是否存活。"""
    return {
        "status": "ok",
        "model": config.llm_model,
        "provider": config.llm_provider,
    }


@app.post("/research")
async def research_sync(req: ResearchRequest):
    """同步接口 —— 等待完整结果后返回。"""
    task_id = str(uuid.uuid4())[:8]
    cancel = asyncio.Event()
    _active_tasks[task_id] = cancel

    try:
        result = []
        async for event in run_agent_with_sse(
            question=req.question,
            level=req.level,
            max_rounds=req.max_rounds,
            language=req.language,
            context=req.context,
            cancel=cancel,
        ):
            if event["event"] == "done":
                result.append(json.loads(event["data"]))
            elif event["event"] == "error":
                raise HTTPException(
                    status_code=500,
                    detail=json.loads(event["data"]),
                )

        if not result:
            raise HTTPException(status_code=500, detail="No result")

        return JSONResponse(content=result[0])

    finally:
        _active_tasks.pop(task_id, None)


@app.post("/research/stream")
async def research_stream(req: ResearchRequest):
    """SSE 流式接口 —— 实时推送进度，Java 网关用 WebClient 订阅。"""

    task_id = str(uuid.uuid4())[:8]
    cancel = asyncio.Event()
    _active_tasks[task_id] = cancel

    async def event_generator():
        try:
            async for event in run_agent_with_sse(
                question=req.question,
                level=req.level,
                max_rounds=req.max_rounds,
                language=req.language,
                context=req.context,
                cancel=cancel,
            ):
                yield event
        finally:
            _active_tasks.pop(task_id, None)

    return EventSourceResponse(event_generator())


@app.delete("/research/{task_id}")
async def cancel_research(task_id: str):
    """取消正在运行的研究任务。"""
    cancel = _active_tasks.get(task_id)
    if cancel is None:
        raise HTTPException(status_code=404, detail="任务不存在或已完成")
    cancel.set()
    return {"status": "cancelled", "task_id": task_id}


@app.get("/research/active")
async def list_active_tasks():
    """列出当前运行中的任务。"""
    return {"active_tasks": list(_active_tasks.keys())}


# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "researcher.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
