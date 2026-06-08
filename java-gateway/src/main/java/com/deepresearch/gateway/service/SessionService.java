package com.deepresearch.gateway.service;

import com.deepresearch.gateway.model.ResearchModels.ResearchSession;
import com.deepresearch.gateway.model.SessionEntity;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.*;

/**
 * 研究会话管理 —— PostgreSQL 持久化。
 *
 * 每个会话记录：id、用户、问题、对话历史、状态、最终报告。
 * 报告生成后不清空 history —— 后续追问能持续。
 */
@Service
public class SessionService {

    private static final Logger log = LoggerFactory.getLogger(SessionService.class);
    private final SessionRepository repo;

    public SessionService(SessionRepository repo) {
        this.repo = repo;
    }

    /**
     * 创建新会话，写入数据库。
     */
    public ResearchSession createSession(String userId, String question) {
        String id = UUID.randomUUID().toString().substring(0, 8);
        SessionEntity entity = new SessionEntity(id, userId, question);
        // 初始历史：第一条用户消息
        entity.setHistory(toJson(List.of("用户: " + question)));
        repo.save(entity);
        log.info("创建会话: id={}, user={}", id, userId);
        return toPojo(entity);
    }

    /**
     * 追加一条消息到会话历史。报告生成后不清空，允许后续追问。
     */
    public void appendHistory(String sessionId, String message) {
        repo.findById(sessionId).ifPresent(entity -> {
            List<String> history = fromJson(entity.getHistory());
            history.add(message);
            // 只保留最近 50 条，防止 JSONB 过大
            if (history.size() > 50) {
                history = history.subList(history.size() - 50, history.size());
            }
            entity.setHistory(toJson(history));
            repo.save(entity);
        });
    }

    /**
     * 写入报告并标记完成。
     */
    public void appendReport(String sessionId, String report) {
        repo.findById(sessionId).ifPresent(entity -> {
            entity.setReport(report);
            entity.setStatus("done");
            repo.save(entity);
            log.info("报告写入: session={}, len={}", sessionId, report.length());
        });
    }

    /**
     * 标记会话为错误。
     */
    public void markError(String sessionId) {
        repo.findById(sessionId).ifPresent(entity -> {
            entity.setStatus("error");
            repo.save(entity);
        });
    }

    /**
     * 获取会话完整历史，拼成 context 字符串传给 Python。
     */
    public String getContextHistory(String sessionId) {
        return repo.findById(sessionId)
                .map(entity -> String.join("\n\n", fromJson(entity.getHistory())))
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

    private final com.fasterxml.jackson.databind.ObjectMapper objectMapper =
            new com.fasterxml.jackson.databind.ObjectMapper();

    private ResearchSession toPojo(SessionEntity e) {
        ResearchSession s = new ResearchSession(e.getId(), e.getUserId(), e.getQuestion());
        s.setReport(e.getReport() != null ? e.getReport() : "");
        s.setStatus(e.getStatus());
        return s;
    }

    @SuppressWarnings("unchecked")
    private List<String> fromJson(String json) {
        try {
            if (json == null || json.isBlank() || "[]".equals(json.trim())) return new ArrayList<>();
            return objectMapper.readValue(json, List.class);
        } catch (Exception e) {
            return new ArrayList<>();
        }
    }

    private String toJson(List<String> items) {
        try {
            return objectMapper.writeValueAsString(items);
        } catch (Exception e) {
            return "[]";
        }
    }
}
