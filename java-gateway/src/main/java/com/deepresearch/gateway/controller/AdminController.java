package com.deepresearch.gateway.controller;

import com.deepresearch.gateway.model.SessionEntity;
import com.deepresearch.gateway.security.UserEntity;
import com.deepresearch.gateway.security.UserRepository;
import com.deepresearch.gateway.service.SessionRepository;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.util.*;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/api/admin")
public class AdminController {

    private final SessionRepository sessionRepo;
    private final UserRepository userRepo;

    public AdminController(SessionRepository sessionRepo, UserRepository userRepo) {
        this.sessionRepo = sessionRepo;
        this.userRepo = userRepo;
    }

    /** 管理仪表盘数据。 */
    @GetMapping("/dashboard")
    public ResponseEntity<Map<String, Object>> dashboard() {
        List<SessionEntity> all = sessionRepo.findAll();

        LocalDate today = LocalDate.now();
        LocalDateTime todayStart = today.atStartOfDay();
        LocalDateTime weekStart = today.minusDays(7).atStartOfDay();
        LocalDateTime monthStart = today.minusDays(30).atStartOfDay();

        List<SessionEntity> todaySessions = filterSince(all, todayStart);
        List<SessionEntity> weekSessions = filterSince(all, weekStart);
        List<SessionEntity> monthSessions = filterSince(all, monthStart);

        Map<String, Object> data = new LinkedHashMap<>();

        // 概览卡片
        long active = all.stream().filter(s -> "running".equals(s.getStatus())).count();
        long allErrors = all.stream().filter(s -> "error".equals(s.getStatus())).count();
        long todayErrors = todaySessions.stream().filter(s -> "error".equals(s.getStatus())).count();
        data.put("totalStudies", all.size());
        data.put("todayStudies", todaySessions.size());
        data.put("activeSessions", active);
        data.put("errorRate", all.size() == 0 ? 0 : Math.round(allErrors * 100.0 / all.size()));
        data.put("todayErrorRate", todaySessions.isEmpty() ? 0 : Math.round(todayErrors * 100.0 / todaySessions.size()));
        data.put("totalUsers", userRepo.count());

        // 按日期统计研究次数（柱状图用）
        data.put("dailyCounts7", dailyCounts(weekSessions, 7));
        data.put("dailyCounts30", dailyCounts(monthSessions, 30));

        // 按状态统计（累计）
        Map<String, Long> byStatus = all.stream()
                .collect(Collectors.groupingBy(SessionEntity::getStatus, Collectors.counting()));
        data.put("byStatus", byStatus);

        // 用户统计
        data.put("userStats", buildUserStats(all, todaySessions));

        // Token 消耗统计
        data.put("tokenStats", buildTokenStats(all));
        data.put("dailyTokens30", dailyTokens(monthSessions, 30));

        return ResponseEntity.ok(data);
    }

    private List<SessionEntity> filterSince(List<SessionEntity> list, LocalDateTime since) {
        return list.stream()
                .filter(s -> s.getCreatedAt() != null && s.getCreatedAt().isAfter(since))
                .toList();
    }

    private List<Map<String, Object>> dailyCounts(List<SessionEntity> sessions, int days) {
        List<Map<String, Object>> result = new ArrayList<>();
        for (int i = days - 1; i >= 0; i--) {
            LocalDate d = LocalDate.now().minusDays(i);
            long count = sessions.stream()
                    .filter(s -> s.getCreatedAt() != null && s.getCreatedAt().toLocalDate().equals(d))
                    .count();
            result.add(Map.of("date", d.toString(), "count", count));
        }
        return result;
    }

    private List<Map<String, Object>> dailyTokens(List<SessionEntity> sessions, int days) {
        List<Map<String, Object>> result = new ArrayList<>();
        for (int i = days - 1; i >= 0; i--) {
            LocalDate d = LocalDate.now().minusDays(i);
            long tokens = 0;
            for (SessionEntity s : sessions) {
                if (s.getCreatedAt() != null && s.getCreatedAt().toLocalDate().equals(d)) {
                    tokens += extractTokens(s);
                }
            }
            result.add(Map.of("date", d.toString(), "tokens", tokens));
        }
        return result;
    }

