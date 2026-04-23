from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent
FIG_DIR = ROOT / "presentation-figures"
COLORS = {
    "LRU": "#386FA4",
    "LFU": "#59A14F",
    "Size-aware": "#F28E2B",
    "Heuristic": "#F28E2B",
    "ML Set A": "#4E79A7",
    "ML Set B": "#59A14F",
    "ML Set C": "#B07AA1",
    "Admit-all + LRU": "#386FA4",
    "Admit-all + LFU": "#59A14F",
    "Admit-all + Size-aware": "#E15759",
}


def load_json(name: str) -> dict:
    return json.loads((ROOT / name).read_text())


def load_summary(name: str) -> dict:
    return load_json(f"{name}.summary.json")


def pct(value: float) -> float:
    return round(value * 100.0, 1)


def gb(value: int | float) -> float:
    return round(value / (1024**3), 2)


def annotate_bars(ax, fmt: str = "{:.1f}") -> None:
    for patch in ax.patches:
        height = patch.get_height()
        ax.annotate(
            fmt.format(height),
            (patch.get_x() + patch.get_width() / 2, height),
            ha="center",
            va="bottom",
            fontsize=9,
            xytext=(0, 3),
            textcoords="offset points",
        )


def save(fig: plt.Figure, name: str) -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG_DIR / name, dpi=240, bbox_inches="tight")
    plt.close(fig)


def setup_style() -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.titleweight": "bold",
            "axes.titlesize": 14,
            "axes.labelsize": 11,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 10,
        }
    )


def chart_stationary_baselines() -> None:
    rows = [
        ("LRU", load_summary("lru_stationary")),
        ("LFU", load_summary("lfu_stationary")),
        ("Size-aware", load_summary("heuristic_stationary")),
    ]
    labels = [label for label, _ in rows]
    hit_rates = [pct(summary["cache_hit_rate"]) for _, summary in rows]
    latencies = [summary["average_latency_ms"] for _, summary in rows]
    evictions = [summary["cache_stats"]["eviction_count_total_latest"] for _, summary in rows]
    colors = [COLORS[label] for label in labels]

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
    axes[0].bar(labels, hit_rates, color=colors)
    axes[0].set_title("Cache Hit Rate")
    axes[0].set_ylabel("Hit rate (%)")
    axes[0].set_ylim(0, max(hit_rates) * 1.25)
    annotate_bars(axes[0])

    axes[1].bar(labels, latencies, color=colors)
    axes[1].set_title("Average Latency")
    axes[1].set_ylabel("Latency (ms)")
    axes[1].set_ylim(0, max(latencies) * 1.25)
    annotate_bars(axes[1])

    axes[2].bar(labels, evictions, color=colors)
    axes[2].set_title("Evictions")
    axes[2].set_ylabel("Count")
    axes[2].set_ylim(0, max(evictions) * 1.25 if max(evictions) else 1)
    annotate_bars(axes[2], "{:.0f}")

    fig.suptitle("Hard Stationary Benchmark: Baseline Policies", fontsize=16, fontweight="bold")
    fig.tight_layout()
    save(fig, "01_stationary_baselines.png")


def chart_phase_shift_ml_ablation() -> None:
    rows = [
        ("Heuristic", load_summary("heuristic_phase_shift")),
        ("ML Set A", load_summary("ml_set_a_phase_shift")),
        ("ML Set B", load_summary("ml_set_b_phase_shift")),
        ("ML Set C", load_summary("ml_set_c_phase_shift")),
    ]
    phases = list(rows[0][1]["phases"])

    fig, axes = plt.subplots(1, 2, figsize=(14, 4.8))
    for label, summary in rows:
        hits = [pct(summary["phases"][phase]["cache_hit_rate"]) for phase in phases]
        lats = [summary["phases"][phase]["average_latency_ms"] for phase in phases]
        axes[0].plot(phases, hits, marker="o", linewidth=2.4, label=label, color=COLORS[label])
        axes[1].plot(phases, lats, marker="o", linewidth=2.4, label=label, color=COLORS[label])

    axes[0].set_title("Hit Rate by Phase")
    axes[0].set_ylabel("Hit rate (%)")
    axes[0].set_ylim(0, 80)
    axes[1].set_title("Latency by Phase")
    axes[1].set_ylabel("Latency (ms)")
    axes[1].legend(loc="upper left", frameon=True)
    fig.suptitle("Hard Phase Shift: ML Feature Ablation", fontsize=16, fontweight="bold")
    fig.tight_layout()
    save(fig, "02_phase_shift_ml_ablation.png")


