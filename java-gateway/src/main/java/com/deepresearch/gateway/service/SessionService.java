package com.deepresearch.gateway.service;

import com.deepresearch.gateway.model.ResearchModels.ResearchSession;
import com.deepresearch.gateway.model.SessionEntity;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.*;

/**
 * 研究会话管理 —— PostgreSQL 持久化 + Redis 热层（冷热分层）。
 *
 * 历史消息格式（JSONB 数组，每项是一个对象）：
 * {"role":"user","content":"...","time":"2026-06-26T10:30:00"}
 * {"role":"agent","content":"...","time":"2026-06-26T10:35:00"}
 *
 * 旧格式兼容：纯文本字符串 "用户: xxx" / "Agent: xxx" 在读取时自动识别。
 *
 * 冷热分层（TODO R4）：
 * - PG（冷层/权威）：history JSONB 全量 + report 全文，永久保存
 * - Redis（热层）：history:{sid} List 存消息，拼 context 时只读它
 * - 写入：先 PG 后 Redis；压缩/硬截断 → DEL key（下次读时从 PG 重建）
 * - 一致性：Redis 要么是 PG 的完整镜像，要么不存在，绝无半同步中间态
 * - report 全文不进 Redis（防内存爆炸），需要时按需回 PG 取
 */
@Service
public class SessionService {

    private static final Logger log = LoggerFactory.getLogger(SessionService.class);
    private static final DateTimeFormatter TIME_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm");
    private static final int COMPRESS_THRESHOLD = 40;  // 历史消息达此数量时触发压缩
    private static final int KEEP_RECENT = 25;          // 压缩后保留最近 N 条，压缩旧的
    private static final String HISTORY_KEY_PREFIX = "history:";
    private static final Duration HISTORY_TTL = Duration.ofHours(24);  // 热层空闲自动回收

    /**
     * 追加消息的原子脚本：key 存在才 RPUSH + EXPIRE（B10/B21）。
     * 两步分开会有两个问题：① exists 与 rpush 之间 key 过期 → 写出「半截列表」；
     * ② rpush 成功但 expire 失败 → key 无 TTL 永久驻留。
     */
    private static final DefaultRedisScript<Long> APPEND_HISTORY_SCRIPT = new DefaultRedisScript<>(
            "if redis.call('exists', KEYS[1]) == 1 then "
                    + "  redis.call('rpush', KEYS[1], ARGV[1]) "
                    + "  redis.call('expire', KEYS[1], ARGV[2]) "
                    + "  return 1 "
                    + "end "
                    + "return 0",
            Long.class);

    private final SessionRepository repo;
    private final WebClient webClient;
    private final StringRedisTemplate redis;

