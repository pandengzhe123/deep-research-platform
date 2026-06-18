package com.deepresearch.gateway.controller;

import com.deepresearch.gateway.model.ResearchModels.ResearchRequest;
import com.deepresearch.gateway.model.ResearchModels.ResearchResponse;
import com.deepresearch.gateway.model.ResearchModels.ResearchSession;
import com.deepresearch.gateway.model.SessionEntity;
import com.deepresearch.gateway.security.JwtTokenProvider;
import com.deepresearch.gateway.service.AgentClient;
import com.deepresearch.gateway.service.ResearchScheduler;
import com.deepresearch.gateway.service.SessionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.security.core.context.ReactiveSecurityContextHolder;
import org.springframework.web.bind.annotation.*;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api")
public class ResearchController {

    private static final Logger log = LoggerFactory.getLogger(ResearchController.class);

    private final AgentClient agentClient;
    private final SessionService sessionService;
    private final ResearchScheduler scheduler;
    private final JwtTokenProvider jwt;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public ResearchController(
            AgentClient agentClient,
            SessionService sessionService,
            ResearchScheduler scheduler,
            JwtTokenProvider jwt
    ) {
        this.agentClient = agentClient;
        this.sessionService = sessionService;
        this.scheduler = scheduler;
        this.jwt = jwt;
    }

    /** 从请求头提取 userId，@AuthenticationPrincipal 在 WebFlux 中不可靠 */
    private String extractUserId(org.springframework.http.server.reactive.ServerHttpRequest request) {
        String auth = request.getHeaders().getFirst("Authorization");
        if (auth != null && auth.startsWith("Bearer ")) {
            String token = auth.substring(7);
            if (jwt.validateToken(token)) return jwt.getUserId(token);
        }
        return "anonymous";
    }

    // ================================================================
    // 研究接口
    // ================================================================

    /**
     * 同步研究 —— 等 Agent 完全跑完，返回完整报告。
     */
    @PostMapping("/research")
    public Mono<ResearchResponse> research(@RequestBody ResearchRequest req,
            org.springframework.http.server.reactive.ServerHttpRequest request) {
        final String uid = extractUserId(request);
        return Mono.fromCallable(() -> {
            // 1. 创建或继续已有会话
            ResearchSession session;
            if (req.sessionId() != null && !req.sessionId().isBlank()) {
                session = sessionService.getSession(req.sessionId());
                if (session == null) session = sessionService.createSession(uid, req.question());
                else {
                    sessionService.appendHistory(req.sessionId(), "用户: " + req.question());
                    sessionService.markRunning(req.sessionId());  // 追问：状态改 running + 刷新活动时间
                }
            } else {
                session = sessionService.createSession(uid, req.question());
            }
            log.info("同步研究: session={}", session.getId());

            // 2. 注入 user_id + session_id + 完整上下文
            String dbContext = sessionService.getContextHistory(session.getId());
            String fullContext = (req.context() != null && !req.context().isBlank())
                    ? req.context() : dbContext;
            ResearchRequest reqWithUser = new ResearchRequest(
                    req.question(), req.level(), req.maxRounds(),
                    req.language(), fullContext, req.kbEnabled(),
                    uid, session.getId(), req.ragDocIds()
            );

            // 3. 执行
            ResearchResponse resp = scheduler.execute(
                    () -> agentClient.research(reqWithUser),
                    60_000
            );

            // 3. 保存报告 + 追加历史
            sessionService.appendReport(session.getId(), resp.report());
            if (resp.needClarify() != null && resp.needClarify()) {
                sessionService.appendHistory(session.getId(), "Agent: （追问）" + (resp.question() != null ? resp.question() : ""));
            } else {
                sessionService.appendHistory(session.getId(), "Agent: （已回复报告）");
            }
            log.info("研究完成: session={}, report_len={}", session.getId(),
                    resp.report() != null ? resp.report().length() : 0);
            return new ResearchResponse(
                    resp.report(), resp.language(), resp.needClarify(),
                    resp.question(), session.getId()
            );
        }).subscribeOn(Schedulers.boundedElastic());
    }

