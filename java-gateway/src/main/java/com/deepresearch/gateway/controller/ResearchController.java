package com.deepresearch.gateway.controller;

import com.deepresearch.gateway.model.ResearchModels.ResearchRequest;
import com.deepresearch.gateway.model.ResearchModels.ResearchResponse;
import com.deepresearch.gateway.model.ResearchModels.ResearchSession;
import com.deepresearch.gateway.service.AgentClient;
import com.deepresearch.gateway.service.ResearchScheduler;
import com.deepresearch.gateway.service.SessionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
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

    public ResearchController(
            AgentClient agentClient,
            SessionService sessionService,
            ResearchScheduler scheduler
    ) {
        this.agentClient = agentClient;
        this.sessionService = sessionService;
        this.scheduler = scheduler;
    }

    // ================================================================
    // 研究接口
    // ================================================================

    /**
     * 同步研究 —— 等 Agent 完全跑完，返回完整报告。
     */
    @PostMapping("/research")
    public Mono<ResearchResponse> research(@RequestBody ResearchRequest req,
            @org.springframework.security.core.annotation.AuthenticationPrincipal String userId) {
        final String uid = userId != null ? userId : "anonymous";
        return Mono.fromCallable(() -> {
            // 1. 创建或继续会话
            ResearchSession session = sessionService.createSession(uid, req.question());
            log.info("同步研究: session={}", session.getId());

            // 2. 注入 user_id 到请求（传给 Python → KB 隔离）
            ResearchRequest reqWithUser = new ResearchRequest(
                    req.question(), req.level(), req.maxRounds(),
                    req.language(), req.context(), req.kbEnabled(),
                    uid
            );

            // 3. 执行
            ResearchResponse resp = scheduler.execute(
                    () -> agentClient.research(reqWithUser),
                    60_000
            );

            // 3. 保存报告 + 追加历史
            sessionService.appendReport(session.getId(), resp.report());
            sessionService.appendHistory(session.getId(), "Agent: 报告已生成");
            log.info("研究完成: session={}, report_len={}", session.getId(),
                    resp.report() != null ? resp.report().length() : 0);
            return new ResearchResponse(
                    resp.report(), resp.language(), resp.needClarify(),
                    resp.question(), session.getId()
            );
        }).subscribeOn(Schedulers.boundedElastic());
    }

    /**
     * SSE 流式研究 —— 实时推送进度。
     */
    @PostMapping(value = "/research/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<String> researchStream(@RequestBody ResearchRequest req,
            @org.springframework.security.core.annotation.AuthenticationPrincipal String userId) {
        final String uid = userId != null ? userId : "anonymous";
        ResearchSession session = sessionService.createSession(uid, req.question());
        String headerEvent = "event: session\ndata: {\"id\": \"" + session.getId() + "\"}\n\n";

        return Flux.just(headerEvent)
                .concatWith(
                        agentClient.researchStream(req)
                                .doOnComplete(() -> log.info("研究完成: session={}", session.getId()))
                                .doOnError(e -> {
                                    log.error("研究异常: session={}, error={}", session.getId(), e.getMessage());
                                    sessionService.markError(session.getId());
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
    public ResponseEntity<ResearchSession> getSession(@PathVariable String id) {
        ResearchSession session = sessionService.getSession(id);
        if (session == null) return ResponseEntity.notFound().build();
        return ResponseEntity.ok(session);
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
        return ResponseEntity.ok(Map.of(
                "status", agentOk ? "ok" : "degraded",
                "agent", agentOk ? "connected" : "unreachable",
                "activeTasks", scheduler.activeCount()
        ));
    }

}
