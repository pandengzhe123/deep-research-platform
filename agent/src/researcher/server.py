"""FastAPI 服务 —— 将 Agent 暴露为 HTTP API，支持 SSE 流式推送进度。

重构后 server.py 不再包含任何 Agent 逻辑——所有 Agent 逻辑在 agent.py。
server.py 只做：① 接收请求 ② 创建 Agent 实例 ③ 把进度事件转成 SSE ④ 返回结果。
"""

from __future__ import annotations

import asyncio as aio
import json
import logging
import traceback
import uuid
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .agent import ClarifyHelper, FastLevel1Agent, Level2Agent, Level3Agent, Level4Agent
from .config import config

log = logging.getLogger(__name__)

app = FastAPI(title="Deep Researcher Agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# 模型定义
# ============================================================

class ResearchRequest(BaseModel):
    question: str
    level: int = 2
    max_rounds: int | None = None
    language: str = "auto"
    context: str = ""


class ProgressEvent(BaseModel):
    event: str
    message: str = ""
    data: dict | None = None

# ============================================================
# 核心 —— 用 asyncio.Queue 把 Agent 的进度事件转成 SSE 流
# ============================================================

_active_tasks: dict[str, aio.Event] = {}


async def run_agent_with_sse(
    question: str,
    level: int,
    max_rounds: int | None,
    language: str,
    context: str,
    cancel: aio.Event,
) -> AsyncGenerator[dict, None]:
    """
    核心函数 —— 创建真正的 Agent（agent.py），用 Queue 收集进度事件，
    转成 SSE 事件流返回。server.py 不再包含任何 Agent 逻辑。
    """
    queue: aio.Queue[dict] = aio.Queue()

    def on_progress(event: dict):
        """Agent 每次内部进度变化时调用此回调 → 事件入队。"""
        try:
            queue.put_nowait(event)
        except aio.QueueFull:
            pass

    try:
        # ---- 澄清（Level 2/3/4 默认开启） ----
        if level != 1:
            full_context = context + "\n\n---\n用户最新消息：" + question if context else question
            clarify = ClarifyHelper()
            check = await clarify.check(full_context)
            if check.get("need_clarify"):
                yield {"event": "status", "data": json.dumps({
                    "step": "clarify",
                    "message": f"需要澄清: {check.get('question', '')}",
                })}
                yield {"event": "done", "data": json.dumps({
                    "report": "", "language": language,
                    "need_clarify": True, "question": check.get("question", ""),
                })}
                return
            on_progress({"step": "planned", "message": f"需求明确: {check.get('summary', '')}"})

        # ---- 创建 Agent（走 agent.py 的真 Agent） ----
        if level == 1:
            agent = FastLevel1Agent(on_progress=on_progress)
        elif level == 3:
            agent = Level3Agent(on_progress=on_progress)
        elif level == 4:
            agent = Level4Agent(on_progress=on_progress)
        else:
            agent = Level2Agent(on_progress=on_progress)

        on_progress({"step": "planning", "message": f"Level {level} Agent 启动..."})

        # ---- 后台跑 Agent，前台推 SSE ----
        async def run_agent():
            try:
                result = await agent.run(question)
                await queue.put({"type": "done", "report": result})
            except Exception as e:
                log.exception("Agent 执行异常")
                await queue.put({"type": "error", "message": str(e), "traceback": traceback.format_exc()})

        task = aio.create_task(run_agent())

        # 从队列读取事件 → yield SSE
        while not task.done():
            try:
                event = await aio.wait_for(queue.get(), timeout=0.1)
            except aio.TimeoutError:
                continue

            if event.get("type") in ("done", "error"):
                break
            yield {"event": "status", "data": json.dumps(event, ensure_ascii=False)}

        # Agent 结束后，取队列里剩余的事件
        while not queue.empty():
            event = queue.get_nowait()
            if event.get("type") == "done":
                yield {"event": "done", "data": json.dumps({
                    "report": event["report"], "language": language,
                })}
                return
            elif event.get("type") == "error":
                yield {"event": "error", "data": json.dumps({
                    "message": event["message"], "traceback": event.get("traceback", ""),
                })}
                return

        # 如果 task 结束了但没有任何 done/error 事件（不应发生）
        result = task.result()
        yield {"event": "done", "data": json.dumps({
            "report": result if isinstance(result, str) else "", "language": language,
        })}

    except Exception as e:
        log.exception("run_agent_with_sse 异常")
        yield {"event": "error", "data": json.dumps({
            "message": str(e), "traceback": traceback.format_exc(),
        })}


# ============================================================
# API 端点
# ============================================================

@app.get("/test-sse")
async def test_sse():
    """测试 SSE 是否实时推送 —— 每秒一条，共 10 条。"""
    async def generate():
        for i in range(10):
            yield {"event": "status", "data": json.dumps({"step": "test", "i": i})}
            await aio.sleep(1)
    return EventSourceResponse(generate())


@app.get("/health")
async def health():
    return {"status": "ok", "model": config.llm_model, "provider": config.llm_provider}


@app.post("/research")
async def research_sync(req: ResearchRequest):
    """同步接口 —— 收集 SSE 事件，等 done 后返回 JSON。"""
    cancel = aio.Event()
    try:
        result = []
        async for event in run_agent_with_sse(
            question=req.question, level=req.level,
            max_rounds=req.max_rounds, language=req.language,
            context=req.context, cancel=cancel,
        ):
            if event["event"] == "done":
                result.append(json.loads(event["data"]))
            elif event["event"] == "error":
                raise HTTPException(status_code=500, detail=json.loads(event["data"]))
        if not result:
            raise HTTPException(status_code=500, detail="无结果")
        return JSONResponse(content=result[0])
    finally:
        pass


@app.post("/research/stream")
async def research_stream(req: ResearchRequest):
    """SSE 流式接口。"""
    cancel = aio.Event()
    return EventSourceResponse(run_agent_with_sse(
        question=req.question, level=req.level,
        max_rounds=req.max_rounds, language=req.language,
        context=req.context, cancel=cancel,
    ))


@app.delete("/research/{task_id}")
async def cancel_research(task_id: str):
    cancel = _active_tasks.get(task_id)
    if cancel is None:
        raise HTTPException(status_code=404, detail="任务不存在或已完成")
    cancel.set()
    return {"status": "cancelled", "task_id": task_id}


@app.get("/research/active")
async def list_active_tasks():
    return {"active_tasks": list(_active_tasks.keys())}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("researcher.server:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
