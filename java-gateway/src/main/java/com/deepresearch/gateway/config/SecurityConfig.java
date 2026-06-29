package com.deepresearch.gateway.config;

import com.deepresearch.gateway.security.JwtTokenProvider;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.config.annotation.web.reactive.EnableWebFluxSecurity;
import org.springframework.security.config.web.server.SecurityWebFiltersOrder;
import org.springframework.security.config.web.server.ServerHttpSecurity;
import org.springframework.security.core.context.ReactiveSecurityContextHolder;
import org.springframework.security.web.server.SecurityWebFilterChain;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilter;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;

import java.util.Collections;

@Configuration
@EnableWebFluxSecurity
public class SecurityConfig {

    private final JwtTokenProvider jwt;

    public SecurityConfig(JwtTokenProvider jwt) {
        this.jwt = jwt;
    }

    @Bean
    public SecurityWebFilterChain filterChain(ServerHttpSecurity http) {
        return http
                .authorizeExchange(ex -> ex
                        .pathMatchers("/api/auth/**", "/api/health").permitAll()
                        .anyExchange().authenticated()
                )
                .addFilterAt(jwtFilter(), SecurityWebFiltersOrder.AUTHENTICATION)
                .csrf(csrf -> csrf.disable())
                .httpBasic(basic -> basic.disable())
                .formLogin(form -> form.disable())
                .build();
    }

    /**
     * JWT 认证过滤器 —— 从 Authorization: Bearer <token> 解析 userId，
     * 注入 SecurityContext。通过 addFilterAt 放在 Security 链的 AUTHENTICATION
     * 位置，确保鉴权之前执行。
     */
    private WebFilter jwtFilter() {
        return (ServerWebExchange exchange, WebFilterChain chain) -> {
            String auth = exchange.getRequest().getHeaders().getFirst("Authorization");
            if (auth != null && auth.startsWith("Bearer ") && jwt.validateToken(auth.substring(7))) {
                String userId = jwt.getUserId(auth.substring(7));
                UsernamePasswordAuthenticationToken authentication =
                        new UsernamePasswordAuthenticationToken(userId, null, Collections.emptyList());
                return chain.filter(exchange)
                        .contextWrite(ReactiveSecurityContextHolder.withAuthentication(authentication));
            }
            return chain.filter(exchange);
        };
    }
}
