package com.deepresearch.gateway.service;

import com.deepresearch.gateway.model.ResearchModels.ResearchSession;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 研究会话管理 —— 当前用内存存储，后续可替换为数据库。
 *
 * 每个会话记录：id、用户、问题、状态、最终报告。
 */
@Service
public class SessionService {

    private static final Logger log = LoggerFactory.getLogger(SessionService.class);
    private final Map<String, ResearchSession> sessions = new ConcurrentHashMap<>();

    /**
     * 创建一个新会话。
     */
    public ResearchSession createSession(String userId, String question) {
        String id = UUID.randomUUID().toString().substring(0, 8);
        ResearchSession session = new ResearchSession(id, userId, question);
        sessions.put(id, session);
        log.info("创建会话: id={}, question={}", id, question);
        return session;
    }

    /**
     * 写入报告并标记完成。
     */
    public void appendReport(String sessionId, String report) {
        ResearchSession session = sessions.get(sessionId);
        if (session != null) {
            session.setReport(report);
            session.setStatus("done");
        }
    }

    /**
     * 标记会话为错误。
     */
    public void markError(String sessionId) {
        ResearchSession session = sessions.get(sessionId);
        if (session != null) session.setStatus("error");
    }

    /**
     * 获取单个会话。
     */
    public ResearchSession getSession(String sessionId) {
        return sessions.get(sessionId);
    }

    /**
     * 获取用户的所有会话，按创建时间倒序。
     */
    public List<ResearchSession> getUserSessions(String userId) {
        return sessions.values().stream()
                .filter(s -> userId.equals(s.getUserId()))
                .sorted(Comparator.comparing(ResearchSession::getId).reversed())
                .toList();
    }

    /**
     * 获取全部会话（管理用）。
     */
    public List<ResearchSession> getAllSessions() {
        return sessions.values().stream()
                .sorted(Comparator.comparing(ResearchSession::getId).reversed())
                .toList();
    }
}
