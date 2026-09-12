"""FastAPI 服务 —— 将 Agent 暴露为 HTTP API，支持 SSE 流式推送进度。

重构后 server.py 不再包含任何 Agent 逻辑——所有 Agent 逻辑在 agent.py。
server.py 只做：① 接收请求 ② 创建 Agent 实例 ③ 把进度事件转成 SSE ④ 返回结果。
"""

from __future__ import annotations

import asyncio as aio
import json
import logging
import os
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .agent import ClarifyHelper, FastLevel1Agent, Level2Agent, Level3Agent, Level4Agent
from .config import config
from .kb import kb
from .llm import LLMClient
from .trace import TraceRun

log = logging.getLogger(__name__)

app = FastAPI(title="Deep Researcher Agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:80",
        "http://127.0.0.1:80",
        os.getenv("EXTRA_CORS_ORIGIN", ""),
    ],
    allow_credentials=True,
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
    kb_enabled: bool | None = False  # 保留旧字段兼容，新前端不传此字段
    search_mode: str = "hybrid"  # "hybrid" | "web_only" | "rag_only"
    user_id: str = "default"
    rag_doc_ids: list[str] = []  # 用户勾选的文档 ID，空=搜全部
    session_id: str = ""  # 会话 ID（用于会话研究锁，防同会话并发研究）


class ProgressEvent(BaseModel):
    event: str
    message: str = ""
    data: dict | None = None

# ============================================================
# 会话研究锁 —— 防止同一会话被并发研究
# ============================================================

_LOCK_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


_LOCK_RENEW_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
else
    return 0
