package com.capstone.gateway.limiter;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Baseline static rate limiter using a thread-safe Token Bucket algorithm.
 * Configured via gateway.static-request-limit property / STATIC_REQUEST_LIMIT environment variable.
 */
@Component
public class StaticTokenBucketRateLimiter implements RateLimiterStrategy {

    private static final Logger logger = LoggerFactory.getLogger(StaticTokenBucketRateLimiter.class);

    private final AtomicLong capacity;
    private final ConcurrentHashMap<String, BucketState> buckets = new ConcurrentHashMap<>();

    public StaticTokenBucketRateLimiter(@Value("${gateway.static-request-limit:10}") long initialCapacity) {
        this.capacity = new AtomicLong(initialCapacity);
        logger.info("Initialized StaticTokenBucketRateLimiter with baseline capacity: {} req/sec", initialCapacity);
    }

    @Override
    public boolean tryAcquire(String key) {
        long cap = capacity.get();
        if (cap <= 0) {
            return false; // Unlimited or blocked state depending on policy
        }

        BucketState bucket = buckets.computeIfAbsent(key, k -> new BucketState(cap));
        return bucket.tryConsume(cap);
    }

    @Override
    public long getCapacity() {
        return capacity.get();
    }

    @Override
    public void updateCapacity(long newCapacity) {
        long old = capacity.getAndSet(newCapacity);
        logger.info("Rate limit capacity updated: {} -> {} req/sec", old, newCapacity);
    }

    private static class BucketState {
        private double tokens;
        private long lastRefillTimestamp;

        public BucketState(long capacity) {
            this.tokens = capacity;
            this.lastRefillTimestamp = System.currentTimeMillis();
        }

        public synchronized boolean tryConsume(long currentCapacity) {
            long now = System.currentTimeMillis();
            double secondsElapsed = (now - lastRefillTimestamp) / 1000.0;
            
            if (secondsElapsed > 0) {
                double tokensToAdd = secondsElapsed * currentCapacity;
                this.tokens = Math.min(currentCapacity, this.tokens + tokensToAdd);
                this.lastRefillTimestamp = now;
            }

            if (this.tokens >= 1.0) {
                this.tokens -= 1.0;
                return true;
            } else {
                return false;
            }
        }
    }
}
