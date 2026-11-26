package com.capstone.backend.controller;

import com.capstone.backend.service.DelaySimulationService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.CrossOrigin;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@CrossOrigin(origins = "*")
@RequestMapping("/api/health")
public class HealthController {

    private final DelaySimulationService delaySimulationService;

    public HealthController(DelaySimulationService delaySimulationService) {
        this.delaySimulationService = delaySimulationService;
    }

    @GetMapping
    public ResponseEntity<Map<String, Object>> checkHealth() {
        // Base delay: 2ms (very fast endpoint)
        delaySimulationService.simulateDelay(2);

        return ResponseEntity.ok(Map.of(
                "status", "UP",
                "service", "backend-service",
                "endpoint", "/api/health",
                "simulatedDelayMs", delaySimulationService.getSimulatedDelayMs()
        ));
    }
}
