package com.deepresearch.gateway.service;

import com.deepresearch.gateway.model.ResearchModels.ResearchRequest;
import com.deepresearch.gateway.model.ResearchModels.ResearchResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.time.Duration;
import java.util.Map;

/**
 * 封装对 Python Agent 的 HTTP 调用。
 *
 * Python Agent 地址通过 application.yml 的 agent.url 配置。
 * 通信全部走 HTTP + JSON，SSE 流用 Flux 返回。
 */
@Service
public class AgentClient {

    private static final Logger log = LoggerFactory.getLogger(AgentClient.class);
    private final WebClient client;

    public AgentClient(WebClient agentWebClient) {
        this.client = agentWebClient;
    }

    /**
     * 同步研究 —— 带重试：Agent 不可用时自动重试 3 次（指数退避）。
     */
    public ResearchResponse research(ResearchRequest request) {
        log.info("同步研究请求: question={}, level={}", request.question(), request.level());

        Exception lastError = null;
        for (int attempt = 0; attempt < 3; attempt++) {
            try {
                return client.post()
                        .uri("/research")
                        .bodyValue(request)
                        .retrieve()
                        .bodyToMono(ResearchResponse.class)
                        .block(Duration.ofMinutes(30));
            } catch (Exception e) {
                lastError = e;
                String msg = e.getMessage() != null ? e.getMessage() : "";
                // 只对连接错误和 5xx 重试，4xx 不重试
                if (msg.contains("500") || msg.contains("503") ||
                    msg.contains("Connection refused") || msg.contains("timeout")) {
                    if (attempt < 2) {
                        long wait = (long) Math.pow(3, attempt + 1); // 3s, 9s, 27s
                        log.warn("Agent 不可用，{}s 后重试 ({}/3): {}", wait, attempt + 1, msg);
                        try { Thread.sleep(wait * 1000); } catch (InterruptedException ie) { break; }
                    }
                } else {
                    break; // 4xx 不重试
                }
            }
        }
        throw new RuntimeException("Agent 不可用，已重试 3 次: " + (lastError != null ? lastError.getMessage() : ""), lastError);
    }

    /**
     * SSE 流式研究。Agent 不可用时自动重试。
     */
    public Flux<String> researchStream(ResearchRequest request) {
        log.info("流式研究请求: question={}, level={}", request.question(), request.level());

        return client.post()
                .uri("/research/stream")
                .bodyValue(request)
                .accept(org.springframework.http.MediaType.TEXT_EVENT_STREAM)
                .retrieve()
                .bodyToFlux(String.class)
                .retryWhen(
                        reactor.util.retry.Retry.backoff(3, Duration.ofSeconds(3))
                                .filter(e -> {
                                    String msg = e.getMessage() != null ? e.getMessage() : "";
                                    return msg.contains("500") || msg.contains("503") ||
                                           msg.contains("Connection refused") || msg.contains("timeout");
                                })
                                .doBeforeRetry(rs -> log.warn("Agent SSE 不可用，重试: {}", rs.failure().getMessage()))
                )
                .doOnError(e -> log.error("SSE 流异常: {}", e.getMessage(), e));
    }

    /**
     * 健康检查 —— 启动时 / 定时探测 Agent 是否存活。
     */
    @SuppressWarnings("unchecked")
    public boolean isHealthy() {
        try {
            Map<String, Object> result = client.get()
                    .uri("/health")
                    .retrieve()
                    .bodyToMono(Map.class)
                    .block(Duration.ofSeconds(3));

            return result != null && "ok".equals(result.get("status"));
        } catch (Exception e) {
            log.warn("Agent 健康检查失败: {}", e.getMessage());
            return false;
        }
    }

    /**
     * 取消正在运行的研究任务。
     */
    public boolean cancel(String taskId) {
        try {
            return Boolean.TRUE.equals(
                    client.delete()
                            .uri("/research/" + taskId)
                            .retrieve()
                            .bodyToMono(Map.class)
                            .map(m -> "cancelled".equals(m.get("status")))
                            .block(Duration.ofSeconds(5))
            );
        } catch (Exception e) {
            log.warn("取消任务失败: taskId={}, error={}", taskId, e.getMessage());
            return false;
        }
    }
}
