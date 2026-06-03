"""LLM 客户端 —— 支持 DeepSeek 和 OpenAI。"""

import json
from typing import Any

from openai import OpenAI

from .config import config


class LLMClient:
    """封装 LLM 调用，支持普通对话、结构化输出、工具调用。"""

    def __init__(self):
        self.client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
        )
        self.model = config.llm_model

    # ============================================================
    # Level 1：普通对话
    # ============================================================

    def chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.1,
    ) -> str:
        """发送一条 system + user 消息，返回文本回复。"""
        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=temperature,
            extra_body=(
                {"thinking": {"type": "disabled"}}
                if config.llm_provider == "deepseek"
                else None
            ),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return resp.choices[0].message.content or ""

    # ============================================================
    # Level 2：Agent 循环 —— 工具调用
    # ============================================================

    def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
    ) -> Any:
        """发送多轮对话 + 工具定义，返回 OpenAI 消息对象。"""
        return self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            tools=tools,
            extra_body=(
                {"thinking": {"type": "disabled"}}
                if config.llm_provider == "deepseek"
                else None
            ),
        ).choices[0].message

    # ============================================================
    # Level 3+：结构化输出
    # ============================================================

    def structured_output(self, system_prompt: str, user_message: str, schema: dict) -> dict:
        """强制 LLM 以指定 JSON 结构返回结果。"""
        # DeepSeek 不支持 json_schema，用 json_object + prompt 里写 schema
        schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
        prompt_with_schema = (
            f"{user_message}\n\n"
            f"请严格按以下 JSON Schema 返回，只返回 JSON，不要加任何解释或 markdown 标记：\n"
            f"```json\n{schema_text}\n```"
        )

        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            extra_body=(
                {"thinking": {"type": "disabled"}}
                if config.llm_provider == "deepseek"
                else None
            ),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_with_schema},
            ],
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content or "{}")
