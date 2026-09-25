package com.deepresearch.gateway.security;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 认证端点的每 IP 固定窗口限流。
 *
 * 目标是挡住「拿字典撞密码」和「脚本批量注册」，不是精确的流量整形 ——
 * 单实例、内存态、一分钟窗口，够用且零依赖。
 *
 * 局限（部署时要知道）：计数在进程内存里，多实例部署时每个实例各算各的，
 * 真实阈值会被放大到 N 倍；那时应换成 Redis 计数。
 */
@Component
public class AuthRateLimiter {

    private final int maxPerMinute;
    private final Map<String, long[]> buckets = new ConcurrentHashMap<>();
    private final AtomicLong lastSweep = new AtomicLong();

    public AuthRateLimiter(@Value("${app.auth-rate-limit-per-minute:20}") int maxPerMinute) {
        this.maxPerMinute = maxPerMinute;
    }

    /** 记一次尝试并返回是否放行。 */
    public boolean allow(String ip) {
        long minute = System.currentTimeMillis() / 60_000L;
        sweepIfStale(minute);
        long[] b = buckets.compute(ip, (k, v) ->
                (v == null || v[0] != minute) ? new long[]{minute, 1} : new long[]{minute, v[1] + 1});
        return b[1] <= maxPerMinute;
    }

    /**
     * 每分钟清一次上一分钟的桶。
     * 不做清理的话 Map 会随来源 IP 数无限增长 —— 内存耗尽本身也是攻击面。
     */
    private void sweepIfStale(long minute) {
        long now = System.currentTimeMillis();
        long prev = lastSweep.get();
        if (now - prev < 60_000L) return;
        if (!lastSweep.compareAndSet(prev, now)) return;
        buckets.entrySet().removeIf(e -> e.getValue()[0] != minute);
    }
}
