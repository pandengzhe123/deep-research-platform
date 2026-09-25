package com.deepresearch.gateway.config;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;

/**
 * 把 {@link ResponseStatusException} 的 reason 显式回给客户端。
 *
 * <p>存在的理由：Spring Boot 默认 {@code server.error.include-message=never}，
 * 于是控制器里写的拒绝原因（「Level 4 仅限管理员使用」「文件超过 20MB 上限」）
 * 根本传不到前端，用户只看到一个光秃秃的 403/413。
 *
 * <p>另一种做法是打开 {@code include-message: always}，但那样会把**所有**未预期异常
 * 的内部文本（含栈信息、内部路径）一并回给公网客户端 —— 用一个全局的信息泄露
 * 换取几个自定义提示，不划算。所以这里只处理我们自己主动抛的那一类，
 * 其余异常继续走 Spring 的默认处理（默认不回传 message，是安全的）。
 */
@RestControllerAdvice
public class ResponseStatusAdvice {

    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<Map<String, Object>> handle(ResponseStatusException e) {
        String reason = e.getReason();
        return ResponseEntity.status(e.getStatusCode())
                .body(Map.of("status", "error",
                        "message", (reason != null && !reason.isBlank()) ? reason : "请求被拒绝"));
    }
}
