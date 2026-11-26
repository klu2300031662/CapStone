# 🛡️ Adaptive Traffic Protection Without Static Rate Limits

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **B.Tech Capstone Project** — A reusable, centralized API traffic protection platform that dynamically adjusts request admission limits based on observed application health, without requiring static configuration.

---

## 1. Project Overview

This platform sits between clients and multiple backend applications, acting as a smart API gateway. Unlike traditional rate limiters that use fixed request-per-second limits, this system measures **how many concurrent requests each backend can handle** and adjusts that limit continuously based on live latency, error rate, and throughput data.

When a backend starts slowing down, the gateway automatically reduces how many new requests it forwards to that backend. When the backend recovers, the limit gradually increases again. Each registered application is monitored and controlled **independently**.

---

## 2. Problem Statement

Traditional traffic protection uses static limits:
- Too low → unnecessary request rejection when the backend has capacity
- Too high → backend overload, cascading failures, poor user experience
- One-size-fits-all → a slow application can consume resources meant for healthy ones

**This project solves that** by measuring real application behaviour and adapting limits accordingly, with per-application isolation so one slow service cannot block another.

---

## 3. Objectives

- Build a reusable API gateway that handles multiple backend applications
- Implement AIMD-style adaptive concurrency control per application
- Isolate applications with independent bulkhead protection
- Provide both static and adaptive operating modes for comparison
- Expose Prometheus metrics and a Streamlit monitoring dashboard
- Support load testing with Locust to generate actual measurements

---

## 4. System Architecture

```
┌─────────────┐       ┌──────────────────────────────────────────────┐
│   Clients   │──────▶│            Central API Gateway               │
│  (Locust /  │       │  ┌─────────────┐    ┌────────────────────┐  │
│   Browser)  │       │  │ Global Cap  │    │  Request Router    │  │
└─────────────┘       │  └─────────────┘    └────────────────────┘  │
                       │  ┌─────────────────────────────────────────┐  │
                       │  │        Per-Application Layer            │  │
                       │  │  ┌──────────────┐  ┌──────────────┐    │  │
                       │  │  │  Bulkhead A  │  │  Bulkhead B  │    │  │
                       │  │  │  (limit=15)  │  │  (limit=6)   │    │  │
                       │  │  └──────┬───────┘  └──────┬───────┘    │  │
                       │  └─────────│─────────────────│────────────┘  │
                       └───────────-│-────────────────│───────────────┘
                                    │                 │
                      ┌─────────────▼──┐   ┌──────────▼─────────┐
                      │  Application A │   │  Application B      │
                      │  port 8001     │   │  port 8002          │
                      │  (normal)      │   │  (configurable)     │
                       └───────────────┘   └────────────────────┘
                                    ▲
                       ┌────────────┴────────────┐
                       │   Adaptive Controller   │
                       │   (AIMD algorithm)      │
                       │   evaluates every 5s    │
                       └────────────┬────────────┘
                                    │
                       ┌────────────▼────────────┐
                       │  Streamlit Dashboard    │
                       │  port 8501              │
                       └─────────────────────────┘
```

---

## 5. Component Descriptions

| Component | File | Description |
|-----------|------|-------------|
| API Gateway | `gateway/main.py` | FastAPI app, all routes, lifecycle hooks |
| Request Forwarder | `gateway/gateway.py` | HTTPX async forwarding, timeout handling |
| Request Router | `gateway/router.py` | Maps `/api/app-name/...` to backend URL |
| App Registry | `gateway/registry.py` | Stores per-app state (limit, metrics, bulkhead) |
| Admission Control | `gateway/admission.py` | Chains global cap + per-app bulkhead |
| Config Loader | `gateway/config.py` | Reads and validates `applications.yaml` |
| Bulkhead | `protection/bulkhead.py` | Per-app concurrency gate (async, resizable) |
| Global Cap | `protection/rate_limiter.py` | Gateway-wide safety ceiling |
| AIMD Algorithm | `controller/algorithm.py` | Stateless evaluation function |
| Controller | `controller/adaptive_controller.py` | Background loop, calls algorithm every N seconds |
| Metrics Collector | `monitoring/collector.py` | Rolling-window latency and error tracking |
| Prometheus Metrics | `monitoring/metrics.py` | All counter/gauge/histogram definitions |
| Logger | `monitoring/logger.py` | Consistent log format across all modules |
| Application A | `applications/app_a.py` | Healthy FastAPI backend, port 8001 |
| Application B | `applications/app_b.py` | Configurable delay/failure backend, port 8002 |
| Dashboard | `dashboard/dashboard.py` | Streamlit live monitoring UI |
| Load Tests | `load_tests/locustfile.py` | Locust user classes for load testing |

---

## 6. Request Flow

