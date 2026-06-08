package com.deepresearch.gateway.controller;

import com.deepresearch.gateway.security.JwtTokenProvider;
import com.deepresearch.gateway.security.UserEntity;
import com.deepresearch.gateway.security.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/api/auth")
public class AuthController {

    private static final Logger log = LoggerFactory.getLogger(AuthController.class);
    private final UserRepository userRepo;
    private final JwtTokenProvider jwt;
    private final BCryptPasswordEncoder encoder = new BCryptPasswordEncoder();

    public AuthController(UserRepository userRepo, JwtTokenProvider jwt) {
        this.userRepo = userRepo;
        this.jwt = jwt;
    }

    /** 注册。 */
    @PostMapping("/register")
    public ResponseEntity<Map<String, Object>> register(@RequestBody Map<String, String> body) {
        String username = body.get("username");
        String password = body.get("password");

        if (username == null || password == null || username.isBlank() || password.isBlank()) {
            return ResponseEntity.badRequest().body(Map.of("status", "error", "message", "用户名和密码不能为空"));
        }
        if (password.length() < 4) {
            return ResponseEntity.badRequest().body(Map.of("status", "error", "message", "密码至少 4 位"));
        }
        if (userRepo.existsByUsername(username)) {
            return ResponseEntity.badRequest().body(Map.of("status", "error", "message", "用户名已存在"));
        }

        UserEntity user = new UserEntity(username, encoder.encode(password));
        userRepo.save(user);
        log.info("用户注册: {}", username);

        String token = jwt.generateToken(String.valueOf(user.getId()), username);
        return ResponseEntity.ok(Map.of("status", "ok", "token", token, "username", username));
    }

    /** 登录。 */
    @PostMapping("/login")
    public ResponseEntity<Map<String, Object>> login(@RequestBody Map<String, String> body) {
        String username = body.get("username");
        String password = body.get("password");

        if (username == null || password == null) {
            return ResponseEntity.badRequest().body(Map.of("status", "error", "message", "用户名和密码不能为空"));
        }

        UserEntity user = userRepo.findByUsername(username).orElse(null);
        if (user == null || !encoder.matches(password, user.getPasswordHash())) {
            return ResponseEntity.status(401).body(Map.of("status", "error", "message", "用户名或密码错误"));
        }

        log.info("用户登录: {}", username);
        String token = jwt.generateToken(String.valueOf(user.getId()), username);
        return ResponseEntity.ok(Map.of("status", "ok", "token", token, "username", username));
    }
}
