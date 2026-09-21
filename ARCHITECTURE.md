# Technical Architecture Specification
> **Project:** Adaptive Traffic Management System Using Dynamic Rate Limiting  
> **Document Scope:** System Context, Component Architecture, Request Flows, and Observability Pipeline

---

## 1. System Context

The Adaptive Traffic Management System sits at the perimeter of a microservices cloud environment. Clients and load generators issue HTTP requests to the **API Gateway**, which routes accepted traffic to internal **Backend Services**.

```mermaid
flowchart TD
    Client[Client / Load Generator] -->|HTTP Requests| Gateway[Spring Cloud API Gateway]
    Gateway -->|Forwarded Requests| Backend[Spring Boot Backend Service]
    Backend -->|Simulated Call| Downstream[Simulated Downstream Dependency]

    Gateway -.->|Exposes /actuator/prometheus| Prometheus[Prometheus Server]
    Backend -.->|Exposes /actuator/prometheus| Prometheus
    Prometheus -.->|Queries Metrics| Grafana[Grafana Dashboard]
```

---

## 2. Component Architecture

### A. API Gateway (`gateway/`)
- **Technology**: Spring Cloud Gateway (Netty, WebFlux)
- **Role**: Entry point, traffic filtering, metrics generation, baseline static rate limiting.
- **Key Modules**:
  - `StaticTokenBucketRateLimiter`: Thread-safe token bucket holding static RPS capacity.
  - `StaticRateLimiterGatewayFilterFactory`: Intercepts request pipeline, evaluates capacity, updates Micrometer metrics, and returns HTTP 429 when capacity is exhausted.
  - `RateLimiterStrategy`: Pluggable abstraction allowing seamless dynamic replacement in future adaptive controller milestones.

### B. Backend Service (`backend/`)
- **Technology**: Spring Boot 3.2.x (Embedded Tomcat, Web MVC)
- **Role**: Simulates domain operations (`/api/products`, `/api/orders`, `/api/health`).
- **Key Modules**:
  - `DelaySimulationService`: Controls baseline endpoint latencies and artificial downstream delays (`SIMULATED_DELAY_MS`).
  - `ProductController` / `OrderController` / `HealthController`: Expose REST endpoints returning JSON responses.

### C. Monitoring Pipeline (`monitoring/`)
- **Prometheus**: Periodically scrapes `/actuator/prometheus` on Gateway and Backend.
- **Grafana**: Visualizes real-time request rates, latency curves (p50/p95), and HTTP 429 rejections.

---

## 3. Request Flow Pipeline

```mermaid
sequenceDiagram
    autonumber
    actor Client as Client / k6 Load Tester
    participant GW as API Gateway (Filter)
    participant Limiter as Token Bucket Limiter
    participant Backend as Backend Service
    participant Metrics as Micrometer / Prometheus

    Client->>GW: GET /api/products
    GW->>Limiter: tryAcquire("global")
    alt Tokens Available (Admitted)
        Limiter-->>GW: true (Allowed)
        GW->>Metrics: Increment accepted_requests
        GW->>Backend: Forward GET /api/products
        Backend-->>Backend: Process (Base Delay + Artificial Delay)
        Backend-->>GW: HTTP 200 OK + Product JSON
        GW-->>Client: HTTP 200 OK
    else Tokens Exhausted (Rejected)
        Limiter-->>GW: false (Denied)
        GW->>Metrics: Increment rejected_requests (HTTP 429)
        GW-->>Client: HTTP 429 Too Many Requests (JSON Error Body)
    end
```

---

## 4. Gateway Responsibilities
1. **Single Entry Point**: Standardizes incoming routing (`/api/**`).
2. **Static Baseline Rate Limiting**: Ensures total admitted traffic per second does not exceed `STATIC_REQUEST_LIMIT`.
3. **Graceful Rejection Handling**: Formats standard HTTP 429 JSON responses without throwing unhandled exceptions.
4. **Perimeter Observability**: Emits custom counters and gauges for total, accepted, and rejected traffic.

---

## 5. Backend Responsibilities
1. **Business Service Simulation**: Handles product, order, and health requests.
2. **Artificial Delay Injection**: Simulates downstream database or third-party service latency via `SIMULATED_DELAY_MS`.
3. **Internal Metrics**: Exposes thread activity, memory, and HTTP response metrics via Spring Boot Actuator.

---

## 6. Monitoring & Load Testing Flow

```mermaid
flowchart LR
    subgraph Execution Phase
        k6[k6 Load Generator] -->|Ramping Traffic 10-100 RPS| GW[API Gateway]
        GW -->|Proxy| BE[Backend Service]
    end

    subgraph Scrape Phase
        GW -->|Metrics Endpoint| Prom[Prometheus]
        BE -->|Metrics Endpoint| Prom
    end

    subgraph Visualization Phase
        Prom -->|Query PromQL| Grafana[Grafana Dashboards]
        Grafana -->|Display| User[DevOps / System Administrator]
    end
```
