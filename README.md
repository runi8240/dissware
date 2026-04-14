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
├── models/
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
├── training/
│   ├── build_training_dataset.py
│   ├── train_logreg.py
│   ├── Dockerfile
│   └── requirements.txt
├── training-data/
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
The `ml` policy is now formulated as an `sklearn` logistic-regression classifier
for the binary target: "will this segment be reused within horizon H?".
The current default horizon is the next `5` requests, configured by
`POLICY_ML_REUSE_HORIZON_REQUESTS`.
The feature-ablation sets are now explicitly defined in code and exposed by the
policy engine:
- `Set A`: `recency_seconds`, `frequency`, `inter_arrival_gap_seconds`, `rolling_hit_count`
- `Set B`: Set A + `size_bytes`, `estimated_object_store_latency_ms`, `transfer_cost_proxy`
- `Set C`: Set B + `query_type`, `object_class`, `workload_phase`
The workload generator is config-driven and supports `stationary`, `bursty`,
`phase-shifted`, and `scale-event` modes with deterministic seeds.
In `scale-event` mode, the workload generator now changes the router's active
worker count during the run through a control endpoint, then collects telemetry
showing hit-rate drop and post-scale recovery by phase.
Telemetry now includes explicit overhead measurements for Redis lookup, policy
inference, object fetch, cache insert/eviction, telemetry emission, duplicate
fetch/admit detection, object-store bytes fetched, SSD occupancy, eviction
count, and cache turnover.

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

To inspect ML policy status and whether a trained model artifact is loaded:

```bash
curl http://localhost:8001/ml/status
```

To inspect the exact Set A / Set B / Set C feature definitions:

```bash
curl http://localhost:8001/ml/feature-sets
```

To inspect the router ring:

```bash
curl http://localhost:8000/ring
```

Offline training pipeline:

1. Run a replay to generate request logs under `training-data/`.
2. Build a labeled dataset from those logs.
3. Train logistic regression and write `models/logreg_reuse_model.joblib`.

Commands:

```bash
docker compose run --rm -e WORKLOAD_CONFIG_FILE=/app/configs/phase-shifted-workload.json workload-generator
docker compose run --rm model-trainer python /app/training/build_training_dataset.py --input-dir /app/training-data --output /app/training-data/reuse_h5.jsonl --horizon-requests 5
docker compose run --rm model-trainer python /app/training/train_logreg.py --dataset /app/training-data/reuse_h5.jsonl --feature-set set-b --output-model /app/models/logreg_reuse_model.joblib
docker compose restart policy-engine
curl http://localhost:8001/ml/status
```