    /**
     * SSE 流式研究 —— 实时推送进度，完成时自动保存报告。
     */
    @PostMapping(value = "/research/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<ServerSentEvent<String>> researchStream(@RequestBody ResearchRequest req,
            org.springframework.http.server.reactive.ServerHttpRequest request) {
        final String uid = extractUserId(request);

        // 1. 创建或继续会话（和 sync 端点一致）
        ResearchSession session;
        if (req.sessionId() != null && !req.sessionId().isBlank()) {
            session = sessionService.getSession(req.sessionId());
            if (session == null) session = sessionService.createSession(uid, req.question());
            else {
                sessionService.appendHistory(req.sessionId(), "用户: " + req.question());
                sessionService.markRunning(req.sessionId());  // 追问：状态改 running + 刷新活动时间
            }
        } else {
            session = sessionService.createSession(uid, req.question());
        }
        final String sessionId = session.getId();

        // 2. 注入 user_id + session_id + context
        String dbContext = sessionService.getContextHistory(sessionId);
        String fullContext = (req.context() != null && !req.context().isBlank())
                ? req.context() : dbContext;
        ResearchRequest reqWithUser = new ResearchRequest(
                req.question(), req.level(), req.maxRounds(),
                req.language(), fullContext, req.kbEnabled(),
                uid, sessionId, req.ragDocIds()
        );

        // 3. 先推一条 session 事件，告诉前端 session_id
        ServerSentEvent<String> sessionEvent = ServerSentEvent.<String>builder()
                .event("session")
                .data("{\"id\":\"" + sessionId + "\"}")
                .build();

        // 4. 转发 Python SSE 事件，拦截 done/error 做持久化
        return Flux.just(sessionEvent)
                .concatWith(
                        agentClient.researchStream(reqWithUser)
                                .doOnNext(sse -> {
                                    String eventName = sse.event();
                                    String data = sse.data();
                                    if ("done".equals(eventName) && data != null) {
                                        try {
                                            JsonNode node = objectMapper.readTree(data);
                                            String report = node.has("report") ? node.get("report").asText() : "";
                                            sessionService.appendReport(sessionId, report);
                                            sessionService.appendHistory(sessionId, "Agent: （已回复报告）");
                                            log.info("流式研究完成: session={}, report_len={}", sessionId, report.length());
                                        } catch (Exception e) {
                                            log.error("解析报告失败: {}", e.getMessage());
                                        }
                                    } else if ("error".equals(eventName)) {
                                        sessionService.markError(sessionId);
                                    }
                                })
                                .doOnError(e -> {
                                    log.error("流式研究异常: session={}, error={}", sessionId, e.getMessage());
                                    sessionService.markError(sessionId);
                                })
                );
    }

    /**
     * 取消正在运行的研究任务。
     */
    @DeleteMapping("/research/{taskId}")
    public ResponseEntity<Map<String, Object>> cancel(@PathVariable String taskId) {
        boolean ok = agentClient.cancel(taskId);
        return ok
                ? ResponseEntity.ok(Map.of("status", "cancelled", "taskId", taskId))
                : ResponseEntity.ok(Map.of("status", "not_found", "taskId", taskId));
    }

    // ================================================================
    // 会话接口
    // ================================================================

    /**
     * 获取某个会话的完整信息（含报告）。
     */
    @GetMapping("/sessions/{id}")
    public ResponseEntity<Map<String, Object>> getSession(@PathVariable String id) {
        ResearchSession session = sessionService.getSession(id);
        if (session == null) return ResponseEntity.notFound().build();
        SessionEntity entity = sessionService.getEntity(id);
        return ResponseEntity.ok(Map.of(
                "session", session,
                "history", entity != null ? entity.getHistory() : "[]"
        ));
    }

    /**
     * 获取所有会话。
     */
    @GetMapping("/sessions")
    public Mono<ResponseEntity<List<ResearchSession>>> listSessions() {
        return ReactiveSecurityContextHolder.getContext()
                .map(ctx -> {
                    String uid = ctx.getAuthentication() != null
                            ? ctx.getAuthentication().getPrincipal().toString()
                            : "anonymous";
                    return ResponseEntity.ok(sessionService.getUserSessions(uid));
                })
                .defaultIfEmpty(ResponseEntity.ok(sessionService.getUserSessions("anonymous")));
    }

    // ================================================================
    // 健康 & 状态
    // ================================================================

    /**
     * 网关自身健康检查。
     */
    @GetMapping("/health")
    public ResponseEntity<Map<String, Object>> health() {
        boolean agentOk = agentClient.isHealthy();
        boolean dbOk = false;
        try {
            dbOk = sessionService.getAllSessions() != null;
        } catch (Exception e) { /* ignore */ }
        String status = (agentOk && dbOk) ? "ok" : "degraded";
        return ResponseEntity.ok(Map.of(
                "status", status,
                "agent", agentOk ? "connected" : "unreachable",
                "database", dbOk ? "connected" : "unreachable",
                "activeTasks", scheduler.activeCount()
        ));
    }

}
