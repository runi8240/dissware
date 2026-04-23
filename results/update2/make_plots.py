from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FIG_DIR = ROOT / "figures"


def load_summary(name: str) -> dict:
    return json.loads((ROOT / name).read_text())


def write_text(name: str, content: str) -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    (FIG_DIR / name).write_text(content.strip() + "\n")


def stationary_plot() -> None:
    rows = [
        ("LRU", load_summary("lru_stationary.summary.json")),
        ("LFU", load_summary("lfu_stationary.summary.json")),
        ("Size-aware", load_summary("heuristic_stationary.summary.json")),
    ]
    coords_latency = " ".join(f"({label},{summary['average_latency_ms']})" for label, summary in rows)
    coords_hits = " ".join(f"({label},{summary['cache_hit_rate']})" for label, summary in rows)
    content = rf"""
\begin{{tikzpicture}}
\begin{{axis}}[
  width=0.96\columnwidth,
  height=5.2cm,
  ybar,
  bar width=18pt,
  ymin=0,
  ylabel={{Average latency (ms)}},
  symbolic x coords={{LRU,LFU,Size-aware}},
  xtick=data,
  xticklabel style={{font=\small}},
  nodes near coords,
  nodes near coords align={{vertical}},
  enlarge x limits=0.35,
  major grid style={{gray!25}},
  axis line style={{black!65}},
  tick style={{black!65}},
  ymajorgrids=true
]
\addplot[fill=blue!55] coordinates {{{coords_latency}}};
\end{{axis}}
\end{{tikzpicture}}

\vspace{{0.5em}}

\begin{{tikzpicture}}
\begin{{axis}}[
  width=0.96\columnwidth,
  height=5.2cm,
  ybar,
  bar width=18pt,
  ymin=0,
  ymax=1.0,
  ylabel={{Cache hit rate}},
  symbolic x coords={{LRU,LFU,Size-aware}},
  xtick=data,
  xticklabel style={{font=\small}},
  nodes near coords,
  nodes near coords align={{vertical}},
  enlarge x limits=0.35,
  major grid style={{gray!25}},
  axis line style={{black!65}},
  tick style={{black!65}},
  ymajorgrids=true
]
\addplot[fill=teal!60] coordinates {{{coords_hits}}};
\end{{axis}}
\end{{tikzpicture}}
"""
    write_text("stationary_policy_comparison.tex", content)


def phase_shift_plot() -> None:
    heuristic = load_summary("heuristic_phase_shift.summary.json")["phases"]
    ml = load_summary("ml_set_b_phase_shift.summary.json")["phases"]
    phases = list(heuristic.keys())
    coords_h_hit = " ".join(f"({idx},{heuristic[p]['cache_hit_rate']})" for idx, p in enumerate(phases, 1))
    coords_m_hit = " ".join(f"({idx},{ml[p]['cache_hit_rate']})" for idx, p in enumerate(phases, 1))
    coords_h_lat = " ".join(f"({idx},{heuristic[p]['average_latency_ms']})" for idx, p in enumerate(phases, 1))
    coords_m_lat = " ".join(f"({idx},{ml[p]['average_latency_ms']})" for idx, p in enumerate(phases, 1))
    content = rf"""
\begin{{tikzpicture}}
\begin{{axis}}[
  width=0.48\columnwidth,
  height=5.0cm,
  xmin=1, xmax=3,
  ymin=0, ymax=1.05,
  ylabel={{Cache hit rate}},
  xtick={{1,2,3}},
  xticklabels={{phase-1,phase-2,phase-3}},
  legend style={{draw=none, font=\footnotesize, at={{(0.5,1.02)}}, anchor=south, legend columns=1}},
  grid=major
]
\addplot+[mark=*, thick, color=orange!85!black] coordinates {{{coords_h_hit}}};
\addplot+[mark=triangle*, thick, color=green!55!black] coordinates {{{coords_m_hit}}};
\legend{{Size-aware heuristic, ML Set B}}
\end{{axis}}
\end{{tikzpicture}}
\hfill
\begin{{tikzpicture}}
\begin{{axis}}[
  width=0.48\columnwidth,
  height=5.0cm,
  xmin=1, xmax=3,
  ylabel={{Average latency (ms)}},
  xtick={{1,2,3}},
  xticklabels={{phase-1,phase-2,phase-3}},
  grid=major
]
\addplot+[mark=*, thick, color=orange!85!black] coordinates {{{coords_h_lat}}};
\addplot+[mark=triangle*, thick, color=green!55!black] coordinates {{{coords_m_lat}}};
\end{{axis}}
\end{{tikzpicture}}
"""
    write_text("phase_shift_comparison.tex", content)


