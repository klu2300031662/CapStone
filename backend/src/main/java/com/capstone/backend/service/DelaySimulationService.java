package com.capstone.backend.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class DelaySimulationService {

    private static final Logger logger = LoggerFactory.getLogger(DelaySimulationService.class);

    @Value("${backend.simulated-delay-ms:0}")
    private long simulatedDelayMs;

    public void simulateDelay(long baseDelayMs) {
        long totalDelay = baseDelayMs + simulatedDelayMs;
        if (totalDelay > 0) {
            try {
                Thread.sleep(totalDelay);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                logger.warn("Thread interrupted during artificial delay simulation", e);
            }
        }
    }

    public long getSimulatedDelayMs() {
        return simulatedDelayMs;
    }

    public void setSimulatedDelayMs(long simulatedDelayMs) {
        this.simulatedDelayMs = simulatedDelayMs;
    }
}
