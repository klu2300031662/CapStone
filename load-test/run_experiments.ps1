# Capstone Baseline Experiment Automation Script (PowerShell)
# Demonstration of Static Rate Limit Limitations

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " STARTING BASELINE EXPERIMENT 1: Normal Backend Latency " -ForegroundColor Cyan
Write-Host " Static Limit: 10 RPS | Target Load: Up to 100 RPS" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# Restart stack with SIMULATED_DELAY_MS=0
$env:SIMULATED_DELAY_MS = "0"
$env:STATIC_REQUEST_LIMIT = "10"
docker compose up -d --build

Write-Host "Waiting 10 seconds for services to initialize..." -ForegroundColor Yellow
Start-Sleep -Seconds 10

Write-Host "Running k6 Load Test for Experiment 1..." -ForegroundColor Green
docker run --rm -i --net=host -e TARGET_HOST="http://localhost:8080" grafana/k6 run - < load-test/rate_limit_test.js

Write-Host "`n==========================================================" -ForegroundColor Cyan
Write-Host " STARTING BASELINE EXPERIMENT 2: Slow Backend Latency (1000ms) " -ForegroundColor Cyan
Write-Host " Demonstrating static limit flaw during downstream degradation" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# Update SIMULATED_DELAY_MS to 1000ms
$env:SIMULATED_DELAY_MS = "1000"
docker compose up -d backend

Write-Host "Waiting 5 seconds for backend configuration update..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

Write-Host "Running k6 Load Test for Experiment 2..." -ForegroundColor Green
docker run --rm -i --net=host -e TARGET_HOST="http://localhost:8080" grafana/k6 run - < load-test/rate_limit_test.js

Write-Host "`nExperiments completed! Check Prometheus at http://localhost:9090 and Grafana at http://localhost:3000" -ForegroundColor BrightGreen
