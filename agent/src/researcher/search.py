"""搜索工具 —— 封装 Tavily API + 网页内容抓取 + LLM 摘要。"""

import asyncio

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from tavily import AsyncTavilyClient

from .config import config
from .llm import LLMClient

SUMMARY_PROMPT = """你是一个网页内容摘要助手。请对以下网页内容做摘要，保留关键信息。

要求：
- 保留核心事实、数据、时间、人名
- 保留原文中的关键引述
- 长度控制在原文的 25-30%
- 用中文写摘要

网页内容：
{content}

请返回 JSON：
{{"summary": "摘要内容", "key_facts": ["事实1", "事实2", "事实3"]}}
"""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "key_facts": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["summary", "key_facts"],
    "additionalProperties": False,
}


class SearchTool:
    """封装搜索 + 网页抓取 + LLM 摘要的完整流水线。"""

    def __init__(self):
        self.tavily = AsyncTavilyClient(api_key=config.tavily_api_key)
        self.llm = LLMClient()

    async def _safe_tavily_search(self, query: str, max_results: int, include_raw: bool, retries: int = 2):
        """带重试的 Tavily 搜索。"""
        for attempt in range(retries):
            try:
                return await self.tavily.search(query, max_results=max_results, include_raw_content=include_raw)
            except Exception:
                if attempt < retries - 1:
                    await asyncio.sleep(2)
        return {"results": [], "query": query}

    async def _ddg_search(self, query: str, max_results: int = 5) -> dict:
        """DuckDuckGo 搜索（免费、不需要 API Key），返回 Tavily 兼容格式。"""
        from ddgs import DDGS
        try:
            results = await asyncio.to_thread(
                lambda: list(DDGS().text(query, max_results=max_results))
            )
        except Exception:
            return {"results": [], "query": query}

        tavily_format = []
        for r in results:
            tavily_format.append({
                "url": r.get("href", ""),
                "title": r.get("title", ""),
                "content": r.get("body", ""),
                "raw_content": None,
            })
        return {"results": tavily_format, "query": query}

    async def _do_search(self, query: str, max_results: int, include_raw: bool) -> dict:
        """搜索：Tavily 优先，失败自动降级到 DuckDuckGo。"""
        result = await self._safe_tavily_search(query, max_results, include_raw)
        if result.get("results"):
            return result
        print(f"  ⚠️ Tavily 无结果，降级到 DuckDuckGo: {query[:50]}...")
        return await self._ddg_search(query, max_results)

    async def search(
        self,
        queries: list[str],
        max_results: int = 5,
    ) -> str:
        """执行搜索、抓取网页、摘要，返回格式化结果。"""
        # 1. 并行搜索（Tavily 优先，失败自动降级 DDG）
        tasks = [
            self._do_search(q, max_results, True)
            for q in queries
        ]
        all_results = await asyncio.gather(*tasks)

        # 2. 按 URL 去重
        seen: dict[str, dict] = {}
        for response in all_results:
            for r in response.get("results", []):
                url = r.get("url", "")
                if url and url not in seen:
                    seen[url] = r

        # 3. 对每个网页抓取 + 摘要（并行）
        summary_tasks = [
            self._fetch_and_summarize(url, r)
            for url, r in seen.items()
        ]
        summaries = await asyncio.gather(*summary_tasks)

        # 4. 格式化输出
        output_parts = ["# 搜索结果\n"]
        for i, s in enumerate(summaries):
            if s is None:
                continue
            output_parts.append(f"\n--- 来源 {i+1}: {s['title']} ---")
            output_parts.append(f"URL: {s['url']}")
            output_parts.append(f"\n摘要:\n{s['summary']}")
            if s.get("key_facts"):
                output_parts.append(f"\n关键事实: {'; '.join(s['key_facts'])}")
            output_parts.append("\n" + "-" * 60)

        return "\n".join(output_parts) if len(output_parts) > 1 else "未找到相关结果。"

    async def search_fast(
        self,
        queries: list[str],
        max_results: int = 3,
    ) -> str:
        """快速搜索 —— 跳过 LLM 摘要，Tavily 优先，失败降级 DDG。"""
        tasks = [
            self._do_search(q, max_results, False)
            for q in queries
        ]
        all_results = await asyncio.gather(*tasks)

        # 2. 按 URL 去重
        seen: dict[str, dict] = {}
        for response in all_results:
            for r in response.get("results", []):
                url = r.get("url", "")
                if url and url not in seen:
                    seen[url] = r

        # 3. 直接用 Tavily 自带的摘要，不调 LLM
        output_parts = ["# 搜索结果\n"]
        for i, (url, r) in enumerate(seen.items()):
            title = r.get("title", url)
            content = r.get("content", "")
            if not content:
                continue
            output_parts.append(f"\n--- 来源 {i+1}: {title} ---")
            output_parts.append(f"URL: {url}")
            output_parts.append(f"\n{content}")
            output_parts.append("\n" + "-" * 60)

        return "\n".join(output_parts) if len(output_parts) > 1 else "未找到相关结果。"

    async def _fetch_and_summarize(self, url: str, result: dict) -> dict | None:
        """抓取网页内容并用 LLM 摘要。"""
        try:
            # 尝试用 Tavily 返回的 raw_content
            content = result.get("raw_content", "")
            if not content:
                content = await self._fetch_url(url)

            if not content or len(content) < 100:
                return {
                    "title": result.get("title", url),
                    "url": url,
                    "summary": result.get("content", "无内容"),
                    "key_facts": [],
                }

            # LLM 摘要
            truncated = content[: config.max_content_length]
            summary_data = self.llm.structured_output(
                system_prompt="你是网页内容摘要助手。",
                user_message=SUMMARY_PROMPT.format(content=truncated),
                schema=SUMMARY_SCHEMA,
            )
            return {
                "title": result.get("title", url),
                "url": url,
                "summary": summary_data.get("summary", ""),
                "key_facts": summary_data.get("key_facts", []),
            }
        except Exception:
            return None

    async def _fetch_url(self, url: str) -> str:
        """抓取网页 HTML 并转成 markdown 文本。"""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; DeepResearch/1.0)"},
                )
                soup = BeautifulSoup(resp.text, "html.parser")
                # 去掉 script/style
                for tag in soup(["script", "style", "nav", "footer"]):
                    tag.decompose()
                return md(str(soup.body or soup))
        except Exception:
            return ""
