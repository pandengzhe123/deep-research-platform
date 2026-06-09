"""LLM 客户端 —— 支持 DeepSeek 和 OpenAI，含自动重试。"""

import json
import time
from typing import Any

from openai import APIError, APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from .config import config


class LLMClient:
    """封装 LLM 调用，支持普通对话、结构化输出、工具调用。"""

    def __init__(self):
        self.client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            timeout=300.0,    # 5 分钟——Level 4 汇总大报告需要时间
            max_retries=0,
        )
        self.model = config.llm_model

    # ============================================================
    # 重试包装器
    # ============================================================

    def _call_with_retry(self, fn, max_retries: int = 3):
        """调用 LLM API，429/5xx/网络错误自动重试（指数退避）。

        不重试: 401（Key 错）、403（权限）、400（请求格式错）。
        """
        last_error = None
        for attempt in range(max_retries):
            try:
                return fn()
            except RateLimitError as e:
                # 429 —— 限流，等久一点
                last_error = e
                wait = (attempt + 1) * 5
                print(f"  ⚠️ LLM 限流，{wait}s 后重试（{attempt+1}/{max_retries}）...")
                time.sleep(wait)
            except APITimeoutError as e:
                # 超时——不重试（不是因为网络抖动，是任务太重了）
                raise  # 直接抛出，让上层 Agent 处理
            except APIConnectionError as e:
                # 网络错误（非超时）——重试
                last_error = e
                wait = 2 ** (attempt + 1)
                print(f"  ⚠️ LLM 网络错误，{wait}s 后重试（{attempt+1}/{max_retries}）...")
                time.sleep(wait)
            except APIError as e:
                # 5xx 服务端错误才重试，4xx 直接抛
                if e.status_code and e.status_code >= 500:
                    last_error = e
                    wait = 2 ** (attempt + 1)
                    print(f"  ⚠️ LLM 服务端错误 {e.status_code}，{wait}s 后重试（{attempt+1}/{max_retries}）...")
                    time.sleep(wait)
                else:
                    raise
        raise last_error or RuntimeError("LLM 调用失败")

    # ============================================================
    # 对外方法
    # ============================================================

    def _extra_body(self):
        return {"thinking": {"type": "disabled"}} if config.llm_provider == "deepseek" else None

    def chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.1,
    ) -> str:
        """发送一条 system + user 消息，返回文本回复。"""
        def _call():
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=temperature,
                extra_body=self._extra_body(),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            return resp.choices[0].message.content or ""

        return self._call_with_retry(_call)

    def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
    ) -> Any:
        """发送多轮对话 + 工具定义，返回 OpenAI 消息对象。"""
        def _call():
            return self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system_prompt}] + messages,
                tools=tools,
                extra_body=self._extra_body(),
            ).choices[0].message

        return self._call_with_retry(_call)

    def structured_output(self, system_prompt: str, user_message: str, schema: dict) -> dict:
        """强制 LLM 以指定 JSON 结构返回结果。"""
        schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
        prompt_with_schema = (
            f"{user_message}\n\n"
            f"请严格按以下 JSON Schema 返回，只返回 JSON，不要加任何解释或 markdown 标记：\n"
            f"```json\n{schema_text}\n```"
        )

        def _call():
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                extra_body=self._extra_body(),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt_with_schema},
                ],
                response_format={"type": "json_object"},
            )
            return json.loads(resp.choices[0].message.content or "{}")

        return self._call_with_retry(_call)
