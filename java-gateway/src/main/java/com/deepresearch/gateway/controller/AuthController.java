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
import org.springframework.web.bind.annotation.GetMapping;
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

    /**
     * 是否允许任何人直接注册（无需邀请码）。
     *
     * <p>注册共三态，由两个变量组合决定：
     * <ul>
     *   <li>{@code register-open=true} → <b>open</b>：任何人都能注册，邀请码不参与</li>
     *   <li>{@code register-open=false} + 配了 {@code REGISTER_INVITE_CODE} → <b>invite</b>：要填对邀请码</li>
     *   <li>{@code register-open=false} + 没配邀请码 → <b>closed</b>：注册接口整体关闭</li>
     * </ul>
     *
     * <p>为什么要有 open 这一态：早期只有「关闭 / 要邀请码」两态，而"完全开放"在
     * 语义上不是这两者中的任何一个 —— 用邀请码硬凑会变成「把邀请码公开写在页面上」的
     * 怪状态。三态各自表达一件事，读配置时不用猜。
     */
    private final boolean registerOpen;

    public AuthController(UserRepository userRepo,
                          JwtTokenProvider jwt,
                          AuthRateLimiter rateLimiter,
                          @Value("${app.register-invite-code:}") String inviteCode,
                          @Value("${app.register-open:false}") boolean registerOpen) {
        this.userRepo = userRepo;
        this.jwt = jwt;
        this.rateLimiter = rateLimiter;
        this.inviteCode = inviteCode == null ? "" : inviteCode.trim();
        this.registerOpen = registerOpen;
        log.info("注册模式: {}", mode());
        if ("closed".equals(mode())) {
            log.info("如需放开注册：设 REGISTER_OPEN=true（任何人可注册），"
                    + "或设 REGISTER_INVITE_CODE=<邀请码>（需邀请码）");
        }
    }

    /** open / invite / closed */
    private String mode() {
        if (registerOpen) return "open";
        return inviteCode.isBlank() ? "closed" : "invite";
    }

    /**
     * 公开告知注册状态，让前端如实渲染。
     *
     * <p>加这个端点的原因：注册关闭时登录页仍摆着一个可以点的「注册新账号」按钮 ——
     * 用户只能靠点下去、拿到 403 才知道关着，体验上就是「点了没反应 / 莫名其妙失败」。
     *
     * <p>返回这个信息不构成信息泄露：任何人直接调一次注册接口就能得到同样的结论。
     * permitAll 已覆盖 /api/auth/**，无需额外配置。
     */
    @GetMapping("/register-open")
    public ResponseEntity<Map<String, Object>> registerOpen() {
        String m = mode();
        return ResponseEntity.ok(Map.of(
                "open", !"closed".equals(m),
                "mode", m,
                // 前端据此决定要不要显示邀请码输入框
                "inviteRequired", "invite".equals(m)));
    }

    /** 注册。按当前模式决定是否需要邀请码。 */
    @PostMapping("/register")
    public ResponseEntity<Map<String, Object>> register(@RequestBody Map<String, String> body,
                                                        ServerHttpRequest request) {
        if (!rateLimiter.allow(clientIp(request))) {
            return tooMany();
        }

        if ("closed".equals(mode())) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN)
                    .body(Map.of("status", "error", "message", "本站已关闭注册"));
        }
        if ("invite".equals(mode()) && !constantTimeEquals(body.get("inviteCode"), inviteCode)) {
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
