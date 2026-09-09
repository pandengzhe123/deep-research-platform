"""搜索工具 —— 封装 Tavily API + 网页内容抓取 + LLM 摘要。"""

import asyncio
import json
import os
import time

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from tavily import AsyncTavilyClient

from .config import config
from .llm import LLMClient


# ============================================================
# Redis 客户端（模块级共享连接池 + 失败降级）
# ============================================================

_redis_client = None
_redis_disabled_until = 0.0     # 连接失败后的冷却截止时间戳


def _get_redis():
    """获取共享 Redis 客户端；不可用时返回 None（调用方降级为不缓存）。"""
    global _redis_client, _redis_disabled_until
    now = time.time()
    if now < _redis_disabled_until:
        return None
    if _redis_client is None:
        try:
            import redis.asyncio as aioredis
            _redis_client = aioredis.from_url(
                config.redis_url,
                decode_responses=True,
                socket_timeout=1,
                socket_connect_timeout=1,
                max_connections=50,
            )
        except Exception as e:
            print(f"  ⚠️ Redis 初始化失败，搜索缓存降级为不缓存: {e}")
            _redis_disabled_until = now + 60
            return None
    return _redis_client


def _mark_redis_failure(msg: str) -> None:
    """Redis 操作失败 → 冷却 60 秒后重试，避免每条查询都刷错误日志。"""
    global _redis_disabled_until
    print(f"  ⚠️ Redis {msg}，搜索缓存降级为不缓存（60s 后重试）")
    _redis_disabled_until = time.time() + 60


