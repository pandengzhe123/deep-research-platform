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
_redis_client_loop = None       # client 绑定的事件循环（loop 变化时重建）
_redis_disabled_until = 0.0     # 连接失败后的冷却截止时间戳


def _safe_print(msg: str) -> None:
    """编码安全的日志输出 —— Windows GBK 控制台对 emoji 会抛 UnicodeEncodeError，
    而降级路径本身崩溃会冒泡打断研究流程。"""
    try:
        print(msg)
    except Exception:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


def _get_redis(force: bool = False):
    """获取共享 Redis 客户端；不可用时返回 None（调用方降级为不缓存）。

    两个关键点：
    1. 客户端与事件循环绑定 —— 进程内多次 asyncio.run() 时旧 client 会失效
       （RuntimeError: Event loop is closed），因此记录创建时的 loop，变化即重建。
    2. 失败后必须丢弃客户端 —— 否则冷却期结束仍返回同一个坏 client，Redis 再也回不来。

    force=True：绕过降级冷却强制尝试一次（用于「锁释放」这类必须尽力完成的操作）。
    """
    global _redis_client, _redis_client_loop, _redis_disabled_until
    now = time.time()
    if not force and now < _redis_disabled_until:
        return None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    # 事件循环变化（或已被丢弃）→ 丢弃旧客户端，下一步重建
    if _redis_client is not None and _redis_client_loop is not loop:
        _redis_client = None
        _redis_client_loop = None

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
            _redis_client_loop = loop
        except Exception as e:
            _safe_print(f"  [WARN] Redis 初始化失败，搜索缓存降级为不缓存: {e}")
            _redis_disabled_until = now + 60
            return None
    return _redis_client


def _mark_redis_failure(msg: str, connection_level: bool = True) -> None:
    """Redis 操作失败 → 丢弃客户端并冷却后重建。

    connection_level=True（连接/超时类）：全局冷却 60s（Redis 疑似不可用）。
    connection_level=False（单条数据类，如某条缓存 JSON 非法）：只丢弃客户端，
    不冷却 —— 避免一条脏数据让整个 Redis 层停摆 60 秒。
    """
    global _redis_client, _redis_client_loop, _redis_disabled_until
    _safe_print(f"  [WARN] Redis {msg}，降级（{'60s 后重建' if connection_level else '下次操作重建'}）")
    _redis_client = None            # 关键：丢弃坏客户端，否则永远返回同一个死 client
    _redis_client_loop = None
    if connection_level:
        _redis_disabled_until = time.time() + 60


def normalize_queries(queries) -> list[str]:
    """把 queries 归一成「非空字符串列表」。

    为什么需要（2026-09-10 实测事故）：工具参数由 LLM 生成，schema 声明是数组，
    但模型偶尔直接给一个字符串 —— `{"queries": "AI 未来十年发展趋势"}`。
    此时 `for q in queries` 会**逐字符迭代字符串**：一条查询被拆成 40 次单字符搜索，
    Tavily/DDG 对单字符必然失败 → 整轮搜索报销，还白烧 40 次请求、把额度打空。

    这里做类型归一 + 去空 + strip，让上层不必再关心模型给的是 str 还是 list。
    """
    if queries is None:
        return []
    if isinstance(queries, str):
        queries = [queries]
    elif not isinstance(queries, (list, tuple, set)):
        return []
    out: list[str] = []
    for q in queries:
        if isinstance(q, str) and q.strip():
            out.append(q.strip())
    return out


