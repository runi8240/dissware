# Local Simulation Progress Update

## Overview

This update documents the work completed so far for the fully local, containerized prototype of the elastic disaggregated data warehouse with predictive tiered storage. The current focus has been on establishing a clean monorepo structure, defining the Docker Compose topology, and implementing the initial service skeletons for the compute worker and policy engine.

The result is a self-contained local simulation scaffold that can be run with Docker Compose and extended incrementally as more functionality is added.

## What Has Been Built

### 1. A Dedicated `local-sim/` Prototype Workspace

The repository originally contained mostly paper and class project files. To avoid mixing runnable system code with the LaTeX and report artifacts, a dedicated `local-sim/` subdirectory was created. This keeps the simulation isolated and makes it easier to evolve independently.

Current structure:

```text
local-sim/
├── docker-compose.yml
├── README.md
├── update.md
├── data/
│   ├── object-store/
│   └── cache/
│       └── compute-worker/
├── services/
│   ├── compute-worker/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/
│   │       └── main.py
│   └── policy-engine/
│       ├── Dockerfile
│       ├── requirements.txt
│       └── app/
│           └── main.py
└── traces/
```

### 2. Docker Compose Topology

A complete `docker-compose.yml` file was added to orchestrate the initial system locally. It defines three services on the same bridge network:

- `redis`
  Acts as the metadata/directory service.

- `compute-worker`
  Accepts simulated read requests, checks metadata in Redis, determines whether the request is a cache hit or miss, and on a miss asks the policy engine for a placement decision.

- `policy-engine`
  Exposes a lightweight HTTP API that makes placement decisions using simple heuristics based on request features.

The services share a single local Docker network named `warehouse-net`, which allows them to communicate by service name:

- `redis://redis:6379/0`
- `http://policy-engine:8001`

The Compose file also mounts host directories into the compute worker container:

- `./data/object-store` -> `/data/object-store`
- `./data/cache/compute-worker` -> `/data/cache`

This simulates:

- a durable object-store backend using a local host directory
- a worker-local cache using another mounted local directory

### 3. Compute Worker Service Skeleton

The compute worker FastAPI application was created in:

- `local-sim/services/compute-worker/app/main.py`

Its purpose is to simulate a worker node in the disaggregated warehouse. Right now it includes:

- a `GET /health` endpoint for basic health checking
- a `POST /read` endpoint to simulate a data or segment access request
- Redis integration for metadata lookup
- async HTTP communication with the policy engine using `httpx`
- mock object-store and cache file handling through mounted directories

#### Request Model

The read endpoint accepts a request payload with:

- `segment_id`
- `size_bytes`
- `frequency`
- `recency_seconds`

These fields are enough to drive a first-pass placement decision and can later be extended with richer telemetry.

#### Current Read Flow

The current skeleton implements the following logic:

1. Receive a segment read request.
2. Check Redis for metadata under a key like `segment:<segment_id>`.
3. If Redis indicates the segment is cached and the local cache file exists:
   - treat it as a cache hit
   - increment a hit counter in Redis
   - return a response indicating the data was served from cache
4. If the metadata is missing or indicates the segment is not cached:
   - treat it as a miss
   - call the policy engine over HTTP
   - create or fetch a mock segment in the object-store directory
   - if the policy decision is `Admit`, copy the object-store file into the local cache directory
   - update Redis metadata with the location and latest decision
   - return a response showing the miss path and placement outcome

#### Metadata Representation

Redis is currently used as a simple hash-based directory. For each segment, the compute worker stores fields such as:

- `location`
- `size_bytes`
- `hits`
- `last_decision`

This is intentionally minimal but matches the role of a directory service in the architecture.

#### Mock Storage Behavior

The compute worker uses the local filesystem to simulate both object storage and the worker cache:

- If a requested segment does not exist in the object-store directory, it creates a mock binary file.
- If the policy engine returns `Admit`, the worker copies that file into the cache directory.

This gives a concrete local approximation of:

