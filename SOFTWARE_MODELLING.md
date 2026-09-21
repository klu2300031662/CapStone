# Software Modelling & DevOps Specifications
> **Project:** Adaptive Traffic Management System Using Dynamic Rate Limiting  
> **Course Scope:** B.Tech Software Modelling and DevOps Capstone (First 20% Milestone)

---

## 1. Use Case Diagram

The Use Case Diagram depicts the interaction between external actors (Clients, Load Testing System, System Administrator) and the core capabilities provided by the Gateway and Monitoring infrastructure.

```mermaid
graph TD
    actorClient([Client / App User])
    actorTester([k6 Load Generator])
    actorAdmin([DevOps / System Admin])

    subgraph Adaptive Traffic System
        UC1(Send API Requests)
        UC2(Enforce Static Rate Limit)
        UC3(Access Backend Resources)
        UC4(Receive HTTP 429 Rejection)
        UC5(Scrape Prometheus Metrics)
        UC6(View Real-Time Grafana Dashboards)
        UC7(Configure Artificial Latency)
    end

    actorClient --> UC1
    actorTester --> UC1
    
    UC1 --> UC2
    UC2 -->|Under Limit| UC3
    UC2 -->|Exceeds Limit| UC4

    actorAdmin --> UC5
    actorAdmin --> UC6
    actorAdmin --> UC7
```

---

## 2. Component Diagram

The Component Diagram shows the high-level software components, interfaces, and dependencies within the system.

```mermaid
componentDiagram
    package "Client Environment" {
        [k6 Load Tester] as LoadGen
    }

    package "API Gateway Container (:8080)" {
        [Routing Engine] as Router
        [Static Rate Limiter Filter] as RateFilter
        [Token Bucket Limiter] as TokenBucket
        [Gateway Metrics Collector] as GWMetrics
    }

    package "Backend Service Container (:8081)" {
        [Product Controller] as ProdCtrl
        [Order Controller] as OrderCtrl
        [Health Controller] as HealthCtrl
        [Delay Simulation Service] as DelaySvc
        [Backend Metrics Collector] as PEMetrics
    }

    package "Monitoring Stack" {
        [Prometheus Scraper (:9090)] as Prom
        [Grafana Engine (:3000)] as Grafana
    }

    LoadGen --> Router : HTTP /api/*
    Router --> RateFilter
    RateFilter --> TokenBucket : tryAcquire()
    RateFilter --> GWMetrics : Record Accepted/Rejected
    RateFilter --> ProdCtrl : HTTP Proxy /api/products
    RateFilter --> OrderCtrl : HTTP Proxy /api/orders
    RateFilter --> HealthCtrl : HTTP Proxy /api/health

    ProdCtrl --> DelaySvc
    OrderCtrl --> DelaySvc
    HealthCtrl --> DelaySvc

    Prom --> GWMetrics : /actuator/prometheus
    Prom --> PEMetrics : /actuator/prometheus
    Grafana --> Prom : PromQL Queries
```

---

## 3. Deployment Diagram

The Deployment Diagram details the node allocation, Docker containers, network protocols, and exposed ports in the containerized deployment environment.

```mermaid
nodeDiagram
    node "Host Machine / Docker Engine" {
        node "Bridge Network: capstone-network" {
            
            node "Docker Container: capstone-gateway" {
                artifact "gateway-1.0.0.jar" as GWJar
                portIn "8080" as GWPort
            }

            node "Docker Container: capstone-backend" {
                artifact "backend-1.0.0.jar" as BEJar
                portIn "8081" as BEPort
            }

            node "Docker Container: capstone-prometheus" {
                artifact "Prometheus v2.51" as PromArt
                portIn "9090" as PromPort
            }

            node "Docker Container: capstone-grafana" {
                artifact "Grafana v10.4" as GrafArt
                portIn "3000" as GrafPort
            }
        }
    }

    GWPort --> BEPort : HTTP / Internal Docker Routing
    PromPort --> GWPort : Scrape /actuator/prometheus
    PromPort --> BEPort : Scrape /actuator/prometheus
    GrafPort --> PromPort : PromQL HTTP API
```

---

## 4. Sequence Diagram: Normal Request (Accepted - HTTP 200)

This sequence diagram models the execution flow for a request admitted within the configured static rate limit.

```mermaid
sequenceDiagram
    autonumber
    actor Client as Client
    participant GW as Spring Cloud Gateway
    participant Filter as StaticRateLimiterFilter
    participant Bucket as TokenBucketState
    participant BE as Backend Controller
    participant Service as DelaySimulationService

    Client->>GW: GET /api/products
    GW->>Filter: Filter Request
    Filter->>Bucket: tryConsume(1)
    Bucket-->>Filter: true (Token Granted)
    Filter->>GW: Proceed down filter chain
    GW->>BE: HTTP GET /api/products
    BE->>Service: simulateDelay(20ms)
    Service-->>BE: Delay Completed
    BE-->>GW: 200 OK + Product List JSON
    GW-->>Client: 200 OK + Product List JSON
```

---

## 5. Sequence Diagram: Rate-Limited Request (Rejected - HTTP 429)

This sequence diagram models the execution flow when traffic exceeds the static limit, triggering rate limiter rejection.

```mermaid
sequenceDiagram
    autonumber
    actor Client as Client
    participant GW as Spring Cloud Gateway
    participant Filter as StaticRateLimiterFilter
    participant Bucket as TokenBucketState
    participant Metrics as Micrometer Registry

    Client->>GW: GET /api/orders (Burst Traffic)
    GW->>Filter: Filter Request
    Filter->>Bucket: tryConsume(1)
    Bucket-->>Filter: false (Tokens Exhausted)
    Filter->>Metrics: Increment gateway_requests_rejected_total
    Filter-->>GW: Short-circuit chain
    GW-->>Client: HTTP 429 Too Many Requests<br/>{"error":"Too Many Requests","limit":10}
```
