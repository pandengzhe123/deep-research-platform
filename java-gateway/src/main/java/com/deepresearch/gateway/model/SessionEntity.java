package com.deepresearch.gateway.model;

import jakarta.persistence.*;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;
import java.time.LocalDateTime;

@Entity
@Table(name = "sessions")
public class SessionEntity {

    @Id
    private String id;

    @Column(name = "user_id", nullable = false)
    private String userId;

    @Column(columnDefinition = "TEXT")
    private String question;

    @Column(columnDefinition = "TEXT")
    private String report;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "jsonb")
    private String history = "[]";

    @Column(name = "search_mode")
    private String searchMode = "hybrid";

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "rag_docs", columnDefinition = "jsonb")
    private String ragDocs = "[]";

    private String status = "running";

    @Column(name = "created_at")
    private LocalDateTime createdAt = LocalDateTime.now();

    public SessionEntity() {}

    public SessionEntity(String id, String userId, String question) {
        this.id = id;
        this.userId = userId;
        this.question = question;
    }

    // getters / setters
    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getUserId() { return userId; }
    public void setUserId(String userId) { this.userId = userId; }
    public String getQuestion() { return question; }
    public void setQuestion(String question) { this.question = question; }
    public String getReport() { return report; }
    public void setReport(String report) { this.report = report; }
    public String getHistory() { return history; }
    public void setHistory(String history) { this.history = history; }
    public String getSearchMode() { return searchMode; }
    public void setSearchMode(String searchMode) { this.searchMode = searchMode; }
    public String getRagDocs() { return ragDocs; }
    public void setRagDocs(String ragDocs) { this.ragDocs = ragDocs; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
}
