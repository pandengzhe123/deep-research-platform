package com.deepresearch.gateway.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.netty.http.client.HttpClient;

import java.time.Duration;

@Configuration
public class WebClientConfig {

    @Bean
    public WebClient agentWebClient(
            @Value("${agent.url}") String agentUrl
    ) {
        // 配置超时：响应最长 30 分钟（Level 3/4 多路并行可能需要很长时间）
        HttpClient httpClient = HttpClient.create()
                .responseTimeout(Duration.ofMinutes(30));

        return WebClient.builder()
                .baseUrl(agentUrl)
                .clientConnector(new ReactorClientHttpConnector(httpClient))
                .build();
    }
}
