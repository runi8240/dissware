# Local Simulation Status Update

## Overview

`local-sim/` is no longer just an initial scaffold. The current system is a runnable local simulation of an elastic disaggregated warehouse with:

- a Redis-backed metadata layer
- three cache-bearing compute workers
- a request router with consistent hashing and scale controls
- a telemetry collector with phase-aware summaries
- a config-driven workload generator
- an offline training pipeline for a logistic-regression admission policy
- benchmark artifacts and preliminary results under `results/update2/`

The prototype now supports end-to-end trace replay, cache admission experiments, worker scale events, telemetry collection, and offline ML model training.

## Current System Layout

```text
local-sim/
├── docker-compose.yml
├── README.md
├── update.md
├── configs/
│   ├── default-workload.json
│   ├── bursty-workload.json
│   ├── phase-shifted-workload.json
│   ├── scale-event-workload.json
│   └── bench/
├── data/
│   ├── object-store/
│   └── cache/
├── models/
├── services/
│   ├── compute-worker/
│   ├── policy-engine/
│   ├── request-router/
│   ├── telemetry-collector/
│   └── workload-generator/
├── training/
├── training-data/
├── traces/
└── results/update2/
```

## What Is Implemented Now

### 1. Multi-service local deployment

The Compose stack currently brings up:

- `redis`
- `policy-engine`
- `telemetry-collector`
- `compute-worker-1`
- `compute-worker-2`
- `compute-worker-3`
- `request-router`
- `model-trainer`
- `workload-generator`

Public ports:

- router: `localhost:8000`
- policy engine: `localhost:8001`
- telemetry collector: `localhost:8002`
- Redis: `localhost:6379`

### 2. Request routing and elasticity controls

The request router now sits in front of the workers and exposes:

- `GET /health`
- `GET /ring`
- `POST /read`
- `POST /admin/scale`

It uses consistent hashing over `segment_id` to assign requests to workers. It also supports scale-event experiments by changing the active worker count during a run. The workload generator can trigger those changes phase by phase.

### 3. Compute workers with bounded local cache

Each compute worker now does more than simple hit/miss forwarding. It:

- checks Redis metadata for worker-local cache state
- serves hits from a mounted local cache directory
- fetches misses from the simulated object-store directory
- calls the policy engine for admission decisions
- enforces a byte-bounded cache capacity
- supports multiple eviction policies:
  - `lru`
  - `lfu`
  - `size-aware-lru`
- tracks cache occupancy, turnover, evictions, duplicate fetches, and duplicate admits
- appends per-request training logs under `training-data/`

Worker endpoints include:

- `GET /health`
- `GET /cache/state`
- `POST /read`

Admission policy selection is configurable with `POLICY_NAME`:

- `baseline-lru`
- `baseline-lfu`
- `baseline-size-aware`
- `baseline-admit-all`
- `ml`

### 4. Policy engine with heuristic and ML-backed admission

The policy engine exposes:

- `GET /health`
- `GET /policies`
- `GET /ml/status`
- `GET /ml/feature-sets`
- `POST /decide`

It currently supports three heuristic baselines plus an ML policy:

- `baseline-lru`
- `baseline-lfu`
- `baseline-size-aware`
- `baseline-admit-all`
- `ml`

The ML policy is formulated as binary classification:

> will this segment be reused within the next `H` requests?

The default reuse horizon is `5` requests. If a trained model artifact is available, the policy engine loads an `sklearn` logistic-regression model from `models/`. If no artifact is present, it falls back to a deterministic score-based approximation with the same interface.

Current feature sets are defined explicitly:

- `Set A`: `recency_seconds`, `frequency`, `inter_arrival_gap_seconds`, `rolling_hit_count`
- `Set B`: Set A plus `size_bytes`, `estimated_object_store_latency_ms`, `transfer_cost_proxy`
- `Set C`: Set B plus encoded `query_type`, `object_class`, and `workload_phase`

### 5. Telemetry and experiment summaries

The telemetry collector records per-request events and exposes:

- `GET /health`
- `POST /events`
- `POST /reset`
- `GET /summary`

Telemetry now includes:

- cache hit rate and average latency
- bytes served and object-store bytes fetched
- policy inference, Redis lookup, object fetch, cache insert, eviction, and telemetry overhead
- latest SSD occupancy, eviction count, and cache turnover
- duplicate fetch and duplicate admit counters
- per-phase summaries for workload experiments
- recovery deltas for scale-out to post-scale transitions

