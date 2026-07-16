package com.deepresearch.gateway.service;

import com.deepresearch.gateway.model.ResearchModels.ResearchSession;
import com.deepresearch.gateway.model.SessionEntity;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.*;

/**
 * 研究会话管理 —— PostgreSQL 持久化。
 *
 * 历史消息格式（JSONB 数组，每项是一个对象）：
 * {"role":"user","content":"...","time":"2026-06-26T10:30:00"}
 * {"role":"agent","content":"...","time":"2026-06-26T10:35:00"}
 *
 * 旧格式兼容：纯文本字符串 "用户: xxx" / "Agent: xxx" 在读取时自动识别。
 */
@Service
public class SessionService {

    private static final Logger log = LoggerFactory.getLogger(SessionService.class);
    private static final DateTimeFormatter TIME_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm");
    private static final int COMPRESS_THRESHOLD = 40;  // 历史消息达此数量时触发压缩
    private static final int KEEP_RECENT = 25;          // 压缩后保留最近 N 条，压缩旧的

    private final SessionRepository repo;
    private final WebClient webClient;

    public SessionService(SessionRepository repo, WebClient agentWebClient) {
        this.repo = repo;
        this.webClient = agentWebClient;
    }

    /**
     * 创建新会话，写入数据库。
     */
    public ResearchSession createSession(String userId, String question) {
        String id = UUID.randomUUID().toString().substring(0, 8);
        SessionEntity entity = new SessionEntity(id, userId, question);
        entity.setHistory(toJson(List.of(msgObj("user", question))));
        repo.save(entity);
        log.info("创建会话: id={}, user={}", id, userId);
        return toPojo(entity);
    }

    /**
     * 追加一条结构化消息到会话历史。
     * 超过 40 条时自动压缩旧消息：调用 Python /compress 将旧对话总结为一条摘要。
     */
    public void appendHistory(String sessionId, String role, String content) {
        repo.findById(sessionId).ifPresent(entity -> {
            List<Object> history = fromJson(entity.getHistory());
            history.add(msgObj(role, content));

            // 超过阈值 → 压缩旧消息
            if (history.size() > COMPRESS_THRESHOLD) {
                int compressCount = history.size() - KEEP_RECENT;
                List<Object> oldMessages = new ArrayList<>(history.subList(0, compressCount));
                List<Object> recentMessages = new ArrayList<>(history.subList(compressCount, history.size()));

                String summary = compressHistory(oldMessages);
                if (!summary.isBlank()) {
                    // 用压缩摘要替换旧消息，保留最近的消息
                    Map<String, String> summaryMsg = new LinkedHashMap<>();
                    summaryMsg.put("role", "system");
                    summaryMsg.put("content", "[对话摘要] " + summary);
                    summaryMsg.put("time", LocalDateTime.now().format(TIME_FMT));
                    List<Object> compressed = new ArrayList<>();
                    compressed.add(summaryMsg);
                    compressed.addAll(recentMessages);
                    history = compressed;
                    log.info("历史压缩: session={}, {}→{} 条", sessionId, compressCount + recentMessages.size(), history.size());
                }
            }

            if (history.size() > 50) {
                history = history.subList(history.size() - 50, history.size());
            }
            entity.setHistory(toJson(history));
            entity.touch();
            repo.save(entity);
        });
    }

    /**
     * 追加一份报告到 JSONB 数组（不覆盖历史报告），并标记完成。
     */
    public void appendReport(String sessionId, String report) {
        repo.findById(sessionId).ifPresent(entity -> {
            List<String> reports = fromJsonStringList(entity.getReport());
            reports.add(report);
            entity.setReport(toJson(reports));
            entity.setStatus("done");
            entity.touch();
            repo.save(entity);
            log.info("报告写入: session={}, len={}, 累计 {} 份", sessionId, report.length(), reports.size());
        });
    }

    /** 获取会话最新一份报告（供前端 API 用）。 */
    public String getLatestReport(String sessionId) {
        return repo.findById(sessionId).map(entity -> {
            List<String> reports = fromJsonStringList(entity.getReport());
            return reports.isEmpty() ? "" : reports.get(reports.size() - 1);
        }).orElse("");
    }

