#!/usr/bin/env bash

set -e

echo -e "\033[36m==========================================================\033[0m"
echo -e "\033[36m STARTING BASELINE EXPERIMENT 1: Normal Backend Latency \033[0m"
echo -e "\033[36m Static Limit: 10 RPS | Target Load: Up to 100 RPS\033[0m"
echo -e "\033[36m==========================================================\033[0m"

export SIMULATED_DELAY_MS=0
export STATIC_REQUEST_LIMIT=10
docker compose up -d --build

echo "Waiting 10 seconds for services to initialize..."
sleep 10

echo "Running k6 Load Test for Experiment 1..."
docker run --rm -i --net=host -e TARGET_HOST="http://localhost:8080" grafana/k6 run - < load-test/rate_limit_test.js

echo -e "\n\033[36m==========================================================\033[0m"
echo -e "\033[36m STARTING BASELINE EXPERIMENT 2: Slow Backend Latency (1000ms) \033[0m"
echo -e "\033[36m Demonstrating static limit flaw during downstream degradation\033[0m"
echo -e "\033[36m==========================================================\033[0m"

export SIMULATED_DELAY_MS=1000
docker compose up -d backend

echo "Waiting 5 seconds for backend configuration update..."
sleep 5

echo "Running k6 Load Test for Experiment 2..."
docker run --rm -i --net=host -e TARGET_HOST="http://localhost:8080" grafana/k6 run - < load-test/rate_limit_test.js

echo -e "\n\033[32mExperiments completed! Check Prometheus at http://localhost:9090 and Grafana at http://localhost:3000\033[0m"
