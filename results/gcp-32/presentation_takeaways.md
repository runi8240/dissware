# GCP Latest Results: Presentation Takeaways

## Main Interpretation

- The hard benchmark is meaningfully harder than the original: cache hit rates are no longer saturated and evictions occur.
- Under hard phase shift, ML Set B improves hit rate from 36.9% to 60.6%.
- Under hard phase shift, ML Set B reduces object-store traffic from 4.62 GB to 3.0 GB.
- In the eviction stress test, admit-all + LRU reaches 61.9% hit rate with 35 evictions.
- In stationary hard load, LFU gets the highest hit rate among the simple baselines: 59.6% versus LRU at 50.8%.
- The offline ML model is modest but not dominant; Set B ROC AUC is 0.5529.
- In hard scale-event runs, Set C has the best hit rate among ML variants at 44.2%, but it also has high latency, indicating replacement churn is costly.

## What To Say In Class

The original workload was too easy because the working set fit in cache. The hard GCP benchmark increases object diversity, object size, and phase shifts while reducing cache capacity to 32 MB. That exposes real cache pressure: policies now differ in hit rate, object-store traffic, and eviction count.

The learned policies improve hit rate under phase shifts, but the latency result is more nuanced because aggressive admission causes eviction churn. This is a realistic systems tradeoff: maximizing hit rate is not always the same as minimizing end-to-end latency.
