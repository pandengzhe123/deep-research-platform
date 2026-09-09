package com.deepresearch.gateway.service;

import com.deepresearch.gateway.model.SessionEntity;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface SessionRepository extends JpaRepository<SessionEntity, String> {

    /** 按用户 ID 查所有会话，按时间倒序。 */
    List<SessionEntity> findByUserIdOrderByCreatedAtDesc(String userId);

    /**
     * 加行锁读取会话（B9）。
     *
     * history 是「整行读 → 改 → 写回」，两个并发请求会后写覆盖前写（PG 丢消息），
     * 而 Redis 侧两次 RPUSH 都成功（多消息）→ 镜像与权威分叉。
     * Python 侧的会话锁挡不住：Java 在转发 Python 之前就已 appendHistory。
     * 用 PESSIMISTIC_WRITE 行锁把同会话的追加串行化（需在事务内调用）。
     */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select s from SessionEntity s where s.id = :id")
    Optional<SessionEntity> findByIdForUpdate(@Param("id") String id);
}
