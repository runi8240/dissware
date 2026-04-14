from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import train_test_split


FEATURE_SETS = {
    "set-a": [
        "recency_seconds",
        "frequency",
        "inter_arrival_gap_seconds",
        "rolling_hit_count",
    ],
    "set-b": [
        "recency_seconds",
        "frequency",
        "inter_arrival_gap_seconds",
        "rolling_hit_count",
        "size_bytes",
        "estimated_object_store_latency_ms",
        "transfer_cost_proxy",
    ],
    "set-c": [
        "recency_seconds",
        "frequency",
        "inter_arrival_gap_seconds",
        "rolling_hit_count",
        "size_bytes",
        "estimated_object_store_latency_ms",
        "transfer_cost_proxy",
        "query_type_code",
        "object_class_code",
        "workload_phase_code",
    ],
}

QUERY_TYPE_CODES = {"scan": 0.0, "lookup": 1.0, "join": 2.0, "aggregate": 3.0, "unknown": -1.0}
OBJECT_CLASS_CODES = {"fact": 0.0, "dimension": 1.0, "aggregate": 2.0, "unknown": -1.0}
WORKLOAD_PHASE_CODES = {
    "stationary": 0.0,
    "bursty-base": 1.0,
    "bursty-burst": 2.0,
    "phase-1": 3.0,
    "phase-2": 4.0,
    "phase-3": 5.0,
    "pre-scale": 6.0,
    "scale-out": 7.0,
    "post-scale": 8.0,
    "scale-in": 9.0,
    "unknown": -1.0,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train logistic regression reuse model.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--feature-set", required=True, choices=sorted(FEATURE_SETS))
    parser.add_argument("--output-model", required=True, type=Path)
    parser.add_argument("--test-size", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--solver", default="lbfgs")
    parser.add_argument("--max-iter", type=int, default=2000)
    parser.add_argument("--c", type=float, default=1.0)
    parser.add_argument("--metadata-out", type=Path, default=None)
    return parser.parse_args()


def load_dataset(dataset_path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in dataset_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def encode_row(row: dict) -> dict[str, float]:
    size_bytes = float(row.get("size_bytes", row.get("bytes_served", 0.0)))
    return {
        "recency_seconds": float(row["recency_seconds"]),
        "frequency": float(row["frequency"]),
        "inter_arrival_gap_seconds": float(row["inter_arrival_gap_seconds"]),
        "rolling_hit_count": float(row["rolling_hit_count"]),
        "size_bytes": size_bytes,
        "estimated_object_store_latency_ms": float(row["estimated_object_store_latency_ms"]),
        "transfer_cost_proxy": float(row["transfer_cost_proxy"]),
        "query_type_code": QUERY_TYPE_CODES.get((row.get("query_type") or "unknown").lower(), -1.0),
        "object_class_code": OBJECT_CLASS_CODES.get((row.get("object_class") or "unknown").lower(), -1.0),
        "workload_phase_code": WORKLOAD_PHASE_CODES.get((row.get("workload_phase") or "unknown").lower(), -1.0),
    }


def main() -> None:
    args = parse_args()
    rows = load_dataset(args.dataset)
    feature_names = FEATURE_SETS[args.feature_set]
    encoded_rows = [encode_row(row) for row in rows]
    x = np.array([[encoded[name] for name in feature_names] for encoded in encoded_rows], dtype=float)
    y = np.array([int(row["reuse_within_horizon"]) for row in rows], dtype=int)

    x_train, x_test, y_train, y_test = train_test_split(
        x,
        y,
        test_size=args.test_size,
        random_state=args.seed,
        stratify=y if len(set(y)) > 1 else None,
    )

    model = LogisticRegression(
        max_iter=args.max_iter,
        random_state=args.seed,
        solver=args.solver,
        C=args.c,
    )
    model.fit(x_train, y_train)

    probabilities = model.predict_proba(x_test)[:, 1]
    predictions = model.predict(x_test)
    metrics = {
        "dataset_rows": len(rows),
        "train_rows": len(x_train),
        "test_rows": len(x_test),
        "accuracy": round(float(accuracy_score(y_test, predictions)), 4),
        "roc_auc": round(float(roc_auc_score(y_test, probabilities)), 4) if len(set(y_test)) > 1 else None,
        "feature_set": args.feature_set,
        "feature_names": feature_names,
        "solver": args.solver,
        "max_iter": args.max_iter,
        "c": args.c,
        "test_size": args.test_size,
        "seed": args.seed,
        "model_family": "sklearn.linear_model.LogisticRegression",
        "hardware_note": "CPU training via scikit-learn; Apple MPS is not used by this model.",
    }

    args.output_model.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "model": model,
        "feature_set": args.feature_set,
        "feature_names": feature_names,
        "metrics": metrics,
        "label_name": "reuse_within_horizon",
        "training_config": {
            "dataset": str(args.dataset),
            "solver": args.solver,
            "max_iter": args.max_iter,
            "c": args.c,
            "test_size": args.test_size,
            "seed": args.seed,
        },
    }
    joblib.dump(artifact, args.output_model)
    metadata_out = args.metadata_out or args.output_model.with_suffix(".metrics.json")
    metadata_out.parent.mkdir(parents=True, exist_ok=True)
    metadata_out.write_text(json.dumps({"output_model": str(args.output_model), "metrics": metrics}, indent=2) + "\n")
    print(json.dumps({"output_model": str(args.output_model), "metrics": metrics}, indent=2))


if __name__ == "__main__":
    main()