    /** 刷新会话的最后活动时间。 */
    public void touch(String sessionId) {
        repo.findById(sessionId).ifPresent(entity -> {
            entity.touch();
            repo.save(entity);
        });
    }

    /** 定时清理：最后活动超过 10 分钟仍为 running 的会话标记为 error。AsyncOpenAI 后 L3/4 通常 5 分钟内完成。 */
    @org.springframework.scheduling.annotation.Scheduled(fixedRate = 300000)
    public void cleanupStaleSessions() {
        List<SessionEntity> all = repo.findAll();
        LocalDateTime cutoff = LocalDateTime.now().minusMinutes(10);
        for (SessionEntity s : all) {
            if ("running".equals(s.getStatus()) && s.getUpdatedAt() != null && s.getUpdatedAt().isBefore(cutoff)) {
                s.setStatus("error");
                repo.save(s);
                log.info("清理僵尸会话: {}", s.getId());
            }
        }
    }

    /** 追问时标记会话为 running + 刷新活动时间。 */
    public void markRunning(String sessionId) {
        repo.findById(sessionId).ifPresent(entity -> {
            entity.setStatus("running");
            entity.touch();
            repo.save(entity);
        });
    }

    public void markError(String sessionId) {
        repo.findById(sessionId).ifPresent(entity -> {
            entity.setStatus("error");
            entity.touch();
            repo.save(entity);
        });
    }

    /**
     * 获取会话完整上下文，传给 Python Agent。
     * 结构化消息 → 格式化为 LLM 可读文本。
     * report 列去重兜底（history 截断时补回）。
     */
    @SuppressWarnings("unchecked")
    public String getContextHistory(String sessionId) {
        return repo.findById(sessionId)
                .map(entity -> {
                    List<Object> history = fromJson(entity.getHistory());
                    StringBuilder ctx = new StringBuilder();

                    // 锚点：原始研究问题，永远不丢（独立字段，不受 history 截断影响）
                    ctx.append("=== 研究主题 ===\n");
                    ctx.append(entity.getQuestion()).append("\n\n");

                    // 格式化历史消息：角色 + 时间 + 内容
                    for (Object item : history) {
                        Map<String, Object> msg = toMsgObject(item);
                        String role = (String) msg.get("role");
                        String content = (String) msg.get("content");
                        String time = (String) msg.getOrDefault("time", "");

                        if ("system".equals(role)) {
                            // 压缩摘要
                            ctx.append("[对话摘要] ").append(content).append("\n\n");
                        } else {
                            String label = "user".equals(role) ? "用户" : "Agent";
                            if (!time.isEmpty()) {
                                ctx.append("[").append(time).append("] ");
                            }
                            ctx.append(label).append(": ").append(content).append("\n\n");
                        }
                    }

                    String historyText = ctx.toString();

                    // report 列兜底：补回 history 截断时丢失的报告，放在最前面（从旧到新）
                    List<String> reports = fromJsonStringList(entity.getReport());
                    List<String> missing = new ArrayList<>();
                    for (String report : reports) {
                        String snippet = report.length() > 200 ? report.substring(0, 200) : report;
                        if (!historyText.contains(snippet)) {
                            missing.add(report);
                        }
                    }
                    if (!missing.isEmpty()) {
                        StringBuilder prefix = new StringBuilder();
                        prefix.append("=== 历史报告（history 截断补回）===\n");
                        for (int i = 0; i < missing.size(); i++) {
                            prefix.append("\n--- 报告 ").append(i + 1).append(" ---\n");
                            prefix.append(missing.get(i));
                        }
                        prefix.append("\n\n");
                        ctx.insert(0, prefix.toString());  // 插在最前面
                    }
                    return ctx.toString();
                })
                .orElse("");
    }

    /**
     * 获取单个会话。
     */
    public SessionEntity getEntity(String sessionId) {
        return repo.findById(sessionId).orElse(null);
    }

    public ResearchSession getSession(String sessionId) {
        return repo.findById(sessionId).map(this::toPojo).orElse(null);
    }

