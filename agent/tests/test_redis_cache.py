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
    key = f"search:{query}:5"
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
    """R2：共享 dedup_scope 的多个 SearchTool 实例共享 URL 去重（L3/L4 并行场景）。"""
    from researcher.search import SearchTool, _get_redis
    scope = "test_r2_scope"
    key = f"seen_urls:{scope}"
    await _get_redis().delete(key)

    a = SearchTool(dedup_scope=scope)
    b = SearchTool(dedup_scope=scope)

    first = await a._filter_new_urls(["http://u1", "http://u2"])
    assert first == {"http://u1", "http://u2"}, f"首次应全部为新: {first}"

    second = await b._filter_new_urls(["http://u1", "http://u3"])
    assert second == {"http://u3"}, f"跨实例去重失败（u1 应被跳过）: {second}"

    members = await _get_redis().smembers(key)
    assert members == {"http://u1", "http://u2", "http://u3"}, f"Redis 集合内容错误: {members}"

    ttl = await _get_redis().ttl(key)
    assert 0 < ttl <= 6 * 3600, f"去重 key TTL 异常: {ttl}"
    await _get_redis().delete(key)


async def test_dedup_local_fallback():
    """R2：未提供 dedup_scope 时降级为实例内本地去重（单研究员场景）。"""
    from researcher.search import SearchTool
    tool = SearchTool()
    assert await tool._filter_new_urls(["http://x"]) == {"http://x"}
    assert await tool._filter_new_urls(["http://x"]) == set(), "本地去重未生效"


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
        test_research_lock_mutual_exclusion,
        test_lock_release_only_own_token,
        test_rate_limit_and_usage,
        test_rate_limit_degrades_when_redis_down,
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