class SearchTool:
    """封装搜索 + 网页抓取 + LLM 摘要的完整流水线。"""

    def __init__(self, on_progress=None):
        self.tavily = AsyncTavilyClient(api_key=config.tavily_api_key)
        self.llm = LLMClient()
        self.trace = None  # TraceRun 实例，由 Agent 在构造后设置
        self._seen_urls: set[str] = set()  # 跨轮 URL 去重，避免重复摘要
        self._cache_ttl = int(os.getenv("SEARCH_CACHE_TTL", "300"))  # 缓存秒数，默认 5 分钟
        self._cache_hits = 0
        self._cache_misses = 0
        self.emit = on_progress or (lambda e: None)
        from ddgs import DDGS
        self._ddgs = DDGS()
        self._http = httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0 (compatible; DeepResearch/1.0)"})

    async def _safe_tavily_search(self, query: str, max_results: int, include_raw: bool, retries: int = 2):
        """带重试的 Tavily 搜索。4xx 不重试（Key 错/权限等永久性错误）。"""
        for attempt in range(retries):
            try:
                return await self.tavily.search(query, max_results=max_results, include_raw_content=include_raw)
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:  # 4xx → 永久错误，不重试
                    print(f"  ⚠️ Tavily API 错误 {e.response.status_code}，不重试")
                    return {"results": [], "query": query}
                if attempt < retries - 1:
                    await asyncio.sleep(2)
            except Exception:
                if attempt < retries - 1:
                    await asyncio.sleep(2)
        return {"results": [], "query": query}

    async def _ddg_search(self, query: str, max_results: int = 5) -> dict:
        """DuckDuckGo 搜索（免费、不需要 API Key），返回 Tavily 兼容格式。"""
        try:
            results = await asyncio.to_thread(
                lambda: list(self._ddgs.text(query, max_results=max_results))
            )
        except Exception:
            print(f"  ⚠️ DuckDuckGo 搜索失败: {query[:50]}...")
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

    async def _cache_get(self, key: str) -> dict | None:
        """读 Redis 缓存；不可用或读失败 → None（调用方降级直查）。"""
        r = _get_redis()
        if r is None:
            return None
        try:
            raw = await r.get(key)
            return json.loads(raw) if raw else None
        except Exception as e:
            _mark_redis_failure(f"读失败: {e}")
            return None

    async def _cache_set(self, key: str, value: dict) -> None:
        """写 Redis 缓存（SET key val EX ttl，服务端负责过期）；不可用 → 静默跳过。"""
        r = _get_redis()
        if r is None:
            return
        try:
            await r.set(key, json.dumps(value, ensure_ascii=False), ex=self._cache_ttl)
        except Exception as e:
            _mark_redis_failure(f"写失败: {e}")

    async def _do_search(self, query: str, max_results: int, include_raw: bool) -> dict:
        """搜索：先查 Redis 缓存（EX 自动过期），Tavily 优先，失败自动降级到 DuckDuckGo。"""
        cache_key = f"search:{query.strip().lower()}:{max_results}"

        cached = await self._cache_get(cache_key)
        if cached is not None:
            self._cache_hits += 1
            print(f"    缓存命中: {query[:40]}... (命中率 {self._cache_hits}/{self._cache_hits + self._cache_misses})")
            return cached

        # 未命中缓存，实际搜索
        self._cache_misses += 1
        fallback_used = False
        result = await self._safe_tavily_search(query, max_results, include_raw)
        if not result.get("results"):
            print(f"  ⚠️ Tavily 无结果，降级到 DuckDuckGo: {query[:50]}...")
            result = await self._ddg_search(query, max_results)
            fallback_used = True  # 记录降级发生（供恢复率评测）

        # 存入 Redis 缓存（TTL 由 Redis 服务端管理，无需手动清理过期项）
        if result.get("results"):
            await self._cache_set(cache_key, result)
        result["fallback_used"] = fallback_used
        return result

    def get_cache_stats(self) -> dict:
        """返回缓存命中率统计。"""
        total = self._cache_hits + self._cache_misses
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "total": total,
            "hit_rate": f"{self._cache_hits / total:.1%}" if total > 0 else "N/A",
            "ttl_seconds": self._cache_ttl,
            "backend": "redis" if _get_redis() is not None else "disabled",
        }

    async def search(
        self,
        queries: list[str],
        max_results: int = 5,
    ) -> str:
        """执行搜索、抓取网页、摘要，返回格式化结果。"""
        t0 = time.time()
        # 1. 并行搜索（Tavily 优先，失败自动降级 DDG）
        tasks = [
            self._do_search(q, max_results, True)
            for q in queries
        ]
        all_results = await asyncio.gather(*tasks)

        # 2. 按 URL 去重（同轮内 + 跨轮）
        seen: dict[str, dict] = {}
        skipped = 0
        for response in all_results:
            for r in response.get("results", []):
                url = r.get("url", "")
                if not url:
                    continue
                if url in self._seen_urls or url in seen:
                    skipped += 1
                else:
                    seen[url] = r
        # 记录本轮新 URL，下一轮不再重复摘要
        self._seen_urls.update(seen.keys())
        if skipped:
            print(f"    跨轮去重：跳过 {skipped} 个已处理 URL，本轮新增 {len(seen)} 个")

        # 3. 并行抓取网页内容
        items = list(seen.items())
        fetch_tasks = [self._fetch_content(url, r) for url, r in items]
        contents = await asyncio.gather(*fetch_tasks)

        # 4. 批量 LLM 摘要（一次调用处理所有网页，大幅减少耗时）
        valid = [(url, r, c) for (url, r), c in zip(items, contents) if c]
        if not valid:
            if self.trace:
                await self.trace.record_search(
                    queries=queries, result_count=0, deduped_count=skipped,
                    total_duration_ms=int((time.time() - t0) * 1000),
                    success=True,
                )
            return "未找到相关结果。"

        summaries = await self._batch_summarize(valid)

        # 5. 格式化输出
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

        result = "\n".join(output_parts) if len(output_parts) > 1 else "未找到相关结果。"
        # 汇总本轮是否有降级发生（供恢复率评测）
        fallback_used = any(r.get("fallback_used") for r in all_results)
        if self.trace:
            await self.trace.record_search(
                queries=queries,
                result_count=len(summaries),
                deduped_count=skipped,
                total_duration_ms=int((time.time() - t0) * 1000),
                success=True,
                fallback_used=fallback_used,
            )
        return result

    async def search_fast(
        self,
        queries: list[str],
        max_results: int = 3,
    ) -> str:
        """快速搜索 —— 跳过 LLM 摘要，Tavily 优先，失败降级 DDG。"""
        t0 = time.time()
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

        result = "\n".join(output_parts) if len(output_parts) > 1 else "未找到相关结果。"
        # 汇总本轮是否有降级发生（与 search() 一致，供恢复率评测）
        fallback_used = any(r.get("fallback_used") for r in all_results)
        if self.trace:
            await self.trace.record_search(
                queries=queries,
                result_count=len(seen),
                deduped_count=0,
                total_duration_ms=int((time.time() - t0) * 1000),
                success=True,
                fallback_used=fallback_used,
            )
        return result

    async def _fetch_content(self, url: str, result: dict) -> str | None:
        """抓取网页内容（优先用 Tavily raw_content，否则 HTTP 抓取）。"""
        try:
            content = result.get("raw_content", "")
            if not content:
                content = await self._fetch_url(url)
            return content if content and len(content) >= 100 else None
        except Exception:
            return None

    async def _batch_summarize(self, items: list[tuple[str, dict, str]]) -> list[dict]:
        """批量摘要：一次 LLM 调用处理多个网页，structured_output 强制 JSON。"""
        # 构建批量 prompt —— 对标 open_deep_research summarize_webpage_prompt
        parts = []
        for i, (url, r, content) in enumerate(items):
            truncated = content[:config.max_content_length]
            parts.append(
                f"<网页 {i+1}>\n"
                f"标题: {r.get('title', url)}\n"
                f"内容: {truncated}\n"
                f"</网页 {i+1}>"
            )

        BATCH_SUMMARY_PROMPT = (
            "你是一个搜索结果摘要助手。对以下每个网页内容分别做摘要。\n\n"
            "指导原则：\n"
            "- 识别并保留网页的核心主题或目的\n"
            "- 保留关键事实、统计数据、数据点和核心论点\n"
            "- 保留可信来源或专家的引用\n"
            "- 时间敏感或历史性的内容应保留时间线\n"
            "- 保留所有重要的日期、人名、地名\n"
            "- 根据不同内容类型调整摘要方式：\n"
            "  新闻：关注 who/what/when/where/why/how\n"
            "  科学内容：保留方法、结果、结论\n"
            "  观点文章：保留主论点和支撑论据\n"
            "  产品页面：保留关键特性、规格、独特卖点\n\n"
            "摘要应比原文短但信息完整——大致保留原文 25-30% 的关键信息量。\n\n"
            + "\n".join(parts)
        )

        BATCH_SUMMARY_SCHEMA = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string", "description": "网页内容摘要"},
                            "key_excerpts": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "原文中的关键引述或重要句子",
                                "maxItems": 5,
                            },
                        },
                        "required": ["summary"],
                    },
                },
            },
            "required": ["items"],
        }

        try:
            data = await self.llm.structured_output(
                system_prompt="你是网页内容摘要助手。只返回 JSON，不加任何解释。",
                user_message=BATCH_SUMMARY_PROMPT,
                schema=BATCH_SUMMARY_SCHEMA,
            )
            summaries = data.get("items", []) if isinstance(data, dict) else []
        except Exception:
            print(f"  ⚠️ 批量摘要失败，降级为原始 snippet——报告质量可能下降")
            self.emit({"step": "searching", "message": "部分搜索结果未能摘要，报告质量可能下降"})
            summaries = [{"summary": items[i][1].get("content", ""), "key_excerpts": []} for i in range(len(items))]

        # 组装返回结果
        results = []
        for i, (url, r, _) in enumerate(items):
            entry = summaries[i] if i < len(summaries) else {}
            results.append({
                "title": r.get("title", url),
                "url": url,
                "summary": entry.get("summary", r.get("content", "")),
                "key_facts": entry.get("key_excerpts", []),
            })
        return results

    async def _fetch_url(self, url: str) -> str:
        """抓取网页 HTML 并转成 markdown 文本。"""
        try:
            resp = await self._http.get(url)
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            return md(str(soup.body or soup))
        except Exception:
            return ""
