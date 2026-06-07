package com.deepresearch.gateway.service;

import com.deepresearch.gateway.model.SessionEntity;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface SessionRepository extends JpaRepository<SessionEntity, String> {

    /** 按用户 ID 查所有会话，按时间倒序。 */
    List<SessionEntity> findByUserIdOrderByCreatedAtDesc(String userId);
}
