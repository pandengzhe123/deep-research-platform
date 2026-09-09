"""Redis 集成测试 —— 搜索缓存（TODO R1）。

需要 Redis 运行；不可用时自动跳过。
启动：docker run -d -p 6379:6379 --name redis-deepresearch redis:7-alpine

运行：cd agent && .venv\\Scripts\\python tests\\test_redis_cache.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


async def test_cache_roundtrip():
    """写入 → 读出内容一致，且 key 有命名前缀。"""
    from researcher.search import SearchTool, _get_redis
    tool = SearchTool()
    key = "search:test_roundtrip:5"
    payload = {"results": [{"url": "u", "title": "t", "content": "c"}], "query": "test_roundtrip"}

    await tool._cache_set(key, payload)
    got = await tool._cache_get(key)
    assert got == payload, f"roundtrip 不一致: {got}"
    assert await _get_redis().exists(key) == 1, "Redis 中不存在该 key"
    await _get_redis().delete(key)


async def test_do_search_hits_redis_cache():
    """预置缓存后 _do_search 直接命中，不触发网络搜索。"""
    from researcher.search import SearchTool, _get_redis
    tool = SearchTool()
    query = "test_cache_hit_query"
    # key 含 include_raw 标记（B34）：_do_search 内部用 include_raw=True 调用
    key = f"search:{query}:5:raw"
    payload = {"results": [{"url": "u", "title": "from-cache", "content": "c"}], "query": query}

    await tool._cache_set(key, payload)
    got = await tool._do_search(query, 5, True)
    assert got["results"][0]["title"] == "from-cache", f"未命中缓存: {got}"
    assert tool._cache_hits == 1 and tool._cache_misses == 0, f"统计错误: {tool.get_cache_stats()}"
    await _get_redis().delete(key)


async def test_cache_ttl_applied():
    """缓存写入必须带 TTL（服务端自动过期，替代原手写清理）。"""
    from researcher.search import SearchTool, _get_redis
    tool = SearchTool()
    key = "search:test_ttl:5"

    await tool._cache_set(key, {"x": 1})
    ttl = await _get_redis().ttl(key)
    assert 0 < ttl <= tool._cache_ttl, f"TTL 异常: {ttl}"
    await _get_redis().delete(key)


async def test_graceful_degradation():
    """Redis 进入冷却期 → 读写降级为 None/静默跳过，不抛异常。"""
    import researcher.search as sm
    from researcher.search import SearchTool

    tool = SearchTool()
    saved = sm._redis_disabled_until
    try:
        sm._redis_disabled_until = 9e18      # 模拟连接失败后的冷却
        assert await tool._cache_get("x") is None, "冷却期应返回 None"
        await tool._cache_set("x", {"a": 1})  # 应静默跳过，不抛异常
        assert tool.get_cache_stats()["backend"] == "disabled"
    finally:
        sm._redis_disabled_until = saved


async def test_cache_stats_fields():
    """统计字段完整（backend 反映后端状态）。"""
    from researcher.search import SearchTool
    tool = SearchTool()
    stats = tool.get_cache_stats()
    for f in ("hits", "misses", "total", "hit_rate", "ttl_seconds", "backend"):
        assert f in stats, f"缺少字段 {f}"
    assert stats["backend"] == "redis"


async def test_cross_instance_url_dedup():
    """R2：共享 dedup_scope 的多个 SearchTool 实例共享 URL 去重（L3/L4 并行场景）。

    语义：`_filter_seen_urls` 只查询；`_mark_urls_processed` 在抓取+摘要成功后标记。
    """
    from researcher.search import SearchTool, _get_redis
    scope = "test_r2_scope"
    key = f"seen_urls:{scope}"
    await _get_redis().delete(key)

    a = SearchTool(dedup_scope=scope)
    b = SearchTool(dedup_scope=scope)

    # 首次：两个 URL 都未被处理
    first = await a._filter_seen_urls(["http://u1", "http://u2"])
    assert first == {"http://u1", "http://u2"}, f"首次应全部为新: {first}"

    # 只标记 u1 成功（u2 模拟抓取失败 → 不标记，B3 修复点）
    await a._mark_urls_processed(["http://u1"])

    # 另一实例：u1 被跳过，u2 仍可重试
    second = await b._filter_seen_urls(["http://u1", "http://u2", "http://u3"])
    assert second == {"http://u2", "http://u3"}, f"去重/可重试语义错误: {second}"

    members = await _get_redis().smembers(key)
    assert members == {"http://u1"}, f"只应标记成功的 URL: {members}"

    ttl = await _get_redis().ttl(key)
    assert 0 < ttl <= 6 * 3600, f"去重 key TTL 异常: {ttl}"
    await _get_redis().delete(key)


async def test_dedup_local_fallback():
    """R2：未提供 dedup_scope 时降级为实例内本地去重（单研究员场景）。"""
    from researcher.search import SearchTool
    tool = SearchTool()
    assert await tool._filter_seen_urls(["http://x"]) == {"http://x"}
    await tool._mark_urls_processed(["http://x"])
    assert await tool._filter_seen_urls(["http://x"]) == set(), "本地去重未生效"


async def test_redis_client_rebuilds_on_loop_change():
    """B1：事件循环变化时重建客户端（避免旧 client 绑旧 loop 抛 Event loop is closed）。"""
    import researcher.search as sm
    from researcher.search import _get_redis

    r1 = _get_redis()
    assert r1 is not None and await r1.ping()
    sm._redis_client_loop = None          # 模拟 loop 变化
    r2 = _get_redis()
    assert r2 is not None and await r2.ping(), "重建后应可用"
    assert sm._redis_client_loop is not None, "应记录新 loop"


async def test_redis_client_discarded_on_failure():
    """B1：失败后必须丢弃客户端（否则冷却结束仍返回同一个坏 client → 永久降级）。"""
    import time as _t
    import researcher.search as sm
    from researcher.search import _get_redis

    r = _get_redis()
    assert r is not None
    sm._mark_redis_failure("测试失败", connection_level=False)
    assert sm._redis_client is None, "失败后应丢弃客户端"
    assert sm._redis_disabled_until < _t.time(), "数据级失败不应触发 60s 冷却"
    r2 = _get_redis()
    assert r2 is not None and await r2.ping(), "应能重建客户端"


async def test_cache_get_rejects_non_dict():
    """B11：缓存内容非 dict / 非法 JSON 时返回 None（不让调用方 AttributeError）。"""
    import json
    from researcher.search import SearchTool, _get_redis

    tool = SearchTool()
    key = "search:test_non_dict:5"
    r = _get_redis()
    await r.set(key, json.dumps([1, 2, 3]), ex=60)
    assert await tool._cache_get(key) is None, "非 dict 应被拒绝"
    await r.set(key, "not-json", ex=60)
    assert await tool._cache_get(key) is None, "非法 JSON 应被拒绝"
    await r.delete(key)


async def test_research_lock_mutual_exclusion():
    """R3：同会话互斥（第二个请求被拒），不同会话互不影响。"""
    from researcher.search import _get_redis
    from researcher.server import acquire_research_lock, release_research_lock

    sid = "test_lock_mutex"
    await _get_redis().delete(f"lock:research:{sid}")

    ok1, tok1 = await acquire_research_lock(sid)
    assert ok1 and tok1, "首次应获取成功"

    ok2, tok2 = await acquire_research_lock(sid)
    assert ok2 is False and tok2 == "", "同会话并发应被拒绝"

    ok3, tok3 = await acquire_research_lock("test_lock_mutex_other")
    assert ok3 and tok3, "不同会话不应受影响"
    await release_research_lock("test_lock_mutex_other", tok3)

    await release_research_lock(sid, tok1)
    assert await _get_redis().exists(f"lock:research:{sid}") == 0


async def test_lock_release_only_own_token():
    """R3：Lua 释放只删自己持有的锁，错误 token 不得误删他人锁。"""
    from researcher.search import _get_redis
    from researcher.server import acquire_research_lock, release_research_lock

    sid = "test_lock_token"
    key = f"lock:research:{sid}"
    await _get_redis().delete(key)

    ok, tok = await acquire_research_lock(sid, ttl=100)
    assert ok and tok

    await release_research_lock(sid, "wrong-token")
    assert await _get_redis().exists(key) == 1, "错误 token 不应删掉锁"

    ttl = await _get_redis().ttl(key)
    assert 0 < ttl <= 100, f"TTL 异常: {ttl}"

    await release_research_lock(sid, tok)
    assert await _get_redis().exists(key) == 0, "正确 token 应释放锁"


async def test_rate_limit_and_usage():
    """R5：限流按用户每分钟计数（超限拒绝）+ 用量计数 + 热榜。"""
    import researcher.server as srv
    from researcher.search import _get_redis

    r = _get_redis()
    uid = "test_r5_user"
    usage_key = f"usage:research:{uid}"

    async def cleanup():
        for k in [k async for k in r.scan_iter(f"rate:research:{uid}:*")]:
            await r.delete(k)
        await r.delete(usage_key)

    await cleanup()

    # 前 N 次允许
    for i in range(srv.RATE_LIMIT_PER_MINUTE):
        allowed, _ = await srv.check_rate_limit(uid)
        assert allowed, f"第 {i + 1} 次应允许"

    # 第 N+1 次拒绝
    allowed, remaining = await srv.check_rate_limit(uid)
    assert not allowed and remaining == 0, f"超过上限应拒绝: allowed={allowed} remaining={remaining}"

    # 用量计数
    await srv.record_usage(uid, "测试热门主题")
    await srv.record_usage(uid, "测试热门主题")
    cnt = await r.get(usage_key)
    assert cnt == "2", f"用量计数错误: {cnt}"

    # 热榜
    score = await r.zscore("hot:topics", "测试热门主题")
    assert score is not None and score >= 2, f"热榜计分错误: {score}"

    await cleanup()


async def test_rate_limit_degrades_when_redis_down():
    """R5：Redis 不可用时限流放行（不阻塞业务）。"""
    import researcher.server as srv
    import researcher.search as sm

    saved = sm._redis_disabled_until
    try:
        sm._redis_disabled_until = 9e18
        allowed, remaining = await srv.check_rate_limit("any_user")
        assert allowed and remaining == -1, f"降级应放行: {allowed} {remaining}"
        await srv.record_usage("any_user", "x")   # 静默跳过，不抛异常
    finally:
        sm._redis_disabled_until = saved


async def test_lock_renewal_extends_ttl():
    """B29：长研究必须能续期，否则锁 TTL 到期后同会话并发研究趁虚而入。"""
    import uuid
    import researcher.server as srv
    from researcher.search import _get_redis

    session_id = "test_renew_" + uuid.uuid4().hex[:8]
    acquired, token = await srv.acquire_research_lock(session_id, ttl=2)
    assert acquired and token, "未拿到锁"
    task = asyncio.create_task(
        srv.renew_research_lock_loop(session_id, token, ttl=2, interval=1))
    try:
        await asyncio.sleep(3.5)            # 已超过原始 TTL(2s)
        r = _get_redis()
        assert await r.exists(f"lock:research:{session_id}") == 1, "锁被 TTL 回收，续期失效"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await srv.release_research_lock(session_id, token)


async def test_lock_renewal_stops_when_token_changed():
    """B29：锁已易主 → 续期任务必须自行退出，不能一直给别人的锁续命。"""
    import uuid
    import researcher.server as srv
    from researcher.search import _get_redis

    session_id = "test_stolen_" + uuid.uuid4().hex[:8]
    acquired, token = await srv.acquire_research_lock(session_id, ttl=10)
    assert acquired and token
    r = _get_redis()
    await r.set(f"lock:research:{session_id}", "other-token", ex=10)   # 模拟易主
    task = asyncio.create_task(
        srv.renew_research_lock_loop(session_id, token, ttl=10, interval=1))
    try:
        await asyncio.sleep(1.5)
        assert task.done(), "锁已易主，续期任务应已退出"
        task.result()                       # 有异常则在此抛出
        assert await r.get(f"lock:research:{session_id}") == "other-token", "不应续别人的锁"
    finally:
        if not task.done():
            task.cancel()
        await r.delete(f"lock:research:{session_id}")


async def test_cache_key_separates_include_raw():
    """B34：include_raw 不同 → 缓存 key 不同，避免一种形态污染另一种。"""
    import uuid
    from researcher.search import SearchTool, _get_redis

    tool = SearchTool()
    query = "test_include_raw_" + uuid.uuid4().hex[:6]

    async def fake_tavily(q, max_results, include_raw, retries=2):
        return {"results": [{"url": "u",
                             "title": "raw" if include_raw else "noraw",
                             "content": "c"}], "query": q}

    tool._safe_tavily_search = fake_tavily
    raw = await tool._do_search(query, 3, True)
    noraw = await tool._do_search(query, 3, False)
    assert raw["results"][0]["title"] == "raw", raw
    assert noraw["results"][0]["title"] == "noraw", f"两种形态共用 key 了: {noraw}"

    r = _get_redis()
    base = query.strip().lower()
    assert await r.exists(f"search:{base}:3:raw") == 1, "raw 形态未独立缓存"
    assert await r.exists(f"search:{base}:3:noraw") == 1, "noraw 形态未独立缓存"
    await r.delete(f"search:{base}:3:raw", f"search:{base}:3:noraw")


async def test_error_codes_and_http_mapping():
    """B35：锁冲突→409、限流→429，错误体必须带 code（原来一律 500）。"""
    import json
    import time
    import uuid
    import researcher.server as srv
    from researcher.search import _get_redis

    assert srv.ERROR_HTTP_STATUS.get("session_locked") == 409
    assert srv.ERROR_HTTP_STATUS.get("rate_limited") == 429

    # ① 会话锁冲突
    session_id = "test_err_" + uuid.uuid4().hex[:8]
    acquired, token = await srv.acquire_research_lock(session_id, ttl=10)
    assert acquired and token
    try:
        events = [e async for e in srv._run_with_lock(
            srv.ResearchRequest(question="q", user_id="err_user", session_id=session_id),
            asyncio.Event())]
        assert len(events) == 1 and events[0]["event"] == "error", events
        payload = json.loads(events[0]["data"])
        assert payload["code"] == "session_locked", payload
    finally:
        await srv.release_research_lock(session_id, token)

    # ② 限流（把每分钟上限临时设为 0）
    saved = srv.RATE_LIMIT_PER_MINUTE
    srv.RATE_LIMIT_PER_MINUTE = 0
    user = "err_user_" + uuid.uuid4().hex[:6]
    try:
        events = [e async for e in srv._run_with_lock(
            srv.ResearchRequest(question="q", user_id=user), asyncio.Event())]
        payload = json.loads(events[0]["data"])
        assert payload["code"] == "rate_limited", payload
        assert payload["remaining"] == 0, payload
        assert payload["retry_after"] == 60, payload
    finally:
        srv.RATE_LIMIT_PER_MINUTE = saved
        await _get_redis().delete(f"rate:research:{user}:{int(time.time()) // 60}")


async def test_quota_event_reports_remaining():
    """B36：remaining 必须被真正使用 —— 放行时以 status 事件回传剩余额度。"""
    import json
    import time
    import uuid
    import researcher.server as srv
    from researcher.search import _get_redis

    saved = srv.RATE_LIMIT_PER_MINUTE
    srv.RATE_LIMIT_PER_MINUTE = 5
    user = "quota_user_" + uuid.uuid4().hex[:6]
    try:
        agen = srv._run_with_lock(
            srv.ResearchRequest(question="q", user_id=user), asyncio.Event())
        first = await agen.__anext__()      # 首个事件应是额度提示
        await agen.aclose()
        assert first["event"] == "status", first
        payload = json.loads(first["data"])
        assert payload["step"] == "quota", payload
        assert payload["remaining"] == 4, payload       # 上限 5 - 已用 1
    finally:
        srv.RATE_LIMIT_PER_MINUTE = saved
        await _get_redis().delete(f"rate:research:{user}:{int(time.time()) // 60}")


async def main():
    from researcher.search import _get_redis

    r = _get_redis()
    if r is None:
        print("  ⚠️ Redis 不可用，跳过集成测试（docker run -d -p 6379:6379 redis:7-alpine）")
        return
    try:
        if not await r.ping():
            print("  ⚠️ Redis PING 失败，跳过集成测试")
            return
    except Exception as e:
        print(f"  ⚠️ Redis 不可用，跳过集成测试: {e}")
        return

    tests = [
        test_cache_roundtrip,
        test_do_search_hits_redis_cache,
        test_cache_ttl_applied,
        test_graceful_degradation,
        test_cache_stats_fields,
        test_cross_instance_url_dedup,
        test_dedup_local_fallback,
        test_redis_client_rebuilds_on_loop_change,
        test_redis_client_discarded_on_failure,
        test_cache_get_rejects_non_dict,
        test_research_lock_mutual_exclusion,
        test_lock_release_only_own_token,
        test_rate_limit_and_usage,
        test_rate_limit_degrades_when_redis_down,
        test_lock_renewal_extends_ttl,
        test_lock_renewal_stops_when_token_changed,
        test_cache_key_separates_include_raw,
        test_error_codes_and_http_mapping,
        test_quota_event_reports_remaining,
    ]
    passed = 0
    for t in tests:
        try:
            await t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {t.__name__}: {e}")

    print(f"\n  {passed}/{len(tests)} 通过")


if __name__ == "__main__":
    asyncio.run(main())
