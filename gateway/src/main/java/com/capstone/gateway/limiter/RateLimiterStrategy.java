package com.capstone.gateway.limiter;

/**
 * Interface defining rate limiter operations.
 * Designed to allow seamless substitution of static rate limiting with dynamic/adaptive rate limiting controllers in future milestones.
 */
public interface RateLimiterStrategy {

    /**
     * Attempts to acquire a single token for a request.
     * @param key Identifies the rate limit bucket key (e.g. endpoint or client IP)
     * @return true if admitted within allowed limit; false if rejected (HTTP 429)
     */
    boolean tryAcquire(String key);

    /**
     * Gets current configured max capacity (requests per second).
     */
    long getCapacity();

    /**
     * Updates rate limit capacity dynamically (prepared for future adaptive controller).
     * @param newCapacity New target capacity in requests/sec
     */
    void updateCapacity(long newCapacity);
}
