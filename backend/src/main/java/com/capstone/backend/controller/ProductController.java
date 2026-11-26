package com.capstone.backend.controller;

import com.capstone.backend.service.DelaySimulationService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.CrossOrigin;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

@RestController
@CrossOrigin(origins = "*")
@RequestMapping("/api/products")
public class ProductController {

    private final DelaySimulationService delaySimulationService;

    public ProductController(DelaySimulationService delaySimulationService) {
        this.delaySimulationService = delaySimulationService;
    }

    @GetMapping
    public ResponseEntity<Map<String, Object>> getProducts() {
        // Base delay: 20ms (fast endpoint)
        delaySimulationService.simulateDelay(20);

        List<Map<String, Object>> products = List.of(
                Map.of("id", 101, "name", "Cloud Router", "category", "Networking", "price", 299.99),
                Map.of("id", 102, "name", "Load Balancer Appliance", "category", "DevOps", "price", 1499.00),
                Map.of("id", 103, "name", "Monitoring Probe", "category", "Observability", "price", 89.50)
        );

        return ResponseEntity.ok(Map.of(
                "status", "success",
                "service", "backend-service",
                "endpoint", "/api/products",
                "artificialDelayAppliedMs", delaySimulationService.getSimulatedDelayMs(),
                "data", products
        ));
    }
}