end
"""


async def acquire_research_lock(session_id: str, ttl: int = 3600) -> tuple[bool, str]:
    """尝试获取会话研究锁。

    返回 (acquired, token)：
      (True,  token) → 拿到锁，研究结束后必须释放
      (False, "")    → 该会话正在研究中，应拒绝本次请求
      (True,  "")    → Redis 不可用 → 放行（不持锁，无需释放）
                      可用性优先：Redis 故障不应让整个研究功能不可用；
                      最坏后果是并发研究导致报告错乱，而非数据损坏。
    """
    from .search import _get_redis

    r = _get_redis()
    if r is None:
        log.warning("Redis 不可用，跳过会话研究锁（放行）")
        return True, ""

    token = uuid.uuid4().hex
    try:
        ok = await r.set(f"lock:research:{session_id}", token, nx=True, ex=ttl)
        if ok:
            log.info(f"获取研究锁: session={session_id}")
            return True, token
        return False, ""
    except Exception as e:
        log.warning(f"获取研究锁失败，放行: {e}")
        return True, ""


async def release_research_lock(session_id: str, token: str) -> None:
    """用 Lua 原子释放：只删自己持有的锁（value 匹配），防止误删他人锁。

    即使处于降级冷却期也强制尝试一次（_get_redis(force=True)）——
    否则 Redis 抖动 60s 后研究正常跑完却不释放锁，该会话会在锁 TTL（1 小时）
    内被「该会话正在研究中」持续拒绝。
    """
    if not token:
        return
    from .search import _get_redis

    r = _get_redis(force=True)
    if r is None:
        log.warning(f"Redis 不可用，研究锁未能释放（将等 TTL 自动过期）: session={session_id}")
        return
    try:
        await r.eval(_LOCK_RELEASE_LUA, 1, f"lock:research:{session_id}", token)
        log.info(f"释放研究锁: session={session_id}")
    except Exception as e:
        log.warning(f"释放研究锁失败: {e}")


async def renew_research_lock_loop(session_id: str, token: str, ttl: int = 3600,
                                   interval: int | None = None) -> None:
    """后台续期会话研究锁，直到被取消。

    锁 TTL 固定 1 小时，但 L3/L4 深研究可能跑更久（多路并行 + 多轮反思），
    一旦 TTL 到期锁自动消失，同一会话的第二个请求就能趁虚而入 —— 报告错乱、
    token 翻倍，正是锁要防的事。故每 TTL/3 续一次。

    续期用 Lua 校验 token，只续自己持有的锁，避免把别人的锁续命。
    取不到 Redis 客户端时跳过本次（降级期不续期，恢复后自动继续）。
    """
    if interval is None:
        interval = max(30, ttl // 3)
    from .search import _get_redis

    while True:
        await aio.sleep(interval)
        r = _get_redis()
        if r is None:
            continue
        try:
            ok = await r.eval(_LOCK_RENEW_LUA, 1, f"lock:research:{session_id}", token, ttl)
            if not ok:
                # 锁已过期或已易主 → 停止续期（不再持有锁，也不该继续「保护」）
                log.warning(f"锁续期失败（锁已不属于本任务，停止续期）: session={session_id}")
                return
            log.debug(f"锁续期成功: session={session_id}")
        except Exception as e:
            log.warning(f"锁续期异常（下次重试）: {e}")


# ============================================================
# 用量统计 + 限流（多用户高频场景）
# ============================================================

RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))  # 每用户每分钟研究次数上限


async def check_rate_limit(user_id: str) -> tuple[bool, int]:
    """固定窗口限流（INCR + EXPIRE）。

    返回 (allowed, remaining)：
      - allowed=False → 超过该用户每分钟上限，应拒绝本次请求
      - remaining=-1  → Redis 不可用（放行，不做限流；可用性优先）
    """
    from .search import _get_redis

    r = _get_redis()
    if r is None:
        return True, -1
    try:
        bucket = int(time.time()) // 60          # 按分钟分桶
        key = f"rate:research:{user_id}:{bucket}"
        # INCR + EXPIRE 放同一 pipeline（MULTI/EXEC）原子执行：
        # 原来分两步，若 INCR 成功后 EXPIRE 失败，key 无 TTL 会永久残留 → 用户被永久限流
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 120)                    # 跨桶安全：2 分钟后自动清理
        cnt, _ = await pipe.execute()
        remaining = RATE_LIMIT_PER_MINUTE - cnt
        return cnt <= RATE_LIMIT_PER_MINUTE, max(0, remaining)
    except Exception as e:
        log.warning(f"限流检查失败，放行: {e}")
        return True, -1


async def record_usage(user_id: str, question: str = "") -> None:
    """累计用量：用户研究总次数（永久计数）+ 热门主题榜（ZSet，保留 Top 100）。"""
    from .search import _get_redis

    r = _get_redis()
    if r is None:
        return
    try:
        await r.incr(f"usage:research:{user_id}")
        if question:
            await r.zincrby("hot:topics", 1, question[:50])
            await r.zremrangebyrank("hot:topics", 0, -101)   # 只保留 Top 100，防无限增长
            await r.expire("hot:topics", 30 * 24 * 3600)     # 长期不用自动回收
    except Exception as e:
        log.warning(f"用量统计失败（不影响主流程）: {e}")

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
    kb_enabled: bool,
    user_id: str,
    rag_doc_ids: list[str],
    search_mode: str,
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

    # ---- 先创建 TraceRun（必须早于澄清）----
    # 澄清本身是一次真实的 LLM 调用（有 token 成本）；原来它创建在 TraceRun 之前，
    # 导致澄清调用的 token 不进 trace，成本归因少一块。放在 try 外还顺带避免了
    # 「澄清阶段抛异常时 except 里 trace 尚未定义」。
    reports_dir = Path(__file__).parent.parent.parent / "reports" / datetime.now().strftime("%Y%m%d_%H%M%S")
    reports_dir.mkdir(parents=True, exist_ok=True)
    trace = TraceRun(
        question=question, output_dir=str(reports_dir),
        level=level, model=config.llm_model, search_mode=search_mode,
    )
    await trace.__aenter__()

    try:
        # ---- 澄清（Level 2/3/4 默认开启） ----
        if level != 1:
            full_context = context + "\n\n---\n用户最新消息：" + question if context else question
            clarify = ClarifyHelper(trace=trace)
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
                await trace.__aexit__(None, None, None)   # 澄清轮也要落 trace（记录澄清调用成本）
                return
            on_progress({"step": "planned", "message": f"需求明确: {check.get('summary', '')}"})

        # ---- 创建 Agent（走 agent.py 的真 Agent） ----
        # 从 search_mode 推导 kb_enabled（前端不再传 kb_enabled 旧字段）
        _kb = kb_enabled or (search_mode in ("hybrid", "rag_only"))
        _kw = dict(on_progress=on_progress, kb_enabled=_kb, user_id=user_id, rag_doc_ids=rag_doc_ids, search_mode=search_mode, trace=trace)
        if level == 1:
            agent = FastLevel1Agent(**_kw)
        elif level == 3:
            agent = Level3Agent(**_kw)
        elif level == 4:
            agent = Level4Agent(**_kw)
        else:
            agent = Level2Agent(**_kw)

        on_progress({"step": "planning", "message": f"Level {level} Agent 启动..."})

        # ---- 后台跑 Agent，前台推 SSE ----
        # 拼接前后文传给 Agent
        full_question = question
        if context:
            full_question = f"对话历史：\n{context}\n\n当前问题：{question}"

        async def run_agent():
            try:
                result = await agent.run(full_question)
                token_summary = {
                    "llm_calls": trace._llm_calls,
                    "total_prompt_tokens": trace._total_prompt_tokens,
                    "total_completion_tokens": trace._total_completion_tokens,
                    "search_calls": trace._search_calls,
                    # 成本归因：prompt 缓存命中部分按折扣价计费，命中率决定真实成本。
                    # 随 done 事件上报 → 网关落到 sessions.token_usage(JSONB) → 前端/评测可直接看。
                    "cache_hit_tokens": trace._total_cache_hit_tokens,
                    "billable_prompt_tokens": trace._total_prompt_tokens - trace._total_cache_hit_tokens,
                    "cache_hit_rate": round(trace.cache_hit_rate, 4),
                }
                await queue.put({"type": "done", "report": result, "tokenUsage": token_summary})
            except aio.CancelledError:
                log.info("Agent 任务被取消，停止研究")
                raise  # 重新抛出让 task.cancel() 的 await 正常结束
            except Exception as e:
                log.exception("Agent 执行异常")
                await queue.put({"type": "error", "message": str(e), "traceback": traceback.format_exc()})

        task = aio.create_task(run_agent())

        try:
            # 从队列读取事件 → yield SSE
            while not task.done():
                try:
                    event = await aio.wait_for(queue.get(), timeout=0.1)
                except aio.TimeoutError:
                    continue

                if event.get("type") == "done":
                    done_data = {"report": event["report"], "language": language}
                    if event.get("tokenUsage"):
                        done_data["tokenUsage"] = event["tokenUsage"]
                    yield {"event": "done", "data": json.dumps(done_data)}
                    return
                elif event.get("type") == "error":
                    yield {"event": "error", "data": json.dumps({
                        "message": event["message"], "traceback": event.get("traceback", ""),
                    })}
                    return
                else:
                    yield {"event": "status", "data": json.dumps(event, ensure_ascii=False)}

            # Agent 结束后，取队列里剩余的事件
            while not queue.empty():
                event = queue.get_nowait()
                if event.get("type") == "done":
                    done_data = {"report": event["report"], "language": language}
                    if event.get("tokenUsage"):
                        done_data["tokenUsage"] = event["tokenUsage"]
                    yield {"event": "done", "data": json.dumps(done_data)}
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
        finally:
            # 客户端断开或生成器退出 → 取消 Agent 任务，停止消耗 token
            if not task.done():
                task.cancel()
                log.info("客户端断开，Agent 任务已取消")
                try:
                    await task
                except (aio.CancelledError, Exception):
                    pass
            # 确保 trace 被 flush（即使客户端断开也保存）
            await trace.__aexit__(None, None, None)

    except Exception as e:
        log.exception("run_agent_with_sse 异常")
        await trace.__aexit__(type(e), e, e.__traceback__)
        yield {"event": "error", "data": json.dumps({
            "message": str(e), "traceback": traceback.format_exc(),
        })}


# ============================================================
# 带会话锁的研究执行
# ============================================================

# 错误码 → HTTP 状态映射（同步端点用；SSE 端点只能靠 event=error + code 表达）
ERROR_HTTP_STATUS = {
    "session_locked": 409,      # 冲突：同会话已有研究在跑
    "rate_limited": 429,        # 限流：语义明确，前端可据此退避重试
}


async def _run_with_lock(req: "ResearchRequest", cancel: aio.Event) -> AsyncGenerator[dict, None]:
    """在会话研究锁保护下执行研究。

    同一会话并发研究 → 报告错乱 + 双倍 token，故第二个请求直接拒绝；
    不同会话/不同用户互不影响（锁粒度为 session_id）。

    执行顺序：① 会话锁 → ② 限流 → ③ 用量统计 → ④ 执行。
    限流放在锁之后：被锁拒绝的请求不应消耗配额（否则同会话重复点击会刷爆限额）。
    """
    # ① 会话研究锁（拿不到直接拒绝，不计配额）
    session_id = (req.session_id or "").strip()
    lock_token = ""
    renew_task: aio.Task | None = None
    if session_id:
        acquired, lock_token = await acquire_research_lock(session_id)
        if not acquired:
            yield {"event": "error", "data": json.dumps({
                "message": "该会话正在研究中，请等待完成后再试",
                "code": "session_locked",
            }, ensure_ascii=False)}
            return
        if lock_token:
            # 长研究（>1h）自动续期，否则锁提前过期 → 并发研究趁虚而入
            renew_task = aio.create_task(renew_research_lock_loop(session_id, lock_token))
    try:
        # ② 限流：每用户每分钟上限（Redis 不可用时放行）
        allowed, remaining = await check_rate_limit(req.user_id)
        if not allowed:
            yield {"event": "error", "data": json.dumps({
                "message": f"请求过于频繁，每分钟最多 {RATE_LIMIT_PER_MINUTE} 次研究，请稍后再试",
                "code": "rate_limited",
                "remaining": 0,
                "retry_after": 60,
            }, ensure_ascii=False)}
            return
        if remaining >= 0:
            # 剩余额度回传前端：多用户高频场景下，用户能提前知道「这是本分钟最后一次」
            yield {"event": "status", "data": json.dumps({
                "step": "quota",
                "message": f"本分钟剩余研究额度 {remaining} 次",
                "remaining": remaining,
            }, ensure_ascii=False)}

        # ③ 用量统计：真正开始研究才计数（含热门主题榜）
        await record_usage(req.user_id, req.question)

        async for event in run_agent_with_sse(
            question=req.question, level=req.level,
            max_rounds=req.max_rounds, language=req.language,
            context=req.context, kb_enabled=req.kb_enabled, user_id=req.user_id,
            rag_doc_ids=req.rag_doc_ids, search_mode=req.search_mode, cancel=cancel,
        ):
            yield event
    finally:
        if renew_task is not None:
            renew_task.cancel()
            try:
                await renew_task
            except (aio.CancelledError, Exception):
                pass
        if lock_token:
            await release_research_lock(session_id, lock_token)


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
    result = []
    async for event in _run_with_lock(req, cancel):
        if event["event"] == "done":
            result.append(json.loads(event["data"]))
        elif event["event"] == "error":
            detail = json.loads(event["data"])
            # 按错误码映射状态码：原来一律 500，调用方无法区分「该退避重试」和「真故障」
            status = ERROR_HTTP_STATUS.get(detail.get("code"), 500)
            headers = None
            if status == 429:
                headers = {"Retry-After": str(detail.get("retry_after", 60))}
            raise HTTPException(status_code=status, detail=detail, headers=headers)
    if not result:
        raise HTTPException(status_code=500, detail="无结果")
    return JSONResponse(content=result[0])


@app.post("/research/stream")
async def research_stream(req: ResearchRequest):
    """SSE 流式接口。"""
    cancel = aio.Event()
    return EventSourceResponse(_run_with_lock(req, cancel))


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


# ============================================================
# 上下文压缩端点
# ============================================================

COMPRESS_HISTORY_PROMPT = """你是一个对话历史压缩助手。你会收到一段较长的对话历史，需要将其压缩成简洁的摘要。

