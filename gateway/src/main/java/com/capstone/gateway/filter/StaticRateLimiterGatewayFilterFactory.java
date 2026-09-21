package com.capstone.gateway.filter;

import com.capstone.gateway.limiter.RateLimiterStrategy;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cloud.gateway.filter.GatewayFilter;
import org.springframework.cloud.gateway.filter.factory.AbstractGatewayFilterFactory;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpResponse;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Mono;

import java.nio.charset.StandardCharsets;
import java.time.Instant;

@Component
public class StaticRateLimiterGatewayFilterFactory extends AbstractGatewayFilterFactory<StaticRateLimiterGatewayFilterFactory.Config> {

    private static final Logger logger = LoggerFactory.getLogger(StaticRateLimiterGatewayFilterFactory.class);

    private final RateLimiterStrategy rateLimiterStrategy;

    private final Counter acceptedCounter;
    private final Counter rejectedCounter;
    private final Counter rejectionReasonCounter;

    public StaticRateLimiterGatewayFilterFactory(RateLimiterStrategy rateLimiterStrategy, MeterRegistry meterRegistry) {
        super(Config.class);
        this.rateLimiterStrategy = rateLimiterStrategy;

        this.acceptedCounter = Counter.builder("gateway_requests_total")
                .tag("status", "accepted")
                .description("Total requests accepted by API Gateway")
                .register(meterRegistry);

        this.rejectedCounter = Counter.builder("gateway_requests_total")
                .tag("status", "rejected")
                .description("Total requests rejected by API Gateway due to rate limiting")
                .register(meterRegistry);

        this.rejectionReasonCounter = Counter.builder("gateway_requests_rejected_total")
                .tag("reason", "static_limit_exceeded")
                .description("Total rate-limit rejections")
                .register(meterRegistry);

        meterRegistry.gauge("gateway_rate_limit_capacity", rateLimiterStrategy, RateLimiterStrategy::getCapacity);
    }

    @Override
    public GatewayFilter apply(Config config) {
        return (exchange, chain) -> {
            String path = exchange.getRequest().getURI().getPath();
            String bucketKey = config.getBucketKey() != null ? config.getBucketKey() : "global";

            boolean allowed = rateLimiterStrategy.tryAcquire(bucketKey);

            if (allowed) {
                acceptedCounter.increment();
                return chain.filter(exchange);
            } else {
                rejectedCounter.increment();
                rejectionReasonCounter.increment();
                
                logger.warn("Rate limit exceeded for request path: {}. Configured limit: {} req/sec", 
                        path, rateLimiterStrategy.getCapacity());

                ServerHttpResponse response = exchange.getResponse();
                response.setStatusCode(HttpStatus.TOO_MANY_REQUESTS);
                response.getHeaders().setContentType(MediaType.APPLICATION_JSON);

                String jsonPayload = String.format(
                        "{\"timestamp\":\"%s\",\"status\":429,\"error\":\"Too Many Requests\"," +
                        "\"message\":\"Static rate limit exceeded. Configured baseline limit: %d req/sec\",\"path\":\"%s\"}",
                        Instant.now().toString(),
                        rateLimiterStrategy.getCapacity(),
                        path
                );

                byte[] bytes = jsonPayload.getBytes(StandardCharsets.UTF_8);
                DataBuffer buffer = response.bufferFactory().wrap(bytes);
                return response.writeWith(Mono.just(buffer));
            }
        };
    }

    public static class Config {
        private String bucketKey = "global";

        public String getBucketKey() {
            return bucketKey;
        }

        public void setBucketKey(String bucketKey) {
            this.bucketKey = bucketKey;
        }
    }
}