1. Client sends `GET /api/app-b/data` to the gateway (port 8000).
2. `RequestRouter` strips the `/api/app-b` prefix → resolves to `app_b`, backend path `/data`.
3. `AdmissionController` checks the **global concurrency cap** first.
4. If global cap is OK, it checks **app_b's Bulkhead** (per-app limit).
5. If the bulkhead is at capacity → returns HTTP **429 Too Many Requests**.
6. If admitted → `GatewayForwarder` sends the request to `http://localhost:8002/data`.
7. Gateway measures round-trip latency and records success/failure.
8. `MetricsCollector` stores the sample in a 30-second rolling window.
9. Every 5 seconds, `AdaptiveController` reads window samples for each app.
10. `evaluate()` decides to increase, decrease, or hold the concurrency limit.
11. Registry and Bulkhead are updated atomically without restarting.
12. Dashboard reads `/admin/applications` to display updated limits.

---

## 7. Adaptive Algorithm Explanation

The algorithm is an **AIMD controller** (Additive Increase / Multiplicative Decrease), inspired by TCP congestion control.

### Decision rules

| Condition | Action |
|-----------|--------|
| `avg_latency < target * 1.5` AND `error_rate < 10%` | **Increase** limit by +1 (additive) |
| `avg_latency >= target * 1.5` OR `error_rate >= 10%` | **Decrease** limit by ×0.75 (multiplicative) |
| Fewer than 5 samples in the window | **Hold** — insufficient data |

### Smoothing (hysteresis)
To prevent rapid oscillations, the raw computed limit is blended with the previous limit:
```
smoothed = 0.3 × old_limit + 0.7 × raw_new_limit
```
This slows down both increases and decreases for stability.

### Bounds
Every result is clamped to `[min_concurrency, max_concurrency]` from the application's config.

### Limitations
- Does not model queue depth or service-time distributions mathematically
- Reacts to aggregate latency over a 30-second window (1-2 cycles to respond to sudden bursts)
- `target_latency_ms` must be tuned for the backend's expected response time
- Not a machine-learning or predictive controller — purely reactive

---

## 8. Technology Stack

| Technology | Version | Purpose |
|------------|---------|---------|
| Python | 3.11+ | Runtime |
| FastAPI | 0.115 | Gateway and backend apps |
| Uvicorn | 0.32 | ASGI server |
| HTTPX | 0.28 | Async HTTP client for forwarding |
| Pydantic | 2.10 | Configuration validation |
| PyYAML | 6.0 | YAML config loading |
| prometheus-client | 0.21 | Metrics exposition |
| Streamlit | 1.40 | Monitoring dashboard |
| Plotly | 5.24 | Charts in dashboard |
| Pandas | 2.2 | Data manipulation |
| Locust | 2.32 | Load testing |
| Pytest | 8.3 | Automated tests |
| pytest-asyncio | 0.24 | Async test support |
| Docker + Compose | 3.9 | Containerized deployment |

---

## 9. Project Structure

```
adaptive-traffic-protection/
│
├── gateway/
│   ├── main.py              ← FastAPI app, routes, lifecycle
│   ├── gateway.py           ← HTTPX forwarder
│   ├── router.py            ← URL path → app_id resolver
│   ├── registry.py          ← Per-app runtime state
│   ├── admission.py         ← Admission control façade
│   └── config.py            ← Config loader + Pydantic models
│
├── controller/
│   ├── adaptive_controller.py  ← Background evaluation loop
│   ├── algorithm.py            ← AIMD evaluate() function
│   └── controller_config.py    ← Re-exports ControllerSettings
│
├── monitoring/
│   ├── metrics.py           ← Prometheus metric definitions
│   ├── collector.py         ← Rolling-window MetricsCollector
│   └── logger.py            ← Shared logging configuration
│
├── protection/
│   ├── bulkhead.py          ← Per-app async concurrency gate
│   └── rate_limiter.py      ← Global gateway safety cap
│
├── applications/
│   ├── app_a.py             ← Normal FastAPI service (port 8001)
│   └── app_b.py             ← Configurable service (port 8002)
│
├── dashboard/
│   └── dashboard.py         ← Streamlit monitoring UI (port 8501)
│
├── load_tests/
│   └── locustfile.py        ← Locust test scenarios
│
├── tests/
│   ├── conftest.py
│   ├── test_bulkhead.py
│   ├── test_controller.py
│   ├── test_gateway.py
│   └── test_multi_application.py
│
├── config/
│   └── applications.yaml    ← Application registry configuration
│
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── pytest.ini
├── .env.example
└── README.md
```

---

## 10. Prerequisites (Windows)