    /**
     * 获取用户的所有会话。
     */
    public List<ResearchSession> getUserSessions(String userId) {
        return repo.findByUserIdOrderByCreatedAtDesc(userId)
                .stream().map(this::toPojo).toList();
    }

    /**
     * 获取全部会话。
     */
    public List<ResearchSession> getAllSessions() {
        return repo.findAll().stream()
                .sorted(Comparator.comparing(SessionEntity::getCreatedAt).reversed())
                .map(this::toPojo).toList();
    }

    // ========== 工具方法 ==========

    private final ObjectMapper objectMapper = new ObjectMapper();

    /** 调用 Python /compress 端点，将旧消息列表压缩为摘要。使用项目共用的 WebClient。 */
    @SuppressWarnings("unchecked")
    private String compressHistory(List<Object> oldMessages) {
        try {
            String body = objectMapper.writeValueAsString(Map.of("messages", oldMessages));
            Map<String, Object> result = webClient.post()
                    .uri("/compress")
                    .bodyValue(Map.of("messages", oldMessages))
                    .retrieve()
                    .bodyToMono(Map.class)
                    .block(Duration.ofSeconds(30));
            if (result != null) {
                String summary = (String) result.getOrDefault("summary", "");
                if (!summary.isBlank()) {
                    log.info("历史压缩完成: {} 条 → {} 字摘要", oldMessages.size(), summary.length());
                }
                return summary;
            }
        } catch (Exception e) {
            log.warn("历史压缩失败（降级为截断）: {}", e.getMessage());
        }
        return "";  // 返回空 → 降级为原截断行为
    }

    private ResearchSession toPojo(SessionEntity e) {
        return new ResearchSession(e.getId(), e.getUserId(), e.getQuestion(),
                getLatestReport(e.getId()), e.getStatus());
    }

    /** 构建结构化消息对象 */
    private static Map<String, String> msgObj(String role, String content) {
        Map<String, String> obj = new LinkedHashMap<>();
        obj.put("role", role);
        obj.put("content", content);
        obj.put("time", LocalDateTime.now().format(TIME_FMT));
        return obj;
    }

    /** 从 JSON 反序列化历史列表。兼容旧格式纯文本字符串和新格式结构化对象。 */
    @SuppressWarnings("unchecked")
    private List<Object> fromJson(String json) {
        try {
            if (json == null || json.isBlank() || "[]".equals(json.trim())) return new ArrayList<>();
            return objectMapper.readValue(json, List.class);
        } catch (Exception e) {
            return new ArrayList<>();
        }
    }

    /** 从 JSON 反序列化纯字符串列表（report 列专用）。 */
    @SuppressWarnings("unchecked")
    private List<String> fromJsonStringList(String json) {
        try {
            if (json == null || json.isBlank() || "[]".equals(json.trim())) return new ArrayList<>();
            List<Object> raw = objectMapper.readValue(json, List.class);
            List<String> result = new ArrayList<>();
            for (Object item : raw) {
                result.add(item instanceof String ? (String) item : item.toString());
            }
            return result;
        } catch (Exception e) {
            return new ArrayList<>();
        }
    }

    /**
     * 将单个历史条目转成标准消息对象。
     * 旧格式（纯文本 "用户: xxx"）→ 转成新格式。
     * 新格式（Map）→ 直接返回。
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> toMsgObject(Object item) {
        if (item instanceof String old) {
            Map<String, Object> obj = new LinkedHashMap<>();
            if (old.startsWith("Agent: ")) {
                obj.put("role", "agent");
                obj.put("content", old.substring(7));
            } else if (old.startsWith("用户: ")) {
                obj.put("role", "user");
                obj.put("content", old.substring(4));
            } else {
                obj.put("role", "unknown");
                obj.put("content", old);
            }
            obj.put("time", "");
            return obj;
        }
        // 新格式：已经是 Map
        return (Map<String, Object>) item;
    }

    private String toJson(Object items) {
        try {
            return objectMapper.writeValueAsString(items);
        } catch (Exception e) {
            return "[]";
        }
    }
}
