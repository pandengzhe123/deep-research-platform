package com.deepresearch.gateway.controller;

import com.deepresearch.gateway.security.RequestUserResolver;
import com.deepresearch.gateway.service.AgentClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.core.io.buffer.DataBufferLimitException;
import org.springframework.core.io.buffer.DataBufferUtils;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.codec.multipart.FilePart;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Scheduler;
import reactor.core.scheduler.Schedulers;

import java.util.Map;
import java.util.concurrent.Executors;

/**
 * 知识库代理端点。
 *
 * <p>这组端点存在的唯一理由是<b>鉴权</b>：Python Agent 自身没有任何认证，它的
 * {@code user_id} 完全由调用方以 URL 查询参数传入。此前 nginx 把 {@code /kb/} 直接
 * 转发到 {@code agent:8000}，等于把「列出 / 上传 / 删除任意用户知识库」暴露给任何
 * 能访问站点的人 —— 多租户隔离是虚构的。
 *
 * <p>现在统一走网关：user_id 一律取自 JWT（{@link RequestUserResolver}），
 * 客户端传什么都作数不了；nginx 里那条 {@code /kb/} 转发也已删除。
 */
@RestController
@RequestMapping("/api/kb")
public class KbController {

    private static final Logger log = LoggerFactory.getLogger(KbController.class);
    private static final Scheduler VIRTUAL = Schedulers.fromExecutor(Executors.newVirtualThreadPerTaskExecutor());

    private final AgentClient agentClient;
    private final RequestUserResolver users;

    /** 单文件上传上限。与 nginx 的 client_max_body_size、spring.codec 保持一致。 */
    private final int maxUploadBytes;

    public KbController(AgentClient agentClient,
                        RequestUserResolver users,
                        @Value("${app.max-upload-bytes:20971520}") int maxUploadBytes) {
        this.agentClient = agentClient;
        this.users = users;
        this.maxUploadBytes = maxUploadBytes;
    }

    /** 列出当前登录用户已上传的文档。 */
    @GetMapping("/files")
    public Mono<ResponseEntity<Map<String, Object>>> list(ServerHttpRequest request) {
        final String uid = users.resolve(request);
        return Mono.fromCallable(() -> ResponseEntity.ok(agentClient.listKb(uid)))
                .subscribeOn(VIRTUAL);
    }

    /**
     * 上传文档到当前登录用户的知识库。
     *
     * <p>整个文件读进内存后再转发（agent 侧也是这么处理的），所以必须限制大小：
     * 限制用 {@link DataBufferUtils#join} 的 maxByteCount 在读取阶段生效，
     * 超限抛 {@link DataBufferLimitException}，不会先把 100MB 读进堆再判断。
     */
    @PostMapping(value = "/upload", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public Mono<ResponseEntity<Map<String, Object>>> upload(
            @RequestPart("file") FilePart file,
            ServerHttpRequest request) {

        final String uid = users.resolve(request);
        final String filename = file.filename();
        log.info("知识库上传: user={}, file={}", uid, filename);

        return DataBufferUtils.join(file.content(), maxUploadBytes)
                .flatMap(buf -> {
                    byte[] bytes = new byte[buf.readableByteCount()];
                    buf.read(bytes);
                    DataBufferUtils.release(buf);
                    return Mono.fromCallable(() -> {
                        Map<String, Object> result = agentClient.uploadKb(uid, filename, bytes);
                        return ResponseEntity.ok(result);
                    }).subscribeOn(VIRTUAL);
                })
                .onErrorMap(DataBufferLimitException.class, e -> new ResponseStatusException(
                        HttpStatus.PAYLOAD_TOO_LARGE,
                        "文件超过 " + (maxUploadBytes / 1024 / 1024) + "MB 上限"))
                .switchIfEmpty(Mono.error(new ResponseStatusException(
                        HttpStatus.BAD_REQUEST, "上传内容为空")));
    }

    /** 删除当前登录用户知识库中的指定文档。 */
    @DeleteMapping("/files/{docId}")
    public Mono<ResponseEntity<Map<String, Object>>> delete(@PathVariable String docId,
                                                           ServerHttpRequest request) {
        final String uid = users.resolve(request);
        return Mono.fromCallable(() -> ResponseEntity.ok(agentClient.deleteKb(uid, docId)))
                .subscribeOn(VIRTUAL);
    }
}