- **Python 3.11 or newer** — download from [python.org](https://www.python.org/downloads/)
- **pip** — bundled with Python
- **PowerShell** — built into Windows (use Windows Terminal for best experience)
- **Docker Desktop** *(optional, for containerized run)* — [docker.com](https://www.docker.com/products/docker-desktop/)
- **Git** *(optional)* — [git-scm.com](https://git-scm.com/)

---

## 11. Installation

```powershell
# 1. Navigate to the project directory
cd d:\College_Related\CapStone_Project\Capstone_Implementation\adaptive-traffic-protection

# 2. Create a virtual environment
python -m venv .venv

# 3. Activate the virtual environment
.venv\Scripts\Activate.ps1

# 4. Install all dependencies
pip install -r requirements.txt
```

> If PowerShell blocks script execution, run:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

---

## 12. Running Without Docker (4 terminals)

Open **4 separate PowerShell windows** in the project directory and activate the venv in each:

```powershell
cd d:\College_Related\CapStone_Project\Capstone_Implementation\adaptive-traffic-protection
.venv\Scripts\Activate.ps1
```

**Terminal 1 — Application A:**
```powershell
uvicorn applications.app_a:app --host 0.0.0.0 --port 8001
```

**Terminal 2 — Application B:**
```powershell
uvicorn applications.app_b:app --host 0.0.0.0 --port 8002
```

**Terminal 3 — Gateway:**
```powershell
uvicorn gateway.main:app --host 0.0.0.0 --port 8000
```

**Terminal 4 — Dashboard (optional):**
```powershell
streamlit run dashboard/dashboard.py --server.port 8501
```

---

## 13. Running with Docker Compose

```powershell
# Build images and start all services
docker compose up --build

# Run in background (detached mode)
docker compose up --build -d

# View logs
docker compose logs -f gateway

# Stop everything
docker compose down
```

---

## 14. Service URLs

| Service | URL | Description |
|---------|-----|-------------|
| Gateway API | http://localhost:8000 | Central gateway entry point |
| Gateway Health | http://localhost:8000/health | Platform health check |
| Prometheus Metrics | http://localhost:8000/metrics | Raw Prometheus metrics |
| Gateway Docs | http://localhost:8000/docs | Interactive API docs (Swagger) |
| Application A | http://localhost:8001 | Direct access to App A |
| Application B | http://localhost:8002 | Direct access to App B |
| Dashboard | http://localhost:8501 | Streamlit monitoring UI |

---

## 15. Running Automated Tests

```powershell
# From the project root (adaptive-traffic-protection/)
cd d:\College_Related\CapStone_Project\Capstone_Implementation\adaptive-traffic-protection

# Run all tests with verbose output
pytest tests/ -v

# Run a specific test file
pytest tests/test_bulkhead.py -v
pytest tests/test_controller.py -v
pytest tests/test_gateway.py -v
pytest tests/test_multi_application.py -v

# Run with coverage (if pytest-cov is installed)
pytest tests/ -v --tb=short
```

**Tests do NOT require the gateway or backends to be running** — they use mocks and in-process testing.

---

## 16. Running Locust Load Tests

```powershell
# Start the full platform first (terminals 1–3 from section 12, or Docker Compose)

# Then run Locust with the web UI:
locust -f load_tests/locustfile.py --host http://localhost:8000

# Open http://localhost:8089 in your browser
# Set number of users (e.g. 50) and spawn rate (e.g. 5), then Start

# Headless mode (60 seconds, 50 users):
locust -f load_tests/locustfile.py --host http://localhost:8000 `
       --headless -u 50 -r 5 --run-time 60s
```

---

## 17. Simulating App B Overload

**Step 1 — Increase App B's response delay to 2 seconds:**

```powershell
Invoke-RestMethod `
  -Uri "http://localhost:8002/admin/delay" `
  -Method POST `
  -Headers @{"X-Admin-Key" = "app-b-admin-key"} `
  -Body '{"delay_ms": 2000}' `
  -ContentType "application/json"
```

**Step 2 — Enable failure injection (30% failure rate):**

```powershell
Invoke-RestMethod `
  -Uri "http://localhost:8002/admin/failure" `
  -Method POST `
  -Headers @{"X-Admin-Key" = "app-b-admin-key"} `
  -Body '{"failure_rate": 0.3, "enabled": true}' `
  -ContentType "application/json"
```

**Step 3 — Watch the dashboard** at http://localhost:8501 — App B's concurrency limit should decrease while App A's remains healthy.

**Reset App B:**

```powershell
Invoke-RestMethod `
  -Uri "http://localhost:8002/admin/delay" `
  -Method POST `
  -Headers @{"X-Admin-Key" = "app-b-admin-key"} `
  -Body '{"delay_ms": 50}' `
  -ContentType "application/json"

Invoke-RestMethod `
  -Uri "http://localhost:8002/admin/failure" `
  -Method POST `
  -Headers @{"X-Admin-Key" = "app-b-admin-key"} `
  -Body '{"failure_rate": 0.0, "enabled": false}' `
  -ContentType "application/json"
```

---

## 18. Comparing Static vs Adaptive Modes

**Switch to Static mode** (limits frozen at current values):

```powershell
Invoke-RestMethod `
  -Uri "http://localhost:8000/admin/controller/mode" `
  -Method POST `
  -Headers @{"X-Api-Key" = "gateway-admin-secret-key-change-in-production"} `
  -Body '{"mode": "static"}' `
  -ContentType "application/json"
```

**Switch back to Adaptive mode:**

```powershell
Invoke-RestMethod `
  -Uri "http://localhost:8000/admin/controller/mode" `
  -Method POST `
  -Headers @{"X-Api-Key" = "gateway-admin-secret-key-change-in-production"} `
  -Body '{"mode": "adaptive"}' `
  -ContentType "application/json"
```

**Recommended comparison procedure:**
1. Run Locust with `AppAUser` + `AppBUser` in **static mode**. Record rejection rate and latency.
2. Stress App B using the admin endpoint (delay = 2000ms).
3. Observe that the rejection rate increases and limits stay fixed.
4. Switch to **adaptive mode**. Watch App B's limit decrease on the dashboard.
5. Observe App A's requests continue succeeding while App B is throttled.

---

## 19. Registering Another Application

Edit `config/applications.yaml` and add a new entry:

```yaml
applications:
  app_a:
    name: "Application A - Normal Service"
    base_url: "http://localhost:8001"
    docker_base_url: "http://app-a:8001"
    initial_concurrency: 10
    min_concurrency: 2
    max_concurrency: 50
    enabled: true
    timeout_seconds: 10.0

  app_b:
    name: "Application B - Configurable Stress Service"
    base_url: "http://localhost:8002"
    docker_base_url: "http://app-b:8002"
    initial_concurrency: 10
    min_concurrency: 2
    max_concurrency: 50
    enabled: true
    timeout_seconds: 10.0

  # Add your new application here:
  my_new_app:
    name: "My New Application"
    base_url: "http://localhost:8003"
    docker_base_url: "http://my-new-app:8003"
    initial_concurrency: 5
    min_concurrency: 1
    max_concurrency: 30
    enabled: true
    timeout_seconds: 15.0
    description: "My new backend service"
```

Then restart the gateway. The new application is immediately:
- Routed at `/api/my-new-app/...`
- Given its own bulkhead with `initial_concurrency = 5`
- Monitored independently by the adaptive controller
- Visible in the dashboard and admin API

**No gateway code changes are needed.**

---

## 20. Management API Reference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | None | Gateway + all apps health summary |
| GET | `/metrics` | None | Prometheus metrics (text format) |
| GET | `/docs` | None | Swagger interactive API docs |
| GET | `/admin/applications` | X-Api-Key | List all apps with live metrics |
| GET | `/admin/controller/status` | X-Api-Key | Controller status and limit history |
| GET | `/admin/controller/mode` | None | Current operating mode |
| POST | `/admin/controller/mode` | X-Api-Key | Switch mode: `{"mode": "adaptive"\|"static"}` |
| GET/POST/PUT/DELETE/PATCH | `/api/{app-name}/{path}` | None | Proxy to registered backend |

**Default Admin API Key:** `gateway-admin-secret-key-change-in-production`  
Set via `ADMIN_API_KEY` environment variable or in `applications.yaml → gateway.admin_api_key`.

---

## 21. Known Limitations

- **Single node only** — the gateway runs as a single process; no distributed coordination
- **No persistent state** — concurrency limits reset to `initial_concurrency` on restart
- **AIMD is reactive, not predictive** — takes 1–2 evaluation cycles (5–10 seconds) to react to sudden load spikes
- **Algorithm parameters need tuning** — `target_latency_ms` must match the backend's expected response time
- **No circuit breaker** — the gateway continues forwarding even to a fully broken backend (just reduces limit)
- **No request queuing** — rejected requests return 429 immediately, not queued for retry
- **Dashboard requires gateway to be running** — shows "Gateway unreachable" if the gateway is down
- **App B admin endpoints are HTTP only** — suitable for local academic demonstration, not production

---

## 22. Future Enhancements

- **Distributed deployment** with shared state via Redis or etcd
- **Circuit breaker pattern** to stop forwarding to a fully-broken backend
- **Request queuing** with configurable queue depth and timeout
- **ML-based controller** using reinforcement learning for predictive limit adjustment
- **Hot configuration reload** to add/remove applications without restarting
- **Weighted routing** to distribute traffic across multiple instances of the same app
- **Authentication middleware** for client-facing routes
- **TLS/HTTPS support** for production use
- **Grafana dashboard** using the Prometheus metrics endpoint

---

## License

MIT © 2024 — B.Tech Capstone Project