- remote durable data in object storage
- worker-local cached replicas

without requiring a real external object-store service yet.

### 4. Policy Engine Service Skeleton

The policy engine FastAPI application was created in:

- `local-sim/services/policy-engine/app/main.py`

It includes:

- a `GET /health` endpoint
- a `POST /decide` endpoint
- typed request and response schemas using Pydantic

#### Current Policy Logic

The current version implements a simple heuristic:

- Return `Admit` if:
  - `frequency` is greater than or equal to the configured threshold, and
  - `size_bytes` is less than or equal to the configured size threshold
- Otherwise return `Retain`

This is a deliberately lightweight starting point. It provides a stable contract between the compute worker and the policy engine while leaving room for more advanced heuristics or ML-driven logic later.

#### Configuration

The policy thresholds are configurable through environment variables:

- `POLICY_ADMIT_SIZE_THRESHOLD_BYTES`
- `POLICY_ADMIT_FREQUENCY_THRESHOLD`

This makes it easy to tune policy behavior without editing code.

### 5. Per-Service Container Definitions

Each service has its own Dockerfile and Python dependency manifest.

#### Compute Worker Dependencies

The compute worker currently uses:

- `fastapi`
- `uvicorn`
- `httpx`
- `redis[hiredis]`
- `pydantic`

#### Policy Engine Dependencies

The policy engine currently uses:

- `fastapi`
- `uvicorn`
- `pydantic`

Each Dockerfile is based on `python:3.11-slim` and runs the service via `uvicorn`.

### 6. Basic Documentation

A short `README.md` was added under `local-sim/` with:

- the prototype layout
- the basic startup command
- the exposed service ports

This provides an entry point for running the stack locally.

## Validation Completed

Two basic validation checks were run after scaffolding:

### Python Syntax Validation

Both service entrypoints were compiled with `python3 -m compileall`, which passed successfully. This confirms the initial Python files are syntactically valid.

### Docker Compose Validation

The Compose configuration was validated with:

```bash
docker compose -f local-sim/docker-compose.yml config
```

This also passed, confirming that the Compose file is structurally valid and resolves correctly.

## What the Current Prototype Demonstrates

At this stage, the prototype already demonstrates the core control path of the target architecture:

- a request arrives at a compute worker
- the worker consults the metadata/directory service
- on a miss, the worker asks a separate policy engine for a placement decision
- the worker reads from the durable base tier
- the worker may populate its local cache based on policy
- the worker updates metadata to reflect the current segment state

This means the foundational service boundaries and communication patterns are now in place.

## What Is Still Missing

The current implementation is intentionally skeletal. The following major pieces are not yet built:

- workload generator for concurrent trace replay
- telemetry collector service
- explicit eviction logic
- background cache management
- richer metadata schema
- multiple compute workers
- realistic object-store fetch latency simulation
- trace ingestion pipeline
- metrics and observability
- tests for the service APIs and end-to-end behavior

## Recommended Next Steps

The most useful next additions would be:

1. Add a workload generator that reads a trace and sends concurrent requests to the compute worker.
2. Add a telemetry collector to record request latency, hit/miss outcomes, and policy decisions.
3. Extend the policy engine so it can return `Evict` in addition to `Admit` and `Retain`.
4. Add support for multiple compute workers in Docker Compose.
5. Add a simple benchmark or experiment driver to measure cache hit rate and latency under different policy thresholds.

## Current Run Command

From the repository root:

```bash
cd "local-sim"
docker compose up --build
```

This starts:

- Redis on port `6379`
- Compute worker on port `8000`
- Policy engine on port `8001`

## Summary

So far, the project has moved from a high-level architecture idea to an executable local scaffold with:

- a clean monorepo structure
- container orchestration
- a Redis-backed metadata service role
- a compute worker microservice
- a policy engine microservice
- mock object-store and cache tiers using local mounted volumes

The current code is not yet a full simulator, but it establishes the correct local architecture and a working control path that can now be extended into a more realistic trace-driven distributed systems prototype.
