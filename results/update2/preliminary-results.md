# Update 2 Preliminary Results

## Summary

These preliminary results show that the local prototype is now mature enough to support the main Update 2 claims:

- the system can compare multiple heuristic policies under the same trace-driven setup
- the system can reproduce phase-shift and scale-event behavior
- the ML policy can be trained offline and benchmarked against heuristics
- the feature-ablation story is now concrete, even if some results still need more repetitions for statistical stability

The results below should be treated as \emph{preliminary single-run pilot results}, not final conclusions. They are strong enough to include in Update 2 as evidence of implementation progress and early behavioral trends.

## 1. Latency Table: LRU vs LFU vs Current Heuristic

| Policy | Avg Latency (ms) | Cache Hit Rate | Object-Store Bytes Fetched |
|---|---:|---:|---:|
| LRU | 2.401 | 0.8625 | 188,743,428 |
| LFU | 2.466 | 0.8625 | 188,743,428 |
| Current Heuristic (Size-Aware) | 2.797 | 0.8625 | 188,743,428 |

### Interpretation

- All three policies achieved the \textbf{same hit rate} and \textbf{same object-store traffic} in this stationary benchmark.
- This suggests that under a stable access distribution, the cache is large enough and the workload is simple enough that \textbf{policy choice has only a small effect on overall admission outcome}.
- Among the three, \textbf{LRU is currently the fastest}, with LFU very close behind.
- The current size-aware heuristic is slightly slower in average latency despite identical hit rate and fetched bytes.

### What this means

This is a useful result for the paper because it shows that under stationary workloads, sophisticated heuristics may not provide a clear advantage over simpler baselines. That supports the broader thesis that \textbf{policy differences become more meaningful under workload shifts and elasticity events}, rather than under easy steady-state conditions.

## 2. Phase-Shifted Workload: Heuristic vs ML (Set B)

### Heuristic

| Phase | Hit Rate | Avg Latency (ms) |
|---|---:|---:|
| phase-1 | 0.7875 | 3.023 |
| phase-2 | 0.3500 | 5.811 |
| phase-3 | 1.0000 | 2.842 |

### ML Set B

| Phase | Hit Rate | Avg Latency (ms) |
|---|---:|---:|
| phase-1 | 0.6250 | 2.636 |
| phase-2 | 0.9125 | 4.750 |
| phase-3 | 1.0000 | 1.932 |

### Interpretation

- The strongest result here is \textbf{phase-2}.
- Under the heuristic, phase-2 hit rate collapses to \textbf{0.35}, while ML Set B sustains \textbf{0.9125}.
- Phase-2 latency also improves from \textbf{5.811 ms} to \textbf{4.750 ms}.
- In phase-3, both policies achieve a perfect hit rate, but ML still has lower latency (\textbf{1.932 ms} vs \textbf{2.842 ms}).
- In phase-1, ML has a lower hit rate than the heuristic, but still slightly lower latency.

### What this means

This suggests the ML policy is already doing something meaningful under \textbf{non-stationary workload transitions}. In particular:

- the ML policy appears more robust to the middle-phase hot-set shift
- the ML policy preserves low latency better once the workload changes
- the heuristic seems more brittle when the dominant working set changes abruptly

This is a strong early result for Update 2 because it aligns directly with the paper’s core motivation: \textbf{predictive tiering should help more under shifting workloads than under stationary ones}.

## 3. Scale-Event Workload: Cold-Start Recovery

### Heuristic

Recovery:

- `scale_out_to_post_scale_hit_rate_delta = +0.2`
- `scale_out_to_post_scale_latency_delta_ms = -3.399`

| Phase | Hit Rate | Avg Latency (ms) |
|---|---:|---:|
| pre-scale | 0.6125 | 3.542 |
| scale-out | 0.8000 | 5.247 |
| post-scale | 1.0000 | 1.848 |
| scale-in | 1.0000 | 2.320 |

### ML Set B

Recovery:

- `scale_out_to_post_scale_hit_rate_delta = +0.2`
- `scale_out_to_post_scale_latency_delta_ms = -4.183`

| Phase | Hit Rate | Avg Latency (ms) |
|---|---:|---:|
| pre-scale | 0.6125 | 3.593 |
| scale-out | 0.8000 | 5.831 |
| post-scale | 1.0000 | 1.648 |
| scale-in | 1.0000 | 1.884 |

### Interpretation

- Both policies show the expected \textbf{cold-start effect}: latency rises during `scale-out`, then drops sharply during `post-scale`.
- Both policies also show identical hit-rate recovery from `scale-out` to `post-scale` (\textbf{+0.2}).
- The ML policy has a slightly \textbf{worse scale-out latency spike} than the heuristic (\textbf{5.831 ms} vs \textbf{5.247 ms}).
- However, once the new worker warms up, ML achieves better steady-state latency:
  - `post-scale`: \textbf{1.648 ms} vs \textbf{1.848 ms}
  - `scale-in`: \textbf{1.884 ms} vs \textbf{2.320 ms}