### 6. Config-driven workload generation

The workload generator can replay synthetic request streams using JSON configs. Supported modes are:

- `stationary`
- `bursty`
- `phase-shifted`
- `scale-event`

These workloads vary hot sets, request mix, semantic tags, and active worker counts. Benchmark-sized configs are also present under `configs/bench/`.

### 7. Offline training pipeline

The training pipeline is already implemented:

1. Replay a workload and write per-request logs to `training-data/*.jsonl`.
2. Build a labeled dataset with `training/build_training_dataset.py`.
3. Train a logistic-regression classifier with `training/train_logreg.py`.
4. Restart the policy engine to load the trained artifact.

Artifacts already present under `models/` include trained models and metrics for:

- `logreg_set_a.joblib`
- `logreg_set_b.joblib`
- `logreg_set_c.joblib`
- `logreg_reuse_model.joblib`

Current metric files show that the trained models are not placeholders; they were produced from logged replay data. For example:

- `Set A`: accuracy `0.8896`, ROC AUC `0.5678`
- `Set B`: accuracy `0.8896`, ROC AUC `0.6383`
- `Set C`: accuracy `0.8742`, ROC AUC `0.6968`

## Experimental Status

The repository now includes run summaries and a preliminary report under `results/update2/`. These artifacts show that the prototype has already been used for comparative experiments, not just service bring-up.

The available summaries cover:

- stationary heuristic comparisons
- phase-shifted workload comparisons
- scale-event recovery experiments
- ML feature-ablation comparisons

Examples already in the repo:

- `heuristic_phase_shift.summary.json`
- `heuristic_scale_event.summary.json`
- `ml_set_b_phase_shift.summary.json`
- `ml_set_b_scale_event.summary.json`
- `lru_stationary.summary.json`
- `lfu_stationary.summary.json`
- `heuristic_stationary.summary.json`

The preliminary results document shows the main system claims are now empirically grounded:

- simple heuristics behave similarly on stationary workloads
- ML performs better than the heuristic in the hardest phase-shift segment of the tested run
- both heuristic and ML show cold-start penalties during scale-out
- feature ablation changes the latency and hit-rate tradeoff in measurable ways

## What the Current Prototype Demonstrates

At this point, the local simulator demonstrates all of the following in one runnable workflow:

- disaggregated control flow between router, workers, policy engine, telemetry, and Redis
- worker-local caching over a shared object-store base tier
- configurable admission and eviction policies
- end-to-end trace-driven experiments
- worker scale-out and scale-in behavior
- telemetry-backed evaluation
- offline supervised training for a predictive admission policy
- saved benchmark outputs and plot inputs for reports

## Remaining Gaps

The system is significantly more complete than the earlier scaffold, but it is still a prototype. Important limitations remain:

- the object store is still simulated with local files rather than a real remote store
- benchmark results in `results/update2/` appear to be pilot runs, not repeated trials
- the ML policy is still limited to logistic regression
- there is no persistent experiment database or dashboard
- fault injection and failure-recovery experiments are not yet implemented
- the metadata layer is still centralized in a single Redis instance

## Current Run and Inspection Commands

Bring up the stack:

```bash
docker compose up --build
```

Run the default workload:

```bash
docker compose run --rm workload-generator
```

Run a benchmark configuration:

```bash
docker compose run --rm -e WORKLOAD_CONFIG_FILE=/app/configs/bench/phase-shift-bench.json workload-generator
```

Inspect the router ring:

```bash
curl http://localhost:8000/ring
```

Inspect a worker cache state from inside the Compose network:

```bash
docker compose exec compute-worker-1 python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/cache/state').read().decode())"
```

Inspect policy status:

```bash
curl http://localhost:8001/ml/status
curl http://localhost:8001/ml/feature-sets
```

Inspect telemetry summary:

```bash
curl http://localhost:8002/summary
```

## Summary

The local simulation has progressed from an initial microservice scaffold into a working experimental platform. The codebase now supports multi-worker request routing, cache admission and eviction policies, telemetry collection, workload replay, elasticity experiments, and an offline-trained logistic-regression admission policy with saved benchmark artifacts.

The most accurate description of the current state is not "service skeletons" anymore. It is a functional local research prototype with enough implementation depth to run comparative systems experiments and produce report-ready results.
