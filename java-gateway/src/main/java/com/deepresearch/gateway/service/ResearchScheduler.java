package com.deepresearch.gateway.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

/**
 * 并发调度器 —— 限制同时运行的研究任务数，防止打爆 LLM API。
 */
@Service
public class ResearchScheduler {

    private static final Logger log = LoggerFactory.getLogger(ResearchScheduler.class);

    // 全局最多 20 个并发研究
    private final Semaphore semaphore = new Semaphore(20);

    /**
     * 在信号量保护下执行研究任务。
     *
     * @param task      要执行的任务
     * @param timeoutMs 等待信号量的超时（毫秒）
     * @return 任务的返回值
     * @throws InterruptedException 等待被中断
     * @throws RuntimeException     排队超时
     */
    public <T> T execute(Supplier<T> task, long timeoutMs) throws InterruptedException {
        log.debug("当前并发: {}/20", 20 - semaphore.availablePermits());

        if (!semaphore.tryAcquire(timeoutMs, TimeUnit.MILLISECONDS)) {
            throw new RuntimeException("当前排队人数过多，请稍后重试");
        }

        try {
            return task.get();
        } finally {
            semaphore.release();
        }
    }

    /**
     * 获取当前活跃任务数。
     */
    public int activeCount() {
        return 20 - semaphore.availablePermits();
    }
}