    private long extractTokens(SessionEntity s) {
        try {
            if (s.getTokenUsage() != null && !s.getTokenUsage().equals("{}")) {
                var usage = new com.fasterxml.jackson.databind.ObjectMapper().readTree(s.getTokenUsage());
                long p = usage.has("total_prompt_tokens") ? usage.get("total_prompt_tokens").asLong() : 0;
                long c = usage.has("total_completion_tokens") ? usage.get("total_completion_tokens").asLong() : 0;
                return p + c;
            }
        } catch (Exception ignored) {}
        return 0;
    }

    private List<Map<String, Object>> buildUserStats(List<SessionEntity> all, List<SessionEntity> today) {
        List<Map<String, Object>> result = new ArrayList<>();
        Map<String, Long> counts = all.stream()
                .collect(Collectors.groupingBy(SessionEntity::getUserId, Collectors.counting()));
        Map<String, Long> todayCounts = today.stream()
                .collect(Collectors.groupingBy(SessionEntity::getUserId, Collectors.counting()));
        Map<String, String> idToName = new HashMap<>();
        for (UserEntity u : userRepo.findAll()) idToName.put(String.valueOf(u.getId()), u.getUsername());
        for (var entry : counts.entrySet()) {
            String uid = entry.getKey();
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("userId", uid);
            item.put("username", idToName.getOrDefault(uid, uid));
            item.put("totalCount", entry.getValue());
            item.put("todayCount", todayCounts.getOrDefault(uid, 0L));
            result.add(item);
        }
        result.sort((a, b) -> Long.compare((Long) b.get("totalCount"), (Long) a.get("totalCount")));
        return result;
    }

    private Map<String, Object> buildTokenStats(List<SessionEntity> all) {
        long totalPrompt = 0, totalCompletion = 0;
        for (SessionEntity s : all) {
            try {
                if (s.getTokenUsage() != null && !s.getTokenUsage().equals("{}")) {
                    var usage = new com.fasterxml.jackson.databind.ObjectMapper().readTree(s.getTokenUsage());
                    totalPrompt += usage.has("total_prompt_tokens") ? usage.get("total_prompt_tokens").asLong() : 0;
                    totalCompletion += usage.has("total_completion_tokens") ? usage.get("total_completion_tokens").asLong() : 0;
                }
            } catch (Exception ignored) {}
        }
        Map<String, Object> tokenStats = new LinkedHashMap<>();
        tokenStats.put("totalPromptTokens", totalPrompt);
        tokenStats.put("totalCompletionTokens", totalCompletion);
        tokenStats.put("totalTokens", totalPrompt + totalCompletion);
        return tokenStats;
    }

    /** 用户列表 + 研究次数统计。 */
    @GetMapping("/users")
    public ResponseEntity<List<Map<String, Object>>> users() {
        List<SessionEntity> all = sessionRepo.findAll();
        Map<String, Long> counts = all.stream()
                .collect(Collectors.groupingBy(SessionEntity::getUserId, Collectors.counting()));

        List<UserEntity> users = userRepo.findAll();
        List<Map<String, Object>> result = new ArrayList<>();
        for (UserEntity u : users) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("id", u.getId());
            item.put("username", u.getUsername());
            item.put("role", u.getRole());
            item.put("enabled", u.getEnabled());
            item.put("researchCount", counts.getOrDefault(String.valueOf(u.getId()), 0L));
            item.put("createdAt", u.getCreatedAt());
            result.add(item);
        }
        return ResponseEntity.ok(result);
    }

    /** 启用/禁用用户。 */
    @PutMapping("/users/{id}/status")
    public ResponseEntity<Map<String, Object>> toggleUser(
            @PathVariable Long id, @RequestBody Map<String, Boolean> body) {
        UserEntity user = userRepo.findById(id).orElse(null);
        if (user == null) {
            return ResponseEntity.notFound().build();
        }
        Boolean enabled = body.getOrDefault("enabled", true);
        user.setEnabled(enabled);
        userRepo.save(user);
        return ResponseEntity.ok(Map.of("status", "ok", "userId", id, "enabled", enabled));
    }

    /** 修改用户角色。 */
    @PutMapping("/users/{id}/role")
    public ResponseEntity<Map<String, Object>> updateRole(
            @PathVariable Long id, @RequestBody Map<String, String> body) {
        UserEntity user = userRepo.findById(id).orElse(null);
        if (user == null) {
            return ResponseEntity.notFound().build();
        }
        String role = body.getOrDefault("role", "user");
        user.setRole(role);
        userRepo.save(user);
        return ResponseEntity.ok(Map.of("status", "ok", "userId", id, "role", role));
    }
}
