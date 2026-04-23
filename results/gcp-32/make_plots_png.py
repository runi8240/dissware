from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent
FIG_DIR = ROOT / "figures-png"


def load_summary(name: str) -> dict:
    return json.loads((ROOT / name).read_text())


def save_figure(fig: plt.Figure, name: str) -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG_DIR / name, dpi=220, bbox_inches="tight")
    plt.close(fig)


def setup_style() -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams["figure.figsize"] = (12, 5)
    plt.rcParams["axes.spines.top"] = False
    plt.rcParams["axes.spines.right"] = False
    plt.rcParams["axes.titlesize"] = 13
    plt.rcParams["axes.labelsize"] = 11


def stationary_plot() -> None:
    rows = [
        ("LRU", load_summary("lru_stationary.summary.json")),
        ("LFU", load_summary("lfu_stationary.summary.json")),
        ("Size-aware", load_summary("heuristic_stationary.summary.json")),
    ]
    labels = [label for label, _ in rows]
    latencies = [summary["average_latency_ms"] for _, summary in rows]
    hit_rates = [summary["cache_hit_rate"] for _, summary in rows]
    colors = ["#4C78A8", "#72B7B2", "#F58518"]

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    axes[0].bar(labels, latencies, color=colors)
    axes[0].set_title("Stationary: Average Latency")
    axes[0].set_ylabel("Latency (ms)")

    axes[1].bar(labels, hit_rates, color=colors)
    axes[1].set_title("Stationary: Cache Hit Rate")
    axes[1].set_ylabel("Hit rate")
    axes[1].set_ylim(0, 1.0)

    fig.suptitle("Stationary Policy Comparison")
    fig.tight_layout()
    save_figure(fig, "stationary_policy_comparison.png")


def phase_shift_plot() -> None:
    phase_shift = {
        "Heuristic": load_summary("heuristic_phase_shift.summary.json"),
        "ML Set B": load_summary("ml_set_b_phase_shift.summary.json"),
    }
    phases = list(phase_shift["Heuristic"]["phases"])
    colors = {"Heuristic": "#F58518", "ML Set B": "#54A24B"}

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
    for label, summary in phase_shift.items():
        hits = [summary["phases"][phase]["cache_hit_rate"] for phase in phases]
        lats = [summary["phases"][phase]["average_latency_ms"] for phase in phases]
        axes[0].plot(phases, hits, marker="o", linewidth=2.5, label=label, color=colors[label])
        axes[1].plot(phases, lats, marker="o", linewidth=2.5, label=label, color=colors[label])

    axes[0].set_title("Phase Shift: Cache Hit Rate")
    axes[0].set_ylabel("Hit rate")
    axes[0].set_ylim(0, 1.05)

    axes[1].set_title("Phase Shift: Average Latency")
    axes[1].set_ylabel("Latency (ms)")

    handles, labels = axes[1].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False)
    fig.suptitle("Phase-Shift Comparison")
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    save_figure(fig, "phase_shift_comparison.png")


def scale_event_plot() -> None:
    scale_event = {
        "Heuristic": load_summary("heuristic_scale_event.summary.json"),
        "ML Set B": load_summary("ml_set_b_scale_event.summary.json"),
    }
    phases = list(scale_event["Heuristic"]["phases"])
    colors = {"Heuristic": "#6F4E7C", "ML Set B": "#54A24B"}

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
    for label, summary in scale_event.items():
        lats = [summary["phases"][phase]["average_latency_ms"] for phase in phases]
        hits = [summary["phases"][phase]["cache_hit_rate"] for phase in phases]
        axes[0].plot(phases, lats, marker="o", linewidth=2.5, label=label, color=colors[label])
        axes[1].plot(phases, hits, marker="o", linewidth=2.5, label=label, color=colors[label])

    axes[0].set_title("Scale Event: Average Latency")
    axes[0].set_ylabel("Latency (ms)")

    axes[1].set_title("Scale Event: Cache Hit Rate")
    axes[1].set_ylabel("Hit rate")
    axes[1].set_ylim(0.5, 1.05)

    handles, labels = axes[1].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False)
    fig.suptitle("Scale-Event Recovery")
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    save_figure(fig, "scale_event_recovery.png")


def ablation_plot() -> None:
    rows = [
        ("Set A", load_summary("ml_set_a_phase_shift.summary.json")),
        ("Set B", load_summary("ml_set_b_phase_shift.summary.json")),
        ("Set C", load_summary("ml_set_c_phase_shift.summary.json")),
    ]
    labels = [label for label, _ in rows]
    latencies = [summary["average_latency_ms"] for _, summary in rows]
    hit_rates = [summary["cache_hit_rate"] for _, summary in rows]
    colors = ["#4C78A8", "#54A24B", "#B279A2"]

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    axes[0].bar(labels, latencies, color=colors)
    axes[0].set_title("Feature Ablation: Average Latency")
    axes[0].set_ylabel("Latency (ms)")

    axes[1].bar(labels, hit_rates, color=colors)
    axes[1].set_title("Feature Ablation: Cache Hit Rate")
    axes[1].set_ylabel("Hit rate")
    axes[1].set_ylim(0, 1.0)

    fig.suptitle("Ablation Study: Set A vs Set B vs Set C")
    fig.tight_layout()
    save_figure(fig, "feature_ablation.png")


def main() -> None:
    setup_style()
    stationary_plot()
    phase_shift_plot()
    scale_event_plot()
    ablation_plot()
    print(f"Wrote PNG figures to {FIG_DIR}")


if __name__ == "__main__":
    main()
