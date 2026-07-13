# PR #6 — human-bottleneck / buried-decision analysis

Quantifies the "before" problem on **SiRumCz/yuanrong-datasystem#6** (a recreation
of upstream gitcode **openeuler/yuanrong-datasystem !1064**, the LogSampler feature):
decisions buried in the thread, too much human involvement, and a volume nobody
can fully read.

## Read this
- **[`REPORT.md`](REPORT.md)** — the analysis report (generated from the data).
- **[`metrics.json`](metrics.json)** — the computed numbers.

## Reproduce
```bash
python3 collect.py          # read-only API pulls -> data/*.json (gh + gitcode REST)
python3 compute_metrics.py  # -> metrics.json + REPORT.md
```
- `collect.py` is **read-only** (GET only). `data/` holds the cached raw inputs so the
  report regenerates offline and the exact inputs are recorded.
- Heuristics (decision-tag set, bot detection, reading-time model) and honest caveats
  are documented in `compute_metrics.py` and in `REPORT.md`'s *Method* section — notably:
  #6's *issue* comments are recreated CI/mirror posts, so human-involvement metrics come
  from the upstream original !1064, while #6's *review* comments are the buried-decision
  corpus analysed here.
