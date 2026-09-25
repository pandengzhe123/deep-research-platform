package com.deepresearch.gateway.service;

import com.deepresearch.gateway.model.ResearchModels.ResearchRequest;
import com.deepresearch.gateway.model.ResearchModels.ResearchResponse;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.http.client.MultipartBodyBuilder;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.BodyInserters;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;
import org.springframework.web.server.ResponseStatusException;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.time.Duration;
import java.util.Map;

/**
 * 封装对 Python Agent 的 HTTP 调用。
 *
 * Python Agent 地址通过 application.yml 的 agent.url 配置。
 * 通信全部走 HTTP + JSON，SSE 流用 Flux 返回。
 *
 * <b>约定</b>：所有需要 user_id 的调用都由调用方从 JWT 解析后传进来，
 * 这个类只负责透传，绝不去读客户端可控的请求参数。
 */
@Service
public class AgentClient {

    private static final Logger log = LoggerFactory.getLogger(AgentClient.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private final WebClient client;

    public AgentClient(WebClient agentWebClient) {
        this.client = agentWebClient;
    }

    /**
     * 同步研究 —— 只对「可能自愈」的故障重试。
     *
     * <p>原先这里把所有异常（包括 4xx）都吞成 {@code RuntimeException("Agent 不可用，已重试 3 次")}，
     * 于是 agent 明确表达的 429（每分钟限流 / 每日配额用尽）与 409（同会话已有研究在跑）
     * 到前端一律变成 500 —— 用户看到「服务坏了」，而不是「你太快了，等一会儿再试」。
     * 这类「真正的信号被压平成通用错误」正是排查困难的主要来源，所以状态码必须透传。
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
            } catch (WebClientResponseException e) {
                // 4xx 是「这次请求本身的问题」，重试无意义，直接透传
                if (e.getStatusCode().value() < 500) {
                    throw upstream(e, "研究请求");
                }
                lastError = e;
                if (attempt < 2) {
                    long wait = (long) Math.pow(3, attempt + 1); // 3s, 9s
                    log.warn("Agent 返回 {}，{}s 后重试 ({}/3)", e.getStatusCode(), wait, attempt + 1);
                    if (!sleepQuietly(wait)) break;
                }
            } catch (Exception e) {
                // 连接层错误（拒绝连接 / 超时）：可能只是 agent 正在重启，值得重试
                lastError = e;
                String msg = e.getMessage() != null ? e.getMessage() : "";
                boolean retryable = msg.contains("Connection refused") || msg.contains("timeout")
                        || msg.contains("Timeout") || msg.contains("connect");
                if (!retryable) break;
                if (attempt < 2) {
                    long wait = (long) Math.pow(3, attempt + 1);
                    log.warn("Agent 不可达，{}s 后重试 ({}/3): {}", wait, attempt + 1, msg);
                    if (!sleepQuietly(wait)) break;
                }
            }
        }
        log.error("研究请求最终失败: {}", lastError != null ? lastError.getMessage() : "未知");
        // 重试耗尽属于「依赖不可用」，用 503 而不是 500：语义准确，也便于监控区分
        throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "研究服务暂时不可用，请稍后重试");
    }

    /** 返回 false 表示被中断，调用方应停止重试（不要吞掉中断标志）。 */
    private boolean sleepQuietly(long seconds) {
        try {
            Thread.sleep(seconds * 1000);
            return true;
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    /**
     * SSE 流式研究。返回 ServerSentEvent 保留事件名（status/done/error）。
     */
    public Flux<ServerSentEvent<String>> researchStream(ResearchRequest request) {
        log.info("流式研究请求: question={}, level={}", request.question(), request.level());

        return client.post()
                .uri("/research/stream")
                .bodyValue(request)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {})
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
     *
     * <p>带上 userId：Agent 侧会校验该任务是否属于这个用户，不属于则拒绝。
     * 归属校验必须放在持有任务表的那一侧（Agent），网关无从知道 taskId 与用户的对应关系。
     */
    public boolean cancel(String taskId, String userId) {
        try {
            return Boolean.TRUE.equals(
                    client.delete()
                            .uri(uri -> uri.path("/research/" + taskId)
                                    .queryParam("user_id", userId).build())
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

    // ================================================================
    // 知识库（user_id 由网关按 JWT 注入，见 KbController）
    // ================================================================

    /** 列出指定用户已上传的知识库文档。 */
    public Map<String, Object> listKb(String userId) {
        try {
            return client.get()
                    .uri(uri -> uri.path("/kb/files").queryParam("user_id", userId).build())
                    .retrieve()
                    .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {})
                    .block(Duration.ofSeconds(20));
        } catch (WebClientResponseException e) {
            throw upstream(e, "获取知识库列表");
        }
    }

    /** 上传文档到指定用户的知识库。含 embedding 推理，超时给足。 */
    public Map<String, Object> uploadKb(String userId, String filename, byte[] content) {
        MultipartBodyBuilder builder = new MultipartBodyBuilder();
        builder.part("file", new ByteArrayResource(content) {
            @Override
            public String getFilename() {
                return filename != null ? filename : "upload.bin";
            }
        }).contentType(MediaType.APPLICATION_OCTET_STREAM);

        try {
            return client.post()
                    .uri(uri -> uri.path("/kb/upload").queryParam("user_id", userId).build())
                    .contentType(MediaType.MULTIPART_FORM_DATA)
                    .body(BodyInserters.fromMultipartData(builder.build()))
                    .retrieve()
                    .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {})
                    .block(Duration.ofMinutes(5));
        } catch (WebClientResponseException e) {
            throw upstream(e, "上传文档");
        }
    }

    /** 删除指定用户知识库中的文档。 */
    public Map<String, Object> deleteKb(String userId, String docId) {
        try {
            return client.delete()
                    .uri(uri -> uri.path("/kb/files/" + docId)
                            .queryParam("user_id", userId).build())
                    .retrieve()
                    .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {})
                    .block(Duration.ofSeconds(30));
        } catch (WebClientResponseException e) {
            throw upstream(e, "删除文档");
        }
    }

    /**
     * 把 Agent 的错误响应转成网关的响应状态。
     *
     * <p>4xx 的 detail 是「这次请求哪里不对」，回传有助排查；5xx 一律换成人话 ——
     * agent 的 500 detail 里常带内部异常文本与文件路径，不该给到公网客户端。
     */
    private ResponseStatusException upstream(WebClientResponseException e, String what) {
        int code = e.getStatusCode().value();
        HttpStatus status = HttpStatus.resolve(code);
        if (status == null) status = HttpStatus.BAD_GATEWAY;

        log.warn("{} 失败: status={}, body={}", what, code, e.getResponseBodyAsString());

        if (code >= 500) {
            return new ResponseStatusException(status, what + "失败，请稍后重试");
        }
        return new ResponseStatusException(status, extractDetail(e));
    }

    /** 从 FastAPI 的错误体里取 detail（可能是字符串，也可能是对象）。 */
    private String extractDetail(WebClientResponseException e) {
        try {
            JsonNode detail = MAPPER.readTree(e.getResponseBodyAsString()).get("detail");
            if (detail == null) return "请求被拒绝";
            if (detail.isTextual()) return detail.asText();
            // agent 的错误体是 {message, code, remaining, retry_after} —— 取 message
            // 直接给用户看；整块 toString() 会在前端显示成一串转义 JSON。
            JsonNode msg = detail.get("message");
            if (msg != null && msg.isTextual()) return msg.asText();
            return detail.toString();
        } catch (Exception ignored) {
            return "请求被拒绝";
        }
    }
}