规则：
1. 保留所有关键事实、数据、约束条件（如用户身份、预算、偏好）
2. 保留用户明确提出的研究方向和重点关注领域
3. 压缩掉具体的搜索细节和冗长的报告正文（那些已经存在报告列中了）
4. 保留时间顺序，标注每次研究的主题
5. 篇幅控制在原始文本的 30% 以内
6. 用中文输出"""


@app.post("/compress")
async def compress_history(req: dict):
    """压缩对话历史，防止上下文溢出。接收旧消息列表，返回压缩摘要。"""
    messages = req.get("messages", [])
    if not messages or len(messages) < 5:
        return {"summary": ""}

    try:
        llm = LLMClient()
        raw = "\n".join(str(m) for m in messages)
        summary = await llm.chat(
            system_prompt=COMPRESS_HISTORY_PROMPT,
            user_message=f"请压缩以下对话历史（保留关键信息，丢弃搜索细节和冗长报告）：\n\n{raw}",
        )
        return {"summary": summary}
    except Exception as e:
        log.error(f"压缩历史失败: {e}")
        return {"summary": ""}


# ============================================================
# 知识库接口
# ============================================================

import os
import tempfile
from pathlib import Path

@app.post("/kb/upload")
async def kb_upload(file: UploadFile, user_id: str = "default"):
    """上传文档到用户的知识库。支持 PDF/TXT/MD。"""
    suffix = Path(file.filename or "unknown").suffix.lower()
    if suffix not in (".pdf", ".txt", ".md", ".docx"):
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {suffix}")

    try:
        # 存临时文件
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        # 入库（用原始文件名）— ingest 含 embedding 推理，扔进线程池避免阻塞
        result = await aio.to_thread(kb.ingest_v2, tmp_path, user_id=user_id, doc_id=file.filename)
        os.unlink(tmp_path)

        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("message", "未知错误"))

        return result
    except HTTPException:
        raise
    except Exception as e:
        log.exception("kb_upload 失败")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/kb/files")
async def kb_files(user_id: str = "default"):
    """列出用户已上传的文档。"""
    return {"files": kb.list_docs(user_id)}


@app.delete("/kb/files/{doc_id}")
async def kb_delete(doc_id: str, user_id: str = "default"):
    """删除用户知识库中的指定文档。"""
    result = kb.delete_doc(doc_id, user_id=user_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message", "未知错误"))
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("researcher.server:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
