package com.deepresearch.gateway.service;

import com.deepresearch.gateway.model.ResearchModels.ResearchRequest;
import com.deepresearch.gateway.model.ResearchModels.ResearchResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
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
     * 同步研究 —— 等 Agent 跑完才返回。
     * 注意：Agent 可能跑 1-3 分钟，所以超时设 10 分钟。
     */
    public ResearchResponse research(ResearchRequest request) {
        log.info("同步研究请求: question={}, level={}", request.question(), request.level());

        return client.post()
                .uri("/research")
                .bodyValue(request)
                .retrieve()
                .bodyToMono(ResearchResponse.class)
                .block(Duration.ofMinutes(10));
    }

    /**
     * SSE 流式研究 —— 实时推送进度。
     * 返回 Flux<String>，网关不做解析，透明转发给前端。
     *
     * Python 返回的 SSE 事件格式：
     *   event: status
     *   data: {"step":"searching","message":"搜索: ...","round":1}
     *
     *   event: done
     *   data: {"report":"# 报告\n...","language":"auto"}
     */
    public Flux<String> researchStream(ResearchRequest request) {
        log.info("流式研究请求: question={}, level={}", request.question(), request.level());

        return client.post()
                .uri("/research/stream")
                .bodyValue(request)
                .accept(org.springframework.http.MediaType.TEXT_EVENT_STREAM)
                .retrieve()
                .bodyToFlux(String.class)
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
