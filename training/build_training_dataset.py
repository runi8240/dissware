from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build labeled reuse dataset from replay logs.")
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--horizon-requests", required=True, type=int)
    return parser.parse_args()


def load_records(input_dir: Path) -> list[dict]:
    records: list[dict] = []
    for path in sorted(input_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return sorted(records, key=lambda record: record["event_time_ns"])


def label_records(records: list[dict], horizon_requests: int) -> list[dict]:
    labeled: list[dict] = []
    for index, record in enumerate(records):
        segment_id = record["segment_id"]
        label = 0
        upper_bound = min(len(records), index + 1 + horizon_requests)
        for future in records[index + 1 : upper_bound]:
            if future["segment_id"] == segment_id:
                label = 1
                break
        labeled_record = dict(record)
        labeled_record["reuse_within_horizon"] = label
        labeled_record["reuse_horizon_requests"] = horizon_requests
        labeled.append(labeled_record)
    return labeled


def main() -> None:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    records = load_records(args.input_dir)
    labeled = label_records(records, args.horizon_requests)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in labeled:
            handle.write(json.dumps(record) + "\n")
    print(
        json.dumps(
            {
                "input_records": len(records),
                "output_records": len(labeled),
                "horizon_requests": args.horizon_requests,
                "output_path": str(args.output),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