def scale_event_plot() -> None:
    heuristic = load_summary("heuristic_scale_event.summary.json")["phases"]
    ml = load_summary("ml_set_b_scale_event.summary.json")["phases"]
    phases = list(heuristic.keys())
    coords_h_lat = " ".join(f"({idx},{heuristic[p]['average_latency_ms']})" for idx, p in enumerate(phases, 1))
    coords_m_lat = " ".join(f"({idx},{ml[p]['average_latency_ms']})" for idx, p in enumerate(phases, 1))
    coords_h_hit = " ".join(f"({idx},{heuristic[p]['cache_hit_rate']})" for idx, p in enumerate(phases, 1))
    coords_m_hit = " ".join(f"({idx},{ml[p]['cache_hit_rate']})" for idx, p in enumerate(phases, 1))
    content = rf"""
\begin{{tikzpicture}}
\begin{{axis}}[
  width=0.96\columnwidth,
  height=5.6cm,
  xmin=1, xmax=4,
  ymin=1.2, ymax=6.4,
  ylabel={{Average latency (ms)}},
  xtick={{1,2,3,4}},
  xticklabels={{Pre,Scale-out,Post,Scale-in}},
  legend style={{draw=none, font=\footnotesize, at={{(0.5,1.08)}}, anchor=south, legend columns=2}},
  grid=major,
  major grid style={{gray!25}},
  axis line style={{black!65}},
  tick style={{black!65}}
]
\addplot+[mark=*, mark size=2.7pt, very thick, color=purple!75!black] coordinates {{{coords_h_lat}}};
\addplot+[mark=square*, mark size=2.7pt, very thick, color=green!55!black] coordinates {{{coords_m_lat}}};
\legend{{Size-aware heuristic, ML Set B}}
\end{{axis}}
\end{{tikzpicture}}

\vspace{{0.5em}}

\begin{{tikzpicture}}
\begin{{axis}}[
  width=0.96\columnwidth,
  height=5.2cm,
  xmin=1, xmax=4,
  ymin=0.5, ymax=1.05,
  ylabel={{Cache hit rate}},
  xtick={{1,2,3,4}},
  xticklabels={{Pre,Scale-out,Post,Scale-in}},
  grid=major,
  major grid style={{gray!25}},
  axis line style={{black!65}},
  tick style={{black!65}}
]
\addplot+[mark=*, mark size=2.7pt, very thick, color=purple!75!black] coordinates {{{coords_h_hit}}};
\addplot+[mark=square*, mark size=2.7pt, dashed, very thick, color=green!55!black] coordinates {{{coords_m_hit}}};
\end{{axis}}
\end{{tikzpicture}}
"""
    write_text("scale_event_recovery.tex", content)


def ablation_plot() -> None:
    set_a = load_summary("ml_set_a_phase_shift.summary.json")
    set_b = load_summary("ml_set_b_phase_shift.summary.json")
    coords_latency = f"(SetA,{set_a['average_latency_ms']}) (SetB,{set_b['average_latency_ms']})"
    coords_hits = f"(SetA,{set_a['cache_hit_rate']}) (SetB,{set_b['cache_hit_rate']})"
    content = rf"""
\begin{{tikzpicture}}
\begin{{axis}}[
  width=0.48\columnwidth,
  height=5.0cm,
  ybar,
  bar width=16pt,
  ymin=0,
  ymax=4.2,
  ylabel={{Average latency (ms)}},
  symbolic x coords={{SetA,SetB}},
  xtick=data,
  nodes near coords,
  enlarge x limits=0.35
]
\addplot[fill=blue!55] coordinates {{{coords_latency}}};
\end{{axis}}
\end{{tikzpicture}}
\hfill
\begin{{tikzpicture}}
\begin{{axis}}[
  width=0.48\columnwidth,
  height=5.0cm,
  ybar,
  bar width=16pt,
  ymin=0,
  ymax=1.0,
  ylabel={{Cache hit rate}},
  symbolic x coords={{SetA,SetB}},
  xtick=data,
  nodes near coords,
  enlarge x limits=0.35
]
\addplot[fill=red!60] coordinates {{{coords_hits}}};
\end{{axis}}
\end{{tikzpicture}}
"""
    write_text("feature_ablation.tex", content)


def main() -> None:
    stationary_plot()
    phase_shift_plot()
    scale_event_plot()
    ablation_plot()
    print(f"Wrote pgfplots figures to {FIG_DIR}")


if __name__ == "__main__":
    main()
