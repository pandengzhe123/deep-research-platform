package com.deepresearch.gateway.security;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

/** 用户数据访问。users 表由 schema.sql 建。 */
public interface UserRepository extends JpaRepository<UserEntity, Long> {
    Optional<UserEntity> findByUsername(String username);
    boolean existsByUsername(String username);
}
