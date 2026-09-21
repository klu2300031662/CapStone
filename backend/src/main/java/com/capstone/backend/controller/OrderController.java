package com.capstone.backend.controller;

import com.capstone.backend.service.DelaySimulationService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/orders")
public class OrderController {

    private final DelaySimulationService delaySimulationService;

    public OrderController(DelaySimulationService delaySimulationService) {
        this.delaySimulationService = delaySimulationService;
    }

    @GetMapping
    public ResponseEntity<Map<String, Object>> getOrders() {
        // Base delay: 50ms (moderate endpoint)
        delaySimulationService.simulateDelay(50);

        List<Map<String, Object>> orders = List.of(
                Map.of("orderId", "ORD-8921", "customer", "Acme Corp", "total", 1798.99, "status", "PROCESSED"),
                Map.of("orderId", "ORD-8922", "customer", "Global Tech", "total", 450.00, "status", "SHIPPED")
        );

        return ResponseEntity.ok(Map.of(
                "status", "success",
                "service", "backend-service",
                "endpoint", "/api/orders",
                "artificialDelayAppliedMs", delaySimulationService.getSimulatedDelayMs(),
                "data", orders
        ));
    }
}
