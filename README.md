# Local Elastic Warehouse Simulation

## Layout

```text
local-sim/
├── docker-compose.yml
├── configs/
├── data/
│   ├── cache/
│   │   └── compute-worker/
│   └── object-store/
├── services/
│   ├── compute-worker/
│   │   ├── app/main.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── request-router/
│   │   ├── app/main.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── policy-engine/
│       ├── app/main.py
│       ├── Dockerfile
│       └── requirements.txt
│   ├── telemetry-collector/
│   │   ├── app/main.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── workload-generator/
│       ├── app/main.py
│       ├── Dockerfile
│       └── requirements.txt
└── traces/
```

## Run

```bash
docker compose up --build
```

The compute worker listens on `localhost:8000` and the policy engine listens on `localhost:8001`.
The request router listens on `localhost:8000` and forwards to three internal compute workers using consistent hashing on `segment_id`.
The telemetry collector listens on `localhost:8002`.
The compute worker now enforces a byte-bounded local cache. Configure it with
`CACHE_CAPACITY_BYTES` and `CACHE_EVICTION_POLICY` (`lru`, `lfu`, or `size-aware-lru`).
Admission policy selection is configured with `POLICY_NAME` and currently supports
`baseline-lru`, `baseline-lfu`, `baseline-size-aware`, and `ml`.
The workload generator is config-driven and supports `stationary`, `bursty`,
`phase-shifted`, and `scale-event` modes with deterministic seeds.

To run a trace replay after the core services are up:

```bash
docker compose run --rm workload-generator
```

To switch workload modes, point `WORKLOAD_CONFIG_FILE` at one of:

```text
/app/configs/default-workload.json
/app/configs/bursty-workload.json
/app/configs/phase-shifted-workload.json
/app/configs/scale-event-workload.json
```

To inspect cache usage and the active eviction policy:

```bash
curl http://localhost:8000/cache/state
```

To inspect the policy-engine interface:

```bash
curl http://localhost:8001/policies
```

To inspect the router ring:

```bash
curl http://localhost:8000/ring
```
