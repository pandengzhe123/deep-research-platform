package com.deepresearch.gateway.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * 请求 & 响应模型 —— 与 Python Agent 的 API 契约完全一致。
 */
public class ResearchModels {

    // ========== 请求 ==========

    /**
     * 发送给 Python Agent 的研究请求。
     * 对应 POST /research 和 POST /research/stream 的请求体。
     */
    public record ResearchRequest(
            String question,
            int level,
            @JsonProperty("max_rounds") Integer maxRounds,
            String language,
            String context,
            @JsonProperty("kb_enabled") Boolean kbEnabled,
            @JsonProperty("user_id") String userId,
            @JsonProperty("session_id") String sessionId
    ) {
        public ResearchRequest {
            if (level == 0) level = 2;
            if (language == null || language.isBlank()) language = "auto";
        }

        /** 快速构造：只传问题，默认 Level 2。 */
        public ResearchRequest(String question) {
            this(question, 2, null, "auto", "", null, null, null);
        }
    }

    // ========== 响应（同步） ==========

    /**
     * Python Agent 返回的研究结果。
     * 对应 POST /research 的响应体。
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record ResearchResponse(
            String report,
            String language,
            @JsonProperty("need_clarify") Boolean needClarify,
            String question,
            @JsonProperty("session_id") String sessionId
    ) {}

    // ========== 会话 ==========

    /**
     * 网关内部使用的研究会话。
     */
    public static class ResearchSession {
        private final String id;
        private final String userId;
        private final String question;
        private String report = "";
        private String status = "running"; // running | done | error

        public ResearchSession(String id, String userId, String question) {
            this.id = id;
            this.userId = userId;
            this.question = question;
        }

        public String getId() { return id; }
        public String getUserId() { return userId; }
        public String getQuestion() { return question; }
        public String getReport() { return report; }
        public String getStatus() { return status; }

        public void setReport(String report) { this.report = report; }
        public void setStatus(String status) { this.status = status; }
    }
}
