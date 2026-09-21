import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate } from 'k6/metrics';

// Custom metrics to track baseline performance
export const rateLimitedRequests = new Counter('rate_limited_requests');
export const successfulRequests = new Counter('successful_requests');
export const errorRequests = new Counter('error_requests');
export const rejectionRate = new Rate('rejection_rate');

const TARGET_HOST = __ENV.TARGET_HOST || 'http://localhost:8082';

export const options = {
  scenarios: {
    ramping_arrival_rate: {
      executor: 'ramping-arrival-rate',
      startRate: 5,
      timeUnit: '1s',
      preAllocatedVUs: 50,
      maxVUs: 200,
      stages: [
        { duration: '15s', target: 10 },  // Stage 1: Within static rate limit (10 RPS)
        { duration: '20s', target: 20 },  // Stage 2: 2x static limit (20 RPS) -> Expecting ~50% HTTP 429
        { duration: '20s', target: 50 },  // Stage 3: Heavy load (50 RPS) -> Expecting ~80% HTTP 429
        { duration: '20s', target: 100 }, // Stage 4: Burst load (100 RPS)
        { duration: '10s', target: 0 },   // Cool down
      ],
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<3000'], // 95% of requests should respond under 3s
  },
};

export default function () {
  // Randomly test product or order endpoints
  const endpoint = Math.random() > 0.5 ? '/api/products' : '/api/orders';
  const url = `${TARGET_HOST}${endpoint}`;

  const params = {
    headers: {
      'User-Agent': 'k6-load-testing-agent/1.0',
      'Accept': 'application/json',
    },
  };

  const res = http.get(url, params);

  const isSuccess = check(res, {
    'status is 200': (r) => r.status === 200,
  });

  const isRateLimited = check(res, {
    'status is 429': (r) => r.status === 429,
  });

  if (res.status === 200) {
    successfulRequests.add(1);
    rejectionRate.add(false);
  } else if (res.status === 429) {
    rateLimitedRequests.add(1);
    rejectionRate.add(true);
  } else {
    errorRequests.add(1);
    rejectionRate.add(false);
  }

  sleep(0.05);
}
