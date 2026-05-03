"""Phase 0.3 latency benchmark.

Usage:
  AGENTOS_MEMORY_URL=http://127.0.0.1:8010 \\
  PAPERCLIP_BOARD_KEY=... \\
  python tools/bench_latency.py [--n 100] [--warmup 5]

Reports p50 / p95 / p99 / max over N calls. Gate target: p99 < 500ms (warm).
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request

from golden_set import GOLDEN_SET


def search(base_url: str, token: str, agent: str, query: str) -> None:
    qs = urllib.parse.urlencode({"agent": agent, "q": query, "limit": 10})
    req = urllib.request.Request(
        f"{base_url}/memory/search?{qs}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()  # drain


def percentile(samples: list[float], p: float) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("AGENTOS_MEMORY_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--token", default=os.environ.get("PAPERCLIP_BOARD_KEY", ""))
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=5)
    args = parser.parse_args()
    if not args.token:
        print("error: PAPERCLIP_BOARD_KEY not set", file=sys.stderr)
        return 2
    if not GOLDEN_SET:
        print("error: golden set is empty", file=sys.stderr)
        return 2

    # Warmup primes the embedding cache (queries + their candidate documents)
    for i in range(args.warmup):
        gq = GOLDEN_SET[i % len(GOLDEN_SET)]
        search(args.url, args.token, gq.agent, gq.query)

    samples_ms: list[float] = []
    for i in range(args.n):
        gq = GOLDEN_SET[i % len(GOLDEN_SET)]
        t0 = time.perf_counter()
        search(args.url, args.token, gq.agent, gq.query)
        samples_ms.append((time.perf_counter() - t0) * 1000)

    p50 = percentile(samples_ms, 50)
    p95 = percentile(samples_ms, 95)
    p99 = percentile(samples_ms, 99)
    mx = max(samples_ms)
    mn = min(samples_ms)
    avg = statistics.mean(samples_ms)

    print(f"n={args.n} warmup={args.warmup} (queries cycle through {len(GOLDEN_SET)} golden entries)")
    print(f"  min   {mn:7.1f} ms")
    print(f"  avg   {avg:7.1f} ms")
    print(f"  p50   {p50:7.1f} ms")
    print(f"  p95   {p95:7.1f} ms")
    print(f"  p99   {p99:7.1f} ms")
    print(f"  max   {mx:7.1f} ms")
    print()
    target = 500.0
    if p99 < target:
        print(f"PASS p99={p99:.1f} ms < {target} ms target")
        return 0
    print(f"FAIL p99={p99:.1f} ms ≥ {target} ms target")
    return 1


if __name__ == "__main__":
    sys.exit(main())