### What this means

This is a nuanced but useful result:

- ML does \textbf{not} eliminate cold-start pain during scale-out
- but ML appears to \textbf{recover to a better latency regime after warmup}

That is a credible, reportable systems result. It shows that learned placement does not magically remove the fundamental cost of elasticity, but it may still help the system recover more effectively after the working set re-stabilizes.

## 4. Feature Ablation: Set A vs Set B

| Model | Avg Latency (ms) | Cache Hit Rate | Object-Store Bytes Fetched |
|---|---:|---:|---:|
| ML Set A | 3.738 | 0.9000 | 325,058,356 |
| ML Set B | 3.106 | 0.8458 | 330,301,206 |

### Interpretation

- Set A has the \textbf{higher hit rate} (\textbf{0.9000} vs \textbf{0.8458}).
- Set A also fetches slightly \textbf{fewer object-store bytes}.
- But Set B has the \textbf{lower average latency} (\textbf{3.106 ms} vs \textbf{3.738 ms}).

### What this means

This is exactly the kind of subtle outcome the feature-ablation study was supposed to uncover:

- a higher hit rate does not automatically imply lower end-to-end latency
- adding cost/size signals can improve latency even if total cache hits decrease
- cost-aware placement may be selecting \emph{more useful} objects rather than merely \emph{more} objects

This is a strong answer to the graders’ feedback. It shows that the ML feature-design question is now concrete:

- \textbf{Set A} captures reuse signals well
- \textbf{Set B} may better optimize latency/cost tradeoffs

That is precisely the kind of result your paper should highlight.

## Report-Ready Takeaways

These are the main claims that are safe to make in Update 2:

1. Under stationary workloads, simple heuristics perform similarly, with only small latency differences.
2. Under phase-shifted workloads, the ML policy with Set B substantially outperforms the heuristic in the hardest transition phase.
3. Under scale events, both heuristic and ML suffer a cold-start penalty, but ML achieves better post-warmup latency.
4. Feature ablation shows that adding cost/size signals changes the policy behavior in a meaningful way; raw hit rate alone is not the full story.

## Cautions

These results are promising, but the following caveats should be stated clearly in the report:

- The numbers appear to come from \textbf{single pilot runs}, not repeated trials.
- The benchmark suite should ideally be repeated several times and averaged.
- The feature-ablation comparison currently shows a tradeoff rather than a clean dominance result.
- The ML model is still a simple logistic-regression baseline; this is good for interpretability, but should be described as an initial learned policy rather than a fully optimized model.

## Suggested Figures for LaTeX

These are the best figures to add to the Update 2 report:

### Figure 1: Stationary Policy Comparison

Use a bar chart:

- x-axis: `LRU`, `LFU`, `Current Heuristic`
- y-axis: average latency

This figure makes the point that stationary workloads do not strongly separate the policies.

### Figure 2: Phase-Shift Hit Rate by Phase

Use a grouped bar chart or line chart:

- x-axis: `phase-1`, `phase-2`, `phase-3`
- series:
  - heuristic hit rate
  - ML Set B hit rate

This should be one of the main figures because phase-2 is your strongest result.

### Figure 3: Scale-Event Recovery Curve

Use a line chart:

- x-axis: `pre-scale`, `scale-out`, `post-scale`, `scale-in`
- y-axis: average latency
- series:
  - heuristic
  - ML Set B

This shows the cold-start spike and recovery behavior clearly.

### Figure 4: Feature Ablation

Use two small bar charts side by side:

- left: average latency for Set A vs Set B
- right: cache hit rate for Set A vs Set B

This figure is especially important for responding to the graders’ criticism about the under-specified ML story.

## Plot Data for LaTeX

### Stationary Policy Comparison

```text
Policy,AvgLatencyMs,CacheHitRate,ObjectStoreBytes
LRU,2.401,0.8625,188743428
LFU,2.466,0.8625,188743428
CurrentHeuristic,2.797,0.8625,188743428
```

### Phase Shift

```text
Phase,HeuristicHitRate,HeuristicLatencyMs,MLSetBHitRate,MLSetBLatencyMs
phase-1,0.7875,3.023,0.6250,2.636
phase-2,0.3500,5.811,0.9125,4.750
phase-3,1.0000,2.842,1.0000,1.932
```

### Scale Event

```text
Phase,HeuristicHitRate,HeuristicLatencyMs,MLSetBHitRate,MLSetBLatencyMs
pre-scale,0.6125,3.542,0.6125,3.593
scale-out,0.8000,5.247,0.8000,5.831
post-scale,1.0000,1.848,1.0000,1.648
scale-in,1.0000,2.320,1.0000,1.884
```

### Feature Ablation

```text
FeatureSet,AvgLatencyMs,CacheHitRate,ObjectStoreBytes
SetA,3.738,0.9000,325058356
SetB,3.106,0.8458,330301206
```

