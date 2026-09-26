# Load tests

Results and their limits: [docs/load-test.md](../docs/load-test.md).

```
# 1. fake GitHub (:9000) and fake LLM (:9001) on the host
uv run python -m scripts.mock_github &
uv run python -m loadtest.stub_llm &

# 2. the stack, pointed at them (any RSA key will do; the mock does not check it unless MOCK_APP_PUBLIC_KEY is set)
export E2E_PRIVATE_KEY="$(openssl genrsa 2048)"
WORKER_CONCURRENCY=16 docker compose -f docker-compose.yml -f docker-compose.load.yml up -d --build api worker

# 3a. burst: 500 webhooks, then watch the queue drain (writes loadtest/results/<label>.json)
uv run python -m loadtest.burst --n 500 --label my-run
# 3b. webhook latency under sustained load (stop the worker first if you want the API alone)
uv run locust -f loadtest/locustfile.py --headless -u 50 -r 50 -t 20s --host http://localhost:8000 --processes 4

# change the fake model live
curl -X POST localhost:9001/_control -d '{"latency_ms": 3000, "fail_models": ["stub-primary"]}'   # primary down
curl -X POST localhost:9001/_control -d '{"fail_rate": 1.0}'                                       # everything down
```
`WEB_CONCURRENCY=4` (env on the api service) runs four web processes. A run prints `valid: false` if fewer
reviews were posted than jobs finished. Use Locust, not `loadtest.burst`, for webhook latency: the burst
driver is a single Python process and measures its own scheduling delay at high concurrency.