    public SessionService(SessionRepository repo, WebClient agentWebClient, StringRedisTemplate redis) {
        this.repo = repo;
        this.webClient = agentWebClient;
        this.redis = redis;
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
    @org.springframework.transaction.annotation.Transactional
    public void appendHistory(String sessionId, String role, String content) {
        // 行锁读取（B9）：同会话并发追加串行化，避免后写覆盖前写导致 PG 丢消息
        repo.findByIdForUpdate(sessionId).ifPresent(entity -> {
            List<Object> history = fromJson(entity.getHistory());
            // 只构造一次消息对象：PG 与 Redis 必须是同一条（含相同的 time），
            // 否则「Redis == PG」的镜像语义不成立（B22）
            Map<String, String> newMsg = msgObj(role, content);
            history.add(newMsg);

            boolean restructured = false;   // 是否发生结构性变更（压缩/硬截断）→ 热层需失效

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
                    restructured = true;
                    log.info("历史压缩: session={}, {}→{} 条", sessionId, compressCount + recentMessages.size(), history.size());
                }
            }

            if (history.size() > 50) {
                history = history.subList(history.size() - 50, history.size());
                restructured = true;
            }
            entity.setHistory(toJson(history));
            entity.touch();
            repo.save(entity);                                  // ① PG 先落（权威）

            syncHistoryCache(sessionId, newMsg, restructured);  // ② Redis 后同步
        });
    }

    /**
     * 同步 Redis 热层（先 PG 后 Redis 的第二步）。
     *
     * - 结构未变 → RPUSH 追加新消息
     * - 压缩/硬截断 → DEL key（下次读时从 PG 全量重建）
     * - key 不存在（TTL 过期/首次）→ 不追加，等读取时重建，避免出现「半截列表」
     * - Redis 异常 → 只记日志，不影响主流程（可用性优先）
     */
    private void syncHistoryCache(String sessionId, Map<String, String> newMsg, boolean restructured) {
        String key = HISTORY_KEY_PREFIX + sessionId;
        try {
            if (restructured) {
                redis.delete(key);
                log.info("历史结构变更 → 失效 Redis 热层: session={}", sessionId);
                return;
            }
            // 原子脚本：key 存在才 RPUSH + EXPIRE（避免半截列表 / 无 TTL 残留）
            Long appended = redis.execute(
                    APPEND_HISTORY_SCRIPT, List.of(key),
                    toJson(newMsg), String.valueOf(HISTORY_TTL.getSeconds()));
            if (appended == null || appended == 0L) {
                // 热层不存在（TTL 过期/首次）→ 不写半截列表，交给 loadHistory 重建
                log.debug("Redis 热层不存在，跳过追加（下次读取时重建）: session={}", sessionId);
            }
        } catch (Exception e) {
            log.warn("Redis 同步历史失败，丢弃热层（下次读重建）: session={}, err={}", sessionId, e.getMessage());
            // 宁可下次从 PG 重建，也不要留下「PG 有、Redis 没有」的脏镜像
            try {
                redis.delete(key);
            } catch (Exception ignore) {
                // best-effort，忽略
            }
        }
    }

    /**
     * 读取会话历史消息：Redis 热层优先，miss 则从 PG 重建（冷热分层核心）。
     * Redis 异常 → 回退 PG，保证功能可用。
     */
    @SuppressWarnings("unchecked")
    private List<Object> loadHistory(String sessionId, SessionEntity entity) {
        String key = HISTORY_KEY_PREFIX + sessionId;
        try {
            List<String> raw = redis.opsForList().range(key, 0, -1);
            if (raw != null && !raw.isEmpty()) {
                try {
                    List<Object> msgs = new ArrayList<>(raw.size());
                    for (String s : raw) {
                        msgs.add(objectMapper.readValue(s, Map.class));
                    }
                    redis.expire(key, HISTORY_TTL);   // 命中即续期：活跃会话不应被空闲回收（B23）
                    return msgs;
                } catch (Exception parseErr) {
                    // 热层内容损坏（如旧格式纯文本条目 "用户: xxx" 无法反序列化为 Map）
                    // → 丢弃该 key，走下面从 PG 重建，避免每次读都异常回退、热层永不生效
                    log.warn("Redis 热层内容不可解析，丢弃重建: session={}, err={}", sessionId, parseErr.getMessage());
                    redis.delete(key);
                }
            }
            // 冷启动 / 热层过期 / 内容损坏 / 压缩后 → 从 PG 全量重建
            List<Object> history = fromJson(entity.getHistory());
            if (!history.isEmpty()) {
                List<String> jsonList = new ArrayList<>(history.size());
                for (Object m : history) {
                    // 规范化：纯文本旧格式条目经 toMsgObject 转成 {role,content,time}
                    jsonList.add(toJson(toMsgObject(m)));
                }
                redis.opsForList().rightPushAll(key, jsonList);
                redis.expire(key, HISTORY_TTL);
                log.info("Redis 重建会话历史: session={}, {} 条", sessionId, jsonList.size());
            }
            return history;
        } catch (Exception e) {
            log.warn("Redis 读取历史失败，回退 PG: session={}, err={}", sessionId, e.getMessage());
            return fromJson(entity.getHistory());
        }
    }

    /**
    /** 更新 token 消耗统计。 */
    public void updateTokenUsage(String sessionId, String tokenUsageJson) {
        repo.findById(sessionId).ifPresent(entity -> {
            entity.setTokenUsage(tokenUsageJson);
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
                    // 冷热分层：history 消息优先从 Redis 热层读（miss 自动从 PG 重建）
                    List<Object> history = loadHistory(sessionId, entity);

                    // 锚点：原始研究问题，永远不丢（独立字段，不受 history 截断影响）
                    String anchor = "=== 研究主题 ===\n" + entity.getQuestion() + "\n\n";

                    // 格式化历史消息：角色 + 时间 + 内容
                    StringBuilder hist = new StringBuilder();
                    for (Object item : history) {
                        Map<String, Object> msg = toMsgObject(item);
                        String role = (String) msg.get("role");
                        String content = (String) msg.get("content");
                        String time = (String) msg.getOrDefault("time", "");

                        if ("system".equals(role)) {
                            // 存储时已带 [对话摘要] 前缀，这里避免重复添加（兼容未带前缀的旧数据）
                            String prefix = content.startsWith("[对话摘要]") ? "" : "[对话摘要] ";
                            hist.append(prefix).append(content).append("\n\n");
                        } else {
                            String label = "user".equals(role) ? "用户" : "Agent";
                            if (!time.isEmpty()) {
                                hist.append("[").append(time).append("] ");
                            }
                            hist.append(label).append(": ").append(content).append("\n\n");
                        }
                    }
                    String historyText = hist.toString();

                    // report 列兜底：补回 history 截断时丢失的报告（用前 200 字指纹判定）
                    List<String> reports = fromJsonStringList(entity.getReport());
                    List<String> missing = new ArrayList<>();
                    String searchable = anchor + historyText;
                    for (String report : reports) {
                        String snippet = report.length() > 200 ? report.substring(0, 200) : report;
                        if (!searchable.contains(snippet)) {
                            missing.add(report);
                        }
                    }
                    if (missing.isEmpty()) {
                        return anchor + historyText;
                    }

                    // 补回段放在【锚点之后、history 之前】：
                    // 锚点始终是最前（唯一稳定的前缀起点），补回的历史报告紧跟其后
                    // （阅读顺序：主题 → 历史背景 → 近期对话）
                    StringBuilder prefix = new StringBuilder();
                    prefix.append("=== 历史报告（history 截断补回）===\n");
                    for (int i = 0; i < missing.size(); i++) {
                        prefix.append("\n--- 报告 ").append(i + 1).append(" ---\n");
                        prefix.append(missing.get(i));
                    }
                    prefix.append("\n\n");

                    return anchor + prefix + historyText;
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
