package com.deepresearch.gateway.controller;

import com.deepresearch.gateway.security.AuthRateLimiter;
import com.deepresearch.gateway.security.JwtTokenProvider;
import com.deepresearch.gateway.security.UserEntity;
import com.deepresearch.gateway.security.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;

@RestController
@RequestMapping("/api/auth")
public class AuthController {

    private static final Logger log = LoggerFactory.getLogger(AuthController.class);

    /** 密码最短长度。原来是 4 位 —— 在线暴力破解几秒就穿了。 */
    private static final int MIN_PASSWORD_LENGTH = 8;

    private final UserRepository userRepo;
    private final JwtTokenProvider jwt;
    private final AuthRateLimiter rateLimiter;
    private final BCryptPasswordEncoder encoder = new BCryptPasswordEncoder();

    /**
     * 注册邀请码。留空 = 彻底关闭注册。
     *
     * <p>公网可自由注册等于把你的付费 API 额度开放给全世界：注册完就能跑研究，
     * 而单次 L4 研究实测消耗 232 万 prompt token。所以「默认关闭、需要显式开启」
     * 才是正确的默认姿态。
     */
    private final String inviteCode;

    public AuthController(UserRepository userRepo,
                          JwtTokenProvider jwt,
                          AuthRateLimiter rateLimiter,
                          @Value("${app.register-invite-code:}") String inviteCode) {
        this.userRepo = userRepo;
        this.jwt = jwt;
        this.rateLimiter = rateLimiter;
        this.inviteCode = inviteCode == null ? "" : inviteCode.trim();
        if (this.inviteCode.isBlank()) {
            log.info("未配置 REGISTER_INVITE_CODE，注册接口已关闭（本站为演示站时这是预期行为）");
        }
    }

    /** 注册。需要邀请码；未配置邀请码时接口整体关闭。 */
    @PostMapping("/register")
    public ResponseEntity<Map<String, Object>> register(@RequestBody Map<String, String> body,
                                                        ServerHttpRequest request) {
        if (!rateLimiter.allow(clientIp(request))) {
            return tooMany();
        }

        if (inviteCode.isBlank()) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN)
                    .body(Map.of("status", "error", "message", "本站已关闭注册"));
        }
        if (!constantTimeEquals(body.get("inviteCode"), inviteCode)) {
            // 不区分「没填」和「填错」，也绝不在日志里记录提交的邀请码
            return ResponseEntity.status(HttpStatus.FORBIDDEN)
                    .body(Map.of("status", "error", "message", "邀请码不正确"));
        }

        String username = body.get("username");
        String password = body.get("password");

        if (username == null || password == null || username.isBlank() || password.isBlank()) {
            return ResponseEntity.badRequest().body(Map.of("status", "error", "message", "用户名和密码不能为空"));
        }
        if (username.length() > 64) {
            return ResponseEntity.badRequest().body(Map.of("status", "error", "message", "用户名过长"));
        }
        if (password.length() < MIN_PASSWORD_LENGTH) {
            return ResponseEntity.badRequest()
                    .body(Map.of("status", "error", "message", "密码至少 " + MIN_PASSWORD_LENGTH + " 位"));
        }
        if (userRepo.existsByUsername(username)) {
            return ResponseEntity.badRequest().body(Map.of("status", "error", "message", "用户名已存在"));
        }

        UserEntity user = new UserEntity(username, encoder.encode(password));
        userRepo.save(user);
        log.info("用户注册: {}", username);

        String token = jwt.generateToken(String.valueOf(user.getId()), username, user.getRole());
        return ResponseEntity.ok(Map.of("status", "ok", "token", token, "username", username, "role", user.getRole()));
    }

    /** 登录。 */
    @PostMapping("/login")
    public ResponseEntity<Map<String, Object>> login(@RequestBody Map<String, String> body,
                                                     ServerHttpRequest request) {
        if (!rateLimiter.allow(clientIp(request))) {
            return tooMany();
        }

        String username = body.get("username");
        String password = body.get("password");

        if (username == null || password == null) {
            return ResponseEntity.badRequest().body(Map.of("status", "error", "message", "用户名和密码不能为空"));
        }

        UserEntity user = userRepo.findByUsername(username).orElse(null);
        // 用户不存在与密码错误返回同一句话，避免暴露哪些用户名有效
        if (user == null || !encoder.matches(password, user.getPasswordHash())) {
            log.warn("登录失败: username={}, ip={}", username, clientIp(request));
            return ResponseEntity.status(401).body(Map.of("status", "error", "message", "用户名或密码错误"));
        }
        if (Boolean.FALSE.equals(user.getEnabled())) {
            return ResponseEntity.status(403).body(Map.of("status", "error", "message", "账号已被禁用"));
        }

        log.info("用户登录: {}", username);
        String token = jwt.generateToken(String.valueOf(user.getId()), username, user.getRole());
        return ResponseEntity.ok(Map.of("status", "ok", "token", token, "username", username, "role", user.getRole()));
    }

    private ResponseEntity<Map<String, Object>> tooMany() {
        return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS)
                .body(Map.of("status", "error", "message", "操作过于频繁，请稍后再试"));
    }

    /**
     * 取客户端 IP。
     *
     * <p>优先 X-Forwarded-For 的第一段（真实客户端），它由 nginx 覆写 ——
     * 这也是为什么 nginx 那边必须显式设置该头：否则所有请求看起来都来自
     * nginx 容器 IP，按 IP 限流就退化成「全站共享一个桶」。
     */
    private String clientIp(ServerHttpRequest request) {
        String xff = request.getHeaders().getFirst("X-Forwarded-For");
        if (xff != null && !xff.isBlank()) {
            int comma = xff.indexOf(',');
            return (comma > 0 ? xff.substring(0, comma) : xff).trim();
        }
        var remote = request.getRemoteAddress();
        return remote != null && remote.getAddress() != null ? remote.getAddress().getHostAddress() : "unknown";
    }

    /** 常量时间比较，避免通过响应耗时逐字符猜邀请码。 */
    private boolean constantTimeEquals(String provided, String expected) {
        if (provided == null) return false;
        return MessageDigest.isEqual(
                provided.getBytes(StandardCharsets.UTF_8),
                expected.getBytes(StandardCharsets.UTF_8));
    }
}
