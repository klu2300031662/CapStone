# Adaptive Traffic Management System Using Dynamic Rate Limiting
> **Engineering Capstone Project (Software Modelling & DevOps)**  
> **Milestone Status:** First 20% Milestone Completed (Static Baseline & Observability Infrastructure)

---

## 1. Project Title
**Adaptive Traffic Management System Using Dynamic Rate Limiting**

---

## 2. Problem Statement
Cloud applications commonly use fixed, static rate limits (e.g., 10 requests/second) at the API gateway layer to protect backend microservices from traffic surges and resource exhaustion. However, static rate limits suffer from fundamental limitations:
- **Underutilization under light load or fast response times**: Fixed limits deny incoming legitimate requests even when backend resources (CPU, Memory, Threads) are underutilized and capable of processing higher throughput.
- **Service overload under downstream degradation**: When downstream dependencies slow down or experience transient faults, static limits continue admitting traffic at the configured rate. This causes request queuing, thread pool exhaustion, latency spikes, and eventual cascading failure across the architecture.

---

## 3. Project Objective
The objective of this two-semester capstone project is to design, model, implement, and evaluate an **Adaptive Traffic Protection System**. Unlike static rate limiters, our system will dynamically adjust traffic admission limits in real-time based on observed system response signals, downstream latency, error rates, and resource utilization.

---

## 4. Current Milestone (First 20% Scope)
This initial milestone establishes a clean, runnable, highly observable baseline foundation:
- **Spring Boot Backend Microservice**: Simulates realistic request processing with configurable artificial delays (`SIMULATED_DELAY_MS`).
- **Spring Cloud Gateway**: Serves as the single entry point with a configurable **Static Rate Limiter Baseline** (`STATIC_REQUEST_LIMIT`). Returns HTTP `429 Too Many Requests` when limits are exceeded.
- **Monitoring Infrastructure**: Spring Boot Actuator, Micrometer, and **Prometheus** for scraping throughput, latency, and HTTP 429 metrics.
- **Grafana Dashboard**: Auto-provisioned real-time visualization of system state, response times, and rate-limiting rejections.
- **Docker Compose Orchestration**: Containerized deployment of the entire microservices stack.
- **k6 Load Testing & Experiments**: Scripted load tests showcasing static rate limit behavior under both normal and degraded backend conditions.

---

## 5. System Architecture

```
                                +-----------------------+
                                |    k6 Load Generator  |
                                +-----------+-----------+
                                            |
                                            v (HTTP Port 8080)
                                +-----------+-----------+
                                |    API Gateway        |
                                | (Spring Cloud Gateway)|
                                | [Static Rate Limiter] |
                                +-----------+-----------+
                                            |
                                            v (HTTP Port 8081)
                                +-----------+-----------+
                                |    Backend Service    |
                                | (Spring Boot App)     |
                                | [Configurable Delay]  |
                                +-----------------------+
                                            |
                                            v
                                +-----------------------+
                                | Simulated Downstream  |
                                +-----------------------+

       Observability Pipeline:
       [ Gateway / Backend Actuator ] ---> [ Prometheus Scraper (9090) ] ---> [ Grafana UI (3000) ]
```

---

## 6. Technology Stack
- **Backend**: Java 21, Spring Boot 3.2.x, Maven
- **API Gateway**: Spring Cloud Gateway (Reactive, Netty)
- **Monitoring & Metrics**: Spring Boot Actuator, Micrometer, Prometheus
- **Visualization**: Grafana 10.4.0
- **Containerization**: Docker, Docker Compose
- **Load Testing**: k6 (Grafana k6)

---

## 7. How the Static Rate Limiter Baseline Works
In this milestone, the Gateway enforces a static rate limit using an in-memory **Token Bucket Algorithm**:
1. Incoming HTTP requests arrive at `Spring Cloud Gateway` on port `8080`.
2. The `StaticRateLimiterGatewayFilterFactory` intercepts requests to `/api/**`.
3. The filter queries `StaticTokenBucketRateLimiter` configured with `STATIC_REQUEST_LIMIT` (default: 10 requests/second).
4. If tokens are available:
   - The token is consumed.
   - Request is forwarded to the backend service.
   - `gateway_requests_total{status="accepted"}` metric is incremented.
5. If tokens are exhausted:
   - The request is immediately rejected without hitting the backend.
   - Returns HTTP `429 Too Many Requests` with a structured JSON error body.
   - `gateway_requests_total{status="rejected"}` and `gateway_requests_rejected_total` metrics are incremented.

---

## 8. How to Run the Project

