# DS-01 measured results
Synthetic five-hour aggregate; each method/budget has 144 gold events. Columns are core hits at 10/20/40/60/80 proposals/hour.
| Method | 10 | 20 | 40 | 60 | 80 |
|---|---:|---:|---:|---:|---:|
| baseline | 11 | 55 | 126 | 130 | 130 |
| weighted | 63 | 113 | 135 | 136 | 137 |
| rrf | 63 | 113 | 135 | 136 | 137 |
| adaptive | 50 | 92 | 135 | 136 | 138 |
| fixed_assoc | 52 | 98 | 135 | 137 | 138 |
| fixed_window | 54 | 94 | 135 | 136 | 138 |
| no_balance | 61 | 116 | 135 | 137 | 138 |
| no_explore | 53 | 101 | 135 | 136 | 137 |

At K=40: baseline core 126/144; context 81/144; extra hits 12; multi-event windows 44/200; footage 15.46 min/hour; mean 2.73ms; peak Python allocations 79.1 KiB. Weighted: 135/144; context 134/144; extra hits 8; multi-event windows 48/200; footage 14.73; mean 12.39ms; peak 172.7 KiB. RRF same hit/context/extra/window/footage counts; mean 11.17ms; peak 170.5 KiB. Adaptive: 135/144; context 134/144; extra hits 7; multi-event windows 48/200; footage 15.30; mean 21.54ms; peak 172.5 KiB. Fixed-window ablation: context 87/144 at same core hits; no_balance: 135/144 core, 134 context, extra hits 5. Timings instrumented and baseline shims differ from production runtime.
15 local targeted tests passed (0.057s on Python 3.13.5); core blob hashes verified. Benchmark generator and raw per-run JSON reproducible via command; aggregate JSON included. Full repository tests not run. Real-world recall, precision, model speed/cost, true false merge, actual entertainment, human evaluation: null. No significance or production superiority claimed.
