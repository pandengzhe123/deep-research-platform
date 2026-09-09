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