### Prerequisites
- Java 21 LTS
- Apache Maven 3.9+
- Docker & Docker Compose
- Node.js / k6 (optional for host execution; Docker image used by default)

### Local Development Build (Maven)
```powershell
# Clean and package both modules
mvn clean package -DskipTests
```

---

## 9. Docker Instructions

### Start All Services with Docker Compose
```powershell
docker compose up --build -d
```

### Verify Container Status
```powershell
docker compose ps
```

### View Application Logs
```powershell
# Gateway logs
docker compose logs -f gateway

# Backend logs
docker compose logs -f backend
```

### Stopping Services
```powershell
docker compose down -v
```

---

## 10. Prometheus Instructions
- **Prometheus UI**: [http://localhost:9090](http://localhost:9090)
- **Scrape Targets Status**: [http://localhost:9090/targets](http://localhost:9090/targets)
- **Key Baseline PromQL Queries**:
  - `sum(rate(gateway_requests_total[10s])) by (status)` (Request Rate by status)
  - `gateway_rate_limit_capacity` (Configured static limit)
  - `sum(gateway_requests_rejected_total)` (Total HTTP 429 rejections)
  - `rate(http_server_requests_seconds_sum[10s]) / rate(http_server_requests_seconds_count[10s])` (Average response latency)

---

## 11. Grafana Instructions
- **Grafana Web UI**: [http://localhost:3000](http://localhost:3000)
- **Credentials**: `admin` / `admin`
- **Dashboard**: The pre-provisioned dashboard **"Adaptive Traffic Baseline - System Overview"** is automatically loaded under Dashboards.
- **Dashboard Panels**:
  1. **Gateway Request Rate (RPS)**: Accepted vs Rejected traffic.
  2. **HTTP 429 Rejection Rate**: Real-time rate limit drops.
  3. **Response Latency**: Gateway and backend processing latency.
  4. **Configured Static Rate Limit**: Gauge displaying active limit (e.g. 10 RPS).
  5. **Total Rejections Counter**: Cumulative rejections count.

---

## 12. k6 Load-Testing Instructions

Run load testing using Docker or native k6 CLI:

```powershell
# Using Docker (Recommended)
docker run --rm -i --net=host -e TARGET_HOST="http://localhost:8080" grafana/k6 run - < load-test/rate_limit_test.js

# Or using automated script
.\load-test\run_experiments.ps1
```

The script executes 4 load stages:
- **Stage 1 (10 RPS)**: Load within limit -> 100% HTTP 200 OK.
- **Stage 2 (20 RPS)**: 2x limit -> ~50% HTTP 200 OK, ~50% HTTP 429.
- **Stage 3 (50 RPS)**: Heavy load -> ~20% HTTP 200 OK, ~80% HTTP 429.
- **Stage 4 (100 RPS)**: Burst load.

---

## 13. Baseline Experiment Results & Observations

### Experiment 1: Normal Latency (Baseline Delay = 0ms)
- **Configured Static Limit**: 10 RPS
- **Applied Traffic**: Ramping up to 100 RPS
- **Observed Behavior**:
  - Exactly 10 RPS admitted to backend; remaining 90 RPS rejected with HTTP 429.
  - Backend latency remains fast (~20ms for `/api/products`).
  - Backend CPU/Thread pool is well under capacity, demonstrating **wasted system throughput capability**.

### Experiment 2: Slow Downstream Latency (`SIMULATED_DELAY_MS=1000`)
- **Configured Static Limit**: 10 RPS
- **Applied Traffic**: 20 RPS
- **Observed Behavior**:
  - The static limit continues admitting 10 RPS into the gateway.
  - Because each backend request now takes 1000ms+, active threads accumulate in the backend.
  - Overall system latency surges, and active requests build up.
  - Demonstrates that a static limit **cannot protect against downstream response degradation**.

---

## 14. Future Work Roadmap (Next Milestones)
In future project milestones, we will extend this baseline system into a fully **Adaptive Traffic Protection System**:
1. **Decision Engine**: Build an adaptive feedback-control loop monitoring real-time latency (p90/p95), error rate, and active thread capacity.
2. **Dynamic Limit Adjustment**: Replace `StaticTokenBucketRateLimiter` with an AIMD (Additive Increase / Multiplicative Decrease) or Gradient-based adaptive controller.
3. **Downstream Protection & Circuit Breaking**: Prevent cascading failures during severe downstream latency spikes.
4. **Oscillation Prevention & Stability Testing**: Implement dampening algorithms to prevent limits from oscillating wildly under noisy traffic.
5. **Comparative Analysis**: Produce quantitative evaluation metrics comparing static vs. adaptive rate limiting across burst, failure, and steady-state workloads.