class SearchTool:
    """封装搜索 + 网页抓取 + LLM 摘要的完整流水线。"""

    def __init__(self, on_progress=None, dedup_scope: str | None = None):
        self.tavily = AsyncTavilyClient(api_key=config.tavily_api_key)
        self.llm = LLMClient()
        self.trace = None  # TraceRun 实例，由 Agent 在构造后设置
        self._seen_urls: set[str] = set()  # 本地 URL 去重（Redis 不可用时的降级路径）
        self._dedup_scope = dedup_scope  # 一次研究的共享去重域（L3/L4 下多个研究员共用）
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
        """读 Redis 缓存；不可用 / 内容非法 → None（调用方降级直查）。"""
        r = _get_redis()
        if r is None:
            return None
        try:
            raw = await r.get(key)
            if not raw:
                return None
            data = json.loads(raw)
            # 缓存可能被外部污染：只接受「含 results 的 dict」这一种合法形态，
            # 否则调用方 .get("results") 会抛 AttributeError
            if not isinstance(data, dict) or "results" not in data:
                _mark_redis_failure(f"缓存内容非法（{type(data).__name__}）", connection_level=False)
                return None
            return data
        except json.JSONDecodeError as e:
            _mark_redis_failure(f"缓存 JSON 解析失败: {e}", connection_level=False)
            return None
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
        # include_raw 必须进 key：它决定 raw_content 是否被抓取，两种结果的体积和
        # 内容都不同。若共用一个 key，先请求的形态会污染后请求的形态（摘要质量下降）
        cache_key = f"search:{query.strip().lower()}:{max_results}:{'raw' if include_raw else 'noraw'}"

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

    async def _filter_seen_urls(self, urls: list[str]) -> set[str]:
        """返回**尚未处理过**的 URL（只查询，不标记）。

        L3/L4 并行研究时各研究员是独立 SearchTool 实例，本地 set 不共享 →
        用 Redis Set 做全局去重。注意：这里只读，真正的标记在抓取+摘要成功后
        （`_mark_urls_processed`），否则一次抓取失败会让该 URL 在本次研究内
        被永久跳过，把暂时性失败变成信息永久丢失。
        """
        r = _get_redis()
        if r is None or not self._dedup_scope:
            return {u for u in urls if u not in self._seen_urls}
        try:
            key = f"seen_urls:{self._dedup_scope}"
            pipe = r.pipeline()                 # 批量 SISMEMBER，一次 RTT
            for u in urls:
                pipe.sismember(key, u)
            flags = await pipe.execute()
            return {u for u, seen in zip(urls, flags) if not seen}
        except Exception as e:
            _mark_redis_failure(f"URL 去重查询失败: {e}")
            return {u for u in urls if u not in self._seen_urls}

    async def _mark_urls_processed(self, urls: list[str]) -> None:
        """标记 URL 已成功处理（抓取 + 摘要成功后才调用）。"""
        if not urls:
            return
        r = _get_redis()
        if r is None or not self._dedup_scope:
            self._seen_urls.update(urls)
            return
        try:
            key = f"seen_urls:{self._dedup_scope}"
            pipe = r.pipeline()
            for u in urls:
                pipe.sadd(key, u)
            pipe.expire(key, 6 * 3600)          # 与 SADD 同 pipeline，避免无 TTL 残留
            await pipe.execute()
        except Exception as e:
            _mark_redis_failure(f"URL 标记失败: {e}")
            self._seen_urls.update(urls)

    async def search(
        self,
        queries: list[str],
        max_results: int = 5,
    ) -> str:
        """执行搜索、抓取网页、摘要，返回格式化结果。"""
        # 归一 + 兜底：str 会被逐字符迭代（见 normalize_queries 的事故说明）
        queries = normalize_queries(queries)
        if not queries:
            return ("搜索失败：没有收到有效的查询词。"
                    "请提供 2-4 个不同角度的查询词（数组）后重试。")
        t0 = time.time()
        # 1. 并行搜索（Tavily 优先，失败自动降级 DDG）
        tasks = [
            self._do_search(q, max_results, True)
            for q in queries
        ]
        all_results = await asyncio.gather(*tasks)

        # 2. 按 URL 去重（同轮内 + 跨轮 + 跨研究员，Redis 不可用时降级本地）
        url_to_result: dict[str, dict] = {}
        for response in all_results:
            for r in response.get("results", []):
                url = r.get("url", "")
                if url and url not in url_to_result:
                    url_to_result[url] = r

        candidates = await self._filter_seen_urls(list(url_to_result.keys()))
        seen = {u: r for u, r in url_to_result.items() if u in candidates}
        skipped = len(url_to_result) - len(seen)
        if skipped:
            print(f"    去重：跳过 {skipped} 个已处理 URL，本轮新增 {len(seen)} 个")

        if not seen:
            # 全部 URL 已在本次研究中处理过 —— 与「网上没资料」是两回事，
            # 必须区分，否则 LLM 会误判并据此改写 query 或提前停止
            if self.trace:
                await self.trace.record_search(
                    queries=queries, result_count=0, deduped_count=skipped,
                    total_duration_ms=int((time.time() - t0) * 1000),
                    success=True,
                )
            return ("本轮搜索结果均已在本次研究中处理过，无新增内容。"
                    "请换一个查询角度，或基于已有信息作答。")

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

        # 4.5 标记「真正处理成功」的 URL（抓取 + 摘要都成功才标记）。
        # 失败的不标记，留给后续轮次/其他研究员重试 —— 否则暂时性失败
        # 会让该 URL 在本次研究内被永久跳过。
        await self._mark_urls_processed([s["url"] for s in summaries if s])

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
        queries = normalize_queries(queries)
        if not queries:
            return "搜索失败：没有收到有效的查询词。"
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
