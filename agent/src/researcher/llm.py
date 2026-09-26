"""LLM 客户端 —— 支持 DeepSeek 和 OpenAI，含自动重试。"""

import asyncio
import json
import logging
import time
from typing import Any

from openai import AsyncOpenAI, APIError, APIConnectionError, APITimeoutError, RateLimitError

from .config import config

log = logging.getLogger(__name__)

# finish_reason 取值：
#   "stop"          正常收尾
#   "length"        被 max_tokens 截断 —— 内容不完整，必须显式处理
#   "tool_calls"    模型请求调用工具
#   "content_filter" 被内容策略截断
FINISH_LENGTH = "length"


def finish_reason_of(resp) -> str:
    """安全取 finish_reason（不同后端或异常响应下可能缺失）。"""
    try:
        return resp.choices[0].finish_reason or ""
    except Exception:
        return ""


def is_truncated(finish_reason: str) -> bool:
    """输出是否被长度上限截断。

    这个判断此前全项目没有任何地方做 —— 于是「报告写到一半被硬切」在 trace 里和
    「正常写完」长得完全一样（success=true、error 为空、token 数看着也正常），
    一直到用户自己发现文章断在半句为止。信号一直都在，只是没人接。
    """
    return finish_reason == FINISH_LENGTH


def _warn_if_truncated(finish_reason: str, method: str, purpose: str) -> None:
    if is_truncated(finish_reason):
        log.warning(
            "LLM 输出被截断（finish_reason=length）：method=%s purpose=%s —— "
            "本次返回内容不完整，调用方需显式处理",
            method, (purpose or "")[:60],
        )


class LLMClient:
    """封装 LLM 调用，全异步，支持并发请求。"""

    def __init__(self):
        self.trace = None  # TraceRun 实例，由 Agent 在构造后设置
        self.client = AsyncOpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            timeout=300.0,    # 5 分钟——Level 4 汇总大报告需要时间
            max_retries=0,
        )
        self.model = config.llm_model

    # ============================================================
    # 异步重试包装器
    # ============================================================

    async def _call_with_retry(self, fn, max_retries: int = 3):
        """调用 LLM API，429/5xx/网络错误自动重试（指数退避）。

        返回 (result, retries)：result 是 fn() 的结果，retries 是实际重试次数。
        不重试: 401（Key 错）、403（权限）、400（请求格式错）。
        """
        last_error = None
        retries = 0
        for attempt in range(max_retries):
            try:
                return await fn(), retries
            except RateLimitError as e:
                # 429 —— 限流，等久一点
                last_error = e
                retries += 1
                wait = (attempt + 1) * 5
                print(f"  ⚠️ LLM 限流，{wait}s 后重试（{attempt+1}/{max_retries}）...")
                await asyncio.sleep(wait)
            except APITimeoutError as e:
                # 超时——不重试（不是因为网络抖动，是任务太重了）
                raise  # 直接抛出，让上层 Agent 处理
            except APIConnectionError as e:
                # 网络错误（非超时）——重试
                last_error = e
                retries += 1
                wait = 2 ** (attempt + 1)
                print(f"  ⚠️ LLM 网络错误，{wait}s 后重试（{attempt+1}/{max_retries}）...")
                await asyncio.sleep(wait)
            except APIError as e:
                # 5xx 服务端错误才重试，4xx 直接抛
                if e.status_code and e.status_code >= 500:
                    last_error = e
                    retries += 1
                    wait = 2 ** (attempt + 1)
                    print(f"  ⚠️ LLM 服务端错误 {e.status_code}，{wait}s 后重试（{attempt+1}/{max_retries}）...")
                    await asyncio.sleep(wait)
                else:
                    raise
        raise last_error or RuntimeError("LLM 调用失败")

    # ============================================================
    # 对外方法
    # ============================================================

    def _extra_body(self):
        return {"thinking": {"type": "disabled"}} if config.llm_provider == "deepseek" else None

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        return_finish_reason: bool = False,
    ):
        """发送一条 system + user 消息，返回文本回复。

        return_finish_reason=True 时返回 (文本, finish_reason)，让调用方（报告生成）
        能判断本次输出是否被截断；默认仍返回纯文本，既有调用方不受影响。
        """
        t0 = time.time()
        async def _call():
            kwargs: dict = dict(
                model=self.model,
                temperature=temperature,
                extra_body=self._extra_body(),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            resp = await self.client.chat.completions.create(**kwargs)
            return resp, resp.choices[0].message.content or ""

        (resp, result), retries = await self._call_with_retry(_call)
        finish = finish_reason_of(resp)
        _warn_if_truncated(finish, "chat", system_prompt)
        if self.trace:
            await self.trace.record_llm(
                method="chat",
                model=getattr(resp, "model", self.model),
                usage=resp.usage.to_dict() if getattr(resp, "usage", None) else None,
                duration_ms=int((time.time() - t0) * 1000),
                request_id=getattr(resp, "id", ""),
                success=True,
                purpose=system_prompt[:120],
                retries=retries,
                finish_reason=finish,
            )
        if return_finish_reason:
            return result, finish
        return result

    async def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
    ) -> Any:
        """发送多轮对话 + 工具定义，返回 OpenAI 消息对象。"""
        t0 = time.time()
        async def _call():
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system_prompt}] + messages,
                tools=tools,
                extra_body=self._extra_body(),
            )
            return resp, resp.choices[0].message

        (resp, msg), retries = await self._call_with_retry(_call)
        finish = finish_reason_of(resp)
        # 工具循环里被截断同样致命：模型话说一半被切，tool_calls 可能只声明了一部分，
        # 「tool_call ↔ tool 1:1 配对」这个不变量当场被破坏。
        _warn_if_truncated(finish, "chat_with_tools", system_prompt)
        if self.trace:
            await self.trace.record_llm(
                method="chat_with_tools",
                model=getattr(resp, "model", self.model),
                usage=resp.usage.to_dict() if getattr(resp, "usage", None) else None,
                duration_ms=int((time.time() - t0) * 1000),
                request_id=getattr(resp, "id", ""),
                success=True,
                purpose=system_prompt[:120],
                retries=retries,
                finish_reason=finish,
            )
        return msg

    async def structured_output(self, system_prompt: str, user_message: str, schema: dict) -> dict:
        """强制 LLM 以指定 JSON 结构返回结果。"""
        schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
        prompt_with_schema = (
            f"{user_message}\n\n"
            f"请严格按以下 JSON Schema 返回，只返回 JSON，不要加任何解释或 markdown 标记：\n"
            f"```json\n{schema_text}\n```"
        )

        t0 = time.time()
        async def _call():
            resp = await self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                extra_body=self._extra_body(),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt_with_schema},
                ],
                response_format={"type": "json_object"},
            )
            return resp, json.loads(resp.choices[0].message.content or "{}")

        (resp, result), retries = await self._call_with_retry(_call)
        finish = finish_reason_of(resp)
        _warn_if_truncated(finish, "structured_output", system_prompt)
        if self.trace:
            await self.trace.record_llm(
                method="structured_output",
                model=getattr(resp, "model", self.model),
                usage=resp.usage.to_dict() if getattr(resp, "usage", None) else None,
                duration_ms=int((time.time() - t0) * 1000),
                request_id=getattr(resp, "id", ""),
                success=True,
                purpose=system_prompt[:120],
                retries=retries,
                finish_reason=finish,
            )
        return result
