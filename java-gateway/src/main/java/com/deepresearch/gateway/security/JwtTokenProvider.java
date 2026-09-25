package com.deepresearch.gateway.security;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Date;

/**
 * JWT 签发、验证、解析。
 *
 * <p>密钥只从环境变量（JWT_SECRET）/ 配置的 jwt.secret 读取，<b>刻意不提供硬编码默认值</b>：
 * 本仓库是公开的，任何写进代码的默认密钥都等于公开的万能签发密钥 —— 知道它的人可以自签
 * 一个 role=admin 的 token，直接绕过全部鉴权。
 *
 * <p>未配置时随机生成一把：安全，但网关重启后已签发的 token 全部失效（需重新登录）。
 * 这是有意的取舍，宁可要求重新登录，也不能让线上跑在一把全世界都知道的密钥上。
 */
@Component
public class JwtTokenProvider {

    private static final Logger log = LoggerFactory.getLogger(JwtTokenProvider.class);

    /** HS256 要求密钥长度 >= 256 bit，即 32 字节。 */
    private static final int MIN_SECRET_BYTES = 32;

    private final SecretKey key;
    private final long expirationMs;

    public JwtTokenProvider(
            @Value("${jwt.secret:}") String secret,
            @Value("${jwt.expiration-ms:86400000}") long expirationMs
    ) {
        this.expirationMs = expirationMs;

        if (secret == null || secret.isBlank()) {
            this.key = Jwts.SIG.HS256.key().build();
            log.warn("未配置 JWT_SECRET，已随机生成密钥：本次启动前签发的所有 token 已失效，"
                    + "网关重启后需要重新登录。生产环境请在 .env 中设置 JWT_SECRET（至少 {} 字节）。",
                    MIN_SECRET_BYTES);
        } else {
            byte[] raw = secret.getBytes(StandardCharsets.UTF_8);
            if (raw.length < MIN_SECRET_BYTES) {
                // 显式拒绝而不是交给 crypto 库抛 WeakKeyException：错误信息里要说清「怎么修」
                throw new IllegalStateException(
                        "JWT_SECRET 强度不足：HS256 要求至少 " + MIN_SECRET_BYTES + " 字节，当前只有 "
                                + raw.length + " 字节。请用 `python -c \"import secrets; "
                                + "print(secrets.token_urlsafe(48))\"` 生成一个更长的随机串。");
            }
            this.key = Keys.hmacShaKeyFor(raw);
        }
    }

    /** 签发 JWT。 */
    public String generateToken(String userId, String username, String role) {
        Date now = new Date();
        return Jwts.builder()
                .subject(userId)
                .claim("username", username)
                .claim("role", role)
                .issuedAt(now)
                .expiration(new Date(now.getTime() + expirationMs))
                .signWith(key)
                .compact();
    }

    /** 验证 JWT 是否有效。 */
    public boolean validateToken(String token) {
        try {
            parseClaims(token);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    /** 从 JWT 提取 user_id（即 subject）。 */
    public String getUserId(String token) {
        return parseClaims(token).getSubject();
    }

    /** 从 JWT 提取 role。 */
    public String getRole(String token) {
        return parseClaims(token).get("role", String.class);
    }

    private Claims parseClaims(String token) {
        return Jwts.parser()
                .verifyWith(key)
                .build()
                .parseSignedClaims(token)
                .getPayload();
    }
}
