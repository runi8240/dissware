# Local Elastic Warehouse Simulation

## Layout

```text
local-sim/
├── docker-compose.yml
├── data/
│   ├── cache/
│   │   └── compute-worker/
│   └── object-store/
├── services/
│   ├── compute-worker/
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
The telemetry collector listens on `localhost:8002`.

To run a trace replay after the core services are up:

```bash
docker compose run --rm workload-generator
```
