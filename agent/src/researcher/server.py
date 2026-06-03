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

from .agent import Level1Agent, Level2Agent
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
    level: int = 2              # 1 或 2（Agent 等级）
    max_rounds: int | None = None  # 覆盖默认搜索轮数
    language: str = "auto"


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
    cancel: asyncio.Event,
) -> AsyncGenerator[dict, None]:
    """运行 Agent 并以 SSE 事件流返回进度。"""
    try:
        # ---- Step 1: 规划搜索 ----
        from .llm import LLMClient
        from .search import SearchTool
        from .agent import (
            AGENT_SYSTEM,
            PLAN_PROMPT,
            PLAN_SCHEMA,
            REPORT_PROMPT,
            TOOLS,
        )

        llm = LLMClient()
        search_tool = SearchTool()
        rounds = max_rounds or config.max_search_rounds

        yield {"event": "status", "data": json.dumps({
            "step": "planning",
            "message": "正在分析问题，规划搜索策略...",
        })}

        if cancel.is_set():
            return

        plan = llm.structured_output(
            system_prompt=PLAN_PROMPT,
            user_message=f"用户问题：{question}\n\n今天日期：{datetime.now().strftime('%Y年%m月%d日')}",
            schema=PLAN_SCHEMA,
        )
        queries = plan.get("search_queries", [question])
        yield {"event": "status", "data": json.dumps({
            "step": "planned",
            "message": f"搜索计划已生成",
            "queries": queries,
            "understanding": plan.get("understanding", ""),
        })}

        if cancel.is_set():
            return

        # ---- Step 2: 搜索-反思循环 ----
        if level == 1:
            # Level 1: 一次搜索
            yield {"event": "status", "data": json.dumps({
                "step": "searching",
                "message": f"搜索中（{len(queries)} 个查询）...",
            })}
            search_results = await search_tool.search(queries)
            all_results = [search_results]
        else:
            # Level 2: 搜索-反思循环
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
                            "content": f"反思已记录",
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