def chart_phase_shift_overall() -> None:
    rows = [
        ("Heuristic", load_summary("heuristic_phase_shift")),
        ("ML Set A", load_summary("ml_set_a_phase_shift")),
        ("ML Set B", load_summary("ml_set_b_phase_shift")),
        ("ML Set C", load_summary("ml_set_c_phase_shift")),
    ]
    labels = [label for label, _ in rows]
    hit_rates = [pct(summary["cache_hit_rate"]) for _, summary in rows]
    object_gb = [gb(summary["bytes_fetched_from_object_store_total"]) for _, summary in rows]
    latencies = [summary["average_latency_ms"] for _, summary in rows]
    colors = [COLORS[label] for label in labels]

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
    axes[0].bar(labels, hit_rates, color=colors)
    axes[0].set_title("Overall Hit Rate")
    axes[0].set_ylabel("Hit rate (%)")
    axes[0].set_ylim(0, max(hit_rates) * 1.25)
    annotate_bars(axes[0])

    axes[1].bar(labels, object_gb, color=colors)
    axes[1].set_title("Object-Store Bytes")
    axes[1].set_ylabel("GB fetched")
    axes[1].set_ylim(0, max(object_gb) * 1.25)
    annotate_bars(axes[1])

    axes[2].bar(labels, latencies, color=colors)
    axes[2].set_title("Average Latency")
    axes[2].set_ylabel("Latency (ms)")
    axes[2].set_ylim(0, max(latencies) * 1.25)
    annotate_bars(axes[2])

    for ax in axes:
        ax.tick_params(axis="x", rotation=20)
    fig.suptitle("Hard Phase Shift: Overall Outcomes", fontsize=16, fontweight="bold")
    fig.tight_layout()
    save(fig, "03_phase_shift_overall.png")


def chart_scale_event_ml_ablation() -> None:
    rows = [
        ("Heuristic", load_summary("heuristic_scale_event")),
        ("ML Set A", load_summary("ml_set_a_scale_event")),
        ("ML Set B", load_summary("ml_set_b_scale_event")),
        ("ML Set C", load_summary("ml_set_c_scale_event")),
    ]
    phases = list(rows[0][1]["phases"])

    fig, axes = plt.subplots(1, 2, figsize=(14, 4.8))
    for label, summary in rows:
        hits = [pct(summary["phases"][phase]["cache_hit_rate"]) for phase in phases]
        evictions = summary["cache_stats"]["eviction_count_total_latest"]
        axes[0].plot(phases, hits, marker="o", linewidth=2.4, label=label, color=COLORS[label])
        axes[1].bar(label, evictions, color=COLORS[label])

    axes[0].set_title("Hit Rate by Elasticity Phase")
    axes[0].set_ylabel("Hit rate (%)")
    axes[0].set_ylim(0, 60)
    axes[0].tick_params(axis="x", rotation=15)
    axes[1].set_title("Total Evictions")
    axes[1].set_ylabel("Count")
    axes[1].tick_params(axis="x", rotation=20)
    annotate_bars(axes[1], "{:.0f}")
    axes[0].legend(loc="upper left", frameon=True)
    fig.suptitle("Hard Scale Event: ML Policies Under Elasticity", fontsize=16, fontweight="bold")
    fig.tight_layout()
    save(fig, "04_scale_event_ml_ablation.png")


def chart_eviction_stress() -> None:
    rows = [
        ("Admit-all + LRU", load_summary("admit_all_lru_phase_shift")),
        ("Admit-all + LFU", load_summary("admit_all_lfu_phase_shift")),
        ("Admit-all + Size-aware", load_summary("admit_all_size_aware_phase_shift")),
    ]
    labels = [label for label, _ in rows]
    hit_rates = [pct(summary["cache_hit_rate"]) for _, summary in rows]
    evictions = [summary["cache_stats"]["eviction_count_total_latest"] for _, summary in rows]
    turnover_gb = [gb(summary["cache_stats"]["cache_turnover_bytes_total_latest"]) for _, summary in rows]
    colors = [COLORS[label] for label in labels]

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
    axes[0].bar(labels, hit_rates, color=colors)
    axes[0].set_title("Hit Rate")
    axes[0].set_ylabel("Hit rate (%)")
    axes[0].set_ylim(0, max(hit_rates) * 1.25)
    annotate_bars(axes[0])

    axes[1].bar(labels, evictions, color=colors)
    axes[1].set_title("Evictions")
    axes[1].set_ylabel("Count")
    axes[1].set_ylim(0, max(evictions) * 1.25)
    annotate_bars(axes[1], "{:.0f}")

    axes[2].bar(labels, turnover_gb, color=colors)
    axes[2].set_title("Cache Turnover")
    axes[2].set_ylabel("GB rewritten")
    axes[2].set_ylim(0, max(turnover_gb) * 1.25)
    annotate_bars(axes[2])

    for ax in axes:
        ax.tick_params(axis="x", rotation=22)
    fig.suptitle("Eviction Stress Test: Replacement Policies", fontsize=16, fontweight="bold")
    fig.tight_layout()
    save(fig, "05_eviction_stress.png")


