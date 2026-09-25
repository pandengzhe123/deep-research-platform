package com.deepresearch.gateway.security;

import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.stereotype.Component;

/**
 * 从请求头解析「已认证用户」与角色。
 *
 * 存在的意义是让所有需要 uid 的控制器共用同一份实现 —— 安全相关的解析逻辑一旦
 * 出现两份拷贝，迟早会分叉（一份修了、另一份没修），而这种分叉正是越权漏洞的温床。
 *
 * 注意：Spring Security 已把未认证请求挡在过滤器链外，走到控制器时 Authorization
 * 头必然合法。返回 "anonymous" 只是防御性兜底：一旦线上真的出现 anonymous，
 * 说明 SecurityConfig 的规则被改松了，应当立刻检查。
 */
@Component
public class RequestUserResolver {

    private final JwtTokenProvider jwt;

    public RequestUserResolver(JwtTokenProvider jwt) {
        this.jwt = jwt;
    }

    /** 解析当前请求的 userId（JWT 的 subject）。无法解析时返回 "anonymous"。 */
    public String resolve(ServerHttpRequest request) {
        String token = bearerToken(request);
        if (token == null) return "anonymous";
        try {
            String uid = jwt.getUserId(token);
            return (uid == null || uid.isBlank()) ? "anonymous" : uid;
        } catch (Exception e) {
            return "anonymous";
        }
    }

    /** 解析当前请求的角色；无有效 token 时返回 null。 */
    public String role(ServerHttpRequest request) {
        String token = bearerToken(request);
        if (token == null) return null;
        try {
            return jwt.getRole(token);
        } catch (Exception e) {
            return null;
        }
    }

    /** 只负责取出并校验；校验失败一律返回 null，调用方不必再判 token 有效性。 */
    private String bearerToken(ServerHttpRequest request) {
        String auth = request.getHeaders().getFirst("Authorization");
        if (auth == null || !auth.startsWith("Bearer ")) return null;
        String token = auth.substring(7);
        return jwt.validateToken(token) ? token : null;
    }
}