def chart_model_metrics() -> None:
    rows = []
    for label, filename in [
        ("Set A", "logreg_set_a.metrics.json"),
        ("Set B", "logreg_set_b.metrics.json"),
        ("Set C", "logreg_set_c.metrics.json"),
    ]:
        metrics = load_json(filename)["metrics"]
        rows.append((label, metrics))

    labels = [label for label, _ in rows]
    accuracy = [metrics["accuracy"] * 100 for _, metrics in rows]
    roc_auc = [metrics["roc_auc"] * 100 for _, metrics in rows]
    colors = [COLORS[f"ML {label}"] for label in labels]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    axes[0].bar(labels, accuracy, color=colors)
    axes[0].set_title("Accuracy")
    axes[0].set_ylabel("Percent")
    axes[0].set_ylim(0, 100)
    annotate_bars(axes[0])

    axes[1].bar(labels, roc_auc, color=colors)
    axes[1].set_title("ROC AUC")
    axes[1].set_ylabel("Percent")
    axes[1].set_ylim(0, 100)
    annotate_bars(axes[1])

    fig.suptitle("Offline Logistic Regression Metrics", fontsize=16, fontweight="bold")
    fig.tight_layout()
    save(fig, "06_model_metrics.png")


def write_takeaways() -> None:
    heuristic_phase = load_summary("heuristic_phase_shift")
    ml_b_phase = load_summary("ml_set_b_phase_shift")
    ml_c_scale = load_summary("ml_set_c_scale_event")
    admit_lru = load_summary("admit_all_lru_phase_shift")
    lfu_stationary = load_summary("lfu_stationary")
    lru_stationary = load_summary("lru_stationary")
    set_b_metrics = load_json("logreg_set_b.metrics.json")["metrics"]

    lines = [
        "# GCP Latest Results: Presentation Takeaways",
        "",
        "## Main Interpretation",
        "",
        "- The hard benchmark is meaningfully harder than the original: cache hit rates are no longer saturated and evictions occur.",
        f"- Under hard phase shift, ML Set B improves hit rate from {pct(heuristic_phase['cache_hit_rate'])}% to {pct(ml_b_phase['cache_hit_rate'])}%.",
        f"- Under hard phase shift, ML Set B reduces object-store traffic from {gb(heuristic_phase['bytes_fetched_from_object_store_total'])} GB to {gb(ml_b_phase['bytes_fetched_from_object_store_total'])} GB.",
        f"- In the eviction stress test, admit-all + LRU reaches {pct(admit_lru['cache_hit_rate'])}% hit rate with {admit_lru['cache_stats']['eviction_count_total_latest']} evictions.",
        f"- In stationary hard load, LFU gets the highest hit rate among the simple baselines: {pct(lfu_stationary['cache_hit_rate'])}% versus LRU at {pct(lru_stationary['cache_hit_rate'])}%.",
        f"- The offline ML model is modest but not dominant; Set B ROC AUC is {set_b_metrics['roc_auc']}.",
        f"- In hard scale-event runs, Set C has the best hit rate among ML variants at {pct(ml_c_scale['cache_hit_rate'])}%, but it also has high latency, indicating replacement churn is costly.",
        "",
        "## What To Say In Class",
        "",
        "The original workload was too easy because the working set fit in cache. The hard GCP benchmark increases object diversity, object size, and phase shifts while reducing cache capacity to 32 MB. That exposes real cache pressure: policies now differ in hit rate, object-store traffic, and eviction count.",
        "",
        "The learned policies improve hit rate under phase shifts, but the latency result is more nuanced because aggressive admission causes eviction churn. This is a realistic systems tradeoff: maximizing hit rate is not always the same as minimizing end-to-end latency.",
        "",
    ]
    (ROOT / "presentation_takeaways.md").write_text("\n".join(lines))


def main() -> None:
    setup_style()
    chart_stationary_baselines()
    chart_phase_shift_ml_ablation()
    chart_phase_shift_overall()
    chart_scale_event_ml_ablation()
    chart_eviction_stress()
    chart_model_metrics()
    write_takeaways()
    print(f"Wrote presentation figures to {FIG_DIR}")
    print(f"Wrote takeaways to {ROOT / 'presentation_takeaways.md'}")


if __name__ == "__main__":
    main()
